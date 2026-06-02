from fastapi import APIRouter

from app.core.service_container import services
from app.schemas.response import Response
from app.schemas.util import UtilConvertToMarkdownRequest
from app.services.utils import UtilService


router = APIRouter()


@router.post(
    '/convert_to_markdown',
    response_model=Response,
    openapi_extra={
        'x-fern-sdk-group-name': 'util',
        'x-fern-sdk-method-name': 'convert_to_markdown',
    },
)
async def convert_to_markdown(request: UtilConvertToMarkdownRequest):
    """
    Convert a given URI to Markdown format
    """
    util_service: UtilService = services.require('util_service')
    markdown = await util_service.convert_to_markdown(request.uri)

    return Response(
        success=True,
        message='Markdown conversion successful',
        data=markdown,
    )
