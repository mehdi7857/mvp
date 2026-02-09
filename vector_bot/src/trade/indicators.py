# src/trade/indicators.py
from __future__ import annotations
from typing import Any, Dict, List, Tuple

def kijun(candles: List[Dict[str, Any]], length: int) -> float:
    if len(candles) < length:
        raise ValueError("not enough candles for kijun")
    highs = [float(c["h"]) for c in candles[-length:]]
    lows  = [float(c["l"]) for c in candles[-length:]]
    return (max(highs) + min(lows)) / 2.0

def atr(candles: List[Dict[str, Any]], length: int = 14) -> float:
    if len(candles) < length + 1:
        raise ValueError("not enough candles for ATR")
    trs: List[float] = []
    for i in range(1, len(candles)):
        h = float(candles[i]["h"])
        l = float(candles[i]["l"])
        pc = float(candles[i-1]["c"])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    # simple moving average ATR
    window = trs[-length:]
    return sum(window) / float(length)

def dmi(candles: List[Dict[str, Any]], length: int = 20) -> Tuple[float, float]:
    """
    Returns (DI_plus, DI_minus) using Wilder smoothing approximation:
    - We compute +DM/-DM and TR, then smooth by simple sum over length (good enough for gating).
    """
    if len(candles) < length + 1:
        raise ValueError("not enough candles for DMI")

    plus_dm: List[float] = []
    minus_dm: List[float] = []
    trs: List[float] = []

    for i in range(1, len(candles)):
        h = float(candles[i]["h"])
        l = float(candles[i]["l"])
        ph = float(candles[i-1]["h"])
        pl = float(candles[i-1]["l"])
        pc = float(candles[i-1]["c"])

        up = h - ph
        dn = pl - l

        pdm = up if (up > dn and up > 0) else 0.0
        mdm = dn if (dn > up and dn > 0) else 0.0

        tr = max(h - l, abs(h - pc), abs(l - pc))

        plus_dm.append(pdm)
        minus_dm.append(mdm)
        trs.append(tr)

    pdm_sum = sum(plus_dm[-length:])
    mdm_sum = sum(minus_dm[-length:])
    tr_sum  = sum(trs[-length:])

    if tr_sum <= 0:
        return 0.0, 0.0

    di_plus = 100.0 * (pdm_sum / tr_sum)
    di_minus = 100.0 * (mdm_sum / tr_sum)
    return di_plus, di_minus
