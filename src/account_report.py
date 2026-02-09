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


def _iter_records(raw: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                yield item


def _funding_amount(rec: Dict[str, Any]) -> Optional[float]:
    delta = rec.get("delta")
    if isinstance(delta, dict):
        for key in ("usdc", "usd", "funding", "fundingUsd", "fundingUSD"):
            v = _safe_float(delta.get(key))
            if v is not None:
                return v
    return None


def _funding_coin(rec: Dict[str, Any]) -> str:
    delta = rec.get("delta")
    if isinstance(delta, dict):
        return str(delta.get("coin") or "UNKNOWN")
    return str(rec.get("coin") or "UNKNOWN")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report funding + trade history PnL for a Hyperliquid account."
    )
    parser.add_argument("--hours", type=float, default=24.0, help="Lookback window in hours (default: 24).")
    args = parser.parse_args()

    if args.hours <= 0:
        raise SystemExit("--hours must be > 0")

    key = _load_key_from_dotvenv()
    if key:
        normalized = HyperliquidTradeClient._normalize_private_key(key)
        os.environ["HYPERLIQUID_PRIVATE_KEY"] = normalized

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(args.hours * 3600 * 1000)

    client = HyperliquidTradeClient()

    # Funding history
    funding_raw = client.info.user_funding_history(client.address, start_ms, end_ms)
    funding_total = 0.0
    funding_by_coin: Dict[str, float] = defaultdict(float)
    funding_count = 0
    for rec in _iter_records(funding_raw):
        amt = _funding_amount(rec)
        if amt is None:
            continue
        coin = _funding_coin(rec)
        funding_by_coin[coin] += amt
        funding_total += amt
        funding_count += 1

    # Trade fills (realized PnL + fees)
    fills_raw = client.info.user_fills_by_time(client.address, start_ms, end_ms, aggregate_by_time=False)
    realized_total = 0.0
    fees_total = 0.0
    realized_by_coin: Dict[str, float] = defaultdict(float)
    fees_by_coin: Dict[str, float] = defaultdict(float)
    fills_count = 0

    for rec in _iter_records(fills_raw):
        coin = str(rec.get("coin") or "UNKNOWN")
        closed_pnl = _safe_float(rec.get("closedPnl")) or 0.0
        fee = _safe_float(rec.get("fee")) or 0.0
        realized_by_coin[coin] += closed_pnl
        fees_by_coin[coin] += fee
        realized_total += closed_pnl
        fees_total += fee
        fills_count += 1

    net_total = funding_total + realized_total - fees_total

    logger.info(f"Account report | window={args.hours:.2f}h")
    logger.info(f"FUNDING_TOTAL_USD={funding_total:.6f} records={funding_count}")
    logger.info(f"REALIZED_PNL_USD={realized_total:.6f} fills={fills_count}")
    logger.info(f"FEES_USD={fees_total:.6f}")
    logger.info(f"NET_USD={net_total:.6f}")

    if funding_by_coin:
        for coin, amt in sorted(funding_by_coin.items(), key=lambda x: abs(x[1]), reverse=True):
            logger.info(f"FUNDING_BY_COIN | coin={coin} amount={amt:.6f}")

    if realized_by_coin or fees_by_coin:
        coins = sorted(set(realized_by_coin.keys()) | set(fees_by_coin.keys()))
        for coin in coins:
            logger.info(
                "TRADES_BY_COIN | "
                f"coin={coin} realized={realized_by_coin.get(coin, 0.0):.6f} "
                f"fees={fees_by_coin.get(coin, 0.0):.6f}"
            )


if __name__ == "__main__":
    main()
