from __future__ import annotations

import logging
import os

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse

from app.core.service_container import services


logger = logging.getLogger(__name__)

logger.info('API router initialized')

from app.api.v1 import bash, shell


logger.info('shell router imported')
from app.api.v1 import file


logger.info('file router imported')
from app.api.v1 import sandbox


logger.info('sandbox router imported')
from app.api.v1 import jupyter


logger.info('jupyter router imported')
from app.api.v1 import nodejs


logger.info('nodejs router imported')
from app.api.v1 import mcp


logger.info('mcp router imported')
from app.api.v1 import browser, browser_sdk


logger.info('browser router imported')
from app.api.v1 import code


logger.info('code router imported')
from app.api.v1 import util


logger.info('util router imported')
from app.api.v1 import skills


logger.info('skills router imported')
from app.api.v1 import proxy_mapping


logger.info('proxy_mapping router imported')
from app.api.v1 import auth


logger.info('auth router imported')
from app.api.v1 import cdp


logger.info('cdp router imported')
from app.api.v1 import display


logger.info('display router imported')
from app.api.v1 import file_watch


logger.info('file_watch router imported')

logger.info('API routers imported')


def register_routes(app: FastAPI):
    # /v1/*
    v1 = APIRouter(prefix='/v1')
    v1.include_router(sandbox.router, prefix='/sandbox', tags=['sandbox'])
    v1.include_router(shell.router, prefix='/shell', tags=['shell'])
    v1.include_router(bash.router, prefix='/bash', tags=['bash'])
    v1.include_router(file.router, prefix='/file', tags=['file'])
    v1.include_router(jupyter.router, prefix='/jupyter', tags=['jupyter'])
    v1.include_router(nodejs.router, prefix='/nodejs', tags=['nodejs'])
    v1.include_router(mcp.router, prefix='/mcp', tags=['mcp'])
    v1.include_router(browser.router, prefix='/browser', tags=['browser'])
    v1.include_router(browser_sdk.router, prefix='/browser', tags=['browser'])
    v1.include_router(code.router, prefix='/code', tags=['code'])
    v1.include_router(util.router, prefix='/util', tags=['util'])
    v1.include_router(skills.router, prefix='/skills', tags=['skills'])
    v1.include_router(proxy_mapping.router, prefix='/proxy', tags=['proxy'])
    v1.include_router(display.router, prefix='/display', tags=['display'])
    v1.include_router(file_watch.router, prefix='/file', tags=['file-watch'])

    app.include_router(v1)

    # Register legacy gem-server compatible routes at root level
    # These maintain backward compatibility with gem-server endpoints
    app.include_router(auth.router, tags=['auth'])
    app.include_router(cdp.router, prefix='/cdp', tags=['cdp'], include_in_schema=False)

    # Legacy gem-server screenshot and actions routes (aliases to /v1/browser/*)
    @app.get(
        '/screenshot',
        tags=['legacy'],
        include_in_schema=False,
    )
    async def legacy_screenshot():
        """Legacy screenshot endpoint - alias to /v1/browser/screenshot"""
        return await browser.take_screenshot()

    @app.post(
        '/actions',
        tags=['legacy'],
        include_in_schema=False,
    )
    async def legacy_actions(request: Request):
        """Legacy actions endpoint - alias to /v1/browser/actions"""
        from app.models.browser import AnyAction
        from app.services.browser import BrowserService
        from pydantic import TypeAdapter

        body = await request.json()
        adapter = TypeAdapter(AnyAction)
        action = adapter.validate_python(body)
        browser_service: BrowserService = services.get('browser_service')
        return await browser_service.execute_action(action)

    @app.get('/health', tags=['health'], include_in_schema=False)
    async def health_check():
        """Health check endpoint"""
        return {"status": "healthy"}

    # Add terminal route
    @app.get('/terminal', tags=['web-ui'], include_in_schema=False)
    async def serve_terminal():
        """Serve the terminal HTML page"""
        terminal_path = os.path.join(
            os.path.dirname(__file__), '..', '..', 'terminal.html'
        )
        return FileResponse(terminal_path, media_type='text/html')

    # Add browser-ui route (CDP-based remote browser view)
    @app.get('/browser-ui', tags=['web-ui'], include_in_schema=False)
    async def serve_browser_ui():
        """Serve the browser-ui HTML page (CDP remote casting)"""
        browser_ui_path = os.path.join(
            os.path.dirname(__file__), '..', '..', 'browser-ui.html'
        )
        return FileResponse(browser_ui_path, media_type='text/html')

    # Add llms.txt route - auto-generated from OpenAPI schema
    @app.get('/llms.txt', tags=['llms'], include_in_schema=False)
    async def serve_llms_txt():
        """Serve auto-generated llms.txt from OpenAPI schema for LLM context"""
        from app.services.sandbox import SandboxService

        sandbox_service: SandboxService = services.get('sandbox_service')
        openapi_schema = app.openapi()
        llms_content = sandbox_service.generate_llms_txt(openapi_schema)
        return PlainTextResponse(content=llms_content, media_type='text/plain')
