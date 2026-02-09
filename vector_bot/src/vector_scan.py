import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv


# ------------------------
# Logging
# ------------------------
def setup_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("vector")
    if logger.handlers:
        return logger  # avoid duplicate handlers
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


# ------------------------
# Helpers
# ------------------------
def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------
# Livecoinwatch client
# ------------------------
LCW_BASE = "https://api.livecoinwatch.com"


def lcw_post(api_key: str, endpoint: str, body: Dict[str, Any], timeout: int = 25) -> Any:
    url = f"{LCW_BASE}{endpoint}"
    headers = {
        "content-type": "application/json",
        "x-api-key": api_key,
    }
    r = requests.post(url, headers=headers, json=body, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"LCW error {r.status_code}: {r.text[:500]}")
    return r.json()


def fetch_coins_list(api_key: str, currency: str, limit: int) -> List[Dict[str, Any]]:
    """
    Returns list of coin dicts. Example fields:
    - code, name, rank, rate (in currency), volume (24h in currency)
    """
    body = {
        "currency": currency,
        "sort": "volume",
        "order": "descending",
        "offset": 0,
        "limit": limit,
        "meta": True,
    }
    data = lcw_post(api_key, "/coins/list", body)
    if not isinstance(data, list):
        raise RuntimeError("Unexpected LCW response: expected a list")
    return data


# ------------------------
# Main scan
# ------------------------
def main() -> int:
    load_dotenv()

    api_key = os.getenv("LIVECOINWATCH_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("LIVECOINWATCH_API_KEY is empty in .env")

    currency = os.getenv("LCW_CURRENCY", "USD").strip().upper()
    max_symbols = int(os.getenv("MAX_SYMBOLS", "50"))
    min_volume_btc = float(os.getenv("MIN_VOLUME_BTC", "500"))
    log_level = os.getenv("VECTOR_LOG_LEVEL", "INFO").strip().upper()

    logger = setup_logger(log_level)
    logger.info("Vector scan starting | max_symbols=%s currency=%s min_volume_btc=%s",
                max_symbols, currency, min_volume_btc)

    ts = utc_stamp()
    logs_dir = Path("logs")

    # 1) Fetch coins
    coins = fetch_coins_list(api_key=api_key, currency=currency, limit=max(200, max_symbols))

    raw_path = logs_dir / f"scan_raw_{ts}.json"
    write_json(raw_path, coins)

    # 2) Find BTC rate in the same currency
    btc = next((c for c in coins if c.get("code") == "BTC"), None)
    if not btc:
        raise RuntimeError("BTC not found in LCW coins list; cannot convert volume to BTC")
    if not isinstance(btc.get("rate"), (int, float)):
        raise RuntimeError("BTC rate missing/invalid; cannot convert volume to BTC")

    btc_rate = float(btc["rate"])
    if btc_rate <= 0:
        raise RuntimeError("BTC rate is non-positive; cannot convert volume to BTC")

    # 3) Filter by BTC-volume (volume_usd / btc_rate_usd)
    filtered: List[Dict[str, Any]] = []
    for c in coins:
        code = c.get("code")
        name = c.get("name")
        rank = c.get("rank")
        rate = c.get("rate")
        vol_cur = c.get("volume")  # in 'currency' units

        if not isinstance(vol_cur, (int, float)):
            continue

        vol_btc = float(vol_cur) / btc_rate

        if vol_btc < min_volume_btc:
            continue

        filtered.append({
            "code": code,
            "name": name,
            "rank": rank,
            "rate_in_currency": rate,
            "volume_24h_in_currency": float(vol_cur),
            "volume_24h_in_btc": vol_btc,
        })

        if len(filtered) >= max_symbols:
            break

    out = {
        "ts_utc": ts,
        "currency": currency,
        "btc_rate_in_currency": btc_rate,
        "min_volume_btc": min_volume_btc,
        "count": len(filtered),
        "items": filtered,
        "raw_file": str(raw_path),
    }

    filtered_path = logs_dir / f"scan_filtered_{ts}.json"
    write_json(filtered_path, out)

    logger.info("Scan complete | symbols=%s raw=%s filtered=%s",
                len(filtered), str(raw_path), str(filtered_path))

    # quick console preview
    for it in filtered[:10]:
        logger.info("TOP | %s | vol_btc=%.2f | vol_cur=%.0f | rate=%.4f",
                    it.get("code"), it["volume_24h_in_btc"], it["volume_24h_in_currency"], float(it.get("rate_in_currency") or 0))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
