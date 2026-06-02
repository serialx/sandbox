#!/bin/bash

browser_ui_language() {
  local raw locale base

  raw="${CHROME_UI_LANG:-${BROWSER_LANG:-en-US}}"
  locale="${raw//-/_}"
  base="${locale%%_*}"

  if [ -z "$locale" ]; then
    return 0
  fi

  if [ "$locale" = "$base" ]; then
    printf '%s' "$base"
    return 0
  fi

  printf '%s:%s' "$locale" "$base"
}
