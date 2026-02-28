import json
from datetime import datetime, timezone

from src.hyperliquid_trade_client import HyperliquidTradeClient


def j(x):
    return json.dumps(x, default=str)


def main():
    c = HyperliquidTradeClient()
    coin = "BTC"
    quote = "USDC"

    print("SNAPSHOT_START", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    pos = c.get_positions(coin=coin)
    bal = c.get_spot_balances()
    print("BEFORE_POS", j(pos))
    print("BEFORE_BAL", j({"UBTC": bal.get("UBTC", 0.0), "BTC": bal.get("BTC", 0.0), "WBTC": bal.get("WBTC", 0.0), "USDC": bal.get("USDC", 0.0)}))

    perp_result = None
    if pos:
        szi = float(pos[0].get("szi") or 0.0)
        if szi != 0.0:
            side = "BUY" if szi < 0 else "SELL"
            perp_result = c.place_perp_order(coin=coin, side=side, notional_usd=12.0, reduce_only=True)
    print("CLOSE_PERP_RESULT", j(None if perp_result is None else {
        "ok": perp_result.ok,
        "verified": perp_result.verified,
        "reason": perp_result.verify_reason,
        "cloid": perp_result.cloid,
        "raw": perp_result.raw,
    }))

    pos_after_perp = c.get_positions(coin=coin)
    print("AFTER_PERP_POS", j(pos_after_perp))
    if pos_after_perp:
        raise RuntimeError("Perp is not flat after close; aborting spot sell")

    spot_result = c.place_spot_order(
        base_coin=coin,
        quote_coin=quote,
        side="SELL",
        notional_usd=12.0,
        use_available_base_size_for_sell=True,
    )
    print("SELL_SPOT_RESULT", j({
        "ok": spot_result.ok,
        "verified": spot_result.verified,
        "reason": spot_result.verify_reason,
        "pair": spot_result.pair,
        "size": spot_result.size,
        "cloid": spot_result.cloid,
        "raw": spot_result.raw,
    }))

    pos2 = c.get_positions(coin=coin)
    bal2 = c.get_spot_balances()
    ubtc = float(bal2.get("UBTC", 0.0) or 0.0)
    btc = float(bal2.get("BTC", 0.0) or 0.0)
    wbtc = float(bal2.get("WBTC", 0.0) or 0.0)
    spot_base = ubtc + btc + wbtc
    perp_szi = float(pos2[0].get("szi") or 0.0) if pos2 else 0.0
    print("AFTER_POS", j(pos2))
    print("AFTER_BAL", j({"UBTC": ubtc, "BTC": btc, "WBTC": wbtc, "USDC": bal2.get("USDC", 0.0)}))
    print("DELTA", j({"perp_szi": perp_szi, "spot_base": spot_base, "net_delta_base": perp_szi + spot_base}))
    print("SNAPSHOT_END", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))


if __name__ == "__main__":
    main()
