from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from loguru import logger

from src.hyperliquid_trade_client import HyperliquidTradeClient


def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def _extract_amount(rec: Dict[str, Any]) -> Optional[Tuple[str, float]]:
    delta = rec.get("delta")
    if isinstance(delta, dict):
        for key in ("usdc", "usd", "funding", "fundingUsd", "fundingUSD"):
            v = _safe_float(delta.get(key))
            if v is not None:
                return f"delta.{key}", v
    candidates = (
        "funding",
        "fundingUsd",
        "fundingUSD",
        "fundingAmount",
        "fundingPayment",
        "payment",
        "amount",
        "delta",
    )
    for key in candidates:
        if key in rec:
            v = _safe_float(rec.get(key))
            if v is not None:
                return key, v
    return None


def _iter_records(raw: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                yield item


def _load_key_from_dotvenv() -> Optional[str]:
    here = Path(__file__).resolve()
    root = here.parent.parent
    cfg = root / ".venv.json"
    if not cfg.exists():
        return None
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Failed reading .venv.json: {e!r}")
        return None
    for key_name in ("HYPERLIQUID_PRIVATE_KEY", "private_key", "PRIVATE_KEY"):
        v = str(data.get(key_name, "")).strip()
        if v:
            return v
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize Hyperliquid funding PnL over a time window.")
    parser.add_argument("--hours", type=float, default=24.0, help="Lookback window in hours (default: 24).")
    args = parser.parse_args()

    if args.hours <= 0:
        raise SystemExit("--hours must be > 0")

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(args.hours * 3600 * 1000)

    # Prefer .venv.json to avoid bad env var overriding the correct key.
    key = _load_key_from_dotvenv()
    if key:
        normalized = HyperliquidTradeClient._normalize_private_key(key)
        os.environ["HYPERLIQUID_PRIVATE_KEY"] = normalized
    client = HyperliquidTradeClient()
    raw = client.info.user_funding_history(client.address, start_ms, end_ms)
    if isinstance(raw, list):
        logger.info(f"RAW_RECORD_SAMPLE | count={len(raw)} sample={raw[:5]}")
    else:
        logger.info(f"RAW_RECORD_SAMPLE | type={type(raw).__name__} value={raw}")

    totals_by_coin: Dict[str, float] = defaultdict(float)
    total = 0.0
    count = 0
    amount_key: Optional[str] = None

    for rec in _iter_records(raw):
        delta = rec.get("delta") if isinstance(rec, dict) else None
        if isinstance(delta, dict):
            coin = str(delta.get("coin") or rec.get("coin") or "UNKNOWN")
        else:
            coin = str(rec.get("coin") or rec.get("asset") or "UNKNOWN")
        extracted = _extract_amount(rec)
        if extracted is None:
            continue
        amount_key = amount_key or extracted[0]
        amount = extracted[1]
        totals_by_coin[coin] += amount
        total += amount
        count += 1

    if count == 0:
        logger.warning("No funding records found in the requested window.")
        logger.info(f"Raw type={type(raw).__name__} keys={list(raw.keys()) if isinstance(raw, dict) else 'n/a'}")
        return

    hours = args.hours
    logger.info(f"Funding summary | window={hours:.2f}h records={count} amount_key={amount_key}")
    logger.info(f"TOTAL_FUNDING_USD={total:.6f}")
    for coin, amt in sorted(totals_by_coin.items(), key=lambda x: abs(x[1]), reverse=True):
        logger.info(f"COIN_FUNDING_USD | coin={coin} amount={amt:.6f}")


if __name__ == "__main__":
    main()
