# src/htf_bias_engine.py
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.market_data import get_ohlc


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def _kijun(highs: List[float], lows: List[float]) -> float:
    return (max(highs) + min(lows)) / 2.0


def _compute_kijun_bias(candles: List[Dict[str, Any]], kijun_len: int = 26) -> Tuple[str, Dict[str, Any]]:
    """
    Kijun(26) direction:
      bull  if close > kijun_now and kijun_now > kijun_prev
      bear  if close < kijun_now and kijun_now < kijun_prev
      else neutral
    """
    if len(candles) < kijun_len + 2:
        return "neutral", {"reason": "not_enough_candles", "have": len(candles), "need": kijun_len + 2}

    highs_now = [c["h"] for c in candles[-kijun_len:]]
    lows_now = [c["l"] for c in candles[-kijun_len:]]
    kijun_now = _kijun(highs_now, lows_now)

    highs_prev = [c["h"] for c in candles[-kijun_len - 1 : -1]]
    lows_prev = [c["l"] for c in candles[-kijun_len - 1 : -1]]
    kijun_prev = _kijun(highs_prev, lows_prev)

    close = float(candles[-1]["c"])

    if close > kijun_now and kijun_now > kijun_prev:
        bias = "bull"
    elif close < kijun_now and kijun_now < kijun_prev:
        bias = "bear"
    else:
        bias = "neutral"

    dbg = {
        "close": round(close, 6),
        "kijun_now": round(kijun_now, 6),
        "kijun_prev": round(kijun_prev, 6),
        "kijun_slope": round(kijun_now - kijun_prev, 6),
        "kijun_len": kijun_len,
        "candles_used": len(candles),
    }
    return bias, dbg


def build_bias_snapshot(
    universe_path: str,
    out_path: str | None = None,
    htf_tf: str = "4h",
    htf_limit: int = 200,
    kijun_len: int = 26,
    max_direction_symbols: int = 12,  # compute direction for top liquidity names only (keeps API light)
) -> str:
    """
    HTF Bias Engine v2:
      - Reads scan_universe_*.json
      - Computes market-level regime/trade_mode (liquidity+breadth)
      - Computes per-symbol liquidity priority (A/B/C)
      - Computes per-symbol HTF direction using Kijun(26) on 4h candles (bull/bear/neutral)
    """
    uni = _load_json(universe_path)
    universe: List[Dict[str, Any]] = uni.get("universe", [])

    mode = uni.get("mode", "normal")  # "normal" or "high"
    threshold_used = _safe_float(uni.get("threshold_used")) or 500.0

    universe_size = len(universe)

    # Breadth proxy: fraction of names in universe with >= 1000 BTC 24h volume
    vols: List[float] = []
    for r in universe:
        v = _safe_float(r.get("vol_btc"))
        if v is not None:
            vols.append(v)

    count_ge_1000 = sum(1 for v in vols if v >= 1000.0)
    breadth = (count_ge_1000 / universe_size) if universe_size > 0 else 0.0

    # Market trade mode (v1 logic kept)
    if universe_size < 12:
        market_trade_mode = "No-trade"
        market_regime = "transition"
    else:
        if (mode == "high") and (breadth >= 0.35):
            market_trade_mode = "Breakout"
            market_regime = "transition"
        else:
            market_trade_mode = "Reactional"
            market_regime = "range"

    # Sort by vol_btc descending for rank-based liquidity score
    def _vol_key(row: Dict[str, Any]) -> float:
        return _safe_float(row.get("vol_btc")) or 0.0

    sorted_universe = sorted(universe, key=_vol_key, reverse=True)

    rows: List[Dict[str, Any]] = []
    for idx, r in enumerate(sorted_universe):
        sym = r.get("symbol")
        vol_btc = _safe_float(r.get("vol_btc"))
        price = _safe_float(r.get("price"))

        if universe_size <= 1:
            liq_score = 1.0
        else:
            liq_score = 1.0 - (idx / (universe_size - 1))

        priority = "A" if liq_score >= 0.7 else ("B" if liq_score >= 0.4 else "C")

        # Direction only for top liquidity names (keeps runtime + API calls under control)
        if idx < max_direction_symbols and isinstance(sym, str) and sym:
            try:
                candles = get_ohlc(sym, htf_tf, limit=htf_limit)
                htf_bias, bias_dbg = _compute_kijun_bias(candles, kijun_len=kijun_len)
                bias_err = None
            except Exception as e:
                htf_bias = "neutral"
                bias_dbg = {"reason": "fetch_or_compute_failed"}
                bias_err = f"{type(e).__name__}: {e}"
        else:
            htf_bias = "neutral"
            bias_dbg = {"reason": "skipped_to_save_api", "idx": idx, "max": max_direction_symbols}
            bias_err = None

        rows.append(
            {
                "symbol": sym,
                "htf_bias": htf_bias,      # bull/bear/neutral
                "regime": market_regime,
                "trade_mode": market_trade_mode,
                "priority": priority,      # A/B/C liquidity priority
                "scores": {
                    "liquidity_score": round(liq_score, 4),
                },
                "htf": {
                    "timeframe": htf_tf,
                    "kijun_len": kijun_len,
                    "debug": bias_dbg,
                    "error": bias_err,
                },
                "inputs": {
                    "vol_btc_24h": vol_btc,
                    "price": price,
                    "threshold_used": threshold_used,
                },
            }
        )

    if out_path is None:
        base = os.path.basename(universe_path).replace("scan_universe_", "bias_snapshot_")
        out_path = os.path.join(os.path.dirname(universe_path), base)

    out = {
        "ts_utc": datetime.utcnow().isoformat() + "Z",
        "source_universe": universe_path,
        "universe_size": universe_size,
        "mode": mode,
        "threshold_used": threshold_used,
        "breadth_ge_1000": round(breadth, 4),
        "market": {"regime": market_regime, "trade_mode": market_trade_mode},
        "bias": rows,
        "note": "HTF Bias v2: adds Kijun(26) bull/bear/neutral on 4h candles for top liquidity symbols.",
    }

    _save_json(out_path, out)
    return out_path


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="HTF Bias Engine v2 (Kijun direction) -> bias_snapshot")
    p.add_argument("--in", dest="inp", required=True, help="Path to scan_universe_*.json")
    p.add_argument("--out", dest="out", default=None, help="Path to bias_snapshot_*.json")
    p.add_argument("--tf", dest="tf", default="4h", help="HTF timeframe (e.g., 4h, 8h)")
    p.add_argument("--limit", dest="limit", type=int, default=200, help="Candles limit")
    p.add_argument("--kijun", dest="kijun", type=int, default=26, help="Kijun length")
    p.add_argument("--maxdir", dest="maxdir", type=int, default=12, help="Max symbols to compute direction for")
    args = p.parse_args()

    outp = build_bias_snapshot(
        args.inp,
        out_path=args.out,
        htf_tf=args.tf,
        htf_limit=args.limit,
        kijun_len=args.kijun,
        max_direction_symbols=args.maxdir,
    )
    print(outp)
