"""Unified runtime version resolution.

Priority: PYTHON_VERSION > PYTHON_CODE_EXEC_VERSION > default
          NODE_VERSION > NODE_CODE_EXEC_VERSION > default
"""

import logging
import os

from app.core.env import parse_int_env

logger = logging.getLogger(__name__)

AIO_SHIM_BIN = '/opt/gem/bin'
TRUE_VALUES = {'1', 'true', 'yes', 'on'}
DEFAULT_PYTHON_VERSION = '3.10'
DEFAULT_PYTHON_KERNEL = 'python3.10'

# Python version → bin directory path
PYTHON_PATH_MAP: dict[str, str] = {
    '3': '',  # system default, no prefix needed
    '3.10': '',  # system default
    '3.11': '/opt/python3.11/bin',
    '3.12': '/opt/python3.12/bin',
}
VERSIONED_PYTHON_PATHS = {
    path for path in PYTHON_PATH_MAP.values() if path
}

# Python version → Jupyter kernel name
PYTHON_KERNEL_MAP: dict[str, str] = {
    '3': DEFAULT_PYTHON_KERNEL,
    '3.10': 'python3.10',
    '3.11': 'python3.11',
    '3.12': 'python3.12',
    'python3': 'python3',
    'python3.10': 'python3.10',
    'python3.11': 'python3.11',
    'python3.12': 'python3.12',
}

# Node version → bin directory path
NODE_PATH_MAP: dict[str, str] = {
    '20': '/opt/nodejs/20/bin',
    '22': '/opt/nodejs/22/bin',
    '24': '/opt/nodejs/24/bin',
}

# Node version → canonical version string (for NodeJSService)
NODE_VERSION_MAP: dict[str, str] = {
    'node': 'node22',
    '20': 'node20',
    '22': 'node22',
    '24': 'node24',
    'node20': 'node20',
    'node22': 'node22',
    'node24': 'node24',
}

NODE_REPL_DEFAULT_PORTS: dict[str, int] = {
    'node20': 8192,
    'node22': 8092,
    'node24': 8392,
}

NODE_REPL_PORT_ENV_KEYS: dict[str, str] = {
    'node20': 'NODEJS_REPL_PORT_20',
    'node22': 'NODEJS_REPL_PORT_22',
    'node24': 'NODEJS_REPL_PORT_24',
}


def _get_python_env() -> str | None:
    """Read Python version from environment with priority chain."""
    return os.environ.get('PYTHON_VERSION') or os.environ.get(
        'PYTHON_CODE_EXEC_VERSION'
    )


def _get_node_env() -> str | None:
    """Read Node version from environment with priority chain."""
    return os.environ.get('NODE_VERSION') or os.environ.get(
        'NODE_CODE_EXEC_VERSION'
    )


def _normalize_python(version: str) -> str:
    """Normalize python version string: 'python3.12' → '3.12', 'python3' → '3'."""
    return version.removeprefix('python')


def _normalize_node(version: str) -> str:
    """Normalize node version string: 'node24' → '24'."""
    return version.removeprefix('node')


def _parse_port_env(value: str | None, *, env_name: str) -> int | None:
    """Parse a TCP port from env, logging and ignoring invalid values."""
    return parse_int_env(
        value,
        env_name=env_name,
        default=None,
        min_value=1,
        max_value=65535,
    )


def canonicalize_node_version(
    version: str | None,
    *,
    fallback: str = 'node22',
) -> str:
    """Normalize a node version string to canonical form."""
    if not version:
        return fallback

    normalized = _normalize_node(version)
    canonical = NODE_VERSION_MAP.get(normalized) or NODE_VERSION_MAP.get(version)
    if canonical:
        return canonical

    logger.warning(f'Unknown Node version "{version}", falling back to {fallback}')
    return fallback


def resolve_python_version() -> str:
    """Resolve Python version string for Jupyter kernel.

    Returns kernel name like 'python3.10', 'python3.12', etc.
    """
    version = _get_python_env()
    if not version:
        return DEFAULT_PYTHON_KERNEL

    normalized = _normalize_python(version)
    kernel = PYTHON_KERNEL_MAP.get(normalized) or PYTHON_KERNEL_MAP.get(version)
    if kernel:
        return kernel

    logger.warning(
        f'Unknown Python version "{version}", falling back to {DEFAULT_PYTHON_KERNEL}'
    )
    return DEFAULT_PYTHON_KERNEL


def resolve_python_path() -> str:
    """Resolve Python bin directory path to prepend to PATH.

    Returns empty string if system default or not configured.
    """
    version = _get_python_env()
    if not version:
        return ''

    normalized = _normalize_python(version)
    path = PYTHON_PATH_MAP.get(normalized)
    if path is not None:
        return path

    logger.warning(f'Unknown Python version "{version}" for PATH')
    return ''


def resolve_node_version() -> str:
    """Resolve Node.js canonical version string.

    Returns like 'node22', 'node24', etc.
    """
    return canonicalize_node_version(_get_node_env(), fallback='node22')


def resolve_node_path() -> str:
    """Resolve Node.js bin directory path to prepend to PATH."""
    version = _get_node_env()
    if not version:
        return ''

    normalized = _normalize_node(version)
    path = NODE_PATH_MAP.get(normalized)
    if path is not None:
        return path

    logger.warning(f'Unknown Node version "{version}" for PATH')
    return ''


def resolve_node_repl_disabled() -> bool:
    """Resolve whether NodeJS REPL startup is disabled."""
    value = os.environ.get('DISABLE_NODEJS_REPL', '')
    return value.strip().lower() in TRUE_VALUES


def resolve_node_repl_ports() -> dict[str, int]:
    """Resolve NodeJS REPL ports with env overrides.

    Resolution order:
    1. Built-in defaults per version
    2. `NODEJS_REPL_PORT` for the current default node version
    3. Version-specific overrides `NODEJS_REPL_PORT_20/22/24`
    """
    ports = NODE_REPL_DEFAULT_PORTS.copy()
    default_version = resolve_node_version()

    legacy_port = _parse_port_env(
        os.environ.get('NODEJS_REPL_PORT'),
        env_name='NODEJS_REPL_PORT',
    )
    if legacy_port is not None:
        ports[default_version] = legacy_port

    for version, env_name in NODE_REPL_PORT_ENV_KEYS.items():
        explicit_port = _parse_port_env(os.environ.get(env_name), env_name=env_name)
        if explicit_port is not None:
            ports[version] = explicit_port

    return ports


def resolve_node_repl_port(version: str | None = None) -> int:
    """Resolve the NodeJS REPL port for a specific version."""
    resolved_version = canonicalize_node_version(
        version,
        fallback=resolve_node_version(),
    )
    return resolve_node_repl_ports()[resolved_version]


def _strip_versioned_python_paths(path: str) -> str:
    """Remove managed non-default Python bin dirs from an inherited PATH."""
    return ':'.join(
        part for part in path.split(':') if part not in VERSIONED_PYTHON_PATHS
    )


def build_version_path_env() -> dict[str, str]:
    """Build PATH environment with resolved Python/Node versions.

    Returns dict with PATH key if any version-specific paths needed,
    inherited versioned Python path pollution was removed, empty dict otherwise.
    """
    prefixes = []
    python_path = resolve_python_path()
    if python_path:
        prefixes.append(python_path)
    node_path = resolve_node_path()
    if node_path:
        prefixes.append(node_path)

    original_path = os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')
    base_path = _strip_versioned_python_paths(original_path)

    if not prefixes:
        return {'PATH': base_path} if base_path != original_path else {}

    # Put AIO shims first so stable wrappers like agent-browser always win,
    # then fnm multishell bin so `fnm use <ver>` still takes effect.
    # The multishell symlink (~/.fnm_shell) is updated by `fnm use` to point
    # to the chosen node installation, so it must precede any hardcoded version paths.
    fnm_multishell = os.environ.get('FNM_MULTISHELL_PATH', '')
    fnm_bin = f'{fnm_multishell}/bin' if fnm_multishell else ''

    parts = [AIO_SHIM_BIN] + ([fnm_bin] if fnm_bin else []) + prefixes
    new_path = ':'.join(parts) + ':' + base_path
    return {'PATH': new_path}


def build_sandbox_env() -> dict[str, str]:
    """Build a complete environment dict for spawning sandbox subprocesses.

    Starts from the current process environment, applies version-based PATH,
    adds PYTHONUNBUFFERED, and strips PTY-related variables (PS1, PROMPT_COMMAND).

    Used by both `/v1/shell` and `/v1/bash` to ensure consistent env handling.
    """
    env = {**os.environ, 'PYTHONUNBUFFERED': '1'}

    # Remove PTY-related variables that interfere with pipe-based execution
    for key in ('PS1', 'PROMPT_COMMAND'):
        env.pop(key, None)

    # Apply version-based PATH (PYTHON_VERSION / NODE_VERSION)
    version_env = build_version_path_env()
    env.update(version_env)

    return env
