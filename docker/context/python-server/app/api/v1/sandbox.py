import json
import subprocess
from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.service_container import services
from app.core.exceptions import BadRequestException, ResourceNotFoundException
from app.models.observation import (
    ObservationExportResult,
    ObservationLiveSnapshot,
    ObservationReportInfo,
    ObservationStartResult,
    ObservationStatus,
    ObservationStopResult,
)
from app.models.sandbox import (
    SandboxDetail,
    SandboxHook,
)
from app.schemas.observation import (
    ObserveExportRequest,
    ObserveStartRequest,
    ObserveStopRequest,
)
from app.schemas.response import Response


if TYPE_CHECKING:
    from app.services.observation import ObservationService
    from app.services.sandbox import SandboxService
    from app.services.shutdown_hooks import ShutdownHookService

router = APIRouter()


class SandboxResponse(Response[str]):
    home_dir: str
    workspace: Optional[str] = None
    version: str
    detail: SandboxDetail


@router.get(
    '',
    response_model=SandboxResponse,
    openapi_extra={
        'x-fern-sdk-group-name': 'sandbox',
        'x-fern-sdk-method-name': 'get_context',
    },
)
async def get_sandbox_context() -> SandboxResponse:
    """
    Get sandbox environment information
    """
    sandbox_service: 'SandboxService' = services.require('sandbox_service')
    sandbox_info = sandbox_service.get_sandbox_info()

    return SandboxResponse(
        success=True,
        message='Environment information retrieved',
        data=sandbox_info.info,
        hint=None,
        detail=sandbox_info.detail,
        version=sandbox_info.version,
        home_dir=sandbox_info.home_dir,
        workspace=sandbox_info.workspace,
    )


@router.get(
    '/packages/python',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'sandbox',
        'x-fern-sdk-method-name': 'get_python_packages',
    },
)
async def python_packages() -> Response[str]:
    """
    Get installed Python packages
    """
    packages = get_python_packages()

    return Response(
        success=True,
        message='Packages information retrieved',
        data=packages,
        hint=None,
    )


@router.get(
    '/packages/nodejs',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'sandbox',
        'x-fern-sdk-method-name': 'get_nodejs_packages',
    },
)
async def nodejs_packages() -> Response[str]:
    """
    Get installed Node.js packages
    """
    packages = get_node_packages()
    packages_info = f"""Node.js Packages:
{packages}
"""

    return Response(
        success=True,
        message='Packages information retrieved',
        data=packages_info,
        hint=None,
    )


def get_python_packages() -> str:
    """获取已安装的Python包信息"""
    try:
        result = subprocess.run(
            ['pip', 'list', '--format=json'], stdout=subprocess.PIPE
        )
        packages = ''

        if result.returncode == 0:
            packages = json.loads(result.stdout)
            return (
                '\n'.join(
                    map(lambda pkg: f'  - {pkg["name"]}=={pkg["version"]}', packages)
                )
                if packages
                else '  - No global packages installed'
            )

        else:
            return '  - No global packages installed'
    except Exception:
        return '  - Unable to retrieve package list'


def get_node_packages() -> str:
    """获取已安装的Node.js包信息"""
    try:
        # 检查是否有全局安装的npm包
        result = subprocess.run(
            ['npm', 'list', '-g', '--depth=0'],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            packages = []
            for line in lines[1:]:  # 跳过标题行
                if line.strip() and not line.startswith('npm ERR'):
                    # 解析npm list的输出格式: "├── package@version" 或 "└── package@version"
                    if '├──' in line or '└──' in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            # parts[1] 已经是 "package@version" 格式
                            packages.append(f'  - {parts[1]}')

            if packages:
                return '\n'.join(packages)
            else:
                return '  - No global packages installed'
        return '  - Unable to retrieve package list'
    except Exception:
        return '  - Unable to retrieve package list'


# ── Shutdown Hooks API ──


class RegisterHookRequest(BaseModel):
    name: str = Field(..., description='Unique name for this hook', pattern=r'^[a-zA-Z0-9_-]+$', max_length=64)
    event: str = Field('shutdown', description='Lifecycle event: "shutdown"')
    command: str = Field(..., description='Shell command to execute', max_length=4096)
    timeout: float = Field(10, description='Per-hook timeout in seconds')
    priority: int = Field(100, description='Execution priority (lower = earlier). Same priority hooks run in parallel')


@router.post(
    '/hooks',
    response_model=Response[SandboxHook],
    openapi_extra={
        'x-fern-sdk-group-name': 'sandbox',
        'x-fern-sdk-method-name': 'register_hook',
    },
)
async def register_hook(request: RegisterHookRequest) -> Response[SandboxHook]:
    """Register a lifecycle hook. Currently supported events: shutdown."""
    if request.event != 'shutdown':
        raise BadRequestException(f'Unsupported event: {request.event!r}. Supported: "shutdown"')
    svc: 'ShutdownHookService' = services.require('shutdown_hook_service')
    hook = svc.register(request.name, request.command, request.timeout, request.priority)
    return Response(success=True, message='Hook registered', data=hook, hint=None)


@router.get(
    '/hooks',
    response_model=Response[list[SandboxHook]],
    openapi_extra={
        'x-fern-sdk-group-name': 'sandbox',
        'x-fern-sdk-method-name': 'list_hooks',
    },
)
async def list_hooks(event: str = 'shutdown') -> Response[list[SandboxHook]]:
    """List registered lifecycle hooks, optionally filtered by event."""
    if event != 'shutdown':
        raise BadRequestException(f'Unsupported event: {event!r}. Supported: "shutdown"')
    svc: 'ShutdownHookService' = services.require('shutdown_hook_service')
    hooks = svc.list_hooks()
    return Response(success=True, message='Hooks listed', data=hooks, hint=None)


@router.delete(
    '/hooks/{name}',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'sandbox',
        'x-fern-sdk-method-name': 'remove_hook',
    },
)
async def remove_hook(name: str) -> Response[None]:
    """Remove a hook by name. ENV hooks cannot be removed."""
    svc: 'ShutdownHookService' = services.require('shutdown_hook_service')
    removed = svc.unregister(name)
    if not removed:
        raise ResourceNotFoundException(f'Hook not found: {name}')
    return Response(success=True, message='Hook removed', data=None, hint=None)


@router.get(
    '/observe/status',
    response_model=Response[ObservationStatus],
    openapi_extra={
        'x-fern-sdk-group-name': 'sandbox',
        'x-fern-sdk-method-name': 'observe_status',
    },
)
async def observe_status() -> Response[ObservationStatus]:
    svc: 'ObservationService' = services.require('observation_service')
    status = await svc.status()
    return Response(
        success=True,
        message='Observation status retrieved',
        data=status,
        hint=None,
    )


@router.get(
    '/observe/live',
    response_model=Response[ObservationLiveSnapshot],
    openapi_extra={
        'x-fern-sdk-group-name': 'sandbox',
        'x-fern-sdk-method-name': 'observe_live',
    },
)
async def observe_live(top_rows: int = 10) -> Response[ObservationLiveSnapshot]:
    svc: 'ObservationService' = services.require('observation_service')
    snapshot = await svc.live_snapshot(top_rows=top_rows)
    return Response(
        success=True,
        message='Observation snapshot retrieved',
        data=snapshot,
        hint=None,
    )


@router.post(
    '/observe/start',
    response_model=Response[ObservationStartResult],
    openapi_extra={
        'x-fern-sdk-group-name': 'sandbox',
        'x-fern-sdk-method-name': 'observe_start',
    },
)
async def observe_start(
    request: ObserveStartRequest,
) -> Response[ObservationStartResult]:
    svc: 'ObservationService' = services.require('observation_service')
    result = await svc.start(
        mode=request.mode,
        idempotency_key=request.idempotency_key,
        duration_seconds=request.duration_seconds,
        interval_seconds=request.interval_seconds,
        include_processes=request.include_processes,
        include_disk=request.include_disk,
    )
    return Response(
        success=True,
        message='Observation session started',
        data=result,
        hint=None,
    )


@router.post(
    '/observe/stop',
    response_model=Response[ObservationStopResult],
    openapi_extra={
        'x-fern-sdk-group-name': 'sandbox',
        'x-fern-sdk-method-name': 'observe_stop',
    },
)
async def observe_stop(
    request: ObserveStopRequest,
) -> Response[ObservationStopResult]:
    svc: 'ObservationService' = services.require('observation_service')
    result = await svc.stop(session_id=request.session_id)
    return Response(
        success=True,
        message='Observation session stopped',
        data=result,
        hint=None,
    )


@router.post(
    '/observe/export',
    response_model=Response[ObservationExportResult],
    openapi_extra={
        'x-fern-sdk-group-name': 'sandbox',
        'x-fern-sdk-method-name': 'observe_export',
    },
)
async def observe_export(
    request: ObserveExportRequest,
) -> Response[ObservationExportResult]:
    svc: 'ObservationService' = services.require('observation_service')
    result = await svc.export_report(
        session_id=request.session_id,
        reason=request.reason,
        idempotency_key=request.idempotency_key,
    )
    return Response(
        success=True,
        message='Observation report exported',
        data=result,
        hint=None,
    )


@router.get(
    '/observe/reports',
    response_model=Response[list[ObservationReportInfo]],
    openapi_extra={
        'x-fern-sdk-group-name': 'sandbox',
        'x-fern-sdk-method-name': 'observe_reports',
    },
)
async def observe_reports() -> Response[list[ObservationReportInfo]]:
    svc: 'ObservationService' = services.require('observation_service')
    reports = await svc.list_reports()
    return Response(
        success=True,
        message='Observation reports listed',
        data=reports,
        hint=None,
    )


@router.get(
    '/observe/reports/{report_id}',
    response_class=FileResponse,
    responses={
        200: {
            'content': {
                'application/gzip': {
                    'schema': {'type': 'string', 'format': 'binary'}
                }
            }
        }
    },
    openapi_extra={
        'x-fern-sdk-group-name': 'sandbox',
        'x-fern-sdk-method-name': 'observe_report_download',
    },
)
async def observe_report_download(report_id: str) -> FileResponse:
    svc: 'ObservationService' = services.require('observation_service')
    path = await svc.get_report_path(report_id)
    return FileResponse(
        path=path,
        filename=path.name,
        media_type='application/gzip',
    )


@router.delete(
    '/observe/reports/{report_id}',
    response_model=Response[ObservationReportInfo],
    openapi_extra={
        'x-fern-sdk-group-name': 'sandbox',
        'x-fern-sdk-method-name': 'observe_report_delete',
    },
)
async def observe_report_delete(
    report_id: str,
) -> Response[ObservationReportInfo]:
    svc: 'ObservationService' = services.require('observation_service')
    info = await svc.delete_report(report_id)
    return Response(
        success=True,
        message='Observation report deleted',
        data=info,
        hint=None,
    )


def check_network_connectivity() -> str:
    """检查网络连接状态"""
    try:
        # 测试DNS解析
        result = subprocess.run(
            ['nslookup', 'google.com'], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return '- Internet connectivity: Available'
        else:
            return '- Internet connectivity: Limited (DNS issues)'
    except Exception:
        return '- Internet connectivity: Unknown'
