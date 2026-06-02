#!/bin/bash

set -e

. "$(dirname "$0")/browser-locale.sh"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S,%3N') INFO $@"
}

browser_version() {
  local version_output
  version_output="$("${BROWSER_EXECUTABLE_PATH}" --version 2>/dev/null || true)"
  printf '%s' "$version_output" | grep -Eo '[0-9]+(\.[0-9]+){1,3}' | head -n 1
}

default_user_agent() {
  local version
  version="$(browser_version)"
  [ -z "$version" ] && return 1
  printf '%s' "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${version} Safari/537.36"
}

has_user_agent_arg() {
  case " ${BROWSER_COMMANDLINE_ARGS:-} ${BROWSER_EXTRA_ARGS:-} " in
    *" --user-agent="*|*" --user-agent "*) return 0 ;;
    *) return 1 ;;
  esac
}

log "Starting browser..."

# Chromium's internal chrome:// UI localization follows GNU gettext's
# LANGUAGE more reliably than --lang alone on Linux.
browser_language="$(browser_ui_language)"
if [ -n "$browser_language" ]; then
  export LANGUAGE="$browser_language"
fi

readarray -t cmd_args < <(xargs -n1 printf '%s\n' <<<"$BROWSER_COMMANDLINE_ARGS")
readarray -t extra_args < <(xargs -n1 printf '%s\n' <<<"$BROWSER_EXTRA_ARGS")

resolved_user_agent=""
if ! has_user_agent_arg; then
  if [ -n "${BROWSER_USER_AGENT:-}" ]; then
    resolved_user_agent="${BROWSER_USER_AGENT}"
    log "Using explicit browser user agent override."
  else
    resolved_user_agent="$(default_user_agent || true)"
    if [ -n "$resolved_user_agent" ]; then
      log "Using dynamic browser user agent derived from browser version."
    else
      log "Skipping browser user agent override because version detection failed."
    fi
  fi
fi

if [ -n "$resolved_user_agent" ]; then
  extra_args=("--user-agent=${resolved_user_agent}" "${extra_args[@]}")
fi

exec "${BROWSER_EXECUTABLE_PATH}" \
  --user-data-dir="/home/${USER}/.config/browser" \
  "${cmd_args[@]}" \
  "${extra_args[@]}"
