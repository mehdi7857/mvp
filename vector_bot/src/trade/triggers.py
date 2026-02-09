from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional

def _last(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    return candles[-1]

def rejection_candle(c: Dict[str, Any], direction: str, wick_ratio: float = 0.55) -> bool:
    """
    wick_ratio: how much of candle range must be wick in the rejection direction.
    LONG rejection near support: long lower wick
    SHORT rejection near resistance: long upper wick
    """
    o = float(c["o"]); h = float(c["h"]); l = float(c["l"]); cl = float(c["c"])
    rng = max(h - l, 1e-9)
    upper_wick = h - max(o, cl)
    lower_wick = min(o, cl) - l

    if direction == "LONG":
        return (lower_wick / rng) >= wick_ratio
    else:
        return (upper_wick / rng) >= wick_ratio

def touched_level(price: float, level: float, tol: float) -> bool:
    return abs(price - level) <= tol

def range_high_low(candles: List[Dict[str, Any]], lookback: int = 64) -> Tuple[float, float]:
    """
    Simple rolling range over last N candles.
    """
    window = candles[-lookback:] if len(candles) >= lookback else candles
    hi = max(float(x["h"]) for x in window)
    lo = min(float(x["l"]) for x in window)
    return hi, lo

def breakout_trigger(candles: List[Dict[str, Any]], lookback: int = 64) -> Optional[str]:
    """
    Returns "UP" if close breaks above range high,
            "DOWN" if close breaks below range low,
            None otherwise.
    Uses range computed from previous candles (exclude last candle from range bounds).
    """
    if len(candles) < 3:
        return None
    last = candles[-1]
    prev = candles[:-1]
    hi, lo = range_high_low(prev, lookback=lookback)
    cl = float(last["c"])
    if cl > hi:
        return "UP"
    if cl < lo:
        return "DOWN"
    return None
