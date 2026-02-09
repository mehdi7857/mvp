import datetime as dt

from src.broker.hl_broker import HyperliquidBroker
from src.trade.hl_history import HLHistory
from eth_account import Account

print("REPORT_LAST_DAY: starting")

broker = HyperliquidBroker()
address = broker.wallet.address

hist = HLHistory(address)

fills = hist.user_fills_last_hours(72)
state = hist.user_state()

if not fills:
    print("No HL fills in last 72h.")
else:
    print(f"Found {len(fills)} HL fills in last 72h:\n")
    for f in fills:
        ts_ms = f.get("time")
        time_utc = None
        if isinstance(ts_ms, (int, float)):
            time_utc = dt.datetime.utcfromtimestamp(ts_ms / 1000).isoformat() + "Z"
        print({
            "coin": f.get("coin"),
            "side": f.get("side"),
            "px": f.get("px"),
            "sz": f.get("sz"),
            "time": ts_ms,
            "time_utc": time_utc,
            "fee": f.get("fee"),
        })

print("Current HL user_state:")
print(state)
