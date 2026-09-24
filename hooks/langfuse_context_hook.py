#!/usr/bin/env python3
"""Record what Claude Code loads into each agent's context, for langfuse_hook.py.

Runs on InstructionsLoaded and SessionStart. It appends one JSON line per
event to <state dir>/langfuse_context/<session id>.jsonl; the Stop hook reads
that file and attaches the records to the traces.

The hook runs on the hot path of every session start and instruction load, so
it uses the standard library only, does local file I/O only (no network),
prints nothing (SessionStart stdout would enter the model context) and always
exits 0.
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

CONTEXT_DIR_NAME = "langfuse_context"
# A session's record file stops growing here; the Stop hook still reads it.
MAX_CONTEXT_FILE_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_CHARS = 20000
PAYLOAD_TEXT_FIELDS = (
    "agent_id", "agent_type", "memory_type", "load_reason",
    "trigger_file_path", "parent_file_path", "source", "model",
)


def _opt(name: str) -> str:
    return os.environ.get(name) or os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name}") or ""


def _enabled(name: str) -> bool:
    return (_opt(name) or "true").lower() == "true"


def _max_chars() -> int:
    try:
        return int(float(_opt("CC_LANGFUSE_MAX_CHARS") or DEFAULT_MAX_CHARS))
    except ValueError:
        return DEFAULT_MAX_CHARS


def resolve_state_dir() -> Path:
    """Same rule as langfuse_hook.py: an absolute, writable
    CC_LANGFUSE_STATE_DIR wins, else ~/.claude/state."""
    default = Path.home() / ".claude" / "state"
    override = _opt("CC_LANGFUSE_STATE_DIR")
    if not override:
        return default
    try:
        candidate = Path(override).expanduser()
        if not candidate.is_absolute():
            return default
        candidate.mkdir(parents=True, exist_ok=True)
        if not os.access(candidate, os.W_OK | os.X_OK):
            return default
        return candidate
    except Exception:
        return default


def get_context_file_path(state_dir: Path, session_id: str) -> Path:
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:128] or "unknown"
    return state_dir / CONTEXT_DIR_NAME / f"{safe_id}.jsonl"


def tracing_configured() -> bool:
    return any(
        os.environ.get(key)
        for key in (
            "LANGFUSE_PUBLIC_KEY", "CC_LANGFUSE_PUBLIC_KEY",
            "CLAUDE_PLUGIN_OPTION_LANGFUSE_PUBLIC_KEY", "CLAUDE_PLUGIN_OPTION_CC_LANGFUSE_PUBLIC_KEY",
        )
    )


def read_instruction_file(file_path: str, max_chars: int) -> Dict[str, Any]:
    """Hash of the whole file plus its content capped at max_chars."""
    try:
        text = Path(file_path).expanduser().read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"read_error": type(e).__name__}
    record: Dict[str, Any] = {
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "chars": len(text),
    }
    if _enabled("CC_LANGFUSE_CAPTURE_INSTRUCTIONS"):
        record["content"] = text[:max_chars]
        record["truncated"] = len(text) > max_chars
    return record


def already_recorded(context_file: Path, key: tuple) -> bool:
    try:
        with context_file.open(encoding="utf-8") as lines:
            for line in lines:
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if isinstance(record, dict) and record_key(record) == key:
                    return True
    except FileNotFoundError:
        return False
    return False


def record_key(record: Dict[str, Any]) -> tuple:
    return (
        record.get("event"), record.get("agent_id"), record.get("file_path"),
        record.get("sha256"), record.get("load_reason"), record.get("source"),
    )


def build_record(payload: Dict[str, Any], max_chars: int) -> Optional[Dict[str, Any]]:
    event = payload.get("hook_event_name") or payload.get("hookEventName")
    if event not in ("InstructionsLoaded", "SessionStart"):
        return None
    record: Dict[str, Any] = {
        "event": event,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    for key in PAYLOAD_TEXT_FIELDS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            record[key] = value
    if event == "InstructionsLoaded":
        file_path = payload.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            return None
        record["file_path"] = file_path
        globs = payload.get("globs")
        if isinstance(globs, list):
            record["globs"] = [glob for glob in globs if isinstance(glob, str)]
        record.update(read_instruction_file(file_path, max_chars))
    return record


def append_record(state_dir: Path, session_id: str, record: Dict[str, Any]) -> None:
    context_file = get_context_file_path(state_dir, session_id)
    context_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        if context_file.stat().st_size >= MAX_CONTEXT_FILE_BYTES:
            return
    except FileNotFoundError:
        pass
    if already_recorded(context_file, record_key(record)):
        return
    line = json.dumps(record, ensure_ascii=False) + "\n"
    # One O_APPEND write per record keeps concurrent agents' lines whole.
    fd = os.open(str(context_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def main() -> int:
    try:
        if not tracing_configured():
            return 0
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            return 0
        session_id = payload.get("session_id") or payload.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            return 0
        record = build_record(payload, _max_chars())
        if record is not None:
            append_record(resolve_state_dir(), session_id, record)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
