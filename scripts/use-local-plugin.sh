#!/usr/bin/env bash
# Installs this checkout as the langfuse-observability Claude Code plugin at user
# scope, or rolls back to the official GitHub release.
#
#   scripts/use-local-plugin.sh [switch] [--dry-run]
#   scripts/use-local-plugin.sh rollback [BACKUP_DIR] [--dry-run]
#
# Honours CLAUDE_CONFIG_DIR. Running Claude Code sessions pick up the change
# when they restart.
set -euo pipefail

MARKETPLACE=langfuse-observability
PLUGIN_ID=langfuse-observability@langfuse-observability
OFFICIAL_SOURCE='{"source":"github","repo":"langfuse/Claude-Observability-Plugin"}'

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
SCRIPT="$REPO/scripts/$(basename "${BASH_SOURCE[0]}")"
CFG=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
SETTINGS=$CFG/settings.json
KNOWN=$CFG/plugins/known_marketplaces.json
INSTALLED=$CFG/plugins/installed_plugins.json
MARKETPLACE_CLONE=$CFG/plugins/marketplaces/$MARKETPLACE
# Claude Code keeps its global config inside the config dir only when
# CLAUDE_CONFIG_DIR is set; .config.json is the legacy name it still prefers.
GLOBAL_CONFIGS=("${CLAUDE_CONFIG_DIR:-$HOME}/.claude.json" "$CFG/.config.json")

DRY_RUN=0
BACKUP_DIR=""
NEUTRAL_DIR=""
WORKTREES=()
PROBLEMS=""

usage() {
  cat <<EOF
Usage:
  $SCRIPT [switch] [--dry-run]
      Install this checkout as $PLUGIN_ID (user scope).
      Re-run after committing a new "version" in .claude-plugin/plugin.json.
  $SCRIPT rollback [BACKUP_DIR] [--dry-run]
      Go back to the official GitHub release (or to the GitHub/git source
      saved in BACKUP_DIR/settings.json).

Config dir: ${CLAUDE_CONFIG_DIR:-~/.claude} (set CLAUDE_CONFIG_DIR to use another one).
EOF
}

say() { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}
indent() { sed 's/^/    /'; }

would() {
  printf 'would run:'
  printf ' %q' "$@"
  printf '\n'
}

run() {
  if ((DRY_RUN)); then would "$@"; else "$@"; fi
}

# claude also reads project settings from its working directory; run it from an
# empty directory so that only user settings apply.
claude_cli() {
  (cd "$NEUTRAL_DIR" && claude "$@")
}

run_claude() {
  if ((DRY_RUN)); then would claude "$@"; else claude_cli "$@"; fi
}

cleanup() {
  local status=$?
  if [[ -n $NEUTRAL_DIR ]]; then rm -rf "$NEUTRAL_DIR"; fi
  if ((status != 0)) && [[ -n $BACKUP_DIR ]]; then
    printf '\nStopped with an error. The previous config is backed up in:\n  %s\n' "$BACKUP_DIR" >&2
    printf 'To go back to the official plugin: %s rollback\n' "$SCRIPT" >&2
  fi
  return "$status"
}
trap cleanup EXIT

need_tools() {
  local tool missing=()
  for tool in claude git jq; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
  done
  ((${#missing[@]} == 0)) || die "missing required tools: ${missing[*]}"
}

same_dir() {
  [[ -n $1 && -d $1 ]] && [[ $(cd "$1" && pwd -P) == "$2" ]]
}

same_json() {
  [[ $(jq -cS . <<<"${1:-null}") == $(jq -cS . <<<"${2:-null}") ]]
}

settings_source() {
  if [[ -f $SETTINGS ]]; then
    jq -c --arg m "$MARKETPLACE" '.extraKnownMarketplaces[$m].source // empty' "$SETTINGS"
  fi
}

known_source() {
  if [[ -f $KNOWN ]]; then
    jq -c --arg m "$MARKETPLACE" '.[$m].source // empty' "$KNOWN"
  fi
}

known_location() {
  if [[ -f $KNOWN ]]; then
    jq -r --arg m "$MARKETPLACE" '.[$m].installLocation // empty' "$KNOWN"
  fi
}

installed_entry() {
  claude_cli plugin list --json |
    jq -c --arg id "$PLUGIN_ID" 'first(.[] | select(.id == $id and .scope == "user")) // empty'
}

installed_sha() {
  if [[ -f $INSTALLED ]]; then
    jq -r --arg id "$PLUGIN_ID" \
      'first(.plugins[$id][]? | select(.scope == "user") | .gitCommitSha // empty) // empty' "$INSTALLED"
  fi
}

is_local_source() {
  [[ $(jq -r '.source // empty' <<<"${1:-null}") == directory ]] &&
    same_dir "$(jq -r '.path // empty' <<<"$1")" "$REPO"
}

# Fills WORKTREES with the registered worktrees under .worktrees/ and adds to
# PROBLEMS every reason not to remove them.
check_worktrees() {
  local wt_root=$REPO/.worktrees line path="" locked=0 wt changes ahead stray
  [[ -d $wt_root ]] || return 0

  while IFS= read -r line; do
    case $line in
      "worktree "*)
        path=${line#worktree }
        locked=0
        ;;
      locked | "locked "*) locked=1 ;;
      "")
        if [[ -n $path && -d $path ]]; then
          path=$(cd "$path" && pwd -P)
          if [[ $path == "$wt_root"/* ]]; then
            WORKTREES+=("$path")
            if ((locked)); then PROBLEMS+="  $path is locked (run: git worktree unlock $path)"$'\n'; fi
          fi
        fi
        path=""
        ;;
    esac
  done < <(git -C "$REPO" worktree list --porcelain && echo)

  for wt in ${WORKTREES[@]+"${WORKTREES[@]}"}; do
    changes=$(git -C "$wt" status --porcelain)
    if [[ -n $changes ]]; then
      PROBLEMS+="  $wt has uncommitted changes:"$'\n'"$(indent <<<"$changes")"$'\n'
    fi
    ahead=$(git -C "$REPO" log --oneline "main..$(git -C "$wt" rev-parse HEAD)")
    if [[ -n $ahead ]]; then
      PROBLEMS+="  $wt has commits that are not in main:"$'\n'"$(indent <<<"$ahead")"$'\n'
    fi
  done

  local find_args=("$wt_root" -mindepth 1)
  for wt in ${WORKTREES[@]+"${WORKTREES[@]}"}; do
    find_args+=(-path "$wt" -prune -o)
  done
  find_args+=(! -type d -print)
  stray=$(find "${find_args[@]}")
  if [[ -n $stray ]]; then
    PROBLEMS+="  .worktrees/ holds files outside any registered worktree:"$'\n'"$(indent <<<"$stray")"$'\n'
  fi
}

without_junk() {
  grep -Ev '^(\.worktrees|\.venv|\.pytest_cache)(/|$)|(^|/)__pycache__(/|$)' || true
}

check_repo() {
  local dirty untracked
  dirty=$(git -C "$REPO" status --porcelain --untracked-files=no)
  if [[ -n $dirty ]]; then
    PROBLEMS+="  uncommitted changes to tracked files:"$'\n'"$(indent <<<"$dirty")"$'\n'
  fi
  untracked=$(git -C "$REPO" ls-files --others --exclude-standard --directory | without_junk)
  if [[ -n $untracked ]]; then
    PROBLEMS+="  untracked files (the install would copy them):"$'\n'"$(indent <<<"$untracked")"$'\n'
  fi
  check_worktrees

  if [[ -n $PROBLEMS ]]; then
    printf 'error: refusing to install from %s:\n%s' "$REPO" "$PROBLEMS" >&2
    exit 1
  fi
}

# Claude Code only reinstalls a plugin when its version string changes.
check_version_changed() {
  local entry version sha
  entry=$(installed_entry)
  [[ -n $entry ]] || return 0
  version=$(jq -r '.version' <<<"$entry")
  [[ $version == "$REPO_VERSION" ]] || return 0
  sha=$(installed_sha)
  [[ -n $sha && $sha != "$REPO_HEAD" ]] || return 0
  if ! git -C "$REPO" diff --quiet "$sha" "$REPO_HEAD" -- 2>/dev/null; then
    die "version $version is already installed (from commit ${sha:0:7}), but this checkout is at ${REPO_HEAD:0:7} with other content.
Claude Code only reinstalls when the version changes: bump \"version\" in
.claude-plugin/plugin.json, commit, and run this script again."
  fi
}

make_backup() {
  local dir f
  dir=$CFG/backups/langfuse-plugin-$1-$(date +%Y%m%d-%H%M%S)
  if ((DRY_RUN)); then
    say "would back up the Claude config to $dir"
    return 0
  fi
  mkdir -p "$CFG/backups"
  mkdir -m 700 "$dir"
  BACKUP_DIR=$dir
  for f in "$SETTINGS" "$KNOWN" "$INSTALLED" "${GLOBAL_CONFIGS[@]}"; do
    if [[ -f $f ]]; then cp -p "$f" "$dir/"; fi
  done
  if [[ -d $MARKETPLACE_CLONE ]]; then cp -a "$MARKETPLACE_CLONE" "$dir/marketplace-clone"; fi
  say "Backed up the Claude config to $dir"
}

set_settings_source() {
  local file=$SETTINGS tmp
  if ((DRY_RUN)); then
    say "would set extraKnownMarketplaces.$MARKETPLACE.source in $SETTINGS to $1"
    return 0
  fi
  if [[ -L $file ]]; then file=$(readlink -f "$file"); fi
  tmp=$(mktemp "$(dirname "$file")/.settings.json.XXXXXX")
  cp -p "$file" "$tmp"
  if ! jq --arg m "$MARKETPLACE" --argjson s "$1" '.extraKnownMarketplaces[$m].source = $s' "$file" >"$tmp"; then
    rm -f "$tmp"
    die "could not update $SETTINGS"
  fi
  mv -f "$tmp" "$file"
}

clean_repo() {
  local wt d leftover
  for wt in ${WORKTREES[@]+"${WORKTREES[@]}"}; do
    run git -C "$REPO" worktree remove "$wt"
  done
  run git -C "$REPO" worktree prune
  for d in .worktrees .venv .pytest_cache; do
    if [[ -e $REPO/$d ]]; then run rm -rf "${REPO:?}/$d"; fi
  done
  while IFS= read -r -d '' d; do
    run rm -rf "$d"
  done < <(find "$REPO" -path "$REPO/.git" -prune -o -type d -name __pycache__ -prune -print0)

  leftover=$(git -C "$REPO" ls-files --others --ignored --exclude-standard --directory | without_junk)
  if [[ -n $leftover ]]; then
    warn "the install also copies these ignored files:"$'\n'"$(indent <<<"$leftover")"
  fi
}

install_or_update() {
  local entry
  entry=$(installed_entry)
  if [[ -n $entry ]]; then
    run_claude plugin update "$PLUGIN_ID" --scope user
  else
    run_claude plugin install "$PLUGIN_ID" --scope user
  fi
}

switch() {
  local branch settings_src known_src entry version sha
  [[ -f $REPO/.claude-plugin/plugin.json ]] || die "$REPO/.claude-plugin/plugin.json not found"
  git -C "$REPO" rev-parse --verify -q refs/heads/main >/dev/null || die "$REPO has no local main branch"
  REPO_VERSION=$(jq -r '.version' "$REPO/.claude-plugin/plugin.json")
  REPO_HEAD=$(git -C "$REPO" rev-parse HEAD)
  branch=$(git -C "$REPO" rev-parse --abbrev-ref HEAD)
  if [[ $branch != main ]]; then warn "$REPO is on '$branch', not main; installing what is checked out"; fi

  check_repo
  check_version_changed
  if ((DRY_RUN)); then say "Dry run: nothing will be changed."; fi
  say "Installing $PLUGIN_ID $REPO_VERSION from $REPO ($branch @ ${REPO_HEAD:0:7})"

  make_backup switch
  clean_repo

  settings_src=$(settings_source)
  known_src=$(known_source)
  if is_local_source "$settings_src" && is_local_source "$known_src"; then
    run_claude plugin marketplace update "$MARKETPLACE"
  else
    run_claude plugin marketplace add "$REPO"
  fi
  install_or_update

  if ((DRY_RUN)); then
    say "Dry run finished: nothing was changed."
    return 0
  fi

  entry=$(installed_entry)
  [[ -n $entry ]] || die "$PLUGIN_ID is not installed at user scope"
  version=$(jq -r '.version' <<<"$entry")
  [[ $version == "$REPO_VERSION" ]] || die "installed version is $version, expected $REPO_VERSION"
  settings_src=$(settings_source)
  is_local_source "$settings_src" || die "settings.json does not point $MARKETPLACE at $REPO (found: ${settings_src:-nothing})"
  sha=$(installed_sha)
  if [[ -n $sha && $sha != "$REPO_HEAD" ]]; then
    warn "installed commit ${sha:0:7} is not the checkout's ${REPO_HEAD:0:7}"
  fi

  cat <<EOF

Done: $PLUGIN_ID $version is installed from $REPO.
  Backup:    $BACKUP_DIR
  Sessions:  running Claude Code sessions switch to it when they restart.
  Update:    commit a new "version" in .claude-plugin/plugin.json, then run $SCRIPT
  Roll back: $SCRIPT rollback
EOF
}

rollback() {
  local target=$OFFICIAL_SOURCE saved add_arg current known entry version location upstream_version=""
  if [[ -n $BACKUP_ARG ]]; then
    [[ -f $BACKUP_ARG/settings.json ]] || die "$BACKUP_ARG/settings.json not found"
    saved=$(jq -c --arg m "$MARKETPLACE" '.extraKnownMarketplaces[$m].source // empty' "$BACKUP_ARG/settings.json")
    case $(jq -r '.source // empty' <<<"${saved:-null}") in
      github | git | url) target=$saved ;;
      *) warn "$BACKUP_ARG has no GitHub or git source for $MARKETPLACE (found: ${saved:-nothing}); using the official one" ;;
    esac
  fi
  jq -e '(keys - ["source", "repo", "url"] | length == 0) and ((.repo // .url) | type == "string")' <<<"$target" >/dev/null ||
    die "unsupported marketplace source $target; restore it by hand"
  add_arg=$(jq -r '.repo // .url' <<<"$target")

  if ((DRY_RUN)); then say "Dry run: nothing will be changed."; fi
  say "Restoring $PLUGIN_ID from $add_arg"
  make_backup rollback

  # `marketplace add` refuses a network source while settings still declare
  # another one for the same name, so point the declaration back first.
  current=$(settings_source)
  if [[ -f $SETTINGS ]] && ! same_json "$current" "$target"; then
    set_settings_source "$target"
  fi
  known=$(known_source)
  if same_json "$known" "$target"; then
    run_claude plugin marketplace update "$MARKETPLACE"
  else
    run_claude plugin marketplace add "$add_arg"
  fi
  install_or_update

  if ((DRY_RUN)); then
    say "Dry run finished: nothing was changed."
    return 0
  fi

  entry=$(installed_entry)
  [[ -n $entry ]] || die "$PLUGIN_ID is not installed at user scope"
  version=$(jq -r '.version' <<<"$entry")
  location=$(known_location)
  if [[ -n $location && -f $location/.claude-plugin/plugin.json ]]; then
    upstream_version=$(jq -r '.version // empty' "$location/.claude-plugin/plugin.json")
  fi
  [[ -n $upstream_version ]] || die "cannot read the plugin version from the $MARKETPLACE marketplace clone"
  [[ $version == "$upstream_version" ]] || die "installed version is $version, the marketplace offers $upstream_version"
  current=$(settings_source)
  same_json "$current" "$target" || die "settings.json does not point $MARKETPLACE at $add_arg"

  cat <<EOF

Done: $PLUGIN_ID $version is installed from $add_arg.
  Backup:    $BACKUP_DIR
  Sessions:  running Claude Code sessions switch to it when they restart.
  Back to the fork: $SCRIPT
EOF
}

positional=()
for arg in "$@"; do
  case $arg in
    -n | --dry-run) DRY_RUN=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    -*)
      usage >&2
      die "unknown option: $arg"
      ;;
    *) positional+=("$arg") ;;
  esac
done
MODE=${positional[0]:-switch}
BACKUP_ARG=""
case $MODE in
  switch) ((${#positional[@]} <= 1)) || die "switch takes no arguments" ;;
  rollback)
    ((${#positional[@]} <= 2)) || die "rollback takes at most one backup directory"
    BACKUP_ARG=${positional[1]:-}
    ;;
  *)
    usage >&2
    die "unknown mode: $MODE"
    ;;
esac

need_tools
[[ -d $CFG ]] || die "Claude config dir $CFG not found"
NEUTRAL_DIR=$(mktemp -d)
"$MODE"
