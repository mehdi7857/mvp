# src/universe_builder.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any, Dict, List, Tuple

# Stable set (extend as needed)
STABLES_DEFAULT = {
    "USDT", "USDC", "FDUSD", "TUSD", "DAI", "USDP", "BUSD", "FRAX", "LUSD",
    "PYUSD", "USDD", "EURC", "XAUT",
    "USD1",  # important for your data
}

STATE_PATH_DEFAULT = os.path.join("logs", "universe_state.json")


@dataclass
class UniverseConfig:
    n: int = 30
    floor_normal: float = 500.0
    floor_high: float = 1000.0
    k_baseline: int = 30
    up_ratio: float = 1.5
    down_ratio: float = 1.2
    confirm_scans: int = 2


def _sanitize_symbol(sym: str) -> str:
    """Strip leading underscores (e.g., ____PEPE -> PEPE, _SUI -> SUI)."""
    s = (sym or "").strip()
    while s.startswith("_"):
        s = s[1:]
    return s


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _extract_rows(scan_filtered: Any) -> List[Dict[str, Any]]:
    """
    Supports common shapes:
      - list[dict]
      - {"items":[...]} / {"symbols":[...]} / {"rows":[...]} / {"data":[...]} / {"result":[...]}
    """
    if isinstance(scan_filtered, list):
        return scan_filtered
    if isinstance(scan_filtered, dict):
        for k in ("items", "symbols", "rows", "data", "result"):
            if k in scan_filtered and isinstance(scan_filtered[k], list):
                return scan_filtered[k]
    raise ValueError("Unrecognized scan_filtered JSON structure")


def _get_symbol(row: Dict[str, Any]) -> str:
    # Your schema includes 'symbol'
    for k in ("symbol", "asset", "base", "code", "ticker"):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    raise ValueError(f"Row missing symbol field: keys={list(row.keys())}")


def _get_vol_btc(row: Dict[str, Any]) -> float:
    """
    Your scan uses 'volume_24h_in_btc'.
    Keep backward compatibility with older names too.
    """
    candidates = (
        "volume_24h_in_btc",
        "volume24h_in_btc",
        "volume_24h_btc",
        "vol_btc",
        "volume_btc",
        "volBTC",
        "volumeBTC",
    )
    for k in candidates:
        if k in row:
            try:
                return float(row[k])
            except Exception:
                pass
    raise ValueError(f"Row missing BTC volume field: keys={list(row.keys())}")


def _get_price(row: Dict[str, Any]) -> float | None:
    """
    Your scan uses 'rate_in_currency' (USD).
    """
    candidates = (
        "rate_in_currency",
        "rate",
        "price",
        "last",
        "last_price",
    )
    for k in candidates:
        if k in row:
            try:
                return float(row[k])
            except Exception:
                return None
    return None


def _compute_activity(nonstable: List[Dict[str, Any]]) -> float:
    vols = sorted((_get_vol_btc(r) for r in nonstable), reverse=True)
    return float(sum(vols[:10]))


def _load_state(path: str) -> Dict[str, Any]:
    if os.path.exists(path):
        try:
            return _load_json(path)
        except Exception:
            return {}
    return {}


def _init_state() -> Dict[str, Any]:
    return {
        "activities": [],
        "mode": "normal",   # "normal" or "high"
        "pending": None,    # {"target":"high"/"normal","count":int}
        "last_updated": None,
    }


def _update_mode_with_hysteresis(
    state: Dict[str, Any],
    activity: float,
    baseline: float,
    cfg: UniverseConfig
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    mode = state.get("mode", "normal")
    pending = state.get("pending")

    want_high = (baseline > 0) and (activity >= cfg.up_ratio * baseline)
    want_normal = (baseline > 0) and (activity <= cfg.down_ratio * baseline)

    target = None
    if mode == "normal" and want_high:
        target = "high"
    elif mode == "high" and want_normal:
        target = "normal"

    if target is None:
        state["pending"] = None
        return mode, state, {"want_high": want_high, "want_normal": want_normal, "switch": False}

    if pending and pending.get("target") == target:
        pending["count"] = int(pending.get("count", 0)) + 1
    else:
        pending = {"target": target, "count": 1}

    state["pending"] = pending

    if pending["count"] >= cfg.confirm_scans:
        mode = target
        state["mode"] = mode
        state["pending"] = None
        return mode, state, {"want_high": want_high, "want_normal": want_normal, "switch": True, "switched_to": mode}

    return mode, state, {"want_high": want_high, "want_normal": want_normal, "switch": False, "pending": pending}


def build_universe(
    scan_filtered_path: str,
    out_path: str | None = None,
    state_path: str = STATE_PATH_DEFAULT,
    stables: set[str] = STABLES_DEFAULT,
    cfg: UniverseConfig = UniverseConfig()
) -> str:
    scan = _load_json(scan_filtered_path)
    rows = _extract_rows(scan)

    # Normalize symbols BEFORE stable filtering, overwrite in-row symbol for downstream cleanliness.
    nonstable: List[Dict[str, Any]] = []
    for r in rows:
        raw_sym = _get_symbol(r)
        sym = _sanitize_symbol(raw_sym)
        r["symbol"] = sym  # normalize for downstream
        if sym in stables:
            continue
        nonstable.append(r)

    # Activity & baseline (uses non-stables)
    state = _load_state(state_path) or _init_state()
    activities: List[float] = list(state.get("activities") or [])

    activity = _compute_activity(nonstable)
    history = activities[-cfg.k_baseline:] if activities else []
    baseline = float(median(history)) if len(history) >= 5 else float(activity)

    mode, state, debug = _update_mode_with_hysteresis(state, activity, baseline, cfg)
    threshold = cfg.floor_high if mode == "high" else cfg.floor_normal

    # Apply threshold + Top-N by BTC volume
    eligible = [r for r in nonstable if _get_vol_btc(r) >= threshold]
    eligible.sort(key=lambda r: _get_vol_btc(r), reverse=True)
    top = eligible[:cfg.n]

    # Update state AFTER decision
    activities.append(float(activity))
    state["activities"] = activities[-max(cfg.k_baseline * 3, 100):]
    state["last_updated"] = datetime.utcnow().isoformat() + "Z"
    _save_json(state_path, state)

    if out_path is None:
        base = os.path.basename(scan_filtered_path).replace("scan_filtered_", "scan_universe_")
        out_path = os.path.join(os.path.dirname(scan_filtered_path), base)

    output = {
        "ts_utc": datetime.utcnow().isoformat() + "Z",
        "source_filtered": scan_filtered_path,
        "config": {
            "n": cfg.n,
            "floor_normal": cfg.floor_normal,
            "floor_high": cfg.floor_high,
            "k_baseline": cfg.k_baseline,
            "up_ratio": cfg.up_ratio,
            "down_ratio": cfg.down_ratio,
            "confirm_scans": cfg.confirm_scans,
            "excluded_stables": sorted(list(stables)),
            "symbol_sanitizer": "strip_leading_underscores",
            "vol_field_preference": "volume_24h_in_btc",
            "price_field_preference": "rate_in_currency",
        },
        "activity": activity,
        "baseline": baseline,
        "mode": mode,
        "threshold_used": threshold,
        "debug": debug,
        "universe": [
            {
                "symbol": r["symbol"],
                "vol_btc": _get_vol_btc(r),
                "price": _get_price(r),
            }
            for r in top
        ],
        "counts": {
            "rows_in_filtered": len(rows),
            "nonstable_rows": len(nonstable),
            "eligible_after_threshold": len(eligible),
            "universe_size": len(top),
        }
    }

    _save_json(out_path, output)
    return out_path


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Build scan_universe from scan_filtered with two-tier liquidity.")
    p.add_argument("--in", dest="inp", required=True, help="Path to scan_filtered_*.json")
    p.add_argument("--out", dest="out", default=None, help="Path to write scan_universe_*.json")
    p.add_argument("--state", dest="state", default=STATE_PATH_DEFAULT, help="State file path for activity baseline")
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--floor", type=float, default=500.0)
    p.add_argument("--floor_high", type=float, default=1000.0)
    args = p.parse_args()

    cfg = UniverseConfig(n=args.n, floor_normal=args.floor, floor_high=args.floor_high)
    outp = build_universe(args.inp, out_path=args.out, state_path=args.state, cfg=cfg)
    print(outp)
