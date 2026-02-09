from __future__ import annotations

import argparse
from typing import Any, Dict, Optional

from loguru import logger

from src.hyperliquid_trade_client import HyperliquidTradeClient


def _pick_position(positions, coin: Optional[str]) -> Optional[Dict[str, Any]]:
    if coin:
        for p in positions:
            if p.get("coin") == coin:
                return p
        return None
    return positions[0] if positions else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Force a reduce-only close on an existing perp position.")
    parser.add_argument("--coin", type=str, default=None, help="Coin symbol (e.g. BTC). If omitted, uses first position.")
    parser.add_argument("--slippage", type=float, default=0.01, help="Slippage for market close.")
    args = parser.parse_args()

    client = HyperliquidTradeClient()
    positions = client.get_positions()
    if not positions:
        logger.info("FORCE_CLOSE | no open positions on exchange")
        return 0

    pos = _pick_position(positions, args.coin)
    if not pos:
        logger.warning(f"FORCE_CLOSE | no position found for coin={args.coin!r}")
        return 1

    coin = str(pos.get("coin"))
    szi = float(pos.get("szi") or 0.0)
    if szi == 0.0:
        logger.warning(f"FORCE_CLOSE | position size is zero for coin={coin}")
        return 1

    sz = abs(szi)
    logger.warning(f"FORCE_CLOSE | sending reduce-only close | coin={coin} sz={sz}")

    resp = client.exchange.market_close(coin, sz, slippage=args.slippage)  # type: ignore
    logger.info(f"FORCE_CLOSE_RESP | coin={coin} raw={resp}")

    after = client.get_positions(coin=coin)
    logger.info(f"FORCE_CLOSE_AFTER | coin={coin} positions_len={len(after)}")
    for p in after:
        logger.info(f"FORCE_CLOSE_POS | coin={p.get('coin')} szi={p.get('szi')} entry_px={p.get('entry_px')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
