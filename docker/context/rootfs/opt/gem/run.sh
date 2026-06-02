#!/bin/bash
#
# AIO Sandbox + GemBrowser Merged Entrypoint
# This script initializes the sandbox environment and starts supervisord.
#

set -e

# ----------------------
# Timing Functions
# ----------------------
TIMING_START=$(date +%s%3N)
TIMING_LAST=$TIMING_START

timing_checkpoint() {
  local checkpoint_name="$1"
  local now=$(date +%s%3N)
  local elapsed_from_start=$((now - TIMING_START))
  local elapsed_from_last=$((now - TIMING_LAST))
  echo "$(date '+%Y-%m-%d %H:%M:%S,%3N') TIMING [${checkpoint_name}] +${elapsed_from_last}ms (total: ${elapsed_from_start}ms)"
  TIMING_LAST=$now
}

# ----------------------
# Logging Function
# ----------------------
log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S,%3N') INFO $@"
}

normalize_bool() {
  local value
  value="$(echo -n "${1:-}" | xargs | tr '[:upper:]' '[:lower:]')"
  case "$value" in
    1|true|yes|on) echo "true" ;;
    *) echo "false" ;;
  esac
}

normalize_node_version() {
  case "${1:-}" in
    20|node20) echo "node20" ;;
    22|node22|'') echo "node22" ;;
    24|node24) echo "node24" ;;
    *)
      log "WARNING: Unknown NODE_VERSION/NODE_CODE_EXEC_VERSION '${1}', falling back to node22"
      echo "node22"
      ;;
  esac
}

resolve_node_repl_port() {
  local env_name="$1"
  local default_port="$2"
  local current_value="${!env_name:-}"

  if [ -z "${current_value}" ]; then
    printf '%s' "${default_port}"
    return 0
  fi

  if ! [[ "${current_value}" =~ ^[0-9]+$ ]] || [ "${current_value}" -lt 1 ] || [ "${current_value}" -gt 65535 ]; then
    log "ERROR: ${env_name} must be a valid TCP port, got '${current_value}'"
    exit 1
  fi

  printf '%s' "${current_value}"
}

setup_xdg_runtime_dir() {
  if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
    export XDG_RUNTIME_DIR="/run/user/${USER_UID}"
    log "XDG_RUNTIME_DIR not set, defaulting to ${XDG_RUNTIME_DIR}"
  else
    case "$XDG_RUNTIME_DIR" in
      /*) ;;
      *)
        log "ERROR: XDG_RUNTIME_DIR must be an absolute path, got: ${XDG_RUNTIME_DIR}"
        exit 1
        ;;
    esac
    log "Using XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}"
  fi

  mkdir -p /run/user
  mkdir -p "$XDG_RUNTIME_DIR"
  chown $USER:$USER "$XDG_RUNTIME_DIR"
  chmod 700 "$XDG_RUNTIME_DIR"
}

setup_dbus_session_socket() {
  export AIO_DBUS_SESSION_SOCKET="${XDG_RUNTIME_DIR}/dbus-session-bus"

  if [ "$AUTOSTART_CJK_IME" = "true" ]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=${AIO_DBUS_SESSION_SOCKET}"
    log "CJK IME enabled (fcitx5)"
  fi
}

# ----------------------
# Lifecycle Hook Runner
# ----------------------
# Usage: run_hook <hook_name> <env_commands>
run_hook() {
  local hook_name="$1"
  local hook_commands="$2"

  # Trim leading and trailing whitespace while preserving internal formatting
  local trimmed_commands
  trimmed_commands="$(printf '%s' "$hook_commands" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  [ -z "$trimmed_commands" ] && return 0

  local strict_mode
  strict_mode="$(echo -n "${RUN_HOOKS_STRICT:-false}" | xargs | tr '[:upper:]' '[:lower:]')"

  log "Executing $hook_name hook..."
  if [ "$strict_mode" = "true" ]; then
    bash -c "$trimmed_commands"
  else
    bash -c "$trimmed_commands" || log "WARNING: $hook_name hook failed with exit code $? (RUN_HOOKS_STRICT=false, continuing...)"
  fi
  log "$hook_name hook completed."
}

log "Starting AIO Sandbox entrypoint..."
timing_checkpoint "entrypoint_start"

# ----------------------
# Convert DISABLE_* to supervisord autostart format
# ----------------------
export AUTOSTART_JUPYTER=$([ "$DISABLE_JUPYTER" != "true" ] && echo "true" || echo "false")
export AUTOSTART_CODE_SERVER=$([ "$DISABLE_CODE_SERVER" != "true" ] && echo "true" || echo "false")
export AUTOSTART_BROWSER=$([ "$DISABLE_BROWSER" != "true" ] && echo "true" || echo "false")
# MCP_BROWSER depends on browser, disable it if browser is disabled
export AUTOSTART_MCP_BROWSER=$([ "$DISABLE_MCP_BROWSER" != "true" ] && [ "$DISABLE_BROWSER" != "true" ] && echo "true" || echo "false")

# VNC/GUI services (tigervnc, websocat, openbox)
export AUTOSTART_VNC=$([ "$DISABLE_VNC" != "true" ] && echo "true" || echo "false")

# CJK Input Method (fcitx5 + dbus), requires VNC and browser to be enabled
export AUTOSTART_CJK_IME=$([ "$ENABLE_CJK_IME" = "true" ] && [ "$DISABLE_VNC" != "true" ] && [ "$DISABLE_BROWSER" != "true" ] && echo "true" || echo "false")
if [ "$AUTOSTART_CJK_IME" = "true" ]; then
    export GTK_IM_MODULE=fcitx
    export QT_IM_MODULE=fcitx
    export XMODIFIERS="@im=fcitx"
fi

# Node.js REPL config
# - `DISABLE_NODEJS_REPL=true` disables both autostart and on-demand startup
# - `NODEJS_REPL_PORT` overrides the selected default version port
# - `NODEJS_REPL_PORT_20/22/24` override specific version ports
export NODE_VERSION="$(normalize_node_version "${NODE_VERSION:-${NODE_CODE_EXEC_VERSION:-node22}}")"
export DISABLE_NODEJS_REPL="$(normalize_bool "${DISABLE_NODEJS_REPL:-false}")"

export NODEJS_REPL_PORT_20="$(resolve_node_repl_port NODEJS_REPL_PORT_20 8192)"
export NODEJS_REPL_PORT_22="$(resolve_node_repl_port NODEJS_REPL_PORT_22 8092)"
export NODEJS_REPL_PORT_24="$(resolve_node_repl_port NODEJS_REPL_PORT_24 8392)"

if [ -n "${NODEJS_REPL_PORT:-}" ]; then
  LEGACY_NODEJS_REPL_PORT="$(resolve_node_repl_port NODEJS_REPL_PORT 0)"
  case "$NODE_VERSION" in
    node20) export NODEJS_REPL_PORT_20="${LEGACY_NODEJS_REPL_PORT}" ;;
    node22) export NODEJS_REPL_PORT_22="${LEGACY_NODEJS_REPL_PORT}" ;;
    node24) export NODEJS_REPL_PORT_24="${LEGACY_NODEJS_REPL_PORT}" ;;
  esac
fi

if [ "$DISABLE_NODEJS_REPL" = "true" ]; then
  export AUTOSTART_NODEJS_REPL_20="false"
  export AUTOSTART_NODEJS_REPL_22="false"
  export AUTOSTART_NODEJS_REPL_24="false"
else
  export AUTOSTART_NODEJS_REPL_20=$([ "$NODE_VERSION" = "node20" ] && echo "true" || echo "false")
  export AUTOSTART_NODEJS_REPL_22=$([ "$NODE_VERSION" = "node22" ] && echo "true" || echo "false")
  export AUTOSTART_NODEJS_REPL_24=$([ "$NODE_VERSION" = "node24" ] && echo "true" || echo "false")
fi

log "Node.js REPL config: disabled=${DISABLE_NODEJS_REPL}, default_version=${NODE_VERSION}, ports={node20:${NODEJS_REPL_PORT_20},node22:${NODEJS_REPL_PORT_22},node24:${NODEJS_REPL_PORT_24}}"

# ----------------------
# Logging Configuration
# ----------------------
export LOG_TOOL_TRACE="${LOG_TOOL_TRACE:-false}"
export LOG_STDOUT_SERVER="${LOG_STDOUT_SERVER:-false}"

log "Python tool trace: ${LOG_TOOL_TRACE}"
log "Log stdout server: ${LOG_STDOUT_SERVER}"

# ----------------------
# Run Init Hook (very early, before user creation)
# ----------------------
run_hook "RUN_HOOK_INIT" "$RUN_HOOK_INIT"
timing_checkpoint "init_hook"

# ----------------------
# Create Non-root User
# ----------------------
log "Creating user ('$USER') with UID ($USER_UID) and GID ($USER_GID)..."
if ! getent group $USER >/dev/null; then
  groupadd --gid $USER_GID $USER
fi
if ! id -u $USER >/dev/null 2>&1; then
  useradd --uid $USER_UID --gid $USER_GID --shell /bin/bash --create-home $USER
fi

export WORKSPACE="${WORKSPACE:-/home/$USER}"
export BROWSER_DOWNLOAD_DIR_EFFECTIVE="${BROWSER_DOWNLOAD_DIR:-${WORKSPACE}/Downloads}"

# Add user to sudoers with NOPASSWD (only if we have permission)
if [ -w /etc/sudoers.d ]; then
  mkdir -p /etc/sudoers.d
  echo "$USER ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers.d/$USER
  chmod 440 /etc/sudoers.d/$USER
else
  log "Warning: Cannot modify sudoers (running in restricted environment)"
fi
# Ensure home directory is owned by the user (volume mounts may be root-owned)
chown $USER:$USER /home/$USER
timing_checkpoint "create_user"

# ----------------------
# X11 Setup (from gembrowser)
# ----------------------
log "Setting up X11 permissions..."
rm -rf /tmp/.X11-unix  # Clean stale sockets on restart
mkdir -p /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix
chown $USER /tmp/.X11-unix/

# ----------------------
# Create Necessary Directories
# ----------------------
log "Creating necessary directories..."
mkdir -p "$LOG_DIR"
chmod 1777 "$LOG_DIR"
chown $USER "$LOG_DIR"
setup_xdg_runtime_dir
setup_dbus_session_socket

log "Creating service state directory..."
mkdir -p /var/lib/aio-sandbox
chown $USER:$USER /var/lib/aio-sandbox

log "Ensuring workspace download directory exists..."
mkdir -p "$WORKSPACE" "$BROWSER_DOWNLOAD_DIR_EFFECTIVE"
chown $USER:$USER "$BROWSER_DOWNLOAD_DIR_EFFECTIVE"
log "Browser downloads directory: ${BROWSER_DOWNLOAD_DIR_EFFECTIVE}"

log "Setting up Nginx directories..."
mkdir -p /var/lib/nginx
chmod 1777 /var/lib/nginx
chown nobody /var/lib/nginx

NGINX_RUNTIME_DIR=/var/lib/aio-sandbox/nginx
mkdir -p "${NGINX_RUNTIME_DIR}"
chmod 755 /var/lib/aio-sandbox "${NGINX_RUNTIME_DIR}"
chown nobody:root "${NGINX_RUNTIME_DIR}"
for temp_dir in body proxy fastcgi scgi uwsgi; do
  mkdir -p "${NGINX_RUNTIME_DIR}/${temp_dir}"
  chown nobody:root "${NGINX_RUNTIME_DIR}/${temp_dir}"
  chmod 700 "${NGINX_RUNTIME_DIR}/${temp_dir}"
done

chown -R $USER:$USER /opt/jupyter
timing_checkpoint "create_directories"

# Control whether bundled skills are copied into user home.
COPY_SKILLS_TO_USER_HOME_RAW="$(echo -n "${AIO_CLI_SKILL_ENABLED:-false}" | xargs | tr '[:upper:]' '[:lower:]')"
if [ "${COPY_SKILLS_TO_USER_HOME_RAW}" = "true" ]; then
  COPY_SKILLS_TO_USER_HOME="true"
else
  COPY_SKILLS_TO_USER_HOME="false"
fi
log "Copy bundled skills to user home: ${COPY_SKILLS_TO_USER_HOME}"

# ----------------------
# Parallel Setup: User config + DNS/Nginx config run concurrently
# ----------------------
log "Starting parallel setup (user_setup + config)..."

# Group A: User-specific Setup (runs as $USER)
(
  # Pre-render opencode config: OPENCODE_JSON > OPENCODE_* env vars > template
  OPENCODE_RENDERED=""
  OPENCODE_TPL="/opt/gem/opencode/config.json"
  if [ -n "${OPENCODE_JSON}" ]; then
    OPENCODE_RENDERED="$(mktemp)"
    printf "%s\n" "${OPENCODE_JSON}" > "${OPENCODE_RENDERED}"
  elif [ -n "${OPENCODE_API_KEY}" ] && [ -n "${OPENCODE_MODEL}" ] && [ -f "${OPENCODE_TPL}" ]; then
    OPENCODE_PROVIDER="${OPENCODE_PROVIDER:-ark}"
    OPENCODE_BASE_URL="${OPENCODE_BASE_URL:-https://ark.cn-beijing.volces.com/api/v3}"
    OPENCODE_RENDERED="$(mktemp)"
    # Auto-detect SDK: anthropic protocol if URL contains "anthropic", otherwise openai-compatible
    if [ -z "${OPENCODE_PROVIDER_NPM}" ]; then
      case "${OPENCODE_BASE_URL}" in
        *anthropic*) OPENCODE_PROVIDER_NPM="@ai-sdk/anthropic" ;;
        *)           OPENCODE_PROVIDER_NPM="@ai-sdk/openai-compatible" ;;
      esac
    fi
    jq --arg provider "${OPENCODE_PROVIDER}" \
       --arg model "${OPENCODE_PROVIDER}/${OPENCODE_MODEL}" \
       --arg url "${OPENCODE_BASE_URL}" \
       --arg key "${OPENCODE_API_KEY}" \
       --arg mid "${OPENCODE_MODEL}" \
       --arg npm "${OPENCODE_PROVIDER_NPM}" \
       '.model = $model
        | .provider[$provider].npm = $npm
        | .provider[$provider].options.baseURL = $url
        | .provider[$provider].options.apiKey = $key
        | .provider[$provider].models[$mid] = {}' \
       "${OPENCODE_TPL}" > "${OPENCODE_RENDERED}"
  fi
  [ -n "${OPENCODE_RENDERED}" ] && chmod 644 "${OPENCODE_RENDERED}" || true

  su - $USER -c "AIO_CLI_SKILL_ENABLED=${COPY_SKILLS_TO_USER_HOME} BROWSER_LANG=${BROWSER_LANG:-en-US} BROWSER_DOWNLOAD_DIR_EFFECTIVE='${BROWSER_DOWNLOAD_DIR_EFFECTIVE}' OPENCODE_RENDERED='${OPENCODE_RENDERED}' bash -s" << 'EOF'
mkdir -p /home/$USER/.npm-global/lib/node_modules

# 1. Copy default configurations
cp -f /opt/gem/bashrc /home/$USER/.bashrc

# code-server
mkdir -p /home/$USER/.config/code-server /home/$USER/.local/share/code-server \
     && chmod -R 755 /home/$USER/.local/share/code-server/
cp -rf /opt/gem/vscode /home/$USER/.config/code-server/vscode

# jupyter
cp -rf /opt/gem/jupyter /home/$USER/.jupyter

# matplotlib
mkdir -p /home/$USER/.config/matplotlib
cp -f /opt/gem/matplotlibrc /home/$USER/.config/matplotlib/matplotlibrc
rm -rf /home/$USER/.cache/matplotlib/fontlist-*.json 2>/dev/null || true

# fcitx5 (CJK input method, only when enabled)
if [ -d "/opt/gem/fcitx5" ] && [ ! -f "/home/$USER/.config/fcitx5/profile" ]; then
  mkdir -p /home/$USER/.config/fcitx5/conf
  cp -r /opt/gem/fcitx5/* /home/$USER/.config/fcitx5/
fi

# 2. Merge staged configs
if [ -d "/opt/aio-staged-configs" ]; then
  rsync -a /opt/aio-staged-configs/ /home/$USER/
fi

# 3. Agent skills
if [ "${AIO_CLI_SKILL_ENABLED}" = "true" ] && [ -d "/opt/skills" ]; then
  mkdir -p /home/$USER/.agents
  cp -rf /opt/skills /home/$USER/.agents/skills
fi

# 4. Browser preferences
touch "$HOME/.Xauthority"
mkdir -p "$HOME/.config/browser/Default"
cp "/opt/gem/preferences.json" "$HOME/.config/browser/Default/Preferences"

# Configure browser language in Chrome Preferences (--lang flag alone is not enough;
# Chrome reads intl.selected_languages from the profile and ignores --lang if present)
BROWSER_LANGS="${BROWSER_LANG}"
BROWSER_BASE="${BROWSER_LANG%%-*}"
# Add base language if BROWSER_LANG has a region subtag (e.g. zh-CN -> zh)
case "${BROWSER_LANG}" in *-*) BROWSER_LANGS="${BROWSER_LANGS},${BROWSER_BASE}";; esac
# Append en-US,en fallback (skip items already present)
[ "${BROWSER_LANG}" != "en-US" ] && [ "${BROWSER_BASE}" != "en" ] && BROWSER_LANGS="${BROWSER_LANGS},en-US,en"
[ "${BROWSER_LANG}" != "en-US" ] && [ "${BROWSER_BASE}" = "en" ] && BROWSER_LANGS="${BROWSER_LANGS},en-US"
jq --arg langs "${BROWSER_LANGS}" \
  '.intl.selected_languages = $langs | .intl.accept_languages = $langs' \
  "$HOME/.config/browser/Default/Preferences" > "$HOME/.config/browser/Default/Preferences.tmp" \
  && mv "$HOME/.config/browser/Default/Preferences.tmp" "$HOME/.config/browser/Default/Preferences"

# Configure download directory (explicit override or default WORKSPACE/Downloads)
mkdir -p "${BROWSER_DOWNLOAD_DIR_EFFECTIVE}"
jq --arg dir "${BROWSER_DOWNLOAD_DIR_EFFECTIVE}" \
  '.download.default_directory = $dir | .download.directory_upgrade = true | .savefile.default_directory = $dir' \
  "$HOME/.config/browser/Default/Preferences" > "$HOME/.config/browser/Default/Preferences.tmp" \
  && mv "$HOME/.config/browser/Default/Preferences.tmp" "$HOME/.config/browser/Default/Preferences"

# opencode config (rendered by root before su, merge with user's existing config)
if [ -n "${OPENCODE_RENDERED}" ] && [ -f "${OPENCODE_RENDERED}" ]; then
  mkdir -p /home/$USER/.config/opencode
  OPENCODE_TARGET="/home/$USER/.config/opencode/config.json"
  cp -f "${OPENCODE_RENDERED}" "${OPENCODE_TARGET}"
fi
rm -f "${OPENCODE_RENDERED}" 2>/dev/null || true
EOF
) &
PID_USER_SETUP=$!

# Group B: DNS + Nginx Configuration (independent, runs as root)
(
  # DNS over HTTPS Configuration
  TRIMMED_DOH_TEMPLATES="$(echo -n "$DNS_OVER_HTTPS_TEMPLATES" | xargs)"
  if [ -n "$TRIMMED_DOH_TEMPLATES" ]; then
    mkdir -p /etc/browser/policies/managed
    cat >/etc/browser/policies/managed/dns_over_https.json <<DOHEOF
{
  "DnsOverHttpsMode": "secure",
  "DnsOverHttpsTemplates": "$TRIMMED_DOH_TEMPLATES"
}
DOHEOF
  fi

  # URL Blocklist/Allowlist Policy
  TRIMMED_BLOCKLIST="$(echo -n "${BROWSER_URL_BLOCKLIST:-}" | xargs)"
  TRIMMED_ALLOWLIST="$(echo -n "${BROWSER_URL_ALLOWLIST:-}" | xargs)"
  if [ -n "$TRIMMED_BLOCKLIST" ] || [ -n "$TRIMMED_ALLOWLIST" ]; then
    URL_POLICY="{}"
    if [ -n "$TRIMMED_BLOCKLIST" ]; then
      URL_POLICY=$(echo "$URL_POLICY" | jq --argjson list "$(echo "$TRIMMED_BLOCKLIST" | jq -R 'split(",") | map(gsub("^\\s+|\\s+$";""))')" '.URLBlocklist = $list')
    fi
    if [ -n "$TRIMMED_ALLOWLIST" ]; then
      URL_POLICY=$(echo "$URL_POLICY" | jq --argjson list "$(echo "$TRIMMED_ALLOWLIST" | jq -R 'split(",") | map(gsub("^\\s+|\\s+$";""))')" '.URLAllowlist = $list')
    fi
    echo "$URL_POLICY" > /etc/browser/policies/managed/url_filter.json
  fi

  # Nginx listen address configuration
  if [ "${FAAS_SANDBOX_RUNTIME_INJECTION_ENABLE_SANDBOXD}" = "true" ]; then
    export PUBLIC_LISTEN_IPV4="127.0.0.1"
    export PUBLIC_LISTEN_IPV6="[::1]"
  else
    export PUBLIC_LISTEN_IPV4="${PUBLIC_LISTEN_IPV4:-0.0.0.0}"
    export PUBLIC_LISTEN_IPV6="${PUBLIC_LISTEN_IPV6:-[::]}"
  fi

  # Runtime port: platform health check port (e.g. ByteFaaS _BYTEFAAS_RUNTIME_PORT)
  # In sandboxd mode, sandboxd occupies the runtime port and proxies to nginx on PUBLIC_PORT,
  # so nginx must also listen on the runtime port for direct platform health checks.
  if [ "${FAAS_SANDBOX_RUNTIME_INJECTION_ENABLE_SANDBOXD}" = "true" ] && [ -n "$_BYTEFAAS_RUNTIME_PORT" ] && [ "$_BYTEFAAS_RUNTIME_PORT" != "$PUBLIC_PORT" ]; then
    export RUNTIME_LISTEN="listen ${PUBLIC_LISTEN_IPV4}:${_BYTEFAAS_RUNTIME_PORT}; listen ${PUBLIC_LISTEN_IPV6}:${_BYTEFAAS_RUNTIME_PORT};"
  else
    export RUNTIME_LISTEN=""
  fi

  # Gateway timeout policy:
  # - connect timeout stays short for fast failure when upstream is unreachable
  # - read/send timeouts are treated as long idle timeouts, not business timeouts
  export NGINX_PROXY_CONNECT_TIMEOUT="${NGINX_PROXY_CONNECT_TIMEOUT:-5s}"
  export NGINX_API_IDLE_TIMEOUT="${NGINX_API_IDLE_TIMEOUT:-86400s}"
  export NGINX_SESSION_IDLE_TIMEOUT="${NGINX_SESSION_IDLE_TIMEOUT:-86400s}"

  # Auth configuration selection
  AUTH_CONFIG="/opt/gem/nginx-server-with-auth.conf"
  NO_AUTH_CONFIG="/opt/gem/nginx-server-without-auth.conf"
  ACTIVE_CONFIG="/opt/gem/nginx-server-active.conf"
  TRIMMED_JWT_PUBLIC_KEY="$(echo -n "$JWT_PUBLIC_KEY" | xargs)"
  TRIMMED_API_KEY="$(echo -n "${SANDBOX_API_KEY:-}" | xargs)"
  if [ -n "$TRIMMED_JWT_PUBLIC_KEY" ] || [ -n "$TRIMMED_API_KEY" ]; then
    envsubst '${PUBLIC_PORT} ${AUTH_BACKEND_PORT} ${SANDBOX_SRV_PORT} ${PUBLIC_LISTEN_IPV4} ${PUBLIC_LISTEN_IPV6} ${RUNTIME_LISTEN}' <"$AUTH_CONFIG" >"$ACTIVE_CONFIG"
  else
    envsubst '${PUBLIC_PORT} ${PUBLIC_LISTEN_IPV4} ${PUBLIC_LISTEN_IPV6} ${RUNTIME_LISTEN}' <"$NO_AUTH_CONFIG" >"$ACTIVE_CONFIG"
    # Security warning when no authentication is configured
    if [ "$PUBLIC_LISTEN_IPV4" != "127.0.0.1" ] || [ "$PUBLIC_LISTEN_IPV6" != "[::1]" ]; then
      echo ""
      echo "================================================================"
      echo "  WARNING: SECURITY RISK - NO AUTHENTICATION CONFIGURED"
      echo "================================================================"
      echo "  The sandbox is listening on ${PUBLIC_LISTEN_IPV4}:${PUBLIC_PORT}"
      echo "  without any authentication. Anyone on the network can execute"
      echo "  arbitrary code in this container."
      echo ""
      echo "  To secure your sandbox, set one of the following:"
      echo "    - SANDBOX_API_KEY=<your-secret-key>  (recommended)"
      echo "    - JWT_PUBLIC_KEY=<base64-encoded-public-key>"
      echo ""
      echo "  Or restrict access to localhost only:"
      echo "    - PUBLIC_LISTEN_IPV4=127.0.0.1"
      echo "    - PUBLIC_LISTEN_IPV6=[::1]"
      echo "    - docker run -p 127.0.0.1:8080:8080 ..."
      echo "================================================================"
      echo ""
    fi
  fi

  # Generate nginx configs
  envsubst '${BROWSER_REMOTE_DEBUGGING_PORT} ${NGINX_PROXY_CONNECT_TIMEOUT} ${NGINX_SESSION_IDLE_TIMEOUT}' <"/opt/gem/nginx.legacy.conf" >"/opt/gem/nginx/legacy.conf"
  envsubst '${WEBSOCKET_PROXY_PORT}' <"/opt/gem/nginx.vnc.conf" >"/opt/gem/nginx/vnc.conf"

  [ -f "/opt/gem/nginx/nginx.python_srv.conf" ] && \
    envsubst '${SANDBOX_SRV_PORT} ${NGINX_PROXY_CONNECT_TIMEOUT} ${NGINX_API_IDLE_TIMEOUT} ${NGINX_SESSION_IDLE_TIMEOUT}' <"/opt/gem/nginx/nginx.python_srv.conf" >"/opt/gem/nginx/python_srv.conf" && rm -f "/opt/gem/nginx/nginx.python_srv.conf"

  if [ -f "/opt/gem/nginx/nginx.opencode.conf" ]; then
    envsubst '${OPENCODE_PORT}' <"/opt/gem/nginx/nginx.opencode.conf" >"/opt/gem/nginx/opencode.conf" && rm -f "/opt/gem/nginx/nginx.opencode.conf"
  fi

  [ -f "/opt/gem/nginx/nginx.gembrowser_compat.conf" ] && \
    envsubst '${SANDBOX_SRV_PORT} ${NGINX_PROXY_CONNECT_TIMEOUT} ${NGINX_SESSION_IDLE_TIMEOUT}' <"/opt/gem/nginx/nginx.gembrowser_compat.conf" >"/opt/gem/nginx/gembrowser_compat.conf" && rm -f "/opt/gem/nginx/nginx.gembrowser_compat.conf"

  if [ -f "/opt/gem/nginx/nginx.mcp_hub.conf" ]; then
    envsubst '${SANDBOX_SRV_PORT}' <"/opt/gem/nginx/nginx.mcp_hub.conf" >"/opt/gem/nginx/mcp_hub.conf" && rm -f "/opt/gem/nginx/nginx.mcp_hub.conf"

    EXTRA_MCP_SERVERS="${EXTRA_MCP_SERVERS#"${EXTRA_MCP_SERVERS%%[![:space:]]*}"}"
    EXTRA_MCP_SERVERS="${EXTRA_MCP_SERVERS%"${EXTRA_MCP_SERVERS##*[![:space:]]}"}"
    if [ -n "${EXTRA_MCP_SERVERS}" ]; then
      if ! echo "${EXTRA_MCP_SERVERS}" | jq -e '.' > /dev/null 2>&1; then
        echo "ERROR: EXTRA_MCP_SERVERS JSON format invalid" >&2
        exit 1
      fi
      jq --argjson extra "${EXTRA_MCP_SERVERS}" \
         '.mcpServers = ((.mcpServers + $extra) | with_entries(select(.value != null)))' \
         /opt/gem/mcp-hub.json.template > /opt/gem/mcp-hub.json.template.tmp \
      && mv /opt/gem/mcp-hub.json.template.tmp /opt/gem/mcp-hub.json.template
    fi
    envsubst '${SANDBOX_SRV_PORT} ${MCP_SERVER_BROWSER_PORT} ${BROWSER_REMOTE_DEBUGGING_PORT}' < /opt/gem/mcp-hub.json.template > /opt/gem/mcp-hub.json && rm -f /opt/gem/mcp-hub.json.template
  fi

  [ -f "/opt/gem/nginx/nginx.jupyter_lab.conf" ] && \
    envsubst '${JUPYTER_LAB_PORT}' <"/opt/gem/nginx/nginx.jupyter_lab.conf" >"/opt/gem/nginx/jupyter_lab.conf" && rm -f "/opt/gem/nginx/nginx.jupyter_lab.conf"
  [ -f "/opt/gem/nginx/nginx.code_server.conf" ] && \
    envsubst '${CODE_SERVER_PORT}' <"/opt/gem/nginx/nginx.code_server.conf" >"/opt/gem/nginx/code_server.conf" && rm -f "/opt/gem/nginx/nginx.code_server.conf"
  [ -f "/opt/gem/nginx/nginx.ui_browser.conf" ] && \
    envsubst '${BROWSER_REMOTE_DEBUGGING_PORT} ${NGINX_PROXY_CONNECT_TIMEOUT} ${NGINX_SESSION_IDLE_TIMEOUT}' <"/opt/gem/nginx/nginx.ui_browser.conf" >"/opt/gem/nginx/ui_browser.conf" && rm -f "/opt/gem/nginx/nginx.ui_browser.conf"

  envsubst '${PUBLIC_PORT} ${PUBLIC_LISTEN_IPV4} ${PUBLIC_LISTEN_IPV6}' <"/opt/gem/nginx-server-port-proxy.conf.template" >"/opt/gem/nginx-server-port-proxy.conf"

) &
PID_CONFIG=$!

# Wait for both parallel tasks
wait $PID_USER_SETUP || { log "ERROR: user_setup failed"; exit 1; }
wait $PID_CONFIG || { log "ERROR: config setup failed"; exit 1; }
timing_checkpoint "parallel_setup"

# ----------------------
# Environment Variables Setup
# ----------------------
export IMAGE_VERSION=$(cat /etc/aio_version 2>/dev/null || echo "unknown")

# Workspace directory (global for all services)
export WORKSPACE="${WORKSPACE:-/home/$USER}"
mkdir -p "$WORKSPACE"
chown "$USER:$USER" "$WORKSPACE"

# AIO_USER: Trusted user identity for sandboxd integration
# If AIO_USER is set, use it to override USER; otherwise fallback to USER
export AIO_USER="${AIO_USER:-$USER}"
export USER="$AIO_USER"

# Node.js version setup (symlinks already exist, just export the version var)
export NODE_CODE_EXEC_VERSION="${NODE_CODE_EXEC_VERSION:-$NODE_VERSION}"
export NGINX_LOG_LEVEL=${NGINX_LOG_LEVEL:-debug}
# Source system-wide Node.js/fnm env (same config used by interactive shells)
# Override HOME so nodejs.sh resolves paths for the sandbox user, not root
export HOME="/home/$USER"
. /etc/profile.d/nodejs.sh
export HOMEPAGE=${HOMEPAGE:-""}
# Resolve BROWSER_LANG to a locale pak that actually exists (e.g. ja-JP -> ja)
BROWSER_LANG_RESOLVED="${BROWSER_LANG:-en-US}"
# Derive locales from browser binary location (works for both /opt/browser and system installs)
BROWSER_BIN=$(readlink -f /usr/local/bin/browser 2>/dev/null || true)
LOCALES_DIR="$(dirname "${BROWSER_BIN:-/opt/browser/chrome}")/locales"
if [ ! -f "${LOCALES_DIR}/${BROWSER_LANG_RESOLVED}.pak" ]; then
  BROWSER_LANG_BASE="${BROWSER_LANG_RESOLVED%%-*}"
  if [ -f "${LOCALES_DIR}/${BROWSER_LANG_BASE}.pak" ]; then
    BROWSER_LANG_RESOLVED="${BROWSER_LANG_BASE}"
  fi
fi
export BROWSER_EXTRA_ARGS="${BROWSER_NO_SANDBOX} --lang=${BROWSER_LANG_RESOLVED} --time-zone-for-testing=${TZ} --homepage ${HOMEPAGE} ${BROWSER_EXTRA_ARGS}"
timing_checkpoint "env_vars_setup"

# ----------------------
# GOST Proxy Configuration (replaces tinyproxy)
# ----------------------
PROXY_SERVER="$(echo -n "$PROXY_SERVER" | xargs)"
if [ -n "${PROXY_SERVER}" ]; then
  PROXY_SERVER=${PROXY_SERVER#\"}
  PROXY_SERVER=${PROXY_SERVER%\"}
  PROXY_SERVER=${PROXY_SERVER#http://}
  PROXY_SERVER=${PROXY_SERVER#https://}

  GOST_CONFIG="/opt/gem/gost.yaml"
  GOST_HOSTS_FILE="/opt/gem/gost-hosts.txt"
  GOST_BYPASS_FILE="/opt/gem/gost-bypass.txt"
  PROXY_MAP_JSON="/var/lib/aio-sandbox/proxy-map.json"
  NGINX_PROXY_MAP_CONF="/opt/gem/nginx-proxy-map.conf"
  PROXY_PORT="${TINYPROXY_PORT:-8118}"
  NGINX_PROXY_MAP_PORT=80

  USER_BYPASS_JSON="/var/lib/aio-sandbox/proxy-map-user-bypasses.json"

  # Initialize files
  > "${GOST_HOSTS_FILE}"
  > "${GOST_BYPASS_FILE}"
  > "${NGINX_PROXY_MAP_CONF}"
  echo "[]" > "${PROXY_MAP_JSON}"
  echo "[]" > "${USER_BYPASS_JSON}"

  # --- Generate self-signed TLS cert for proxy-map HTTPS termination ---
  PROXY_MAP_TLS_KEY="/opt/gem/proxy-map-tls.key"
  PROXY_MAP_TLS_CRT="/opt/gem/proxy-map-tls.crt"

  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "${PROXY_MAP_TLS_KEY}" \
    -out "${PROXY_MAP_TLS_CRT}" \
    -days 3650 -subj "/CN=AIO Sandbox Proxy Map" \
    2>/dev/null

  # Extract SPKI hash — tells Chromium to trust this specific key for any hostname
  PROXY_MAP_SPKI=$(openssl x509 -in "${PROXY_MAP_TLS_CRT}" -pubkey -noout \
    | openssl pkey -pubin -outform DER \
    | openssl dgst -sha256 -binary \
    | base64)

  log "Generated proxy-map TLS cert (SPKI: ${PROXY_MAP_SPKI})"

  # --- PROXY_AUTH_CMD: execute a shell command to obtain proxy credentials ---
  # The command stdout should be "username:password".
  # The result is prepended to PROXY_SERVER as user:pass@host:port.
  PROXY_AUTH_CMD="${PROXY_AUTH_CMD:-}"
  if [ -n "${PROXY_AUTH_CMD}" ] && [ "${PROXY_SERVER}" != "true" ]; then
    PROXY_AUTH_STDERR=$(mktemp)
    PROXY_AUTH_RESULT=$(bash -c "${PROXY_AUTH_CMD}" 2>"${PROXY_AUTH_STDERR}") || {
      log "WARNING: PROXY_AUTH_CMD failed: $(cat "${PROXY_AUTH_STDERR}")"
    }
    rm -f "${PROXY_AUTH_STDERR}"
    if [ -n "${PROXY_AUTH_RESULT}" ]; then
      # Strip any existing auth from PROXY_SERVER before injecting
      PROXY_SERVER_CLEAN="${PROXY_SERVER}"
      if [[ "${PROXY_SERVER_CLEAN}" == *"@"* ]]; then
        PROXY_SERVER_CLEAN="${PROXY_SERVER_CLEAN##*@}"
      fi
      PROXY_SERVER="${PROXY_AUTH_RESULT}@${PROXY_SERVER_CLEAN}"
      log "PROXY_AUTH_CMD: injected auth into PROXY_SERVER (addr=${PROXY_SERVER_CLEAN})"
    else
      log "WARNING: PROXY_AUTH_CMD returned empty output, skipping auth injection"
    fi
  fi

  # --- Parse PROXY_SERVER auth credentials ---
  # PROXY_SERVER may be in the format user:pass@host:port (e.g. from rd-proxy/ZTI).
  # GOST requires addr to be host:port only; auth goes in connector.auth.
  PROXY_ADDR="${PROXY_SERVER}"
  PROXY_AUTH_USER=""
  PROXY_AUTH_PASS=""
  if [[ "${PROXY_SERVER}" == *"@"* ]] && [ "${PROXY_SERVER}" != "true" ]; then
    PROXY_AUTH_PART="${PROXY_SERVER%@*}"
    PROXY_ADDR="${PROXY_SERVER##*@}"
    # Only parse auth if the part before @ contains a colon (user:pass)
    if [[ "${PROXY_AUTH_PART}" == *":"* ]]; then
      PROXY_AUTH_USER="${PROXY_AUTH_PART%%:*}"
      PROXY_AUTH_PASS="${PROXY_AUTH_PART#*:}"
    fi
    if [ -n "${PROXY_AUTH_USER}" ] && [ -n "${PROXY_AUTH_PASS}" ]; then
      log "Parsed PROXY_SERVER: addr=${PROXY_ADDR}, auth_user=${PROXY_AUTH_USER:0:10}..."
    else
      log "WARNING: PROXY_SERVER contains '@' but auth credentials are incomplete, ignoring auth"
      PROXY_AUTH_USER=""
      PROXY_AUTH_PASS=""
    fi
  fi

  # --- Generate GOST config ---
  cat > "${GOST_CONFIG}" <<GOST_YAML_EOF
api:
  addr: "127.0.0.1:18080"

services:
  - name: browser-proxy
    addr: "127.0.0.1:${PROXY_PORT}"
    handler:
      type: http
$(if [ "${PROXY_SERVER}" != "true" ]; then echo "      chain: upstream"; fi)
    listener:
      type: tcp
    hosts: proxy-map

$(if [ "${PROXY_SERVER}" != "true" ]; then cat <<CHAIN_INNER_EOF
chains:
  - name: upstream
    hops:
      - name: hop-0
        bypass: proxy-bypass
        nodes:
          - name: upstream-proxy
            addr: "${PROXY_ADDR}"
            connector:
              type: http
$(if [ -n "${PROXY_AUTH_USER}" ]; then cat <<AUTH_EOF
              auth:
                username: "${PROXY_AUTH_USER}"
                password: "${PROXY_AUTH_PASS}"
AUTH_EOF
fi)
            dialer:
              type: tcp
CHAIN_INNER_EOF
fi)

hosts:
  - name: proxy-map
    reload: 3s
    file:
      path: ${GOST_HOSTS_FILE}

bypasses:
  - name: proxy-bypass
    reload: 3s
    file:
      path: ${GOST_BYPASS_FILE}
GOST_YAML_EOF

  log "GOST config generated at ${GOST_CONFIG}"

  # --- Parse PROXY_MAP (domain grouping: same domain -> one nginx server block) ---
  #
  # Format: source>target[,source>target,...]
  #   source: [protocol://]host[:port][/path]  (supports wildcard * in host)
  #   target: [host:]port[/path]  (host defaults to 127.0.0.1)
  #
  # Same-domain entries are grouped into one nginx server block with multiple locations.
  #
  PROXY_MAP_VAL="$(echo -n "${PROXY_MAP:-}" | xargs)"
  if [ -n "${PROXY_MAP_VAL}" ]; then
    # We need associative arrays for domain grouping
    declare -A DOMAIN_LOCATIONS      # domain -> nginx location blocks (accumulated)
    declare -A DOMAIN_SEEN           # domain -> 1 (tracks unique domains)
    MAPPINGS_JSON="["
    FIRST=true

    IFS=',' read -ra MAP_ENTRIES <<< "$PROXY_MAP_VAL"
    for entry in "${MAP_ENTRIES[@]}"; do
      entry=$(echo "$entry" | xargs)
      [ -z "$entry" ] && continue
      source_part="${entry%%>*}"
      target_part="${entry##*>}"

      if [ -z "$source_part" ] || [ -z "$target_part" ]; then
        log "WARNING: Invalid PROXY_MAP entry '${entry}', skipping"
        continue
      fi

      # Extract host from source (strip protocol and path)
      source_host="${source_part}"
      source_host="${source_host#*://}"    # strip protocol
      source_host="${source_host%%/*}"     # strip path
      # Warn about source port (ignored at GOST level)
      if echo "$source_host" | grep -qE ':[0-9]+$' && ! echo "$source_host" | grep -q '^\*'; then
        source_port_part="${source_host##*:}"
        log "WARNING: Source port :${source_port_part} in '${source_part}' is ignored — all ports for this domain will be mapped"
      fi
      source_host="${source_host%%:*}"     # strip port (GOST hosts can't distinguish by port)

      # Extract path from source (if any)
      source_path="/"
      source_raw_no_proto="${source_part#*://}"
      if echo "$source_raw_no_proto" | grep -q '/'; then
        source_path="/${source_raw_no_proto#*/}"
        # Normalize: strip trailing * (it's just a visual hint, nginx does prefix match)
        source_path="${source_path%\*}"
        # Ensure trailing slash for prefix matching (except for root /)
        if [ "$source_path" != "/" ] && [ "${source_path: -1}" != "/" ]; then
          source_path="${source_path}/"
        fi
      fi

      # Expand target shorthand: ":3000" -> "127.0.0.1:3000"
      if [ "${target_part:0:1}" = ":" ]; then
        target_part="127.0.0.1${target_part}"
      fi
      target_host_port="${target_part%%/*}"
      target_path=""
      if echo "$target_part" | grep -q '/'; then
        target_path="/${target_part#*/}"
        target_path="${target_path%\*}"
      fi

      # Domain grouping: track unique domains
      if [ -z "${DOMAIN_SEEN[$source_host]+_}" ]; then
        DOMAIN_SEEN[$source_host]=1
        DOMAIN_LOCATIONS[$source_host]=""
      fi

      # Accumulate nginx location block for this domain
      DOMAIN_LOCATIONS[$source_host]+="
        location ${source_path} {
            proxy_pass http://${target_host_port}${target_path};
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_set_header Upgrade \$http_upgrade;
            proxy_set_header Connection \$connection_upgrade;
            proxy_http_version 1.1;
            proxy_read_timeout 600s;
            proxy_send_timeout 600s;
        }"

      # JSON state
      if [ "$FIRST" = true ]; then FIRST=false; else MAPPINGS_JSON+=","; fi
      MAPPINGS_JSON+="{\"source\":\"${source_part}\",\"target\":\"${target_part}\",\"source_host\":\"${source_host}\",\"source_path\":\"${source_path}\",\"internal_port\":${NGINX_PROXY_MAP_PORT}}"

      log "Proxy map: ${source_part} -> ${target_part} (domain group ${source_host} via nginx :${NGINX_PROXY_MAP_PORT})"
    done

    MAPPINGS_JSON+="]"
    echo "${MAPPINGS_JSON}" > "${PROXY_MAP_JSON}"

    # Generate per-domain nginx server blocks and GOST hosts entries
    for domain in "${!DOMAIN_SEEN[@]}"; do
      # GOST hosts: domain -> 127.0.0.1 (GOST uses ".domain" for wildcards, not "*.domain")
      gost_host="${domain}"
      if [ "${gost_host:0:2}" = "*." ]; then
        gost_host="${gost_host:1}"  # *.example.com -> .example.com
      fi
      echo "127.0.0.1 ${gost_host}" >> "${GOST_HOSTS_FILE}"

      # Domain-level bypass entries (for documentation/service-level matching)
      echo "${domain}" >> "${GOST_BYPASS_FILE}"

      # nginx server block with server_name for Host-based routing
      cat >> "${NGINX_PROXY_MAP_CONF}" <<NGINX_BLOCK_EOF
server {
    listen 127.0.0.1:${NGINX_PROXY_MAP_PORT};
    listen 127.0.0.1:443 ssl;
    ssl_certificate ${PROXY_MAP_TLS_CRT};
    ssl_certificate_key ${PROXY_MAP_TLS_KEY};
    server_name ${domain};
    client_max_body_size 0;
${DOMAIN_LOCATIONS[$domain]}
}

NGINX_BLOCK_EOF
    done

    # Add 127.0.0.1 to bypass — GOST hop-level bypass checks the resolved IP,
    # and all mapped domains resolve to 127.0.0.1 via hosts.
    echo "127.0.0.1" >> "${GOST_BYPASS_FILE}"
  fi

  # --- Parse PROXY_EXCLUDE ---
  PROXY_EXCLUDE_VAL="$(echo -n "${PROXY_EXCLUDE:-}" | xargs)"
  if [ -n "${PROXY_EXCLUDE_VAL}" ]; then
    # Write to user bypasses JSON
    BYPASS_JSON="["
    FIRST_BYPASS=true
    IFS=',' read -ra EXCLUDES <<< "$PROXY_EXCLUDE_VAL"
    for pattern in "${EXCLUDES[@]}"; do
      pattern=$(echo "$pattern" | xargs)
      echo "${pattern}" >> "${GOST_BYPASS_FILE}"
      if [ "$FIRST_BYPASS" = true ]; then FIRST_BYPASS=false; else BYPASS_JSON+=","; fi
      BYPASS_JSON+="\"${pattern}\""
    done
    BYPASS_JSON+="]"
    echo "${BYPASS_JSON}" > "${USER_BYPASS_JSON}"
    log "GOST bypass (exclude): ${PROXY_EXCLUDE_VAL}"
  fi

  # --- Parse PROXY_INCLUDE (whitelist mode, overrides EXCLUDE) ---
  PROXY_INCLUDE_VAL="$(echo -n "${PROXY_INCLUDE:-}" | xargs)"
  if [ -n "${PROXY_INCLUDE_VAL}" ]; then
    # Clear excludes — INCLUDE takes priority
    # Keep PROXY_MAP bypass entries (they must always bypass chain)
    PROXY_MAP_BYPASSES=""
    if [ -n "${PROXY_MAP_VAL}" ]; then
      for domain in "${!DOMAIN_SEEN[@]}"; do
        PROXY_MAP_BYPASSES+="${domain}"$'\n'
      done
      PROXY_MAP_BYPASSES+="127.0.0.1"$'\n'
    fi
    echo -n "${PROXY_MAP_BYPASSES}" > "${GOST_BYPASS_FILE}"

    # Write to user bypasses JSON and bypass file
    BYPASS_JSON="["
    FIRST_BYPASS=true
    IFS=',' read -ra INCLUDES <<< "$PROXY_INCLUDE_VAL"
    for pattern in "${INCLUDES[@]}"; do
      pattern=$(echo "$pattern" | xargs)
      echo "${pattern}" >> "${GOST_BYPASS_FILE}"
      if [ "$FIRST_BYPASS" = true ]; then FIRST_BYPASS=false; else BYPASS_JSON+=","; fi
      BYPASS_JSON+="\"${pattern}\""
    done
    BYPASS_JSON+="]"
    echo "${BYPASS_JSON}" > "${USER_BYPASS_JSON}"
    # Set bypass to whitelist mode in gost config
    sed -i 's/name: proxy-bypass/name: proxy-bypass\n    whitelist: true/' "${GOST_CONFIG}"
    log "GOST bypass (include/whitelist): ${PROXY_INCLUDE_VAL}"
  fi

  # --- PAC file generation (unchanged logic, controls browser-side bypass) ---
  TRIMMED_BYPASS="$(echo -n "${PROXY_BYPASS_LIST:-}" | xargs)"
  if [ -n "${TRIMMED_BYPASS}" ]; then
    PAC_FILE="/opt/gem/proxy.pac"
    {
      echo 'function FindProxyForURL(url, host) {'
      IFS=',' read -ra BYPASS_DOMAINS <<< "$TRIMMED_BYPASS"
      for domain in "${BYPASS_DOMAINS[@]}"; do
        domain=$(echo "$domain" | xargs)
        clean_domain="${domain#\*.}"
        echo "  if (dnsDomainIs(host, \"${clean_domain}\") || host === \"${clean_domain}\") return \"DIRECT\";"
      done
      echo "  return \"PROXY 127.0.0.1:${PROXY_PORT}\";"
      echo '}'
    } > "$PAC_FILE"
    export BROWSER_EXTRA_ARGS="${BROWSER_EXTRA_ARGS} --proxy-pac-url=file://${PAC_FILE}"
    log "Generated PAC file at ${PAC_FILE} with bypass: ${TRIMMED_BYPASS}"
  else
    export BROWSER_EXTRA_ARGS="${BROWSER_EXTRA_ARGS} --proxy-server=http://127.0.0.1:${PROXY_PORT}"
  fi

  # Trust the proxy-map self-signed cert via SPKI hash
  export BROWSER_EXTRA_ARGS="${BROWSER_EXTRA_ARGS} --ignore-certificate-errors-spki-list=${PROXY_MAP_SPKI}"

  # Ensure proxy config files are writable by the sandbox user (for runtime API)
  chown $USER:$USER "${GOST_HOSTS_FILE}" "${GOST_BYPASS_FILE}" "${PROXY_MAP_JSON}" "${NGINX_PROXY_MAP_CONF}" "${USER_BYPASS_JSON}" 2>/dev/null || true
  chmod 644 "${PROXY_MAP_TLS_CRT}" 2>/dev/null || true
  chmod 600 "${PROXY_MAP_TLS_KEY}" 2>/dev/null || true
else
  rm -f /opt/gem/supervisord/supervisord.gost.conf
  # Ensure nginx-proxy-map.conf exists even without PROXY_SERVER,
  # since nginx.conf unconditionally includes it.
  touch /opt/gem/nginx-proxy-map.conf
fi
timing_checkpoint "gost_config"

# ----------------------
# Generate Index Page
# ----------------------
if [ -f "/opt/aio/index.html.template" ]; then
  envsubst '${DISABLE_JUPYTER},${DISABLE_CODE_SERVER}' \
    < /opt/aio/index.html.template > /opt/aio/index.html
  rm -rf /opt/aio/index.html.template
fi
timing_checkpoint "index_page"

# ----------------------
# Display Startup Banner
# ----------------------
print_banner() {
  echo ""
  echo -e "\033[36m █████╗ ██╗ ██████╗     ███████╗ █████╗ ███╗   ██╗██████╗ ██████╗  ██████╗ ██╗  ██╗\033[0m"
  echo -e "\033[36m██╔══██╗██║██╔═══██╗    ██╔════╝██╔══██╗████╗  ██║██╔══██╗██╔══██╗██╔═══██╗╚██╗██╔╝\033[0m"
  echo -e "\033[36m███████║██║██║   ██║    ███████╗███████║██╔██╗ ██║██║  ██║██████╔╝██║   ██║ ╚███╔╝\033[0m"
  echo -e "\033[36m██╔══██║██║██║   ██║    ╚════██║██╔══██║██║╚██╗██║██║  ██║██╔══██╗██║   ██║ ██╔██╗\033[0m"
  echo -e "\033[36m██║  ██║██║╚██████╔╝    ███████║██║  ██║██║ ╚████║██████╔╝██████╔╝╚██████╔╝██╔╝ ██╗\033[0m"
  echo -e "\033[36m╚═╝  ╚═╝╚═╝ ╚═════╝     ╚══════╝╚═╝  ╚═╝╚═╝  ╚═══╝╚═════╝ ╚═════╝  ╚═════╝ ╚═╝  ╚═╝\033[0m"
  echo ""
  echo -e "\033[32m AIO(All-in-One) Agent Sandbox Environment\033[0m"
  if [ -n "${IMAGE_VERSION}" ]; then
    echo -e "\033[34m Image Version: ${IMAGE_VERSION}\033[0m"
  fi
  echo -e "\033[33m Dashboard: http://localhost:${PUBLIC_PORT}\033[0m"
  echo -e "\033[33m Documentation: http://localhost:${PUBLIC_PORT}/v1/docs\033[0m"
  echo ""
  echo -e "\033[35m================================================================\033[0m"
}

print_banner
timing_checkpoint "banner"

# ----------------------
# Shutdown Hooks File
# ----------------------
echo "[]" > "/var/lib/aio-sandbox/shutdown-hooks.json"
chown $USER:$USER /var/lib/aio-sandbox/shutdown-hooks.json

# ----------------------
# Run Pre-services Hook
# ----------------------
run_hook "RUN_HOOK_PRE_SERVICES" "$RUN_HOOK_PRE_SERVICES"
timing_checkpoint "pre_services_hook"

# ----------------------
# Post-Ready Hook (one-time, runs in background after services are up)
# This runs independently of supervisord so nginx restarts cannot re-trigger it.
# ----------------------
if [ -n "$RUN_HOOK_POST_READY" ]; then
  (
    _trim_wait_item() {
      local _value="$1"
      _value="${_value#"${_value%%[![:space:]]*}"}"
      _value="${_value%"${_value##*[![:space:]]}"}"
      printf '%s' "$_value"
    }

    _join_wait_items() {
      local _first=1
      local _item
      for _item in "$@"; do
        if [ $_first -eq 1 ]; then
          printf '%s' "$_item"
          _first=0
        else
          printf ',%s' "$_item"
        fi
      done
    }

    # Build the readiness targets to wait for (mirrors nginx-wait.sh logic)
    _hook_ports=()
    _hook_files=()
    if [ -n "$WAIT_PORTS" ]; then
      IFS=',' read -ra _hook_ports <<< "$WAIT_PORTS"
    else
      [ -n "$SANDBOX_SRV_PORT" ] && _hook_ports+=("$SANDBOX_SRV_PORT")
      [ -n "$BROWSER_REMOTE_DEBUGGING_PORT" ] && _hook_ports+=("$BROWSER_REMOTE_DEBUGGING_PORT")
    fi
    if [ -n "$WAIT_FILES" ]; then
      IFS=',' read -ra _hook_files <<< "$WAIT_FILES"
    fi

    _normalized_ports=()
    for _p in "${_hook_ports[@]}"; do
      _p=$(_trim_wait_item "$_p")
      [ -n "$_p" ] && _normalized_ports+=("$_p")
    done
    _hook_ports=("${_normalized_ports[@]}")

    _normalized_files=()
    for _f in "${_hook_files[@]}"; do
      _f=$(_trim_wait_item "$_f")
      [ -n "$_f" ] && _normalized_files+=("$_f")
    done
    _hook_files=("${_normalized_files[@]}")

    _start=$(date +%s)
    _timeout="${WAIT_TIMEOUT:-120}"

    while [ ${#_hook_ports[@]} -gt 0 ] || [ ${#_hook_files[@]} -gt 0 ]; do
      _pending_ports=()
      for _p in "${_hook_ports[@]}"; do
        nc -z localhost "$_p" >/dev/null 2>&1 || _pending_ports+=("$_p")
      done
      _hook_ports=("${_pending_ports[@]}")

      _pending_files=()
      for _f in "${_hook_files[@]}"; do
        [ -f "$_f" ] || _pending_files+=("$_f")
      done
      _hook_files=("${_pending_files[@]}")

      [ ${#_hook_ports[@]} -eq 0 ] && [ ${#_hook_files[@]} -eq 0 ] && break
      if [ $(( $(date +%s) - _start )) -ge "$_timeout" ]; then
        log "Skipping RUN_HOOK_POST_READY because readiness wait timed out after ${_timeout}s."
        [ ${#_hook_ports[@]} -gt 0 ] && log "Pending post-ready ports: $(_join_wait_items "${_hook_ports[@]}")"
        [ ${#_hook_files[@]} -gt 0 ] && log "Pending post-ready files: $(_join_wait_items "${_hook_files[@]}")"
        exit 0
      fi
      sleep 1
    done

    run_hook "RUN_HOOK_POST_READY" "$RUN_HOOK_POST_READY"
  ) &
fi

# ----------------------
# Start Supervisord
# ----------------------
log "Starting supervisord as the main process..."
timing_checkpoint "supervisord_start"
exec /usr/bin/supervisord -n -c /opt/gem/supervisord.conf
