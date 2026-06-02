#!/bin/bash
# Wrapper: always connect agent-browser to the VNC Chrome via CDP.
# Without this, agent-browser daemon launches its own headless Chrome,
# which is invisible in VNC and doesn't share cookies/state.

set -e

CDP_PORT="${BROWSER_REMOTE_DEBUGGING_PORT:-9222}"
SELF="$(readlink -f "$0" 2>/dev/null || printf '%s' "$0")"

resolve_path() {
  readlink -f "$1" 2>/dev/null || printf '%s' "$1"
}

find_real() {
  local real candidate resolved

  # Preferred path after Dockerfile rewrites.
  real="$(command -v agent-browser-real 2>/dev/null || true)"
  if [ -n "$real" ]; then
    real="$(resolve_path "$real")"
    if [ -x "$real" ] && [ "$real" != "$SELF" ]; then
      printf '%s\n' "$real"
      return 0
    fi
  fi

  # Compatibility fallback for older images where the rename-to-real step
  # was not baked in, but the raw binary is still present in fnm/node dirs.
  for candidate in \
    "/home/${USER:-gem}/.fnm_shell/bin/agent-browser" \
    /opt/nodejs/22/bin/agent-browser \
    /opt/nodejs/20/bin/agent-browser \
    /opt/nodejs/24/bin/agent-browser \
    /opt/fnm/node-versions/*/installation/bin/agent-browser \
    /opt/fnm/node-versions/*/installation/lib/node_modules/agent-browser/bin/agent-browser-linux-*; do
    [ -e "$candidate" ] || continue
    resolved="$(resolve_path "$candidate")"
    [ -x "$resolved" ] || continue
    case "$resolved" in
      "$SELF"|/opt/gem/bin/agent-browser)
        continue
        ;;
    esac
    printf '%s\n' "$resolved"
    return 0
  done

  return 1
}

REAL="$(find_real || true)"

if [ -z "$REAL" ]; then
  echo "ERROR: agent-browser real binary not found" >&2
  exit 1
fi

# If user already passed --cdp, don't inject again
for arg in "$@"; do
  if [ "$arg" = "--cdp" ]; then
    exec "$REAL" "$@"
  fi
done

exec "$REAL" --cdp "$CDP_PORT" "$@"
