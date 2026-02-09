# src/monitor_manual_stops.py
from __future__ import annotations

import os
import re
from pathlib import Path
import argparse
import time
import json
from typing import Dict, Any, Optional

from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from eth_account import Account
from src.hl_keys import get_hl_private_key
from hyperliquid.utils.error import ClientError

from src.market_data import get_ohlc, resolve_coin_for_hyperliquid
from src.trade.indicators import atr_wilder

BASE_URL = "https://api.hyperliquid.xyz"

STATE_PATH = os.path.join("logs", "manual_stop_state.json")


def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_PATH):
        return {"stops": {}}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"stops": {}}


def save_state(st: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def _sanitize_key(raw: str) -> str:
    v = (raw or "").strip().strip('"').strip("'")
    return v.replace("\r", "").replace("\n", "").replace("\t", "").replace(" ", "")


def _valid_key(raw: str) -> bool:
    body = raw[2:] if raw.lower().startswith("0x") else raw
    return bool(re.fullmatch(r"[0-9a-fA-F]{64}", body))


def get_key() -> str:
    pk, _src = get_hl_private_key()
    return pk


def mk_clients() -> tuple[Info, Exchange, str]:
    key = get_key()
    acct = Account.from_key(key)
    address = acct.address
    info = Info(BASE_URL, skip_ws=True)
    info.timeout = 10
    ex = Exchange(acct, BASE_URL)
    return info, ex, address


def fetch_open_positions(info: Info, address: str) -> Dict[str, Dict[str, Any]]:
    """
    Returns {coin: {side, size, entry_px}} for open positions only.
    """
    us = info.user_state(address)
    # user_state structure can vary by SDK version; we defensively parse.
    out: Dict[str, Dict[str, Any]] = {}

    asset_positions = us.get("assetPositions") if isinstance(us, dict) else None
    if not isinstance(asset_positions, list):
        return out

    for ap in asset_positions:
        pos = ap.get("position") if isinstance(ap, dict) else None
        if not isinstance(pos, dict):
            continue

        coin = pos.get("coin")
        szi = pos.get("szi")  # size signed (string)
        entry = pos.get("entryPx")

        if not isinstance(coin, str):
            continue
        try:
            szi_f = float(szi)
            entry_f = float(entry)
        except Exception:
            continue

        if abs(szi_f) < 1e-12:
            continue

        side = "LONG" if szi_f > 0 else "SHORT"
        out[coin] = {"side": side, "size": abs(szi_f), "entry_px": entry_f}

    return out


def get_mid_price(info: Info, coin: str) -> Optional[float]:
    # Pull ctx and read mid/mark; safest: allMids
    try:
        mids = info.all_mids()
        v = mids.get(coin)
        return float(v) if v is not None else None
    except Exception:
        return None


def compute_sl(entry_px: float, side: str, atr_15m: float, atr_mult: float = 1.5) -> float:
    dist = atr_mult * float(atr_15m)
    if side == "LONG":
        return entry_px - dist
    return entry_px + dist


def close_reduce_only_ioc(ex: Exchange, coin: str, side_close: str, size: float, mid: float) -> dict:
    is_buy = (side_close.upper() == "BUY")
    # marketable IOC limit with exchange rounding if available
    if hasattr(ex, "_slippage_price"):
        px = ex._slippage_price(coin, is_buy, slippage=0.01, px=mid)  # type: ignore[attr-defined]
    else:
        px = mid * (1.001 if is_buy else 0.999)
    return ex.order(
        coin,
        is_buy,
        float(size),
        float(px),
        {"limit": {"tif": "Ioc"}},
        reduce_only=True,
    )


def main():
    parser = argparse.ArgumentParser(description="Monitor manual stops on Hyperliquid positions.")
    parser.add_argument("--once", action="store_true", help="Run one iteration and exit (health check).")
    args = parser.parse_args()

    info, ex, address = mk_clients()
    st = load_state()
    stops: Dict[str, Any] = st.get("stops", {})

    ATR_TF = "15m"
    ATR_LEN = 14
    ATR_MULT = 1.5
    POLL_S = 3.0

    print("MANUAL_STOP_MONITOR: started | atr_mult=1.5 tf=15m poll=1s")

    while True:
        try:
            open_pos = fetch_open_positions(info, address)
        except ClientError as e:
            if getattr(e, "status_code", None) == 429 or "429" in repr(e):
                print("WARN | rate_limited (429) | backing off")
                time.sleep(max(POLL_S, 5.0))
                continue
            raise

        # Register new positions with an SL (if not already tracked)
        for coin, p in open_pos.items():
            key = f"{coin}"
            if key in stops:
                continue

            # Compute ATR from candles using your existing Hyperliquid candleSnapshot fetcher
            # Map to your scanner symbol if needed: here coin already HL-native, so we can use it directly.
            candles = get_ohlc(coin, ATR_TF, limit=320, validate_coin=False)
            atr = atr_wilder(candles, length=ATR_LEN)

            sl = compute_sl(entry_px=float(p["entry_px"]), side=p["side"], atr_15m=atr, atr_mult=ATR_MULT)

            stops[key] = {
                "coin": coin,
                "side": p["side"],
                "size": float(p["size"]),
                "entry_px": float(p["entry_px"]),
                "atr_15m": float(atr),
                "sl_px": float(sl),
                "ts": time.time(),
            }
            save_state({"stops": stops})
            print(f"TRACK | coin={coin} side={p['side']} size={p['size']} entry={p['entry_px']} atr15={atr:.4f} sl={sl:.2f}")

        # Check SL hits
        for key, tr in list(stops.items()):
            coin = tr["coin"]
            # If position is no longer open, stop tracking
            if coin not in open_pos:
                print(f"UNTRACK | coin={coin} position closed (manual or other)")
                stops.pop(key, None)
                save_state({"stops": stops})
                continue

            mid = get_mid_price(info, coin)
            if mid is None:
                continue

            side = tr["side"]
            sl_px = float(tr["sl_px"])

            hit = (mid <= sl_px) if side == "LONG" else (mid >= sl_px)
            if hit:
                close_side = "SELL" if side == "LONG" else "BUY"
                resp = close_reduce_only_ioc(ex, coin=coin, side_close=close_side, size=float(tr["size"]), mid=float(mid))
                print(f"STOP_HIT | coin={coin} mid={mid:.2f} sl={sl_px:.2f} close_side={close_side} resp={resp}")
                # remove from tracking; next loop will re-add if still open (partial fills)
                stops.pop(key, None)
                save_state({"stops": stops})

        st = {"stops": stops}
        if args.once:
            print("MANUAL_STOP_MONITOR: once complete")
            return
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
