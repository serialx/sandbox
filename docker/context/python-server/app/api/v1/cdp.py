"""CDP (Chrome DevTools Protocol) proxy endpoints.

This module provides a proxy for CDP endpoints with URL rewriting support.
It rewrites WebSocket URLs in CDP responses to use the correct host/prefix.
"""

import logging
import os
from typing import Any, Dict, Optional, List, Union
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.core.env import get_env_int
from app.utils import normalize_path_prefix


logger = logging.getLogger(__name__)

# CDP target host configuration
CDP_HOST = os.environ.get('BROWSER_REMOTE_DEBUGGING_HOST', '127.0.0.1')
CDP_PORT = get_env_int(
    'BROWSER_REMOTE_DEBUGGING_PORT',
    9222,
    min_value=1,
    max_value=65535,
)
CDP_BASE_URL = f'http://{CDP_HOST}:{CDP_PORT}'


def rewrite_ws_urls(
    data: Union[Dict[str, Any], List[Any], str, Any],
    request: Request,
) -> Union[Dict[str, Any], List[Any], str, Any]:
    """Recursively rewrite WebSocket URLs in CDP response data.

    Replaces ws://127.0.0.1:9222/... with ws://host:port/[prefix]/cdp/...
    Supports X-Forwarded-Prefix header for reverse proxy scenarios.
    """
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key in ('webSocketDebuggerUrl', 'devtoolsFrontendUrl') and isinstance(
                value, str
            ):
                result[key] = rewrite_single_url(value, request)
            else:
                result[key] = rewrite_ws_urls(value, request)
        return result
    elif isinstance(data, list):
        return [rewrite_ws_urls(item, request) for item in data]
    elif isinstance(data, str):
        # Check if it's a WebSocket URL
        if data.startswith('ws://') or data.startswith('wss://'):
            return rewrite_single_url(data, request)
        return data
    return data


def rewrite_single_url(url: str, request: Request) -> str:
    """Rewrite a single WebSocket URL.

    Supports X-Forwarded-* headers for reverse proxy scenarios:
    - X-Forwarded-Host: Target host
    - X-Forwarded-Proto: Protocol (http/https)
    - X-Forwarded-Prefix: Path prefix (e.g., /api/v1)
    """
    if not url:
        return url

    # Parse the original URL
    parsed = urlparse(url)

    # Priority: X-Forwarded-Host > Host header > default
    host = (
        request.headers.get('x-forwarded-host')
        or request.headers.get('host')
        or 'localhost:8080'
    )

    # Determine protocol (ws/wss based on X-Forwarded-Proto or request scheme)
    forwarded_proto = request.headers.get('x-forwarded-proto', '').lower()
    if forwarded_proto in ('https', 'http'):
        ws_proto = 'wss' if forwarded_proto == 'https' else 'ws'
    else:
        ws_proto = 'wss' if request.url.scheme == 'https' else 'ws'

    # Get and normalize path prefix from X-Forwarded-Prefix header
    path_prefix = normalize_path_prefix(request.headers.get('x-forwarded-prefix'))

    # Rewrite the URL
    # Original: ws://127.0.0.1:9222/devtools/page/xxx
    # New: ws://host:port/[prefix]/cdp/devtools/page/xxx
    path = parsed.path
    if path.startswith('/'):
        new_url = f'{ws_proto}://{host}{path_prefix}/cdp{path}'
    else:
        new_url = f'{ws_proto}://{host}{path_prefix}/cdp/{path}'

    logger.debug(f'Rewrote URL: {url} -> {new_url}')
    return new_url


router = APIRouter()


@router.get(
    '/json',
)
async def cdp_json(request: Request) -> Response:
    """Get list of inspectable pages with rewritten URLs."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f'{CDP_BASE_URL}/json')
            data = response.json()
            rewritten = rewrite_ws_urls(data, request)
            return JSONResponse(content=rewritten)
        except httpx.RequestError as e:
            logger.error(f'Error fetching CDP pages: {e}')
            raise HTTPException(status_code=502, detail=f'CDP request failed: {e}')


@router.get(
    '/json/version',
)
async def cdp_json_version(request: Request) -> Response:
    """Get browser version information with rewritten URLs."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f'{CDP_BASE_URL}/json/version')
            data = response.json()
            rewritten = rewrite_ws_urls(data, request)
            return JSONResponse(content=rewritten)
        except httpx.RequestError as e:
            logger.error(f'Error fetching CDP version: {e}')
            raise HTTPException(status_code=502, detail=f'CDP request failed: {e}')


@router.get(
    '/json/list',
)
async def cdp_json_list(request: Request) -> Response:
    """Get list of inspectable pages (alias for /json)."""
    return await cdp_json(request)


@router.get(
    '/json/protocol',
)
async def cdp_json_protocol() -> Response:
    """Get the Chrome DevTools Protocol schema."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f'{CDP_BASE_URL}/json/protocol')
            return JSONResponse(content=response.json())
        except httpx.RequestError as e:
            logger.error(f'Error fetching CDP protocol: {e}')
            raise HTTPException(status_code=502, detail=f'CDP request failed: {e}')


@router.put(
    '/json/new',
)
async def cdp_json_new(request: Request, url: Optional[str] = None) -> Response:
    """Open a new tab/page."""
    async with httpx.AsyncClient() as client:
        try:
            target_url = f'{CDP_BASE_URL}/json/new'
            if url:
                target_url += f'?{url}'
            response = await client.put(target_url)
            data = response.json()
            rewritten = rewrite_ws_urls(data, request)
            return JSONResponse(content=rewritten)
        except httpx.RequestError as e:
            logger.error(f'Error creating new CDP page: {e}')
            raise HTTPException(status_code=502, detail=f'CDP request failed: {e}')


@router.get(
    '/json/activate/{target_id}',
)
async def cdp_json_activate(target_id: str) -> Response:
    """Activate (bring to front) a page."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f'{CDP_BASE_URL}/json/activate/{target_id}')
            return Response(
                content=response.text, media_type='text/plain', status_code=response.status_code
            )
        except httpx.RequestError as e:
            logger.error(f'Error activating CDP page: {e}')
            raise HTTPException(status_code=502, detail=f'CDP request failed: {e}')


@router.get(
    '/json/close/{target_id}',
)
async def cdp_json_close(target_id: str) -> Response:
    """Close a page."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f'{CDP_BASE_URL}/json/close/{target_id}')
            return Response(
                content=response.text, media_type='text/plain', status_code=response.status_code
            )
        except httpx.RequestError as e:
            logger.error(f'Error closing CDP page: {e}')
            raise HTTPException(status_code=502, detail=f'CDP request failed: {e}')
