from __future__ import annotations

import logging

from app.logger import setup_logging


# Bootstrap logging before heavy imports so we capture the exact start time
setup_logging('INFO')
bootstrap_logger = logging.getLogger(__name__)
bootstrap_logger.info('python-server bootstrap start')

import os

import click


def _normalize_startup_env() -> None:
    """Treat blank legacy runtime env vars as unset before app import."""
    for env_name in (
        'NODEJS_REPL_PORT',
        'NODEJS_REPL_PORT_20',
        'NODEJS_REPL_PORT_22',
        'NODEJS_REPL_PORT_24',
    ):
        value = os.environ.get(env_name)
        if value is not None and not value.strip():
            os.environ.pop(env_name, None)
            logging.info('Treating blank %s as unset during startup', env_name)


@click.command()
@click.option(
    '--host',
    type=str,
    default='127.0.0.1',
    help='Bind socket to this host.',
    show_default=True,
)
@click.option(
    '--port',
    type=int,
    default=8091,
    help='Bind socket to this port. If 0, an available port will be picked.',
    show_default=True,
)
@click.option('--reload', is_flag=True, default=False, help='Enable auto-reload.')
@click.option(
    '--ws-ping-interval',
    type=float,
    default=20.0,
    help='WebSocket ping interval in seconds.',
    show_default=True,
)
@click.option(
    '--log-level',
    type=click.Choice(
        ['critical', 'error', 'warning', 'info', 'debug', 'trace'], case_sensitive=False
    ),
    default='INFO',
    help='Log level.',
    show_default=True,
)
@click.option(
    '--mcp-config',
    type=click.Path(exists=False),
    default='mcp-servers.json',
    help='Path to MCP servers configuration file.',
    show_default=False,
)
@click.option(
    '--filter-mcp-servers',
    type=str,
    default='',
    help='Comma-separated list of MCP servers to use. Empty for all servers.',
    show_default=True,
)
@click.option(
    '--workspace',
    type=str,
    default='/tmp',
    help='Path to the workspace directory.',
    show_default=True,
)
def cli(
    host: str,
    port: int,
    reload: bool,
    ws_ping_interval: float,
    log_level: str,
    mcp_config: str,
    filter_mcp_servers: str,
    workspace: str,
) -> None:
    """
    Starts the server to control sandbox environment.
    """
    log_level = 'DEBUG' if reload else log_level
    setup_logging(log_level)
    _normalize_startup_env()
    logging.info(f'Log level: {log_level}')

    # Set MCP config path as environment variable
    os.environ['LOG_LEVEL'] = log_level
    os.environ['MCP_SERVERS_CONFIG'] = mcp_config
    os.environ['DISABLE_AIOHTTP_TRANSPORT'] = 'true'
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    os.environ['MARKITDOWN_ENABLE_PLUGINS'] = 'true'

    # Set development mode flag for static file handling
    if reload:
        os.environ['DEVELOPMENT_MODE'] = 'true'

    # Set MCP server filtering
    if filter_mcp_servers:
        os.environ['MCP_FILTER_SERVERS'] = filter_mcp_servers
        logging.info(f'Filtering MCP servers: {filter_mcp_servers}')

    if workspace:
        os.environ['WORKSPACE'] = workspace
        os.makedirs(workspace, exist_ok=True)

    logging.info(f'Starting server at http://{host}:{port}.')
    logging.info(f'Using MCP config: {mcp_config}')

    # Production optimizations
    production_config = {
        'app': 'app.server:create_app',
        'host': host,
        'port': port,
        'reload': reload,
        'ws_ping_interval': ws_ping_interval,
        'log_config': None,
        'log_level': 'debug',
    }

    # Development mode: optimize for fast reload
    if reload:
        production_config.update(
            {
                'reload_delay': 0.25,  # 减少 reload 延迟到 250ms（默认 0.25s）
                'timeout_graceful_shutdown': 1,  # 优雅关闭超时 1 秒（默认 None）
            }
        )
    else:
        # Add production optimizations when not in development
        production_config.update(
            {
                'log_level': 'info',
                'workers': 1,  # Single worker for async apps with shared state
                'loop': 'uvloop',  # Use uvloop for better async performance
                'access_log': False,  # Disable access logs for better performance
                'server_header': False,  # Remove server header
                'date_header': False,  # Remove date header
                'timeout_graceful_shutdown': 45,  # 45s: shutdown hooks (30s) + cleanup
            }
        )

    logging.info('import uvicorn start')
    from uvicorn import run as uvicorn_run

    logging.info('import uvicorn done')

    uvicorn_run(**production_config)


if __name__ == '__main__':
    cli()
