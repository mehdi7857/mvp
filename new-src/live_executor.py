from __future__ import annotations

from dataclasses import dataclass
from typing import Set, Tuple

from loguru import logger

from .models import Snapshot


@dataclass
class OrderIntent:
    coin: str
    side: str
    notional_usd: float
    kind: str   # "OPEN" | "CLOSE"
    reason: str
    ts_ms: int


class LiveExecutorSkeleton:
    """
    No-exchange execution. Generates order intent only (logs), with de-dup to avoid spam.
    """
    def __init__(self, notional_usd: float = 1000.0) -> None:
        self.notional_usd = float(notional_usd)
        self._seen: Set[Tuple[str, str, str, int]] = set()  # (kind, coin, side, ts_ms)

    def _key(self, intent: OrderIntent) -> Tuple[str, str, str, int]:
        return (intent.kind, intent.coin, intent.side, intent.ts_ms)

    def build_open_intent(self, snap: Snapshot, side: str, reason: str) -> OrderIntent:
        return OrderIntent(
            coin=snap.coin,
            side=side,
            notional_usd=self.notional_usd,
            kind="OPEN",
            reason=reason,
            ts_ms=snap.time,
        )

    def build_close_intent(self, snap: Snapshot, side: str, reason: str) -> OrderIntent:
        return OrderIntent(
            coin=snap.coin,
            side=side,
            notional_usd=self.notional_usd,
            kind="CLOSE",
            reason=reason,
            ts_ms=snap.time,
        )

    def log_intent(self, intent: OrderIntent) -> None:
        k = self._key(intent)
        if k in self._seen:
            return
        self._seen.add(k)

        logger.info(
            f"ORDER_INTENT | kind={intent.kind} coin={intent.coin} side={intent.side} "
            f"notional=${intent.notional_usd:.2f} ts={intent.ts_ms} | reason={intent.reason}"
        )
