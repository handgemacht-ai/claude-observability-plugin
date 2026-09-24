"""Check the Langfuse trace of one e2e session.

Usage: python3 tests/e2e/check_e2e.py <session_id> [--agent deeplead] [--wait 120]

Reads LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL from the
environment. Prints PASS or FAIL per check and exits 1 when any check fails.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_BASE_URL = "http://localhost:15300"
DEFAULT_ENVIRONMENT = "plugin-e2e"
FIELDS = "core,basic,time,io,metadata,model,usage"

# The agent chain each lead agent of the fixture project starts, as
# (subagent type, Agent call description) per level.
CHAINS: dict[str, list[tuple[str, str]]] = {
    "deeplead": [("level1", "Level 1"), ("level2", "Level 2"), ("level3", "Level 3")],
    "lead": [("planner", "Plan the chain"), ("worker", "Fetch the code word")],
}


def require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        sys.exit(f"{name} is not set")
    return value


class LangfuseApi:
    def __init__(self) -> None:
        public_key = require_env("LANGFUSE_PUBLIC_KEY")
        secret_key = require_env("LANGFUSE_SECRET_KEY")
        self.base_url = os.environ.get("LANGFUSE_BASE_URL") or DEFAULT_BASE_URL
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self._auth = "Basic " + token

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + path + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"Authorization": self._auth})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    def observations(self, session_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor = None
        while True:
            params: dict[str, Any] = {"sessionId": session_id, "limit": 100, "fields": FIELDS}
            if cursor:
                params["cursor"] = cursor
            page = self.get("/api/public/v2/observations", params)
            items.extend(page.get("data") or [])
            cursor = (page.get("meta") or {}).get("cursor")
            if not cursor or not page.get("data"):
                return items

    def trace_names_and_tags(self, session_id: str) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        query = {
            "view": "observations",
            "dimensions": [{"field": "traceName"}, {"field": "tags"}],
            "metrics": [{"measure": "count", "aggregation": "count"}],
            "filters": [{"column": "sessionId", "operator": "=", "value": session_id, "type": "string"}],
            "fromTimestamp": (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "toTimestamp": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "config": {"row_limit": 50},
        }
        return self.get("/api/public/v2/metrics", {"query": json.dumps(query)}).get("data") or []


def wait_for_observations(api: LangfuseApi, session_id: str, wait_seconds: float) -> list[dict[str, Any]]:
    """Poll until the observation count stops changing, or the wait runs out."""
    deadline = time.monotonic() + wait_seconds
    previous_count = -1
    observations: list[dict[str, Any]] = []
    while True:
        observations = api.observations(session_id)
        if observations and len(observations) == previous_count:
            return observations
        previous_count = len(observations)
        if time.monotonic() >= deadline:
            return observations
        time.sleep(10)


def as_tags(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(tag) for tag in value}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {value}
        return as_tags(parsed)
    return set()


def run_checks(
    observations: list[dict[str, Any]],
    trace_rows: list[dict[str, Any]],
    main_agent: str,
    environment: str,
) -> list[tuple[str, bool, str]]:
    chain = CHAINS[main_agent]
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    trace_ids = sorted({o.get("traceId") for o in observations})
    check("one trace", len(trace_ids) == 1, f"traces={len(trace_ids)} observations={len(observations)}")

    ids = [o.get("id") for o in observations]
    duplicate_ids = len(ids) - len(set(ids))
    check("no duplicate observations", duplicate_ids == 0, f"duplicate ids={duplicate_ids}")

    environments = sorted({str(o.get("environment")) for o in observations})
    check("environment", environments == [environment], f"environments={environments}")

    by_id = {o.get("id"): o for o in observations}
    agents = [o for o in observations if o.get("type") == "AGENT"]
    root_name = f"{main_agent} · Conversational Turn"
    roots = [o for o in agents if o.get("name") == root_name]
    check("root agent", len(roots) == 1, f"{root_name!r} x{len(roots)}")

    parent = roots[0] if len(roots) == 1 else None
    levels_found = 0
    for depth, (agent_type, description) in enumerate(chain, start=1):
        name = f"Subagent: {agent_type} · {description}"
        matches = [o for o in agents if o.get("name") == name]
        if len(matches) != 1 or parent is None:
            check(f"level {depth} agent", False, f"{name!r} x{len(matches)}")
            parent = None
            continue
        agent = matches[0]
        metadata = agent.get("metadata") or {}
        launch = by_id.get(agent.get("parentObservationId")) or {}
        nested = (
            launch.get("type") == "TOOL"
            and launch.get("name") == "Tool: Agent"
            and launch.get("parentObservationId") == parent.get("id")
        )
        depth_ok = str(metadata.get("agent_depth")) == str(depth) and metadata.get("agent_type") == agent_type
        check(
            f"level {depth} agent",
            nested and depth_ok,
            f"{name!r} agent_depth={metadata.get('agent_depth')} under Tool: Agent={nested}",
        )
        if nested and depth_ok:
            levels_found += 1
        parent = agent

    chain_agents = roots[:1] + [
        o for o in agents
        if o.get("name") in {f"Subagent: {t} · {d}" for t, d in chain}
    ]
    without_instructions = [
        agent.get("name") for agent in chain_agents
        if not any(
            o.get("parentObservationId") == agent.get("id") and o.get("name") == "Instructions"
            for o in observations
        )
    ]
    check(
        "Instructions under every agent",
        bool(chain_agents) and not without_instructions,
        f"agents={len(chain_agents)} missing={without_instructions}",
    )

    expected_trace_name = f"{main_agent} · Claude Code Turn"
    expected_tags = {"claude-code", f"agent:{main_agent}"} | {f"subagent:{t}" for t, _ in chain}
    trace_names = sorted({str(row.get("traceName")) for row in trace_rows})
    tags: set[str] = set()
    for row in trace_rows:
        tags |= as_tags(row.get("tags"))
    check("trace name", trace_names == [expected_trace_name], f"trace names={trace_names}")
    check(
        "trace tags",
        expected_tags <= tags,
        f"missing={sorted(expected_tags - tags)} tags={sorted(tags)}",
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session_id")
    parser.add_argument("--agent", default="deeplead", choices=sorted(CHAINS))
    parser.add_argument("--wait", type=float, default=120, help="seconds to wait for ingestion")
    parser.add_argument("--environment", default=DEFAULT_ENVIRONMENT)
    args = parser.parse_args()
    return check_session(args.session_id, args.agent, args.wait, args.environment)


def check_session(
    session_id: str, main_agent: str, wait_seconds: float, environment: str = DEFAULT_ENVIRONMENT
) -> int:
    api = LangfuseApi()
    try:
        observations = wait_for_observations(api, session_id, wait_seconds)
        trace_rows = api.trace_names_and_tags(session_id)
    except urllib.error.HTTPError as error:
        print(f"FAIL Langfuse API error {error.code}: {error.read()[:300].decode(errors='replace')}")
        return 1
    except (urllib.error.URLError, OSError) as error:
        print(f"FAIL Langfuse API not reachable at {api.base_url}: {error}")
        return 1
    results = run_checks(observations, trace_rows, main_agent, environment)
    trace_ids = sorted({o.get("traceId") for o in observations})
    print(f"session_id={session_id} trace_id={','.join(str(t) for t in trace_ids)}")
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
