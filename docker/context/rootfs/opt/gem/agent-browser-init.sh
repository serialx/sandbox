#!/bin/bash
# One-shot init: wait for Chrome CDP, inject BROWSER_INIT_COOKIES.
# The native agent-browser daemon starts automatically on first CLI command
# and runs in the background (self-managed via .pid + .sock).
set -e

PORT="${BROWSER_REMOTE_DEBUGGING_PORT:-9222}"

resolve_agent_browser_cmd() {
  if [ -n "${AGENT_BROWSER_CMD:-}" ] && [ -x "${AGENT_BROWSER_CMD}" ]; then
    printf '%s\n' "${AGENT_BROWSER_CMD}"
    return 0
  fi

  local candidate path_cmd
  path_cmd="$(command -v agent-browser 2>/dev/null || true)"
  for candidate in \
    /opt/gem/bin/agent-browser \
    /opt/gem/agent-browser-wrapper.sh \
    "${path_cmd}" \
    /usr/local/bin/agent-browser; do
    [ -n "${candidate}" ] || continue
    [ -x "${candidate}" ] || continue
    printf '%s\n' "${candidate}"
    return 0
  done

  return 1
}

AGENT_BROWSER_CMD="${AGENT_BROWSER_CMD:-$(resolve_agent_browser_cmd || true)}"
export AGENT_BROWSER_CMD

# 1. Wait for Chrome CDP to be ready
for i in $(seq 1 30); do
  if curl -s "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -s "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
  echo "ERROR: Chrome CDP not ready on port ${PORT} after 30s" >&2
  exit 1
fi

echo "Chrome CDP ready on port ${PORT}"

# 2. Inject BROWSER_INIT_COOKIES (if set)
if [ -n "${BROWSER_INIT_COOKIES}" ]; then
  echo "${BROWSER_INIT_COOKIES}" | python3 -c "
import json, os, sys, subprocess

try:
    cookies = json.load(sys.stdin)
except Exception as e:
    print(f'WARNING: Failed to parse BROWSER_INIT_COOKIES: {e}', file=sys.stderr)
    sys.exit(0)

if not cookies:
    sys.exit(0)

agent_browser_cmd = os.environ.get('AGENT_BROWSER_CMD', '').strip()
if not agent_browser_cmd:
    print('WARNING: agent-browser command not found; skipping init cookies', file=sys.stderr)
    sys.exit(0)

ok = 0
for i, c in enumerate(cookies):
    try:
        cmd = [agent_browser_cmd,
               '--cdp', os.environ.get('BROWSER_REMOTE_DEBUGGING_PORT', '9222'),
               'cookies', 'set', c['name'], c['value']]
        if c.get('url'):
            cmd += ['--url', c['url']]
        if c.get('domain'):
            cmd += ['--domain', c['domain']]
        if c.get('path'):
            cmd += ['--path', c['path']]
        if c.get('expires'):
            cmd += ['--expires', str(c['expires'])]
        if c.get('httpOnly') or c.get('http_only'):
            cmd += ['--httpOnly']
        if c.get('secure'):
            cmd += ['--secure']
        if c.get('sameSite') or c.get('same_site'):
            ss = c.get('sameSite') or c.get('same_site')
            cmd += ['--sameSite', ss]
        subprocess.run(cmd, timeout=10, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        ok += 1
    except Exception as e:
        print(f'WARNING: Cookie [{i}] failed: {e}', file=sys.stderr)
print(f'Injected {ok}/{len(cookies)} initial cookies')
"
fi

# 3. Done. The native daemon is now running in the background
#    (auto-started by the first 'agent-browser' command above via the CDP wrapper,
#     or will start lazily on the first CLI command from the user).
echo "agent-browser init complete"
