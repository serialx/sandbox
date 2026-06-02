from typing import Literal


ServiceName = Literal[
    'mcp_client',
    'jupyter_service',
    'terminal_manager',
    'terminal_ws_manager',
    'bash_manager',
    'nodejs_service',
    'file_service',
    'browser_service',
    'browser_sdk_service',
    'sandbox_service',
    'util_service',
    'skills_service',
    'auth_service',
    'pool_manager',
    'proxy_mapping_service',
    'shutdown_hook_service',
    'display_service',
    'state_store',
    'file_watch_service',
    'observation_service',
]


class ServiceContainer:
    def __init__(self):
        self._services = {}

    def register(self, name: ServiceName, service):
        self._services[name] = service

    def get(self, name: ServiceName):
        return self._services.get(name)

    def require(self, name: ServiceName):
        """Get a service or raise ServiceUnavailableException (503) if not initialized."""
        from app.core.exceptions import ServiceUnavailableException

        svc = self._services.get(name)
        if svc is None:
            raise ServiceUnavailableException(name)
        return svc

    def clear(self):
        self._services.clear()


services = ServiceContainer()
