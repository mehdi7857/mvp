from __future__ import annotations

from typing import Any, Dict, Tuple

from src.market_data import get_ohlc
from src.htf_bias_engine import _compute_kijun_bias  # reuse your proven logic


def get_htf_bias(symbol: str, tf: str = "4h", limit: int = 220, kijun_len: int = 52) -> Tuple[str, Dict[str, Any]]:
    """
    Returns:
      bias: "BULL" | "BEAR" | "NEUTRAL"
      dbg:  dict (close/kijun_now/kijun_prev/slope/...)
    Uses your existing _compute_kijun_bias but with kijun_len=52 (official).
    """
    candles = get_ohlc(symbol, tf, limit=limit, validate_coin=True)
    b, dbg = _compute_kijun_bias(candles, kijun_len=kijun_len)  # bull/bear/neutral
    b = (b or "neutral").lower()

    if b == "bull":
        return "BULL", dbg
    if b == "bear":
        return "BEAR", dbg
    return "NEUTRAL", dbg
