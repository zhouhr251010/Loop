"""Async HTTP client helpers for Infinity embedding and reranking services."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx


logger = logging.getLogger(__name__)


def _float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value.strip())
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value.strip())
    except ValueError:
        return default


class InfinityClient:
    """Small reusable async JSON client with bounded retry/backoff behavior."""

    def __init__(self) -> None:
        timeout_seconds = _float_env("LOOP_INFINITY_TIMEOUT_SECONDS", 30.0)
        self.retries = max(1, _int_env("LOOP_INFINITY_RETRIES", 3))
        self.backoff_seconds = max(
            0.0,
            _float_env("LOOP_INFINITY_RETRY_BACKOFF_SECONDS", 0.5),
        )
        self.async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout_seconds,
                connect=timeout_seconds,
                read=timeout_seconds,
                write=timeout_seconds,
                pool=timeout_seconds,
            ),
        )

    async def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON to Infinity, retrying only transient failures."""
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = await self.async_client.post(url, json=payload)
                if 400 <= response.status_code < 500:
                    response.raise_for_status()
                if response.status_code >= 500:
                    response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise RuntimeError(
                        f"Infinity response at {url} was not a JSON object.",
                    )
                return data
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status_code = exc.response.status_code
                logger.warning(
                    "Infinity request failed url=%s attempt=%s/%s error_type=%s "
                    "status_code=%s",
                    url,
                    attempt,
                    self.retries,
                    exc.__class__.__name__,
                    status_code,
                )
                if 400 <= status_code < 500:
                    raise RuntimeError(
                        f"Infinity request rejected with HTTP {status_code}: {url}",
                    ) from exc
            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.ReadError,
                httpx.RequestError,
            ) as exc:
                last_exc = exc
                logger.warning(
                    "Infinity request failed url=%s attempt=%s/%s error_type=%s",
                    url,
                    attempt,
                    self.retries,
                    exc.__class__.__name__,
                )
            except Exception as exc:
                logger.exception(
                    "Infinity request failed url=%s attempt=%s/%s error_type=%s",
                    url,
                    attempt,
                    self.retries,
                    exc.__class__.__name__,
                )
                raise RuntimeError(f"Infinity request failed: {url}") from exc

            if attempt < self.retries:
                await asyncio.sleep(self.backoff_seconds * attempt)

        raise RuntimeError(f"Infinity request failed after retries: {url}") from last_exc

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self.async_client.aclose()


_client: InfinityClient | None = None


def get_infinity_client() -> InfinityClient:
    """Return the process-local Infinity async client."""
    global _client
    if _client is None:
        _client = InfinityClient()
    return _client


async def close_infinity_client() -> None:
    """Close the singleton client during application shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
