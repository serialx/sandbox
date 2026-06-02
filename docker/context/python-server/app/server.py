from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from fastapi import FastAPI

from app.core.config import settings


logger = logging.getLogger(__name__)

logger.info('start import server')

# from app.core.middleware import auto_extend_timeout_middleware

PREFIX = '/v1'
MCP_ROUTER_PATH = '/mcp'


@asynccontextmanager
async def app_lifespan(app: 'FastAPI'):
    app_lifespan_start_time = time.time()
    logger.info('lifespan start import services')
    from app.core.service_container import services
    from app.services.auth import AuthConfig, AuthService
    from app.services.browser import BrowserService
    from app.services.browser_sdk import BrowserSDKService
    from app.services.file import FileService
    from app.services.jupyter import JupyterService
    from app.services.mcp_client import MCPClient
    from app.services.nodejs import NodeJSService
    from app.services.observation import ObservationService
    from app.services.sandbox import SandboxService
    from app.services.bash import PipeBashManager
    from app.services.shell import OpenHandsShellManager
    from app.services.shell_ws import TerminalWsManager
    from app.services.skills import SkillService
    from app.services.proxy_mapping import ProxyMappingService
    from app.services.display import DisplayService
    from app.services.file_watch import FileWatchService
    from app.services.shutdown_hooks import ShutdownHookService
    from app.services.state_store import StateStore
    from app.services.utils import UtilService

    state_store = StateStore()
    await state_store.initialize()
    services.register('state_store', state_store)
    services.register('file_watch_service', FileWatchService(store=state_store))
    observation_service = ObservationService()
    await observation_service.initialize()
    services.register('observation_service', observation_service)

    services.register('display_service', DisplayService())
    services.register('shutdown_hook_service', ShutdownHookService())
    services.register('proxy_mapping_service', ProxyMappingService())
    services.register('sandbox_service', SandboxService())
    services.register('mcp_client', MCPClient())
    services.register('jupyter_service', JupyterService())
    services.register('terminal_manager', OpenHandsShellManager())
    services.register('terminal_ws_manager', TerminalWsManager())
    services.register('bash_manager', PipeBashManager())
    services.register('nodejs_service', NodeJSService())
    services.register('file_service', FileService())
    services.register('browser_service', BrowserService())
    services.register('browser_sdk_service', BrowserSDKService())
    services.register('util_service', UtilService())
    services.register('skills_service', SkillService())
    services.register('auth_service', AuthService(config=AuthConfig()))

    # Initialize session pool manager
    from app.services.session_pool import SessionPoolManager

    pool_manager = SessionPoolManager()
    services.register('pool_manager', pool_manager)

    logger.debug('Services registered: %s', services._services)

    logger.info(
        f'Services initialized in {time.time() - app_lifespan_start_time:.2f} seconds'
    )
    logger.info('MCP server registration deferred until first request')

    # Register session pools and warm up in background (non-blocking)
    warmup_task = None
    try:
        terminal_manager: OpenHandsShellManager = services.get('terminal_manager')
        jupyter_service: JupyterService = services.get('jupyter_service')

        pool_manager.register('shell', terminal_manager._pool)
        pool_manager.register('jupyter', jupyter_service._pool)

        async def _warmup_pools():
            pool_init_start = time.time()
            try:
                await pool_manager.initialize_all()
                logger.info(
                    f'Session pools warmed up in {time.time() - pool_init_start:.2f} seconds'
                )
            except Exception as e:
                logger.warning(f'Failed to warm up session pools: {e}')

        warmup_task = asyncio.create_task(_warmup_pools())
    except Exception as e:
        logger.warning(f'Failed to register session pools: {e}')

    # Start background cleanup for bash sessions
    bash_manager = services.get('bash_manager')
    if bash_manager:
        bash_manager.start_cleanup_task()

    # Start proxy auth file watcher if PROXY_AUTH_WATCH is configured
    proxy_auth_watch = os.getenv('PROXY_AUTH_WATCH', '').strip()
    proxy_auth_cmd = os.getenv('PROXY_AUTH_CMD', '').strip()
    proxy_server = os.getenv('PROXY_SERVER', '').strip()
    if proxy_auth_watch and proxy_auth_cmd and proxy_server and proxy_server != 'true':
        proxy_svc: ProxyMappingService = services.get('proxy_mapping_service')
        watch_files = [os.path.expandvars(f.strip()) for f in proxy_auth_watch.split(',') if f.strip()]
        # Strip any existing auth from PROXY_SERVER to get pure addr
        addr, _, _ = ProxyMappingService.parse_proxy_server(proxy_server)
        proxy_svc.start_auth_watcher(
            watch_files=watch_files,
            auth_cmd=proxy_auth_cmd,
            server=addr,
        )

    yield

    # Wait for warmup to finish before shutdown cleanup
    if warmup_task and not warmup_task.done():
        warmup_task.cancel()
        try:
            await warmup_task
        except asyncio.CancelledError:
            pass

    # Stop proxy auth watcher
    proxy_svc = services.get('proxy_mapping_service')
    if proxy_svc:
        proxy_svc.stop_auth_watcher()

    # Run user-registered shutdown hooks FIRST (before internal cleanup)
    shutdown_hook_svc = services.get('shutdown_hook_service')
    if shutdown_hook_svc:
        try:
            results = await shutdown_hook_svc.run_all()
            if results:
                succeeded = sum(1 for r in results if not r.timed_out and r.exit_code == 0)
                logger.info(f'Shutdown hooks: {succeeded}/{len(results)} succeeded')
        except Exception as e:
            logger.warning(f'Error running shutdown hooks: {e}')

    # Stop file watch service
    file_watch_service = services.get('file_watch_service')
    if file_watch_service:
        try:
            await file_watch_service.shutdown()
            logger.info('File watch service shut down')
        except Exception as e:
            logger.warning(f'Error shutting down file watch service: {e}')

    # Close state store
    observation_service = services.get('observation_service')
    if observation_service:
        try:
            await observation_service.shutdown()
            logger.info('Observation service shut down')
        except Exception as e:
            logger.warning(f'Error shutting down observation service: {e}')

    # Close state store
    state_store_svc = services.get('state_store')
    if state_store_svc:
        try:
            await state_store_svc.close()
            logger.info('State store closed')
        except Exception as e:
            logger.warning(f'Error closing state store: {e}')

    # Stop display recording if active (graceful stop preserves MP4)
    display_service = services.get('display_service')
    if display_service:
        try:
            await display_service.cleanup()
            logger.info('Display service cleaned up')
        except Exception as e:
            logger.warning(f'Error cleaning up display service: {e}')

    # Graceful shutdown: clean up resources
    logger.info('Shutting down, cleaning up resources...')

    # Shutdown session pools first
    pool_manager = services.get('pool_manager')
    if pool_manager:
        try:
            await pool_manager.shutdown_all()
            logger.info('Session pools shut down')
        except Exception as e:
            logger.warning(f'Error shutting down session pools: {e}')

    # Close all shell sessions (active sessions, not pooled)
    terminal_manager = services.get('terminal_manager')
    if terminal_manager:
        try:
            await terminal_manager.cleanup_all_sessions()
            logger.info('Shell sessions cleaned up')
        except Exception as e:
            logger.warning(f'Error cleaning up shell sessions: {e}')

    # Close all pipe bash sessions
    bash_manager = services.get('bash_manager')
    if bash_manager:
        try:
            await bash_manager.cleanup_all_sessions()
            logger.info('Bash sessions cleaned up')
        except Exception as e:
            logger.warning(f'Error cleaning up bash sessions: {e}')

    # Close all Jupyter kernels (active sessions, not pooled)
    jupyter_service = services.get('jupyter_service')
    if jupyter_service:
        try:
            jupyter_service.cleanup_all_sessions()
            logger.info('Jupyter sessions cleaned up')
        except Exception as e:
            logger.warning(f'Error cleaning up Jupyter sessions: {e}')

    # Close browser SDK session
    browser_sdk_service = services.get('browser_sdk_service')
    if browser_sdk_service:
        try:
            await browser_sdk_service.close()
            logger.info('Browser SDK session closed')
        except Exception as e:
            logger.warning(f'Error closing browser SDK session: {e}')

    # Close MCP client connections
    mcp_client = services.get('mcp_client')
    if mcp_client:
        try:
            await mcp_client.close()
            logger.info('MCP client connections closed')
        except Exception as e:
            logger.warning(f'Error closing MCP client: {e}')

    services.clear()
    logger.info('Cleanup complete')


class LazyMCPMount:
    def __init__(self) -> None:
        self._app = None
        self._session_stack: AsyncExitStack | None = None
        self._initialized = False
        self._lock = asyncio.Lock()
        # Track which servers have been loaded
        self._loaded_servers: set[str] = set()
        self._server_configs: dict[str, dict] = {}
        # Map prefix to server name for tool identification
        self._prefix_to_server: dict[str, str] = {}

    async def _load_server(self, server_name: str, config: dict) -> None:
        """Load a single MCP server using import_server() for static composition."""
        from fastmcp import FastMCP

        from app.mcp import mcpServer

        if server_name in self._loaded_servers:
            return

        logger.info(f'Loading MCP server: {server_name}')

        timeout = config.get('timeout', 30)

        try:
            composite_proxy = FastMCP.as_proxy(
                {'mcpServers': {server_name: config}},
                name=server_name,
            )
            # Use config prefix if specified, otherwise no prefix
            prefix = config.get('prefix')

            # Record prefix to server mapping for tool identification
            # If no prefix, tools can be identified by server_name matching tool name prefix
            effective_prefix = prefix if prefix else server_name
            self._prefix_to_server[effective_prefix] = server_name

            # Import server with prefix (None means no prefix added to tool names)
            # Use timeout to prevent blocking if the external server is not ready
            proxy_tools = await asyncio.wait_for(
                composite_proxy.get_tools(), timeout=timeout
            )
            await asyncio.wait_for(
                mcpServer.import_server(composite_proxy, prefix=prefix),
                timeout=timeout,
            )

            self._loaded_servers.add(server_name)
            logger.info(f'Imported MCP server: {server_name} with {len(proxy_tools)} tools (prefix={prefix})')
        except asyncio.TimeoutError:
            logger.warning(
                f'Timeout loading MCP server: {server_name} (timeout={timeout}s), skipping'
            )
        except Exception as e:
            logger.warning(
                f'Failed to load MCP server: {server_name}: {e}, skipping'
            )

    async def load_servers_on_demand(self, requested_servers: set[str]) -> None:
        """Load hidden servers on demand when requested via ?search=."""
        async with self._lock:
            for server_name in requested_servers:
                if server_name not in self._loaded_servers:
                    config = self._server_configs.get(server_name)
                    if config and config.get('hidden', False):
                        await self._load_server(server_name, config)

    async def _initialize(self) -> None:
        from app.mcp import mcpServer, register_mcp_server

        logger.info('Lazy initializing MCP server')
        register_mcp_server()

        from app.middleware.mcp_proxy import MCPProxyMiddleware
        from app.services.mcp_client import MCPClient

        self._server_configs = MCPClient.load_mcp_servers()

        # Load non-hidden servers at startup
        for server_name, config in self._server_configs.items():
            if config.get('hidden', False):
                logger.info(f'Deferring hidden MCP server: {server_name}')
                continue
            await self._load_server(server_name, config)

        logger.info(f'Loaded {len(self._loaded_servers)} non-hidden MCP servers')

        # Add middleware for handling search filtering and lazy loading
        mcpServer.add_middleware(
            MCPProxyMiddleware(
                server_configs=self._server_configs,
                lazy_loader=self,
            )
        )

        mcp_app = mcpServer.http_app(path='/', stateless_http=True, json_response=True)

        # Mark as already instrumented to prevent OTel auto-instrumentation
        # from creating duplicate spans (outer FastAPI already creates one)
        mcp_app._is_instrumented_by_opentelemetry = True

        self._app = mcp_app
        self._session_stack = AsyncExitStack()
        lifespan_cm = self._app.lifespan(self._app)
        await self._session_stack.enter_async_context(lifespan_cm)
        self._initialized = True
        logger.info('MCP server lazily initialized')

    async def ensure_loaded(self):
        if not self._initialized:
            async with self._lock:
                if not self._initialized:
                    await self._initialize()
        return self._app

    async def __call__(self, scope, receive, send):
        app = await self.ensure_loaded()
        await app(scope, receive, send)

    async def shutdown(self) -> None:
        if self._session_stack is not None:
            await self._session_stack.aclose()


lazy_mcp_mount = LazyMCPMount()


description = """
- Browser
    - CDP: [/cdp/json/version](/cdp/json/version)
- Jupyter
    - Notebook: [/jupyter](/jupyter)
- MCP
    - Streamable HTTP: [/mcp](/mcp) or [/v1/mcp](/v1/mcp)
"""


def create_app() -> 'FastAPI':
    logger.info('Preparing FastAPI application with lazy MCP mount')
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse

    from app.middleware.request_logging import RequestLoggingMiddleware
    from app.middleware.slash_normalizer import SlashNormalizerMiddleware
    from app.logger import _is_sandboxd_env

    app = FastAPI(
        version=os.environ.get('IMAGE_VERSION', '1.7.3'),
        description=description,
        docs_url=None,  # Disable default docs
        redoc_url=None,  # Disable redoc
        openapi_url=f'{PREFIX}/openapi.json',
        lifespan=app_lifespan,
    )

    # Set up CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ORIGINS,
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )
    # LogID middleware for internal sandboxd request tracing.
    if _is_sandboxd_env():
        from app.middleware.logid import LogIDMiddleware

        app.add_middleware(LogIDMiddleware)
    app.add_middleware(RequestLoggingMiddleware)

    # Mount MCP - use middleware to handle /mcp without trailing slash
    # See: https://github.com/jlowin/fastmcp/issues/435
    app.add_middleware(SlashNormalizerMiddleware, paths=[MCP_ROUTER_PATH])

    logger.info('Sandbox API server starting')

    # Register exception handlers
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.core.exceptions import (
        AppException,
        app_exception_handler,
        general_exception_handler,
        http_exception_handler,
        validation_exception_handler,
    )

    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)

    # Mount static files only in development mode (--reload flag)
    # In production, Nginx serves /static directly
    if os.getenv('DEVELOPMENT_MODE') == 'true':
        from fastapi.staticfiles import StaticFiles

        static_dir = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'static')
        app.mount('/static', StaticFiles(directory=static_dir), name='static')
        logger.info('Development mode: Mounted /static for Swagger UI assets')
    else:
        logger.info('Production mode: /static served by Nginx')

    # Custom Swagger UI endpoint with subpath support (must be before routes)
    @app.get(f'{PREFIX}/docs', include_in_schema=False)
    async def custom_swagger_ui_html():
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>{app.title} - Swagger UI</title>
    <script>
        // Auto-detect URL prefix from current path
        (function() {{
            const currentPath = window.location.pathname;
            const docsPattern = /{PREFIX.replace('/', '\\/')}\\/docs$/;
            const prefix = currentPath.replace(docsPattern, '');
            const staticPrefix = prefix + '/static/sandbox/swagger-ui-dist@5';

            // Dynamically load CSS
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.type = 'text/css';
            link.href = staticPrefix + '/swagger-ui.css';
            document.head.appendChild(link);

            // Dynamically load favicon
            const favicon = document.createElement('link');
            favicon.rel = 'shortcut icon';
            favicon.href = prefix + '/static/sandbox/favicon.png';
            document.head.appendChild(favicon);
        }})();
    </script>
</head>
<body>
    <div id="swagger-ui"></div>
    <script>
        // Load Swagger UI scripts and initialize
        (function() {{
            const currentPath = window.location.pathname;
            const docsPattern = /{PREFIX.replace('/', '\\/')}\\/docs$/;
            const prefix = currentPath.replace(docsPattern, '');
            const openapiUrl = prefix + '{PREFIX}/openapi.json';
            const staticPrefix = prefix + '/static/sandbox/swagger-ui-dist@5';

            // Load swagger-ui-bundle.js
            const bundleScript = document.createElement('script');
            bundleScript.src = staticPrefix + '/swagger-ui-bundle.js';
            bundleScript.onload = function() {{
                // Initialize Swagger UI
                const ui = SwaggerUIBundle({{
                    url: openapiUrl,
                    dom_id: '#swagger-ui',
                    deepLinking: true,
                    presets: [
                        SwaggerUIBundle.presets.apis
                    ],
                    layout: "BaseLayout"
                }});
                window.ui = ui;
            }};
            document.body.appendChild(bundleScript);
        }})();
    </script>
</body>
</html>"""

        return HTMLResponse(content=html)

    # Register routes
    logger.info('start import routes')
    from app.api.router import register_routes

    register_routes(app)

    logger.info('registering lazy MCP mount')

    app.mount(MCP_ROUTER_PATH, lazy_mcp_mount)
    app.add_event_handler('shutdown', lazy_mcp_mount.shutdown)
    logger.info('Sandbox MCP endpoint mounted lazily')

    logger.info('Sandbox API routes registered and server ready')

    return app
