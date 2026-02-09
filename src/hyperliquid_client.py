from __future__ import annotations

import asyncio
import random
from typing import Any, Optional

import httpx
from loguru import logger


class HyperliquidClient:
    """
    Hardened async client for Hyperliquid public /info endpoint.

    Goals:
    - Never raise network exceptions to callers
    - Retry only on transient conditions (timeouts/network/429/5xx)
    - Safe JSON parsing (never crashes on bad payload)
    """

    def __init__(
        self,
        base_url: str = "https://api.hyperliquid.xyz",
        timeout_seconds: float = 6.0,
        retry_attempts: int = 4,
        backoff_base_seconds: float = 0.6,
        backoff_cap_seconds: float = 8.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.retry_attempts = int(retry_attempts)
        self.backoff_base_seconds = float(backoff_base_seconds)
        self.backoff_cap_seconds = float(backoff_cap_seconds)

        timeout = httpx.Timeout(
            self.timeout_seconds,
            connect=self.timeout_seconds,
            read=self.timeout_seconds,
            write=self.timeout_seconds,
            pool=self.timeout_seconds,
        )

        limits = httpx.Limits(
            max_connections=20,
            max_keepalive_connections=10,
            keepalive_expiry=30.0,
        )

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
        )

    async def __aenter__(self) -> "HyperliquidClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _sleep_seconds(self, attempt: int) -> float:
        # exponential backoff + jitter
        base = self.backoff_base_seconds * (2 ** (attempt - 1))
        base = min(self.backoff_cap_seconds, base)
        jitter = 0.8 + 0.4 * random.random()  # 0.8..1.2
        return base * jitter

    @staticmethod
    def _should_retry_status(code: int) -> bool:
        # retry: rate limit or server errors
        return code == 429 or 500 <= code <= 599

    async def post_info(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        """
        Returns parsed JSON dict or None if all retries fail.
        NEVER raises network exceptions (hardening).
        """
        last_err: Optional[str] = None

        for attempt in range(1, self.retry_attempts + 1):
            try:
                resp = await self._client.post("/info", json=payload)

                # If status is bad:
                if resp.status_code >= 400:
                    if self._should_retry_status(resp.status_code):
                        last_err = f"HTTP {resp.status_code}"
                        logger.warning(
                            f"HL /info transient status {resp.status_code} "
                            f"(attempt {attempt}/{self.retry_attempts})"
                        )
                    else:
                        # Non-retryable client error: payload / permissions / endpoint
                        logger.error(
                            f"HL /info non-retryable status {resp.status_code}. "
                            f"Check payload/endpoint. (attempt {attempt}/{self.retry_attempts})"
                        )
                        return None
                else:
                    # OK status -> parse JSON safely
                    try:
                        data = resp.json()
                        if isinstance(data, dict):
                            return data
                        # HL should return dict; if not, treat as bad
                        last_err = "non-dict-json"
                        logger.warning(
                            f"HL /info unexpected JSON type={type(data).__name__} "
                            f"(attempt {attempt}/{self.retry_attempts})"
                        )
                    except Exception as e:
                        last_err = f"json-decode:{type(e).__name__}"
                        logger.warning(
                            f"HL /info JSON decode failed {type(e).__name__} "
                            f"(attempt {attempt}/{self.retry_attempts})"
                        )

            except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                last_err = type(e).__name__
                logger.warning(
                    f"HL /info timeout {type(e).__name__} "
                    f"(attempt {attempt}/{self.retry_attempts})"
                )
            except (httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError) as e:
                last_err = type(e).__name__
                logger.warning(
                    f"HL /info network error {type(e).__name__} "
                    f"(attempt {attempt}/{self.retry_attempts})"
                )
            except Exception as e:
                # unknown errors: treat as transient, but still bounded by retries
                last_err = type(e).__name__
                logger.warning(
                    f"HL /info unexpected error {type(e).__name__} "
                    f"(attempt {attempt}/{self.retry_attempts})"
                )

            # backoff before next attempt
            sleep_s = self._sleep_seconds(attempt)
            await asyncio.sleep(sleep_s)

        logger.error(f"HL /info failed after retries. last_err={last_err}")
        return None
