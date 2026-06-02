"""Middleware to normalize trailing slashes for specific paths."""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send


class SlashNormalizerMiddleware:
    """Normalize paths by adding trailing slash to avoid 307 redirects.

    Starlette's Mount mechanism redirects /path -> /path/ (307) which breaks
    clients that don't follow redirects properly for POST requests.
    This middleware rewrites the path internally to avoid the redirect.

    See: https://github.com/jlowin/fastmcp/issues/435
    """

    def __init__(self, app: ASGIApp, paths: list[str] | None = None) -> None:
        """Initialize the middleware.

        Args:
            app: The ASGI application to wrap.
            paths: List of paths to normalize (add trailing slash).
                   Defaults to ['/mcp'].
        """
        self.app = app
        self.paths = paths or ['/mcp']

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] == 'http':
            path = scope.get('path', '')
            if path in self.paths:
                # Rewrite path to include trailing slash
                scope = dict(scope)
                scope['path'] = path + '/'
        await self.app(scope, receive, send)
