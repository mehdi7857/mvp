from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx
from loguru import logger


class HyperliquidClient:
    """
    Minimal hardened client for Hyperliquid public /info endpoint.
    """

    def __init__(
        self,
        base_url: str = "https://api.hyperliquid.xyz",
        timeout_seconds: float = 6.0,
        retry_attempts: int = 4,
        backoff_base_seconds: float = 0.6,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retry_attempts = retry_attempts
        self.backoff_base_seconds = backoff_base_seconds
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Content-Type": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def post_info(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        """
        Returns parsed JSON dict or None if all retries fail.
        NEVER raises network exceptions (hardening).
        """
        last_err: Optional[Exception] = None

        for attempt in range(1, self.retry_attempts + 1):
            try:
                resp = await self._client.post(
                    "/info",
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                resp.raise_for_status()
                return resp.json()
            except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                last_err = e
                logger.warning(
                    f"HL /info timeout (attempt {attempt}/{self.retry_attempts})"
                )
            except (httpx.ConnectError, httpx.NetworkError) as e:
                last_err = e
                logger.warning(
                    f"HL /info network error {type(e).__name__} "
                    f"(attempt {attempt}/{self.retry_attempts})"
                )
            except httpx.HTTPStatusError as e:
                last_err = e
                status = e.response.status_code if e.response is not None else "?"
                logger.warning(
                    f"HL /info bad status {status} (attempt {attempt}/{self.retry_attempts})"
                )
            except Exception as e:
                last_err = e
                logger.warning(
                    f"HL /info unexpected {type(e).__name__} (attempt {attempt}/{self.retry_attempts})"
                )

            # exponential backoff with cap
            sleep_s = min(8.0, self.backoff_base_seconds * (2 ** (attempt - 1)))
            await asyncio.sleep(sleep_s)

        logger.error(
            f"HL /info failed after retries. last_err={type(last_err).__name__ if last_err else 'None'}"
        )
        return None
