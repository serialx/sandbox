"""Unified Result[T] return type for all SDK operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ErrorDetails:
    """Structured error information returned on failure."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class Result(Generic[T]):
    """
    Unified return type for all async SDK methods.

    Usage::

        result = await page.open("https://example.com")
        if result.success:
            print(result.data.title)
        else:
            print(result.error.code, result.error.message)
    """

    success: bool
    data: T | None = None
    error: ErrorDetails | None = None

    @staticmethod
    def ok(data: T = None) -> Result[T]:  # type: ignore[assignment]
        return Result(success=True, data=data)

    @staticmethod
    def fail(code: str, message: str) -> Result[T]:
        return Result(success=False, error=ErrorDetails(code=code, message=message))
