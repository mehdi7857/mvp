from __future__ import annotations

from typing import Optional

from loguru import logger

from .hyperliquid_client import HyperliquidClient
from .models import Snapshot


async def fetch_snapshot(client: HyperliquidClient, coin: str) -> Optional[Snapshot]:
    """
    Fetch single-coin funding & premium snapshot.
    Returns Snapshot or None if unavailable / malformed.
    """
    payload = {"type": "metaAndAssetCtxs"}

    data = await client.post_info(payload)
    if data is None:
        return None

    try:
        # Hyperliquid returns something like: [meta, assetCtxs]
        # meta has universe list with coins
        meta = data[0]
        ctxs = data[1]

        universe = meta["universe"]
        idx = None
        for i, u in enumerate(universe):
            if u.get("name") == coin:
                idx = i
                break

        if idx is None:
            logger.warning(f"Coin not found in universe: {coin}")
            return None

        ctx = ctxs[idx]
        funding = float(ctx.get("funding") or ctx.get("fundingRate") or 0.0)
        premium = float(ctx.get("premium") or ctx.get("markPxPremium") or 0.0)

        # time: some endpoints include time; if missing, use 0
        t = int(ctx.get("time") or 0)

        # Important: allow zeros, but ensure keys exist
        return Snapshot(coin=coin, fundingRate=funding, premium=premium, time=t)

    except Exception as e:
        logger.warning(f"Malformed HL response for {coin}: {type(e).__name__}: {e}")
        return None
