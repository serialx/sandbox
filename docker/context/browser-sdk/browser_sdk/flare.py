"""BrowserFlare — the SDK entry point / session factory."""

from __future__ import annotations

import logging

from browser_sdk.errors import E_CONFIG_INVALID_TYPE, E_CONFIG_MISSING_LOCAL, E_CONFIG_MISSING_REMOTE
from browser_sdk.models import BrowserFlareConfig, SessionConfig
from browser_sdk.result import Result
from browser_sdk.session.base import Session

logger = logging.getLogger(__name__)


class BrowserFlare:
    """
    Top-level factory for creating browser sessions.

    Usage::

        flare = BrowserFlare()

        # Local session (connects to existing Chromium via CDP)
        result = await flare.connect(SessionConfig(type="local"))

        # Remote session (connects to sandbox API)
        result = await flare.connect(SessionConfig(
            type="remote",
            remote=RemoteConfig(api_endpoint="https://sandbox.example.com"),
        ))

        if result.success:
            session = result.data
            page_result = await session.browser.pages.get_current()
    """

    def __init__(self, config: BrowserFlareConfig | None = None) -> None:
        self._config = config or BrowserFlareConfig()

    @property
    def config(self) -> BrowserFlareConfig:
        return self._config

    async def connect(
        self,
        session_config: SessionConfig,
    ) -> Result[Session]:
        """
        Create and return a connected Session.

        For ``type="local"``, connects to a local Chromium via CDP.
        For ``type="remote"``, connects to a remote sandbox via HTTP.
        """
        if session_config.type == "local":
            return await self._connect_local(session_config)
        elif session_config.type == "remote":
            return await self._connect_remote(session_config)
        else:
            return Result.fail(
                E_CONFIG_INVALID_TYPE,
                f"Unknown session type: {session_config.type!r}. Must be 'local' or 'remote'.",
            )

    async def _connect_local(self, config: SessionConfig) -> Result[Session]:
        from browser_sdk.session.local import LocalSession

        return await LocalSession.create(config, self._config)

    async def _connect_remote(self, config: SessionConfig) -> Result[Session]:
        if config.remote is None:
            return Result.fail(
                E_CONFIG_MISSING_REMOTE,
                "RemoteConfig is required for type='remote'",
            )
        from browser_sdk.session.remote import RemoteSession

        return await RemoteSession.create(config, self._config)
