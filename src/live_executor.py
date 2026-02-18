from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
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
# Live Executor (PERP + SPOT hedge)
# --------------------------------------------------

class LiveExecutor:
    """
    Hedge executor.
    safe_mode=True  => plan only (no real orders)
    safe_mode=False => real orders sent via client.place_perp_order(...) + place_spot_order(...)
    """

    def __init__(self, notional_usd: float, safe_mode: bool = True, spot_quote: str = "USDC") -> None:
        # 3.2) HARD CAP (you requested)
        assert notional_usd <= 100.0, "Real-money test cap exceeded"

        self.notional_usd = float(notional_usd)
        self.safe_mode = bool(safe_mode)
        self.spot_quote = str(spot_quote).upper().strip()

        self.client = None
        self.supports_spot_hedge = False

        logger.info(
            f"LiveExecutor initialized | HEDGE_MODE(perp+spot) | notional=${self.notional_usd:.2f} "
            f"| spot_quote={self.spot_quote} | safe_mode={self.safe_mode}"
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

    def spot_hedge_capability(self, coin: str) -> bool:
        self._ensure_client()
        supports = bool(getattr(self.client, "supports_spot", False)) and bool(  # type: ignore[union-attr]
            self.client.can_trade_spot_pair(coin, self.spot_quote)  # type: ignore[union-attr]
        )
        self.supports_spot_hedge = supports
        return supports

    # 3.4) EXECUTE HEDGED ORDER
    def execute(self, plan: OrderPlan) -> Optional[Any]:
        """
        Sends real orders only if safe_mode=False.
        Uses self.client.place_perp_order(...) and self.client.place_spot_order(...).

        plan.side:
          - LONG_PERP  => OPEN: BUY  / CLOSE: SELL
          - SHORT_PERP => OPEN: SELL / CLOSE: BUY
        """
        if self.safe_mode:
            logger.warning("SAFE_MODE=ON | execute() skipped.")
            return None

        self._ensure_client()
        if plan.kind == "OPEN":
            if plan.side in ("SHORT_PERP", "SHORT_PERP_LONG_SPOT"):
                # For funding>0 carry: open spot long first, then perp short.
                spot = self.client.place_spot_order(  # type: ignore[union-attr]
                    base_coin=plan.coin,
                    quote_coin=self.spot_quote,
                    side="BUY",
                    notional_usd=plan.notional_usd,
                )
                if not getattr(spot, "ok", False):
                    return SimpleNamespace(ok=False, verified=False, verify_reason="spot_open_failed", raw=getattr(spot, "raw", {}))

                logger.warning(
                    f"LIVE ORDER SENT | OPEN {plan.coin} SELL ${plan.notional_usd:.2f} reduce_only=False"
                )
                perp = self.client.place_perp_order(  # type: ignore[union-attr]
                    coin=plan.coin,
                    side="SELL",
                    notional_usd=plan.notional_usd,
                    reduce_only=False,
                )
                if not getattr(perp, "ok", False) or not getattr(perp, "verified", False):
                    logger.error("HEDGE_ROLLBACK | perp open failed after spot open, trying spot unwind")
                    try:
                        self.client.place_spot_order(  # type: ignore[union-attr]
                            base_coin=plan.coin,
                            quote_coin=self.spot_quote,
                            side="SELL",
                            notional_usd=plan.notional_usd,
                            use_available_base_size_for_sell=True,
                        )
                    except Exception as rollback_err:
                        logger.error(f"HEDGE_ROLLBACK_FAIL | spot unwind failed | err={rollback_err!r}")
                    return perp
                return perp

            # funding<0 carry (long perp + short spot) needs borrow/margin short, not supported in v1.
            return SimpleNamespace(
                ok=False,
                verified=False,
                verify_reason="unsupported_long_perp_short_spot_in_v1",
                raw={},
            )

        # CLOSE
        if plan.side in ("SHORT_PERP", "SHORT_PERP_LONG_SPOT"):
            logger.warning(
                f"LIVE ORDER SENT | CLOSE {plan.coin} BUY ${plan.notional_usd:.2f} reduce_only=True"
            )
            perp = self.client.place_perp_order(  # type: ignore[union-attr]
                coin=plan.coin,
                side="BUY",
                notional_usd=plan.notional_usd,
                reduce_only=True,
            )
            if not getattr(perp, "ok", False) or not getattr(perp, "verified", False):
                return perp
            spot = self.client.place_spot_order(  # type: ignore[union-attr]
                base_coin=plan.coin,
                quote_coin=self.spot_quote,
                side="SELL",
                notional_usd=plan.notional_usd,
                use_available_base_size_for_sell=True,
            )
            if not getattr(spot, "ok", False):
                logger.error("HEDGE_CLOSE_WARN | perp closed but spot close failed; manual spot check required")
                return SimpleNamespace(ok=False, verified=False, verify_reason="spot_close_failed_after_perp_close", raw=getattr(spot, "raw", {}))
            return perp

        # Legacy long-perp close path: perp-only close.
        logger.warning(
            f"LIVE ORDER SENT | CLOSE {plan.coin} SELL ${plan.notional_usd:.2f} reduce_only=True"
        )
        return self.client.place_perp_order(  # type: ignore[union-attr]
            coin=plan.coin,
            side="SELL",
            notional_usd=plan.notional_usd,
            reduce_only=True,
        )
