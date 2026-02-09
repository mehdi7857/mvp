from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional
import math

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

from src.hl_keys import get_hl_private_key
from src.market_data import resolve_coin_for_hyperliquid
from src.trade.config_live import LiveConfig
from src.trade.derivatives_hl import fetch_meta_and_ctx


def _round_size(sz: float, decimals: int) -> float:
    p = 10 ** decimals
    return int(sz * p) / p


class HyperliquidBroker:
    def __init__(self):
        pk, src = get_hl_private_key()
        print(f"HL_BROKER_KEY_SOURCE={src}")
        self.wallet = Account.from_key(pk)
        self.info = Info("https://api.hyperliquid.xyz")
        self.info.timeout = 10
        self.ex = Exchange(self.wallet, base_url="https://api.hyperliquid.xyz")

    def _meta_ctx_for_coin(self, coin: str) -> tuple[int, Dict[str, Any]]:
        meta_and_ctx = fetch_meta_and_ctx()
        meta = meta_and_ctx[0]
        ctxs = meta_and_ctx[1]
        universe = meta.get("universe", [])

        for i, u in enumerate(universe):
            if isinstance(u, dict) and u.get("name") == coin:
                sz_dec = int(u.get("szDecimals", 2))
                ctx = ctxs[i]
                return sz_dec, ctx

        raise RuntimeError(f"Coin not found in universe: {coin}")

    def execute_test_trade(
        self,
        cfg: LiveConfig,
        side: str = "BUY",
        notional_usd: float = 5.0,
        flatten: bool = True,
    ) -> Dict[str, Any]:
        """
        Places a tiny IOC order, and (optionally) immediately flattens with reduce-only IOC.
        """
        coin = resolve_coin_for_hyperliquid(cfg.symbol)
        sz_dec, ctx = self._meta_ctx_for_coin(coin)

        mid = float(ctx.get("midPx") or ctx.get("markPx"))
        if mid <= 0:
            raise RuntimeError("Bad midPx/markPx from ctx")

        min_notional = 10.0
        target_notional = max(float(notional_usd), min_notional)
        sz = target_notional / mid
        # round up to ensure notional >= minimum after rounding
        p = 10 ** sz_dec
        sz = math.ceil(sz * p) / p
        if sz <= 0:
            raise RuntimeError("Computed size <= 0")

        is_buy = (side.upper() == "BUY")
        # marketable limit for IOC (rounded to exchange wire format)
        if hasattr(self.ex, "_slippage_price"):
            px_open = self.ex._slippage_price(coin, is_buy, slippage=0.01, px=mid)  # type: ignore[attr-defined]
        else:
            px_open = mid * (1.001 if is_buy else 0.999)

        resp_open = self.ex.order(
            coin,
            is_buy,
            sz,
            px_open,
            {"limit": {"tif": "Ioc"}},
            reduce_only=False,
        )

        resp_close: Optional[Any] = None
        if flatten:
            if hasattr(self.ex, "_slippage_price"):
                px_close = self.ex._slippage_price(coin, (not is_buy), slippage=0.01, px=mid)  # type: ignore[attr-defined]
            else:
                px_close = mid * (0.999 if is_buy else 1.001)
            resp_close = self.ex.order(
                coin,
                (not is_buy),
                sz,
                px_close,
                {"limit": {"tif": "Ioc"}},
                reduce_only=True,
            )

        return {
            "symbol": cfg.symbol,
            "coin": coin,
            "side": side.upper(),
            "notional_usd": float(notional_usd),
            "mid_price": mid,
            "size": sz,
            "open": resp_open,
            "close": resp_close,
        }

    def place_entry_ioc(self, coin: str, side: str, size: float, mid: float) -> dict:
        is_buy = (side.upper() == "BUY")
        px = mid * (1.001 if is_buy else 0.999)
        return self.ex.order(
            coin,
            is_buy,
            size,
            px,
            {"limit": {"tif": "Ioc"}},
            reduce_only=False,
        )

    def close_reduce_only_ioc(self, coin: str, side: str, size: float, mid: float) -> dict:
        # side here is the CLOSE side: BUY to close short, SELL to close long
        is_buy = (side.upper() == "BUY")
        px = mid * (1.001 if is_buy else 0.999)
        return self.ex.order(
            coin,
            is_buy,
            size,
            px,
            {"limit": {"tif": "Ioc"}},
            reduce_only=True,
        )
