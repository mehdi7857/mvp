import json
from datetime import datetime, timezone

from src.hyperliquid_trade_client import HyperliquidTradeClient

c = HyperliquidTradeClient()
pos = c.get_positions() or []
bal = c.get_spot_balances() or {}

btc_pos = None
for p in pos:
    if str(p.get("coin", "")).upper() == "BTC":
        btc_pos = p
        break

ubtc_keys = [k for k in bal.keys() if str(k).upper() in {"UBTC", "BTC", "UBTC0", "UBTC/USDC"}]
ubtc_bal = {k: bal.get(k) for k in ubtc_keys}

out = {
    "snapshot_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    "btc_perp_position": btc_pos,
    "all_positions_count": len(pos),
    "ubtc_balances": ubtc_bal,
    "usdc_balance": bal.get("USDC"),
}
print(json.dumps(out, default=str))
