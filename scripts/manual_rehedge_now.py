from __future__ import annotations

import argparse

from src.hyperliquid_trade_client import HyperliquidTradeClient


def main() -> int:
    p = argparse.ArgumentParser(description="Close current perp, then open spot-long + perp-short hedge.")
    p.add_argument("--coin", default="BTC")
    p.add_argument("--quote", default="USDC")
    p.add_argument("--notional", type=float, default=12.0)
    args = p.parse_args()

    coin = args.coin.upper().strip()
    quote = args.quote.upper().strip()
    notional = float(args.notional)

    c = HyperliquidTradeClient()

    print("STEP 0 | before")
    pos = c.get_positions(coin=coin)
    print("positions_before", pos)
    bs = c.get_spot_balances()
    print(
        "spot_before",
        {
            "BTC": bs.get("BTC", 0.0),
            "UBTC": bs.get("UBTC", 0.0),
            "WBTC": bs.get("WBTC", 0.0),
            "USDC": bs.get("USDC", 0.0),
        },
    )

    if pos:
        szi = float(pos[0].get("szi") or 0.0)
        if szi != 0.0:
            side = "SELL" if szi > 0 else "BUY"
            r_close = c.place_perp_order(coin=coin, side=side, notional_usd=notional, reduce_only=True)
            print("close_perp", {"ok": r_close.ok, "verified": r_close.verified, "reason": r_close.verify_reason})

    pos2 = c.get_positions(coin=coin)
    print("positions_after_close", pos2)
    if pos2:
        raise RuntimeError("Perp not flat after close; aborting re-open")

    r_spot = c.place_spot_order(base_coin=coin, quote_coin=quote, side="BUY", notional_usd=notional)
    print(
        "open_spot",
        {"ok": r_spot.ok, "verified": r_spot.verified, "reason": r_spot.verify_reason, "pair": r_spot.pair, "size": r_spot.size},
    )
    if not (r_spot.ok and r_spot.verified):
        raise RuntimeError(f"Spot open failed: {r_spot.verify_reason}")

    r_perp = c.place_perp_order(coin=coin, side="SELL", notional_usd=notional, reduce_only=False)
    print(
        "open_perp_short",
        {"ok": r_perp.ok, "verified": r_perp.verified, "reason": r_perp.verify_reason, "size": r_perp.size},
    )
    if not (r_perp.ok and r_perp.verified):
        try:
            rb = c.place_spot_order(
                base_coin=coin,
                quote_coin=quote,
                side="SELL",
                notional_usd=notional,
                use_available_base_size_for_sell=True,
            )
            print("rollback_spot", {"ok": rb.ok, "verified": rb.verified, "reason": rb.verify_reason})
        except Exception as e:  # noqa: BLE001
            print("rollback_spot_err", repr(e))
        raise RuntimeError(f"Perp short open failed: {r_perp.verify_reason}")

    pos3 = c.get_positions(coin=coin)
    bs3 = c.get_spot_balances()
    perp_szi = float(pos3[0].get("szi") or 0.0) if pos3 else 0.0
    spot_btc = float(bs3.get("BTC", 0.0) or 0.0)
    spot_ubtc = float(bs3.get("UBTC", 0.0) or 0.0)
    spot_wbtc = float(bs3.get("WBTC", 0.0) or 0.0)
    spot_base = spot_btc + spot_ubtc + spot_wbtc
    net_delta_base = spot_base + perp_szi

    print("STEP 4 | after")
    print("positions_after", pos3)
    print("spot_after", {"BTC": spot_btc, "UBTC": spot_ubtc, "WBTC": spot_wbtc, "USDC": bs3.get("USDC", 0.0)})
    print("delta_summary", {"perp_szi": perp_szi, "spot_base": spot_base, "net_delta_base": net_delta_base})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
