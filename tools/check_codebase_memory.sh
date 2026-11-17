#!/usr/bin/env bash
# SessionStart check: is the codebase-memory graph usable in this checkout?
#
# Printed as JSON on stdout so Claude Code injects it into the session context
# (and shows the user a message when something is wrong). Never fails the
# session: every problem is reported, not raised.
#
# Registered in .claude/settings.json under hooks.SessionStart.
#
# Reads .mcp.json because the checkouts do not agree on any of it: the server
# is called `codebase-memory` in some and `codebase-memory-mcp` in others (the
# tool prefix follows the name), the command is an absolute path in some and
# `cmd /c codebase-memory-mcp` off PATH in others, and CBM_CACHE_DIR moves the
# graph store away from ~/.cache (IC and the manager keep theirs in C:\cbm).

set -u

root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$root" 2>/dev/null || true

# Windows-style root ("F:/TRADING/..."), which is what the project key derives from.
win_root="$(pwd -W 2>/dev/null || pwd)"
project_key="$(printf '%s' "$win_root" | sed -e 's/://' -e 's#/#-#g')"

mcp_json="$root/.mcp.json"

problems=""
add_problem() { problems="${problems}${problems:+; }$1"; }

# Windows path -> path this shell can stat.
to_unix() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1" 2>/dev/null && return; fi
  printf '%s' "$1" | sed -e 's#\\\\#/#g' -e 's#^\([A-Za-z]\):#/\L\1#'
}

json_string() { sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$mcp_json" | head -1; }

server_name=""; binary=""; cache_dir=""
if [ ! -f "$mcp_json" ]; then
  add_problem ".mcp.json is missing from the checkout root (it is gitignored, so a fresh clone has to recreate it)"
else
  server_name="$(sed -n 's/.*"\([A-Za-z0-9_-]*codebase[A-Za-z0-9_-]*\)"[[:space:]]*:[[:space:]]*{.*/\1/p' "$mcp_json" | head -1)"
  [ -n "$server_name" ] || add_problem ".mcp.json declares no codebase-memory server"

  # The program to look for: the command itself, or what it hands to cmd /c.
  cmd_raw="$(json_string command)"
  args_raw="$(sed -n 's/.*"args"[[:space:]]*:[[:space:]]*\[\([^]]*\)\].*/\1/p' "$mcp_json" | head -1 \
              | tr ',' '\n' | sed -n 's/.*"\([^"]*\)".*/\1/p')"
  for cand in "$cmd_raw" $args_raw; do
    case "$(printf '%s' "$cand" | tr 'A-Z' 'a-z')" in
      ''|cmd|cmd.exe|powershell|powershell.exe|/c|/k|-c|-command) continue ;;
    esac
    case "$cand" in
      */*|*\\*) p="$(to_unix "$cand")"; [ -f "$p" ] && { binary="$cand"; break; } ;;
      *)        command -v "$cand" >/dev/null 2>&1 && { binary="$cand"; break; } ;;
    esac
  done
  if [ -z "$binary" ]; then
    if [ -n "$cmd_raw" ]; then
      add_problem "the server binary in .mcp.json cannot be found (${cmd_raw}${args_raw:+ $(printf '%s' "$args_raw" | tr '\n' ' ')})"
    else
      add_problem ".mcp.json has no command for the codebase-memory server"
    fi
  fi

  cbm_cache="$(json_string CBM_CACHE_DIR)"
  [ -n "$cbm_cache" ] && cache_dir="$(to_unix "$cbm_cache")"
fi
[ -n "$cache_dir" ] || cache_dir="$HOME/.cache/codebase-memory-mcp"

# The graph for THIS checkout has to exist, or every graph query comes back empty.
db="$cache_dir/$project_key.db"
db_info=""
if [ ! -f "$db" ]; then
  add_problem "no graph indexed for $project_key (expected $db) - run index_repository"
else
  db_info="graph $(( $(wc -c < "$db") / 1048576 )) MB in $cache_dir, last indexed $(date -r "$db" '+%Y-%m-%d %H:%M' 2>/dev/null || echo unknown)"
fi

# Working tree moved on since the last index? Only a hint, never a failure.
stale=""
if [ -n "$db_info" ] && command -v git >/dev/null 2>&1; then
  newer="$(git ls-files -z 2>/dev/null \
    | xargs -0 -r ls -1 --time-style=+%s -l 2>/dev/null \
    | awk -v t="$(date -r "$db" +%s 2>/dev/null || echo 0)" '$6 > t {n++} END {print n+0}')"
  [ "${newer:-0}" -gt 0 ] 2>/dev/null && stale=" - $newer tracked file(s) changed since then, consider reindexing"
fi

if [ -z "$problems" ]; then
  printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"codebase-memory READY for %s (%s%s). The tools of this checkout are named mcp__%s__*. Per CLAUDE.md, code discovery starts in the graph: load them with ToolSearch and use search_graph / search_code / get_architecture before Grep or Glob. Confirm they really answer before relying on them; if they do not, say so to the user before anything else."},"suppressOutput":true}\n' \
    "$project_key" "$db_info" "$stale" "$server_name"
else
  printf '{"systemMessage":"codebase-memory NO disponible: %s","hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"codebase-memory IS NOT USABLE: %s. CLAUDE.md requires the graph for code discovery, so tell the user this in your first reply, before starting any work, and say what you will do instead."}}\n' \
    "$problems" "$problems"
fi
