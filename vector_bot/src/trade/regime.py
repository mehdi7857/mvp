from __future__ import annotations

from typing import Any, Dict, List

from src.trade.indicators import atr_wilder


def compute_atr_ratio_regime(candles_15m: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Volatility Regime via ATR17/ATR108 ratio (15m).
    Returns:
      regime in {COMPRESSION, EXPANSION, TRANSITION, RANGE}
      ratio, atr17, atr108, slope_hint

    Thresholds are conservative and can be tuned after forward test.
    """
    atr17 = atr_wilder(candles_15m, length=17)
    atr108 = atr_wilder(candles_15m, length=108)
    if atr108 <= 0:
        return {"regime": "TRANSITION", "ratio": None, "atr17": atr17, "atr108": atr108, "note": "bad_atr108"}

    ratio = atr17 / atr108

    # crude slope hint: compare current ratio to ratio computed on an older window
    # (keeps it simple; no extra indicators)
    slope_hint = None
    if len(candles_15m) >= 160:
        older = candles_15m[:-30]
        atr17_old = atr_wilder(older, length=17)
        atr108_old = atr_wilder(older, length=108)
        if atr108_old > 0:
            ratio_old = atr17_old / atr108_old
            slope_hint = ratio - ratio_old

    # conservative buckets
    # low ratio => compression
    if ratio < 0.85:
        regime = "COMPRESSION"
    # high ratio => expansion
    elif ratio > 1.15:
        regime = "EXPANSION"
    else:
        # mid-zone: decide transition vs range using slope hint
        if slope_hint is None:
            regime = "RANGE"
        else:
            regime = "TRANSITION" if abs(slope_hint) > 0.05 else "RANGE"

    return {
        "regime": regime,
        "ratio": float(ratio),
        "atr17": float(atr17),
        "atr108": float(atr108),
        "slope_hint": None if slope_hint is None else float(slope_hint),
    }
