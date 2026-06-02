"""Request-scoped LogID middleware for internal sandboxd deployments."""

from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logid import get_logid, set_logid
from vendors.bytedlogger._runtime import generate_logid

LOGID_HEADER = 'X-Tt-Logid'
LOGID_HEADERS = (LOGID_HEADER,)


class LogIDMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    @staticmethod
    def _resolve_logid(headers: Headers) -> str:
        logid = headers.get(LOGID_HEADER)
        if logid:
            return logid

        return generate_logid()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        logid = self._resolve_logid(headers)
        previous_logid = get_logid()
        set_logid(logid)
        cleaned = False

        def cleanup() -> None:
            nonlocal cleaned
            if not cleaned:
                set_logid(previous_logid)
                cleaned = True

        async def send_with_logid(message: Message) -> None:
            if message['type'] == 'http.response.start':
                response_headers = MutableHeaders(scope=message)
                response_headers[LOGID_HEADER] = logid
            try:
                await send(message)
            finally:
                if message['type'] == 'http.response.body' and not message.get('more_body', False):
                    cleanup()

        try:
            await self.app(scope, receive, send_with_logid)
        finally:
            cleanup()


__all__ = ['LOGID_HEADER', 'LOGID_HEADERS', 'LogIDMiddleware', 'generate_logid']
