from __future__ import annotations

from typing import Any, Dict, Optional
import requests

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"

def _post(payload: Dict[str, Any], timeout_s: int = 12) -> Any:
    r = requests.post(HYPERLIQUID_INFO_URL, json=payload, timeout=timeout_s)
    r.raise_for_status()
    return r.json()

def fetch_meta_and_ctx(timeout_s: int = 12) -> Any:
    return _post({"type": "metaAndAssetCtxs"}, timeout_s=timeout_s)

def extract_derivatives_for_coin(meta_and_ctx: Any, coin: str) -> Dict[str, Optional[float]]:
    """
    Maps Hyperliquid metaAndAssetCtxs -> our derivatives bundle.

    HL provides (per coin ctx): funding, openInterest, premium, oraclePx, markPx, day volumes.
    HL does NOT provide long/short ratio or taker buy/sell in this object.

    Returns keys:
      open_interest (required)
      funding       (required)
      basis         (required)  -> uses ctx['premium'] if present else (mark-oracle)/oracle
      long_short_ratio (optional, None on HL ctx)
      taker_buy_sell   (optional, None on HL ctx)
      oi_over_mcap     (optional, None on HL ctx)
    """
    out: Dict[str, Optional[float]] = {
        "open_interest": None,
        "long_short_ratio": None,
        "taker_buy_sell": None,
        "basis": None,
        "funding": None,
        "oi_over_mcap": None,
    }

    if not isinstance(meta_and_ctx, list) or len(meta_and_ctx) < 2:
        return out

    meta = meta_and_ctx[0]
    ctxs = meta_and_ctx[1]

    if not isinstance(meta, dict) or not isinstance(ctxs, list):
        return out

    universe = meta.get("universe")
    if not isinstance(universe, list):
        return out

    idx = None
    for i, u in enumerate(universe):
        if isinstance(u, dict) and u.get("name") == coin:
            idx = i
            break
    if idx is None or idx >= len(ctxs):
        return out

    ctx = ctxs[idx]
    if not isinstance(ctx, dict):
        return out

    # funding
    if "funding" in ctx:
        try:
            out["funding"] = float(ctx["funding"])
        except Exception:
            pass

    # open interest
    if "openInterest" in ctx:
        try:
            out["open_interest"] = float(ctx["openInterest"])
        except Exception:
            pass

    # basis / premium
    # HL provides "premium" directly; keep it as basis (dimensionless)
    if "premium" in ctx:
        try:
            out["basis"] = float(ctx["premium"])
        except Exception:
            pass
    else:
        # fallback compute from mark/oracle
        mark = None
        oracle = None
        if "markPx" in ctx:
            try: mark = float(ctx["markPx"])
            except Exception: pass
        if "oraclePx" in ctx:
            try: oracle = float(ctx["oraclePx"])
            except Exception: pass
        if (mark is not None) and (oracle is not None) and oracle != 0:
            out["basis"] = (mark - oracle) / oracle

    return out
