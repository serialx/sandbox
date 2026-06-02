from __future__ import annotations

import ipaddress
import logging
import os
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.core.exceptions import BadRequestException
from app.logging.decorators import trace_api

logger = logging.getLogger(__name__)


def check_plugins_enabled() -> bool:
    return os.getenv('MARKITDOWN_ENABLE_PLUGINS', 'false').strip().lower() in (
        'true',
        '1',
        'yes',
    )


def _is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname == 'localhost':
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _parse_content_type(
    content_type: str | None,
) -> tuple[str | None, str | None]:
    if not content_type:
        return None, None

    mimetype: str | None = None
    charset: str | None = None

    for index, raw_part in enumerate(content_type.split(';')):
        part = raw_part.strip()
        if not part:
            continue
        if index == 0:
            mimetype = part.lower()
            continue

        key, sep, value = part.partition('=')
        if sep and key.strip().lower() == 'charset':
            charset = value.strip().strip('"') or None

    return mimetype, charset


def _is_text_like_mimetype(mimetype: str | None) -> bool:
    return bool(
        mimetype
        and (
            mimetype.startswith('text/')
            or mimetype
            in {
                'application/json',
                'application/xml',
                'application/javascript',
                'application/x-javascript',
                'application/x-yaml',
                'application/yaml',
            }
        )
    )


def _build_netloc(hostname: str, port: int) -> str:
    if ':' in hostname and not hostname.startswith('['):
        return f'[{hostname}]:{port}'
    return f'{hostname}:{port}'


class UtilService:
    @trace_api('util')
    async def convert_to_markdown(self, uri: str) -> str:
        normalized_uri = uri.strip()
        if not normalized_uri:
            raise BadRequestException('uri must not be empty')

        parsed = urlparse(normalized_uri)
        if parsed.scheme in ('http', 'https') and _is_loopback_host(parsed.hostname):
            return await self._convert_loopback_url_to_markdown(normalized_uri)

        from markitdown import MarkItDown

        return MarkItDown(enable_plugins=check_plugins_enabled()).convert_uri(
            normalized_uri
        ).markdown

    async def _convert_loopback_url_to_markdown(self, uri: str) -> str:
        from markitdown import MarkItDown, StreamInfo

        async with httpx.AsyncClient(
            proxy=None,
            trust_env=False,
            follow_redirects=True,
            timeout=30.0,
        ) as client:
            response = await self._fetch_loopback_response(client, uri)

        final_url = str(response.url)
        parsed = urlparse(final_url)
        mimetype, charset = _parse_content_type(response.headers.get('content-type'))
        stream_info = StreamInfo(
            mimetype=mimetype,
            extension=Path(parsed.path).suffix or None,
            charset=charset,
            filename=Path(parsed.path).name or None,
            url=final_url,
        )
        converter = MarkItDown(enable_plugins=check_plugins_enabled())
        try:
            return converter.convert_stream(
                BytesIO(response.content),
                stream_info=stream_info,
            ).markdown
        except Exception:
            if _is_text_like_mimetype(mimetype):
                return response.text
            raise

    async def _fetch_loopback_response(
        self,
        client: httpx.AsyncClient,
        uri: str,
    ) -> httpx.Response:
        last_error: httpx.RequestError | None = None
        for candidate_uri in self._iter_loopback_candidates(uri):
            try:
                response = await client.get(candidate_uri)
            except httpx.RequestError as exc:
                last_error = exc
                logger.info(
                    'Loopback markdown fetch failed for %s: %s',
                    candidate_uri,
                    exc,
                )
                continue

            response.raise_for_status()
            return response

        if last_error is not None:
            raise last_error
        raise RuntimeError(f'Unable to fetch loopback URI: {uri}')

    def _iter_loopback_candidates(self, uri: str) -> list[str]:
        parsed = urlparse(uri)
        hostname = parsed.hostname or '127.0.0.1'
        original_port = parsed.port or (443 if parsed.scheme == 'https' else 80)

        candidates = [uri]
        seen_ports = {original_port}
        for env_name, default_value in (
            ('PUBLIC_PORT', '8080'),
            ('SANDBOX_SRV_PORT', '8091'),
        ):
            raw_port = os.getenv(env_name, default_value).strip()
            if not raw_port.isdigit():
                continue
            port = int(raw_port)
            if port in seen_ports:
                continue
            seen_ports.add(port)
            candidates.append(
                parsed._replace(netloc=_build_netloc(hostname, port)).geturl()
            )

        return candidates
