from src.hyperliquid_trade_client import HyperliquidTradeClient

c = HyperliquidTradeClient()
coin = 'BTC'

pos = c.get_positions(coin=coin)
if pos:
    szi = float(pos[0].get('szi') or 0.0)
    if szi != 0.0:
        side = 'BUY' if szi < 0 else 'SELL'
        r = c.place_perp_order(coin=coin, side=side, notional_usd=12.0, reduce_only=True)
        print('CLOSE_PERP', {'ok': r.ok, 'verified': r.verified, 'reason': r.verify_reason})

b = c.get_spot_balances()
base = float(b.get('UBTC', 0.0) or 0.0) + float(b.get('BTC', 0.0) or 0.0) + float(b.get('WBTC', 0.0) or 0.0)
if base > 0:
    r2 = c.place_spot_order(
        base_coin='BTC',
        quote_coin='USDC',
        side='SELL',
        notional_usd=12.0,
        use_available_base_size_for_sell=True,
    )
    print('CLOSE_SPOT', {'ok': r2.ok, 'verified': r2.verified, 'reason': r2.verify_reason})

print('AFTER_POS', c.get_positions(coin=coin))
bb = c.get_spot_balances()
print('AFTER_SPOT', {'UBTC': bb.get('UBTC', 0.0), 'BTC': bb.get('BTC', 0.0), 'WBTC': bb.get('WBTC', 0.0), 'USDC': bb.get('USDC', 0.0)})
