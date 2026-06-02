from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Query

from app.core.service_container import services
from app.schemas.proxy_mapping import (
    ProxyBypassRequest,
    ProxyDiagnoseResult,
    ProxyHealthCheck,
    ProxyMappingAddRequest,
    ProxyMappingRoute,
    ProxyUpstreamInfo,
    ProxyUpstreamUpdateRequest,
)
from app.schemas.response import Response

if TYPE_CHECKING:
    from app.services.proxy_mapping import ProxyMappingService

router = APIRouter()


def _service() -> ProxyMappingService:
    return services.require('proxy_mapping_service')


# --- Mappings ---


@router.get(
    '/mappings',
    response_model=Response[list[ProxyMappingRoute]],
    operation_id='list_proxy_mappings',
    openapi_extra={
        'x-fern-sdk-group-name': 'proxy',
        'x-fern-sdk-method-name': 'list_mappings',
    },
)
async def list_mappings():
    """List all proxy domain-to-port mappings."""
    mappings = await _service().list_mappings()
    return Response(success=True, data=mappings)


@router.post(
    '/mappings',
    response_model=Response[ProxyMappingRoute],
    operation_id='add_proxy_mapping',
    openapi_extra={
        'x-fern-sdk-group-name': 'proxy',
        'x-fern-sdk-method-name': 'add_mapping',
    },
)
async def add_mapping(request: ProxyMappingAddRequest):
    """Add or update a proxy mapping. Supports wildcards in host (*.example.com)."""
    entry = await _service().add_mapping(request.source, request.target)
    return Response(
        success=True, message=f'{request.source} -> {request.target}', data=entry
    )


@router.delete(
    '/mappings/{source:path}',
    response_model=Response,
    operation_id='remove_proxy_mapping',
    openapi_extra={
        'x-fern-sdk-group-name': 'proxy',
        'x-fern-sdk-method-name': 'remove_mapping',
    },
)
async def remove_mapping(source: str):
    """Remove a proxy mapping by source pattern."""
    removed = await _service().remove_mapping(source)
    if not removed:
        return Response(success=False, message=f'Mapping for {source} not found')
    return Response(success=True, message=f'Removed mapping for {source}')


# --- Excludes (bypass) ---


@router.get(
    '/excludes',
    response_model=Response[list[str]],
    operation_id='list_proxy_excludes',
    openapi_extra={
        'x-fern-sdk-group-name': 'proxy',
        'x-fern-sdk-method-name': 'list_excludes',
    },
)
async def list_excludes():
    """List all proxy bypass/exclude patterns."""
    patterns = await _service().list_bypasses()
    return Response(success=True, data=patterns)


@router.post(
    '/excludes',
    response_model=Response,
    operation_id='add_proxy_exclude',
    openapi_extra={
        'x-fern-sdk-group-name': 'proxy',
        'x-fern-sdk-method-name': 'add_exclude',
    },
)
async def add_exclude(request: ProxyBypassRequest):
    """Add a bypass/exclude pattern. Matched domains connect directly."""
    added = await _service().add_bypass(request.pattern)
    if not added:
        return Response(
            success=False, message=f'Pattern {request.pattern} already exists'
        )
    return Response(success=True, message=f'Added exclude: {request.pattern}')


@router.delete(
    '/excludes',
    response_model=Response,
    operation_id='remove_proxy_exclude',
    openapi_extra={
        'x-fern-sdk-group-name': 'proxy',
        'x-fern-sdk-method-name': 'remove_exclude',
    },
)
async def remove_exclude(request: ProxyBypassRequest):
    """Remove a bypass/exclude pattern."""
    removed = await _service().remove_bypass(request.pattern)
    if not removed:
        return Response(
            success=False, message=f'Pattern {request.pattern} not found'
        )
    return Response(success=True, message=f'Removed exclude: {request.pattern}')


# --- Diagnose ---


@router.get(
    '/diagnose',
    response_model=Response[ProxyDiagnoseResult],
    operation_id='diagnose_proxy',
    openapi_extra={
        'x-fern-sdk-group-name': 'proxy',
        'x-fern-sdk-method-name': 'diagnose',
    },
)
async def diagnose(
    url: str = Query(..., description='URL to diagnose routing for'),
):
    """Diagnose how a URL would be routed through the proxy."""
    result = await _service().diagnose(url)
    return Response(success=True, data=result)


# --- Health check ---


@router.get(
    '/health',
    response_model=Response[ProxyHealthCheck],
    operation_id='proxy_health_check',
    openapi_extra={
        'x-fern-sdk-group-name': 'proxy',
        'x-fern-sdk-method-name': 'health',
    },
)
async def health_check():
    """Check proxy subsystem health: GOST alive, nginx alive, config consistency."""
    result = await _service().health_check()
    return Response(success=True, data=result)


# --- Upstream proxy ---


@router.get(
    '/upstream',
    response_model=Response[ProxyUpstreamInfo | None],
    operation_id='get_proxy_upstream',
    openapi_extra={
        'x-fern-sdk-group-name': 'proxy',
        'x-fern-sdk-method-name': 'get_upstream',
    },
)
async def get_upstream():
    """Get the current upstream proxy configuration."""
    info = await _service().get_upstream()
    return Response(success=True, data=info)


@router.put(
    '/upstream',
    response_model=Response[ProxyUpstreamInfo],
    operation_id='set_proxy_upstream',
    openapi_extra={
        'x-fern-sdk-group-name': 'proxy',
        'x-fern-sdk-method-name': 'set_upstream',
    },
)
async def set_upstream(request: ProxyUpstreamUpdateRequest):
    """Set or update the upstream proxy. Supports user:pass@host:port format.

    Takes effect immediately — no browser restart needed.
    """
    info = await _service().set_upstream(request.server, auth_cmd=request.auth_cmd)
    return Response(success=True, message=f'Upstream set to {info.addr}', data=info)


@router.delete(
    '/upstream',
    response_model=Response,
    operation_id='remove_proxy_upstream',
    openapi_extra={
        'x-fern-sdk-group-name': 'proxy',
        'x-fern-sdk-method-name': 'remove_upstream',
    },
)
async def remove_upstream():
    """Remove upstream proxy (switch to direct mode)."""
    removed = await _service().remove_upstream()
    if not removed:
        return Response(success=False, message='No upstream proxy configured')
    return Response(success=True, message='Switched to direct mode')
