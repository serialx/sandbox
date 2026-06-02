import logging
import os
import platform
import shutil
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import List, Set

from pydantic import BaseModel


logger = logging.getLogger(__name__)


def get_workspace() -> str:
    """Return the effective workspace directory from the environment."""
    return os.environ.get('WORKSPACE', '/tmp')


def get_xdg_runtime_dir() -> Path:
    """Return the active per-user runtime directory.

    Prefer the explicit XDG runtime dir from the environment. For local dev or
    tests where it is not set, reuse /run/user/<uid> when it already exists and
    is writable; otherwise fall back to a private temp dir.
    """
    runtime_dir = os.environ.get('XDG_RUNTIME_DIR')
    if runtime_dir:
        path = Path(runtime_dir).expanduser()
        if not path.is_absolute():
            raise ValueError(
                f'XDG_RUNTIME_DIR must be an absolute path: {runtime_dir}'
            )
        return path

    run_user_dir = Path('/run/user') / str(os.getuid())
    if run_user_dir.exists() and os.access(run_user_dir, os.W_OK | os.X_OK):
        return run_user_dir

    return Path(tempfile.gettempdir()) / f'aio-runtime-{os.getuid()}'


def get_aio_runtime_dir(*parts: str, ensure: bool = False) -> Path:
    """Return an aio-sandbox runtime-state path under XDG_RUNTIME_DIR."""
    path = get_xdg_runtime_dir() / 'aio-sandbox'
    for part in parts:
        path /= part

    if ensure:
        path.mkdir(parents=True, exist_ok=True)

    return path


def validate_cwd(cwd: str | None, *, default: str | None = None) -> str | None:
    """Validate and normalize a working directory path.

    - expanduser + abspath normalization
    - Check directory exists
    - Check r+x permission
    - Return abspath (preserve symlinks)

    Args:
        cwd: The working directory path to validate. None or empty returns default.
        default: Fallback value when cwd is None/empty.

    Returns:
        Validated absolute path, or default if cwd is None/empty.

    Raises:
        ValueError: Directory does not exist or is not accessible.
    """
    if not cwd:
        return default

    cwd = os.path.expanduser(cwd)
    cwd = os.path.abspath(cwd)

    if not os.path.isdir(cwd):
        raise ValueError(f'Working directory does not exist: {cwd}')
    if not os.access(cwd, os.R_OK | os.X_OK):
        raise ValueError(f'Working directory is not accessible: {cwd}')

    return cwd


def normalize_cwd(cwd):
    """标准化 cwd 路径，支持相对路径 (legacy, prefer validate_cwd)"""

    if not cwd:
        return None

    cwd = os.path.expanduser(cwd)
    cwd = os.path.abspath(cwd)
    return cwd


def get_system_info() -> dict:
    """获取系统环境信息"""
    import distro

    info = {
        'os': distro.name(pretty=True) or platform.system(),
        'os_version': distro.version(best=True) or platform.release(),
        'arch': platform.machine(),
        'platform': platform.platform(),
    }
    return info


def get_command_version(command: str) -> str:
    """获取命令版本信息"""
    try:
        result = subprocess.run(
            [command, '--version'], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip().split('\n')[0]
        return 'Not available'
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
        return 'Not available'


def _run(args: list[str], timeout: float = 2.0) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr or '').strip()
    except Exception:
        return ''


class CmdInfo(BaseModel):
    bin: Path | None
    version: str | None


def resolve_cmd_info(cmd: str) -> CmdInfo:
    """
    输入命令名（如 'python3.12' / 'python3.11' / 'git'），
    返回 {'bin': Path|None, 'version': str|None}
    - bin 为 realpath（若能解析）
    - version 优先用 '<cmd> --version' 的首行，失败则 None
    """
    p = shutil.which(cmd)
    if not p:
        return CmdInfo(bin=None, version=None)

    # realpath（去掉软链）
    try:
        bin_path = Path(os.path.realpath(p))
    except Exception:
        bin_path = Path(p).resolve()

    # 常见版本旗标：先 '--version'，若空再 '-V'
    ver = _run([p, '--version']) or _run([p, '-V'])
    ver = ver.splitlines()[0] if ver else None

    return CmdInfo(bin=bin_path, version=ver)


def get_node_version() -> str:
    """获取 Node.js 版本"""
    return get_command_version('node')


def get_npm_version() -> str:
    """获取 npm 版本"""
    return get_command_version('npm')


def get_bc_version() -> str:
    """获取 bc 版本"""
    return get_command_version('bc')


def get_listening_ports() -> List[str]:
    ports: Set[int] = set()
    try:
        import psutil

        for c in psutil.net_connections(kind='tcp'):
            if getattr(c, 'status', None) == psutil.CONN_LISTEN and getattr(
                c, 'laddr', None
            ):
                ports.add(str(c.laddr.port))
        for c in psutil.net_connections(kind='udp'):
            la = getattr(c, 'laddr', None)
            if la:
                ports.add(str(la.port))
    except Exception:
        pass
    return sorted(ports)


def deep_merge(target, source):
    """deep merge dict, not overwrite existing keys"""
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)
    return target


def normalize_path_prefix(prefix: str | None) -> str:
    """
    Normalize X-Forwarded-Prefix header value for URL construction.

    Args:
        prefix: The path prefix from X-Forwarded-Prefix header

    Returns:
        Normalized prefix string:
        - Empty string if prefix is None or empty
        - Starts with '/' and does not end with '/'
        - e.g., '/api/v1', '/proxy'

    Examples:
        >>> normalize_path_prefix(None)
        ''
        >>> normalize_path_prefix('')
        ''
        >>> normalize_path_prefix('/api/v1')
        '/api/v1'
        >>> normalize_path_prefix('/api/v1/')
        '/api/v1'
        >>> normalize_path_prefix('api/v1')
        '/api/v1'
        >>> normalize_path_prefix('api/v1/')
        '/api/v1'
    """
    if not prefix:
        return ''

    # Remove leading and trailing whitespace
    prefix = prefix.strip()

    if not prefix:
        return ''

    # Ensure starts with '/'
    if not prefix.startswith('/'):
        prefix = '/' + prefix

    # Remove trailing '/'
    if prefix.endswith('/'):
        prefix = prefix.rstrip('/')

    return prefix


_TRUNCATION_MARKER = '\n\n... [output truncated: {omitted} characters omitted] ...\n\n'


def middle_truncate(text: str, max_length: int) -> str:
    """Truncate text by removing the middle portion, keeping head and tail.

    Similar to Claude Code's BASH_MAX_OUTPUT_LENGTH behavior: preserves
    the beginning (context/setup) and end (final result/errors) of output.

    Args:
        text: The text to potentially truncate.
        max_length: Maximum allowed length. Must be positive.

    Returns:
        Original text if within limit, otherwise head + marker + tail.
    """
    if len(text) <= max_length:
        return text

    # Reserve space for the marker (estimate, then adjust)
    omitted_estimate = len(text) - max_length
    marker = _TRUNCATION_MARKER.format(omitted=omitted_estimate)

    available = max_length - len(marker)
    if available <= 0:
        # max_length too small to even fit the marker — just hard-truncate
        return text[:max_length]

    head_size = available // 2
    tail_size = available - head_size

    # Recalculate with exact omitted count
    omitted = len(text) - head_size - tail_size
    marker = _TRUNCATION_MARKER.format(omitted=omitted)

    return text[:head_size] + marker + text[-tail_size:]


def is_binary(filename):
    """
    :param filename: File to check.
    :returns: True if it's a binary file, otherwise False.
    """
    from binaryornot.helpers import get_starting_chunk, is_binary_string

    logger.debug('is_binary: %(filename)r', locals())

    # Check if the file extension is in a list of known binary types
    binary_extensions = [
        # Python
        '.pyc', '.pyo', '.pyd',
        # Java/JVM
        '.class', '.jar', '.war', '.ear',
        # C/C++
        '.o', '.a', '.so', '.dll', '.dylib', '.lib', '.exe',
        # Images
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.ico', '.svg', '.webp', '.tiff', '.tif',
        '.psd', '.raw', '.heif', '.heic', '.avif',
        # Audio
        '.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.m4a', '.opus',
        # Video
        '.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg',
        # Archives
        '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar', '.tgz', '.tbz2', '.txz',
        # Documents
        '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp',
        # Databases
        '.db', '.sqlite', '.sqlite3', '.mdb', '.accdb',
        # Fonts
        '.ttf', '.otf', '.woff', '.woff2', '.eot',
        # Other
        '.bin', '.dat', '.deb', '.rpm', '.dmg', '.pkg', '.msi', '.iso', '.img',
    ]
    for ext in binary_extensions:
        if filename.endswith(ext):
            return True

    text_extensions = [
        # Documents
        '.txt', '.md', '.mdx', '.rst', '.tex', '.rtf',
        # Programming languages
        '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.c', '.cpp', '.cc', '.cxx',
        '.h', '.hpp', '.cs', '.php', '.rb', '.go', '.rs', '.swift', '.kt', '.scala',
        '.m', '.r', '.lua', '.pl', '.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat',
        '.cmd', '.vb', '.pas', '.asm', '.s', '.d', '.nim', '.zig', '.dart', '.elm',
        '.clj', '.cljs', '.ex', '.exs', '.erl', '.hrl', '.ml', '.mli', '.fs', '.fsx',
        '.jl', '.cr', '.rkt', '.scm', '.lisp', '.el', '.vim', '.hs', '.lhs', '.purs',
        # Web
        '.html', '.htm', '.xml', '.css', '.scss', '.sass', '.less', '.vue', '.svelte',
        # Config/Data
        '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.config', '.env',
        '.properties', '.plist', '.lock',
        # Documentation
        '.rst', '.adoc', '.asciidoc', '.org',
        # Other
        '.sql', '.graphql', '.gql', '.proto', '.thrift', '.avro',
        '.makefile', '.mk', '.cmake', '.gradle', '.sbt', '.pom',
        '.gitignore', '.dockerignore', '.editorconfig', '.gitattributes',
        '.csv', '.tsv', '.log',
    ]
    for ext in text_extensions:
        if filename.endswith(ext):
            return False

    # Check if the starting chunk is a binary string
    chunk = get_starting_chunk(filename, length=1024)
    return is_binary_string(chunk)
