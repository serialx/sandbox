"""Authentication service for gembrowser compatibility.

This service provides JWT-based authentication and ticket generation,
maintaining backward compatibility with gem-server.
"""

import base64
import binascii
import hmac
import logging
import os
import time
import uuid
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from cachelib import SimpleCache
from fastapi import HTTPException, Request, status
from jwt import ExpiredSignatureError, PyJWTError, decode


logger = logging.getLogger(__name__)


class AuthConfig:
    """Configuration for authentication service."""

    def __init__(
        self,
        jwt_public_key_env: str = 'JWT_PUBLIC_KEY',
        ticket_ttl_env: str = 'TICKET_TTL_SECONDS',
        api_key_env: str = 'SANDBOX_API_KEY',
        jwt_algorithms: Optional[list[str]] = None,
        default_ticket_ttl: int = 30,
        default_cache_ttl: int = 1800,
    ):
        self.jwt_algorithms: list[str] = jwt_algorithms or ['RS256']
        self.default_ticket_ttl: int = default_ticket_ttl
        self.default_cache_ttl: int = default_cache_ttl

        self.jwt_public_key: Optional[str] = self._load_jwt_public_key(
            jwt_public_key_env
        )
        self.ticket_ttl: int = self._load_ticket_ttl(ticket_ttl_env)
        self.api_key: Optional[str] = self._load_api_key(api_key_env)

    def _load_jwt_public_key(self, env_var: str) -> Optional[str]:
        """Load and decode JWT public key from environment variable."""
        b64_encoded_key = os.environ.get(env_var)
        if not b64_encoded_key:
            logger.warning(
                f"Environment variable '{env_var}' is not set. "
                'JWT validation will be disabled.'
            )
            return None

        try:
            decoded_key = base64.b64decode(b64_encoded_key).decode('utf-8')
            logger.info(f"Successfully decoded JWT public key from '{env_var}'.")
            return decoded_key
        except (binascii.Error, UnicodeDecodeError) as e:
            logger.error(
                f"Failed to decode JWT public key from '{env_var}': {e}. "
                'Service will be unable to validate JWTs.'
            )
            return None

    def _load_ticket_ttl(self, env_var: str) -> int:
        """Load ticket TTL from environment variable."""
        raw_value = os.environ.get(env_var)
        if raw_value is None:
            return self.default_ticket_ttl

        try:
            parsed_ttl = int(raw_value)
            if parsed_ttl >= 0:
                logger.info(
                    f'Ticket TTL is set to {parsed_ttl} seconds from {env_var}.'
                )
                return parsed_ttl
            else:
                logger.warning(
                    f"Value for '{env_var}' cannot be negative ('{raw_value}'). "
                    f'Falling back to default of {self.default_ticket_ttl} seconds.'
                )
        except ValueError:
            logger.warning(
                f"Invalid value for '{env_var}' ('{raw_value}'). It must be an integer. "
                f'Falling back to default of {self.default_ticket_ttl} seconds.'
            )

        return self.default_ticket_ttl

    def _load_api_key(self, env_var: str) -> Optional[str]:
        """Load API key from environment variable."""
        raw_value = os.environ.get(env_var, '').strip()
        if not raw_value:
            return None
        logger.info(f"API key authentication enabled via '{env_var}'.")
        return raw_value


class AuthService:
    """Authentication service for ticket and JWT validation."""

    def __init__(
        self,
        config: AuthConfig,
        jwt_cache: Optional[SimpleCache] = None,
        ticket_cache: Optional[SimpleCache] = None,
    ):
        self.config = config
        self.jwt_cache = jwt_cache or SimpleCache()
        self.ticket_cache = ticket_cache or SimpleCache()

    def create_ticket(self) -> Dict[str, Any]:
        """Create a short-lived authentication ticket."""
        ticket = str(uuid.uuid4())
        self.ticket_cache.set(ticket, True, timeout=self.config.ticket_ttl)
        logger.info(
            f'Generated a new ticket with a {self.config.ticket_ttl}s TTL: {ticket}'
        )

        return {
            'ticket': ticket,
            'expires_in': self.config.ticket_ttl,
        }

    def validate_ticket(self, ticket: str) -> bool:
        """Validate if a ticket is still active."""
        return bool(self.ticket_cache.get(ticket))

    def parse_ticket_from_uri(self, original_uri: str) -> Optional[str]:
        """Extract ticket parameter from URI query string."""
        parsed_uri = urlparse(original_uri)
        query_params = parse_qs(parsed_uri.query)
        ticket_list = query_params.get('ticket')
        return ticket_list[-1] if ticket_list else None

    def _validate_jwt(self, token: str) -> Dict[str, Any]:
        """Validate a JWT token."""
        if not self.config.jwt_public_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail='Authentication is not possible. JWT public key has not been configured.',
            )

        if self.jwt_cache.get(token):
            return {'status': 'ok', 'message': 'Access granted from cache.'}

        try:
            payload = decode(
                token, self.config.jwt_public_key, algorithms=self.config.jwt_algorithms
            )

            exp = payload.get('exp')
            if exp and isinstance(exp, int):
                ttl = exp - int(time.time())
                if ttl > 0:
                    self.jwt_cache.set(token, True, timeout=ttl)
            else:
                self.jwt_cache.set(token, True, timeout=self.config.default_cache_ttl)

            return {'status': 'ok', 'message': 'Access granted after validation.'}

        except ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail='Token has expired'
            )
        except PyJWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=f'Invalid token: {e}'
            )

    def _extract_api_key(self, request: Request) -> Optional[str]:
        """Extract API key from request headers or query parameters."""
        # Check X-AIO-API-Key header
        api_key = request.headers.get('X-AIO-API-Key')
        if api_key:
            return api_key

        # Check query parameter
        original_uri = request.headers.get('x-original-uri', '')
        parsed_uri = urlparse(original_uri)
        query_params = parse_qs(parsed_uri.query)
        api_key_list = query_params.get('api_key')
        if api_key_list:
            return api_key_list[-1]

        return None

    def _validate_api_key(self, provided_key: str) -> Dict[str, str]:
        """Validate the provided API key against the configured key."""
        if hmac.compare_digest(provided_key, self.config.api_key):
            return {'status': 'ok', 'message': 'API key validated.'}
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid API key.',
        )

    def authenticate_request(self, request: Request) -> Dict[str, str]:
        """Handle authentication request supporting ticket, API key, and JWT."""
        # Priority 1: Ticket validation
        original_uri = request.headers.get('x-original-uri', '')
        ticket = self.parse_ticket_from_uri(original_uri)

        if ticket:
            if self.validate_ticket(ticket):
                logger.info(
                    f"Successfully validated ticket '{ticket}' from URI '{original_uri}'"
                )
                return {'status': 'ok', 'message': 'Ticket validated.'}
            else:
                logger.warning(
                    f"Invalid or expired ticket '{ticket}' received in URI: {original_uri}"
                )
                raise HTTPException(
                    status_code=401, detail='Invalid or expired ticket.'
                )

        # Priority 2: API Key validation (via X-AIO-API-Key header or query param)
        if self.config.api_key:
            explicit_api_key = self._extract_api_key(request)
            if explicit_api_key:
                return self._validate_api_key(explicit_api_key)

        # Priority 3: Bearer token (API Key or JWT)
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Authorization header is missing',
            )

        try:
            token_type, token = auth_header.split(maxsplit=1)
            if token_type.lower() != 'bearer':
                raise ValueError('Invalid token type')
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization header format. Must be 'Bearer <token>'",
            )

        # If API key is configured, check Bearer token as API key first
        if self.config.api_key:
            if hmac.compare_digest(token, self.config.api_key):
                return {'status': 'ok', 'message': 'API key validated.'}
            # If JWT is also configured, fall through to JWT validation
            if not self.config.jwt_public_key:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail='Invalid API key.',
                )

        return self._validate_jwt(token)
