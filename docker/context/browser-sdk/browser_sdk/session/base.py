"""Session — abstract base class for the top-level session container."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from browser_sdk.browser.base import Browser
from browser_sdk.display.base import Display
from browser_sdk.models import OperationContext, ProxyConfig, Resolution
from browser_sdk.result import Result


class Session(ABC):
    """
    A connected, operable session — the top-level container.

    Provides access to:
    - ``display``: System-level GUI controller (always available)
    - ``browser``: Semantic browser controller (optional, available when browser is present)
    """

    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def created_at(self) -> datetime: ...

    @property
    @abstractmethod
    def display(self) -> Display: ...

    @property
    @abstractmethod
    def browser(self) -> Browser | None: ...

    @abstractmethod
    async def close(
        self,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        """Close session and release all resources."""

    @abstractmethod
    async def update_proxy(
        self,
        proxy: ProxyConfig,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        """Dynamically update session proxy configuration."""

    @abstractmethod
    async def update_resolution(
        self,
        resolution: Resolution,
        extra: Any | None = None,
        ctx: OperationContext | None = None,
    ) -> Result[None]:
        """Dynamically adjust display resolution."""
