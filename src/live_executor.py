from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any

from loguru import logger

from src.models import Snapshot


# --------------------------------------------------
# Plans / Intents
# --------------------------------------------------

@dataclass(frozen=True)
class OrderIntent:
    kind: str            # "OPEN" or "CLOSE"
    coin: str
    side: str            # "LONG_PERP" or "SHORT_PERP"
    notional_usd: float
    ts: int
    reason: str


@dataclass(frozen=True)
class OrderPlan:
    kind: str
    coin: str
    side: str
    notional_usd: float
    reduce_only: bool
    partial: bool
    retries: int
    note: str


# --------------------------------------------------
# Live Executor (PERP-ONLY)
# --------------------------------------------------

class LiveExecutor:
    """
    PERP-ONLY executor.
    safe_mode=True  => plan only (no real orders)
    safe_mode=False => real orders sent via client.place_perp_order(...)
    """

    def __init__(self, notional_usd: float, safe_mode: bool = True) -> None:
        # 3.2) HARD CAP (you requested)
        assert notional_usd <= 100.0, "Real-money test cap exceeded"

        self.notional_usd = float(notional_usd)
        self.safe_mode = bool(safe_mode)

        self.client = None

        logger.info(
            f"LiveExecutor initialized | PERP_ONLY | notional=${self.notional_usd:.2f} | safe_mode={self.safe_mode}"
        )

    # -------------------------
    # logging helpers
    # -------------------------

    def build_intent(
        self,
        snap: Snapshot,
        kind: str,
        side: str,
        reason: str,
    ) -> OrderIntent:
        return OrderIntent(
            kind=kind,
            coin=snap.coin,
            side=side,
            notional_usd=self.notional_usd,
            ts=snap.time,
            reason=reason,
        )

    def log_intent(self, intent: OrderIntent) -> None:
        logger.info(
            "ORDER_INTENT | kind=%s coin=%s side=%s notional=$%.2f ts=%s | reason=%s"
            % (intent.kind, intent.coin, intent.side, intent.notional_usd, intent.ts, intent.reason)
        )

    def build_plan(self, intent: OrderIntent) -> OrderPlan:
        if intent.kind == "OPEN":
            return OrderPlan(
                kind="OPEN",
                coin=intent.coin,
                side=intent.side,
                notional_usd=intent.notional_usd,
                reduce_only=False,
                partial=False,
                retries=0,
                note="PERP_ONLY OPEN",
            )

        # CLOSE
        return OrderPlan(
            kind="CLOSE",
            coin=intent.coin,
            side=intent.side,
            notional_usd=intent.notional_usd,
            reduce_only=True,
            partial=False,
            retries=0,
            note="PERP_ONLY CLOSE (reduce-only)",
        )

    def log_plan(self, plan: OrderPlan) -> None:
        logger.info(
            "ORDER_PLAN | kind=%s coin=%s side=%s notional=$%.2f | reduce_only=%s partial=%s retries=%d | %s"
            % (
                plan.kind,
                plan.coin,
                plan.side,
                plan.notional_usd,
                plan.reduce_only,
                plan.partial,
                plan.retries,
                plan.note,
            )
        )
        if self.safe_mode:
            logger.warning("SAFE_MODE=ON | No orders will be sent. Plan only.")

    # -------------------------
    # main API
    # -------------------------

    def preview(self, snap: Snapshot, kind: str, side: str, reason: str) -> OrderPlan:
        """
        Build + log intent and plan. Always safe.
        Returns plan for optional execution.
        """
        intent = self.build_intent(snap, kind, side, reason)
        self.log_intent(intent)
        plan = self.build_plan(intent)
        self.log_plan(plan)
        return plan

    def _ensure_client(self) -> None:
        if self.client is not None:
            return
        from src.hyperliquid_trade_client import HyperliquidTradeClient
        self.client = HyperliquidTradeClient()

    def ensure_client(self) -> None:
        self._ensure_client()

    # 3.4) EXECUTE PERP ORDER
    def execute(self, plan: OrderPlan) -> Optional[Any]:
        """
        Sends real orders only if safe_mode=False.
        Uses self.client.place_perp_order(...)

        plan.side:
          - LONG_PERP  => OPEN: BUY  / CLOSE: SELL
          - SHORT_PERP => OPEN: SELL / CLOSE: BUY
        """
        if self.safe_mode:
            logger.warning("SAFE_MODE=ON | execute() skipped.")
            return None

        self._ensure_client()
        if plan.kind == "OPEN":
            side = "BUY" if plan.side in ("LONG_PERP", "LONG_PERP_SHORT_SPOT") else "SELL"
            logger.warning(
                f"LIVE ORDER SENT | OPEN {plan.coin} {side} ${plan.notional_usd:.2f} reduce_only=False"
            )
            return self.client.place_perp_order(  # type: ignore[union-attr]
                coin=plan.coin,
                side=side,
                notional_usd=plan.notional_usd,
                reduce_only=False,
            )

        # CLOSE
        side = "SELL" if plan.side in ("LONG_PERP", "LONG_PERP_SHORT_SPOT") else "BUY"
        logger.warning(
            f"LIVE ORDER SENT | CLOSE {plan.coin} {side} ${plan.notional_usd:.2f} reduce_only=True"
        )
        return self.client.place_perp_order(  # type: ignore[union-attr]
            coin=plan.coin,
            side=side,
            notional_usd=plan.notional_usd,
            reduce_only=True,
        )
