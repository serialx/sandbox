# Node.js / fnm environment for all users
# Sourced by /etc/profile.d/ (login shells) and /etc/bash.bashrc (all bash shells)

export FNM_DIR="/opt/fnm"
export FNM_VERSION_FILE_STRATEGY="local"
export FNM_RESOLVE_ENGINES="true"
export FNM_COREPACK_ENABLED="false"

# Per-user fnm multishell symlink (writable, supports `fnm use`)
# fnm expects FNM_MULTISHELL_PATH to be a symlink → node installation dir.
# `fnm use <ver>` re-points this symlink to switch versions.
export FNM_MULTISHELL_PATH="$HOME/.fnm_shell"
if [ ! -L "$FNM_MULTISHELL_PATH" ]; then
  rm -rf "$FNM_MULTISHELL_PATH"
  ln -sf /opt/fnm/aliases/default "$FNM_MULTISHELL_PATH"
fi

# npm global prefix per user
export NPM_CONFIG_PREFIX="$HOME/.npm-global"
export AIO_SHIM_BIN="${AIO_SHIM_BIN:-/opt/gem/bin}"

# AIO shims first (stable wrappers like agent-browser), then fnm-managed node,
# then npm global bin, then user local bin.
# Guard: skip if already sourced (bash.bashrc + profile.d may both run)
if [ -z "$_FNM_ENV_SET" ]; then
  export _FNM_ENV_SET=1
  export PATH="$AIO_SHIM_BIN:$FNM_MULTISHELL_PATH/bin:$NPM_CONFIG_PREFIX/bin:$HOME/.local/bin:$PATH"
fi
