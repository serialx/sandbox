"""Authentication endpoints for gembrowser compatibility.

These endpoints provide JWT-based authentication and ticket generation,
maintaining backward compatibility with gem-server.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, Request, status

from app.core.service_container import services
from app.services.auth import AuthService


def get_auth_service() -> AuthService:
    """Dependency provider for the AuthService from ServiceContainer."""
    auth_service = services.get('auth_service')
    if auth_service is None:
        raise RuntimeError('AuthService not registered in ServiceContainer')
    return auth_service


router = APIRouter()


@router.post(
    '/tickets',
    status_code=status.HTTP_200_OK,
    openapi_extra={
        'x-fern-sdk-group-name': 'auth',
        'x-fern-sdk-method-name': 'create_ticket',
    },
)
async def create_ticket(
    service: AuthService = Depends(get_auth_service),
) -> Dict[str, Any]:
    """Create and return a short-lived authentication ticket.

    This is a non-idempotent action; each call creates a new, unique ticket.
    """
    return service.create_ticket()


@router.get(
    '/auth',
    status_code=status.HTTP_200_OK,
    openapi_extra={
        'x-fern-sdk-group-name': 'auth',
        'x-fern-sdk-method-name': 'authenticate',
    },
)
async def authenticate_request(
    request: Request, service: AuthService = Depends(get_auth_service)
) -> Dict[str, str]:
    """Authenticate a request using ticket or JWT.

    This endpoint receives authentication subrequests (e.g., from Nginx auth_request).
    It validates the request based on either a ticket in the 'x-original-uri'
    header or a JWT in the 'Authorization' header.
    """
    return service.authenticate_request(request)
