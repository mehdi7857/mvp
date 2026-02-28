from src.hyperliquid_trade_client import HyperliquidTradeClient

c = HyperliquidTradeClient()
quote = 'USDC'
base = 'BTC'
reserve_usd = 0.30

print('STEP0_BEFORE')
pos0 = c.get_positions()
bs0 = c.get_spot_balances()
print('positions_before', pos0)
print('spot_before', {k: bs0.get(k, 0.0) for k in sorted(bs0.keys()) if (bs0.get(k,0.0) or 0.0) != 0.0})

for p in pos0:
    coin = str(p.get('coin'))
    szi = float(p.get('szi') or 0.0)
    if szi == 0.0:
        continue
    side = 'SELL' if szi > 0 else 'BUY'
    r = c.place_perp_order(coin=coin, side=side, notional_usd=12.0, reduce_only=True)
    print('close_perp', {'coin': coin, 'before_szi': szi, 'ok': r.ok, 'verified': r.verified, 'reason': r.verify_reason})

bs1 = c.get_spot_balances()
for token, amt in list(bs1.items()):
    token = str(token).upper()
    qty = float(amt or 0.0)
    if token == quote or qty <= 0:
        continue
    if not c.can_trade_spot_pair(token, quote):
        print('skip_spot_sell_no_pair', {'token': token, 'qty': qty})
        continue
    try:
        rs = c.place_spot_order(base_coin=token, quote_coin=quote, side='SELL', notional_usd=12.0, use_available_base_size_for_sell=True)
        print('sell_spot', {'token': token, 'qty': qty, 'ok': rs.ok, 'verified': rs.verified, 'reason': rs.verify_reason, 'pair': rs.pair})
    except Exception as e:
        print('sell_spot_err', {'token': token, 'qty': qty, 'err': repr(e)})

bs2 = c.get_spot_balances()
usdc = float(bs2.get('USDC', 0.0) or 0.0)
if usdc <= 10.5:
    raise RuntimeError(f'Not enough USDC after cleanup: {usdc}')

notional_each = max(10.0, (usdc - reserve_usd) / 2.0)
print('target', {'usdc': usdc, 'reserve_usd': reserve_usd, 'notional_each': notional_each})

r_spot = c.place_spot_order(base_coin=base, quote_coin=quote, side='BUY', notional_usd=notional_each)
print('open_spot', {'ok': r_spot.ok, 'verified': r_spot.verified, 'reason': r_spot.verify_reason, 'pair': r_spot.pair, 'size': r_spot.size})
if not (r_spot.ok and r_spot.verified):
    raise RuntimeError(f'spot open failed: {r_spot.verify_reason}')

r_perp = c.place_perp_order(coin=base, side='SELL', notional_usd=notional_each, reduce_only=False)
print('open_perp_short', {'ok': r_perp.ok, 'verified': r_perp.verified, 'reason': r_perp.verify_reason, 'size': r_perp.size})
if not (r_perp.ok and r_perp.verified):
    rb = c.place_spot_order(base_coin=base, quote_coin=quote, side='SELL', notional_usd=notional_each, use_available_base_size_for_sell=True)
    print('rollback_spot', {'ok': rb.ok, 'verified': rb.verified, 'reason': rb.verify_reason, 'pair': rb.pair})
    raise RuntimeError(f'perp open failed: {r_perp.verify_reason}')

posf = c.get_positions(coin=base)
bsf = c.get_spot_balances()
mid = c._get_mid(c._resolve_spot_pair(base, quote))
spot_base = float(bsf.get('BTC',0.0) or 0.0) + float(bsf.get('UBTC',0.0) or 0.0) + float(bsf.get('WBTC',0.0) or 0.0)
perp_szi = float((posf[0] if posf else {}).get('szi') or 0.0)
perp_usd = abs(float((posf[0] if posf else {}).get('position_value') or 0.0))
spot_usd = spot_base * float(mid)
print('STEP4_AFTER')
print('positions_after', posf)
print('spot_after', {k: bsf.get(k, 0.0) for k in ['USDC','BTC','UBTC','WBTC','XAUT']})
print('allocation', {'spot_usd': spot_usd, 'perp_usd': perp_usd, 'delta_base': spot_base + perp_szi})
