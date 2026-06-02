"""RemoteSession — connects to a remote sandbox via HTTP API."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

import httpx

from browser_sdk.browser.base import Browser
from browser_sdk.browser.remote import RemoteBrowser
from browser_sdk.display.base import Display
from browser_sdk.display.remote import RemoteDisplay
from browser_sdk.errors import (
    E_CONNECT_FAILED,
    E_CONNECT_TIMEOUT,
    E_SESSION_CLOSED,
    E_SESSION_UPDATE_PROXY_FAILED,
    E_SESSION_UPDATE_RESOLUTION_FAILED,
)
from browser_sdk.models import (
    BrowserFlareConfig,
    BrowserInfo,
    OperationContext,
    ProxyConfig,
    RemoteConfig,
    Resolution,
    SessionConfig,
)
from browser_sdk.result import Result
from browser_sdk.session.base import Session

logger = logging.getLogger(__name__)


class RemoteSession(Session):
    """
    Session that operates on a remote sandbox via HTTP API.

    Created by ``BrowserFlare.connect()`` — not instantiated directly.
    """

    def __init__(
        self,
        session_id: str,
        display: RemoteDisplay,
        browser: RemoteBrowser,
        client: httpx.AsyncClient,
        base_url: str,
        config: SessionConfig,
    ) -> None:
        self._id = session_id
        self._created_at = datetime.now()
        self._display = display
        self._browser = browser
        self._client = client
        self._base_url = base_url
        self._config = config
        self._closed = False

    @property
    def id(self) -> str:
        return self._id

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def display(self) -> Display:
        return self._display

    @property
    def browser(self) -> Browser | None:
        return self._browser

    async def close(self, ctx: OperationContext | None = None) -> Result[None]:
        if self._closed:
            return Result.fail(E_SESSION_CLOSED, "Session already closed")
        self._closed = True
        try:
            await self._client.post(f"{self._base_url}/v1/session/close")
        except Exception:
            pass
        await self._client.aclose()
        logger.info("RemoteSession closed: %s", self._id)
        return Result.ok()

    async def update_proxy(
        self,
        proxy: ProxyConfig,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        if self._closed:
            return Result.fail(E_SESSION_CLOSED, "Session is closed")
        try:
            resp = await self._client.post(
                f"{self._base_url}/v1/session/update_proxy",
                json=proxy.model_dump(),
            )
            resp.raise_for_status()
            return Result.ok()
        except Exception as e:
            return Result.fail(E_SESSION_UPDATE_PROXY_FAILED, str(e))

    async def update_resolution(
        self,
        resolution: Resolution,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        if self._closed:
            return Result.fail(E_SESSION_CLOSED, "Session is closed")
        try:
            resp = await self._client.post(
                f"{self._base_url}/v1/session/update_resolution",
                json=resolution.model_dump(),
            )
            resp.raise_for_status()
            return Result.ok()
        except Exception as e:
            return Result.fail(E_SESSION_UPDATE_RESOLUTION_FAILED, str(e))

    @staticmethod
    async def create(
        config: SessionConfig,
        sdk_config: BrowserFlareConfig,
    ) -> Result["RemoteSession"]:
        """
        Connect to a remote sandbox and return a ready-to-use session.
        """
        remote = config.remote or RemoteConfig(api_endpoint="")
        base_url = remote.api_endpoint.rstrip("/")
        session_id = config.session_id or uuid.uuid4().hex[:12]

        client = httpx.AsyncClient(timeout=httpx.Timeout(sdk_config.connect_timeout))

        # Verify connectivity and get browser info
        try:
            resp = await client.get(f"{base_url}/v1/browser/info")
            resp.raise_for_status()
            info_data = resp.json()
        except httpx.TimeoutException:
            await client.aclose()
            return Result.fail(
                E_CONNECT_TIMEOUT,
                f"Connection to {base_url} timed out after {sdk_config.connect_timeout}s",
            )
        except Exception as e:
            await client.aclose()
            return Result.fail(E_CONNECT_FAILED, f"Failed to connect to {base_url}: {e}")

        browser_info = BrowserInfo(
            session_id=session_id,
            cdp_endpoint=info_data.get("cdp_endpoint", ""),
            config=config,
            start_time=datetime.now(),
            user_agent=info_data.get("user_agent", ""),
        )

        display = RemoteDisplay(client, base_url)
        browser = RemoteBrowser(client, base_url, browser_info)

        session = RemoteSession(
            session_id=session_id,
            display=display,
            browser=browser,
            client=client,
            base_url=base_url,
            config=config,
        )
        logger.info(
            "RemoteSession created: id=%s endpoint=%s",
            session_id,
            base_url,
        )
        return Result.ok(session)
