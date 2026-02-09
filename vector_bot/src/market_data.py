# src/market_data.py
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Set

import requests

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"

# Cache Hyperliquid meta (coin universe) locally to avoid repeated calls
META_CACHE_PATH_DEFAULT = os.path.join("logs", "hl_meta.json")
META_TTL_SECONDS_DEFAULT = 6 * 60 * 60  # 6 hours

# Interval -> milliseconds (must match Hyperliquid interval strings)
_INTERVAL_MS: Dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
}


def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def sanitize_coin(symbol: str) -> str:
    """
    Normalize symbol names for Hyperliquid coin field.
    - Strips leading underscores (common from some scanners)
      ____PEPE -> PEPE
      _SUI     -> SUI
    """
    s = (symbol or "").strip()
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


def _cache_is_fresh(path: str, ttl_s: int) -> bool:
    if not os.path.exists(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age <= ttl_s


def get_hyperliquid_coins(
    timeout_s: int = 12,
    cache_path: str = META_CACHE_PATH_DEFAULT,
    ttl_s: int = META_TTL_SECONDS_DEFAULT,
    force_refresh: bool = False,
) -> Set[str]:
    """
    Fetch Hyperliquid coin list via /info type=meta and cache it.
    Returns a set of coin strings (e.g., BTC, ETH, kPEPE, ...).
    """
    if (not force_refresh) and _cache_is_fresh(cache_path, ttl_s):
        try:
            cached = _load_json(cache_path)
            coins = cached.get("coins")
            if isinstance(coins, list) and all(isinstance(x, str) for x in coins):
                return set(coins)
        except Exception:
            pass  # fall through to refresh

    payload = {"type": "meta"}
    r = requests.post(HYPERLIQUID_INFO_URL, json=payload, timeout=timeout_s)
    r.raise_for_status()
    data = r.json()

    coins: List[str] = []
    if isinstance(data, dict) and isinstance(data.get("universe"), list):
        for item in data["universe"]:
            if isinstance(item, dict):
                nm = item.get("name")
                if isinstance(nm, str) and nm.strip():
                    coins.append(nm.strip())

    if not coins:
        raise RuntimeError("Hyperliquid meta returned no coins/universe list")

    _save_json(
        cache_path,
        {"ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "coins": coins},
    )
    return set(coins)


def resolve_coin_for_hyperliquid(
    symbol: str,
    coins: Optional[Set[str]] = None,
    timeout_s: int = 12,
) -> str:
    """
    Resolve a scanner symbol to a Hyperliquid 'coin' name.

    Rules:
      1) sanitize leading underscores
      2) if exists in meta -> use it
      3) else try "k" + coin (PEPE -> kPEPE, SHIB -> kSHIB, ...)
      4) else raise ValueError

    Returns the Hyperliquid coin string to use in candleSnapshot.
    """
    coin = sanitize_coin(symbol)
    if coins is None:
        coins = get_hyperliquid_coins(timeout_s=timeout_s)

    if coin in coins:
        return coin

    kcoin = f"k{coin}"
    if kcoin in coins:
        return kcoin

    raise ValueError(f"Unsupported coin: {coin}")


def get_ohlc(
    symbol: str,
    timeframe: str,
    limit: int = 200,
    timeout_s: int = 12,
    retries: int = 3,
    backoff_s: float = 0.5,
    validate_coin: bool = True,
) -> List[Dict[str, Any]]:
    """
    Fetch candles from Hyperliquid 'candleSnapshot' endpoint.

    Returns candles in ascending time order:
      [{"t": ms, "o": float, "h": float, "l": float, "c": float, "v": float|None}, ...]

    Features:
      - symbol sanitize (strip leading underscores)
      - meta coin validation (optional)
      - auto-maps to Hyperliquid "k" coins when needed (PEPE -> kPEPE)
      - retries with exponential backoff for transient failures
    """
    if timeframe not in _INTERVAL_MS:
        raise ValueError(f"Unsupported timeframe '{timeframe}'. Supported: {sorted(_INTERVAL_MS.keys())}")

    used_coin = sanitize_coin(symbol)

    coins: Optional[Set[str]] = None
    if validate_coin:
        coins = get_hyperliquid_coins(timeout_s=timeout_s)
        used_coin = resolve_coin_for_hyperliquid(symbol, coins=coins, timeout_s=timeout_s)

    now_ms = int(time.time() * 1000)
    interval_ms = _INTERVAL_MS[timeframe]

    # Request slightly more than needed to avoid boundary issues
    lookback_ms = int(limit * interval_ms * 1.2)
    start_ms = now_ms - lookback_ms
    end_ms = now_ms

    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": used_coin,
            "interval": timeframe,
            "startTime": start_ms,
            "endTime": end_ms,
        },
    }

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(HYPERLIQUID_INFO_URL, json=payload, timeout=timeout_s)
            r.raise_for_status()
            data = r.json()

            if not isinstance(data, list):
                raise RuntimeError(f"Unexpected candleSnapshot response type: {type(data)}")

            candles: List[Dict[str, Any]] = []
            for c in data:
                if not isinstance(c, dict):
                    continue
                t = c.get("t")
                o = _safe_float(c.get("o"))
                h = _safe_float(c.get("h"))
                l = _safe_float(c.get("l"))
                cl = _safe_float(c.get("c"))
                v = _safe_float(c.get("v"))

                if t is None or o is None or h is None or l is None or cl is None:
                    continue

                candles.append({"t": int(t), "o": o, "h": h, "l": l, "c": cl, "v": v})

            candles.sort(key=lambda x: x["t"])
            if limit and len(candles) > limit:
                candles = candles[-limit:]
            return candles

        except ValueError:
            # Unsupported coin is deterministic -> do not retry
            raise
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(backoff_s * (2 ** attempt))
            else:
                break

    raise RuntimeError(
        f"get_ohlc failed for symbol={symbol} used_coin={used_coin} tf={timeframe} after {retries+1} attempts"
    ) from last_err
