import logging

from fastapi import APIRouter

from app.core.service_container import services
from app.models.display import DisplayRecordRequest, DisplayRecordResult
from app.schemas.response import Response
from app.services.display import DisplayService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    '/record',
    response_model=Response[DisplayRecordResult],
    openapi_extra={
        'x-fern-sdk-group-name': 'display',
        'x-fern-sdk-method-name': 'record',
    },
)
async def record(request: DisplayRecordRequest):
    """
    Control display screen recording (start/stop/status).

    Records the entire X11 desktop using ffmpeg x11grab,
    including browser UI, multiple tabs, popups, terminal, etc.
    """
    display_service: DisplayService = services.require('display_service')
    result = await display_service.record(
        action=request.action,
        save_path=request.save_path,
        fps=request.fps,
        crf=request.crf,
        max_duration=request.max_duration,
        width=request.width,
        height=request.height,
    )
    return Response(
        success=True,
        message=f'Display record {request.action} completed',
        data=result,
    )
