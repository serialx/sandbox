"""MCP Proxy Middleware for search filtering with lazy loading support.

Hidden servers are deferred during initialization and loaded on-demand
when explicitly requested via ?search= parameter.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, Set

from fastmcp.server.middleware import Middleware, MiddlewareContext


if TYPE_CHECKING:
    from fastmcp.tools.tool import Tool


class LazyLoader(Protocol):
    """Protocol for lazy loading hidden servers."""

    _prefix_to_server: dict[str, str]

    async def load_servers_on_demand(self, requested_servers: Set[str]) -> None: ...


logger = logging.getLogger(__name__)


class MCPProxyMiddleware(Middleware):
    """
    Middleware to handle search filtering with lazy loading:
    - search: Filter tools via query parameter ?search=server1,server2,tag1,tag2
              Matches against both server names and tool tags
    - lazy loading: Hidden servers are loaded on-demand when requested via ?search=
    """

    def __init__(
        self, server_configs: dict[str, dict], lazy_loader: LazyLoader | None = None
    ):
        self._server_configs = server_configs
        self._lazy_loader = lazy_loader

    def _get_tool_server(self, tool: 'Tool') -> str | None:
        """Get server name for a tool by matching its key prefix.

        Tools from external servers have keys prefixed with the server's prefix.
        We match the tool.key against known prefixes to identify its server.
        Falls back to checking server: tags for backward compatibility.
        """
        # Primary: match tool key prefix against known servers
        if self._lazy_loader:
            tool_key = getattr(tool, 'key', tool.name)
            for prefix, server_name in self._lazy_loader._prefix_to_server.items():
                if tool_key.startswith(f'{prefix}_'):
                    return server_name

        # Fallback: check tags (for local tools or backward compatibility)
        for tag in tool.tags:
            if tag.startswith('server:'):
                return tag[7:]
        return None

    def _get_search_terms(self) -> Set[str] | None:
        """Get the set of search terms from ?search query parameter."""
        try:
            from fastmcp.server.dependencies import get_http_request

            request = get_http_request()
            search_param = request.query_params.get('search', '')
            if search_param:
                terms = {s.strip() for s in search_param.split(',') if s.strip()}
                logger.debug(f'Search filter applied: {terms}')
                return terms
        except Exception as e:
            logger.debug(f'Could not get HTTP request for search param: {e}')
        return None

    def _tool_matches_search(self, tool: 'Tool', search_terms: Set[str]) -> bool:
        """Check if tool matches any search term (server name or tag)."""
        # Match against server name
        server = self._get_tool_server(tool)
        if server and server in search_terms:
            return True

        # Match against any tag
        for tag in tool.tags:
            if tag in search_terms:
                return True

        return False

    def _get_hidden_servers_in_search(self, search_terms: Set[str]) -> Set[str]:
        """Get hidden server names that are in search terms."""
        hidden_servers = set()
        for term in search_terms:
            config = self._server_configs.get(term)
            if config and config.get('hidden', False):
                hidden_servers.add(term)
        return hidden_servers

    def _is_server_hidden(self, server_name: str) -> bool:
        """Check if a server is configured as hidden."""
        config = self._server_configs.get(server_name, {})
        return config.get('hidden', False)

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        """Filter tools based on search parameter with lazy loading support."""
        search_terms = self._get_search_terms()
        did_lazy_load = False

        # Lazy load hidden servers if requested via ?search=
        if search_terms and self._lazy_loader:
            hidden_in_search = self._get_hidden_servers_in_search(search_terms)
            if hidden_in_search:
                logger.info(f'Lazy loading hidden servers: {hidden_in_search}')
                await self._lazy_loader.load_servers_on_demand(hidden_in_search)
                did_lazy_load = True

        tools = await call_next(context)

        # If we just loaded new servers, call_next may have returned stale tools
        # Re-fetch from mcpServer to get the updated list including new tools
        if did_lazy_load:
            from app.mcp import mcpServer

            all_tools = await mcpServer.get_tools()
            tools = list(all_tools.values())
            logger.debug(f'Refreshed tools after lazy load: {len(tools)} total')

        # If search is specified, filter by search terms
        if search_terms is not None:
            filtered = [
                tool for tool in tools if self._tool_matches_search(tool, search_terms)
            ]
            logger.info(f'Tool filtering (search): {len(tools)} -> {len(filtered)}')
            return filtered

        # No search filter: return all tools EXCEPT those from hidden servers
        # (hidden servers may have been loaded by a previous search request)
        filtered = []
        for tool in tools:
            server = self._get_tool_server(tool)
            # Local tools (no server tag) are always shown
            if server is None:
                filtered.append(tool)
            # Hide tools from hidden servers when no search filter
            elif not self._is_server_hidden(server):
                filtered.append(tool)

        logger.info(f'Tool filtering (default): {len(tools)} -> {len(filtered)}')
        return filtered
