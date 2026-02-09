# src/report_funding_3w.py
from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from src.hyperliquid_trade_client import HyperliquidTradeClient

INFO_URL = "https://api.hyperliquid.xyz/info"


def _utc_iso_from_ms(ms: int) -> str:
    return dt.datetime.utcfromtimestamp(ms / 1000.0).isoformat() + "Z"


def _post_info(payload: Dict[str, Any], timeout_s: int = 12) -> Any:
    r = requests.post(INFO_URL, json=payload, timeout=timeout_s)
    r.raise_for_status()
    return r.json()


def fetch_user_funding_by_time(
    user: str,
    start_ms: int,
    end_ms: int,
    timeout_s: int = 12,
) -> List[Dict[str, Any]]:
    """
    Fetch user's funding ledger entries from HL /info using type='userFunding'.
    HL time-range endpoints are paginated (commonly 500 max). To get all data,
    we iterate by advancing startTime to the last returned timestamp + 1.
    """
    out: List[Dict[str, Any]] = []
    cursor = start_ms

    while True:
        payload = {
            "type": "userFunding",
            "user": user,
            "startTime": cursor,
            "endTime": end_ms,
        }
        data = _post_info(payload, timeout_s=timeout_s)

        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected userFunding response type: {type(data)}")

        if not data:
            break

        # Normalize and append
        # Typical fields include: time, coin, funding (or delta), and sometimes rate/position.
        # We keep raw and compute a robust 'amount' float if possible.
        for e in data:
            if not isinstance(e, dict):
                continue
            t = e.get("time")
            if t is None:
                continue
            e["time_utc"] = _utc_iso_from_ms(int(t))
            out.append(e)

        # Pagination: advance cursor
        last_t = int(data[-1].get("time"))
        if last_t >= end_ms:
            break

        # Avoid infinite loops if API returns same last time
        if last_t < cursor:
            break

        cursor = last_t + 1

        # Safety: don’t hammer the API
        time.sleep(0.05)

    return out


def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def _extract_amount_usd(entry: Dict[str, Any]) -> Optional[float]:
    # Hyperliquid userFunding puts the amount under delta.usdc
    d = entry.get("delta")
    if isinstance(d, dict):
        f = _safe_float(d.get("usdc"))
        if f is not None:
            return f

    # fallback for any other shapes
    for k in ("funding", "delta", "usdc", "amount", "value"):
        v = entry.get(k)
        f = _safe_float(v)
        if f is not None:
            return f
    return None


def _extract_coin(entry: Dict[str, Any]) -> str:
    d = entry.get("delta")
    if isinstance(d, dict):
        c = d.get("coin")
        if isinstance(c, str) and c:
            return c
    for k in ("coin", "asset", "symbol"):
        c = entry.get(k)
        if isinstance(c, str) and c:
            return c
    return "UNKNOWN"


def summarize_funding(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = 0.0
    pos_sum = 0.0
    neg_sum = 0.0

    by_coin: Dict[str, float] = {}
    by_day: Dict[str, float] = {}

    # optional stats if present
    rate_sum = 0.0
    rate_cnt = 0
    largest_evt = {"amt": 0.0, "entry": None}

    normalized: List[Dict[str, Any]] = []

    for e in entries:
        coin = _extract_coin(e)
        t = int(e.get("time"))
        day = dt.datetime.utcfromtimestamp(t / 1000.0).strftime("%Y-%m-%d")

        amt = _extract_amount_usd(e) or 0.0
        total += amt
        if amt >= 0:
            pos_sum += amt
        else:
            neg_sum += amt

        by_coin[coin] = by_coin.get(coin, 0.0) + amt
        by_day[day] = by_day.get(day, 0.0) + amt

        if abs(amt) > abs(largest_evt["amt"]):
            largest_evt = {"amt": amt, "entry": e}

        d = e.get("delta")
        if isinstance(d, dict):
            fr = _safe_float(d.get("fundingRate"))
            if fr is not None:
                rate_sum += fr
                rate_cnt += 1

        ne = dict(e)
        ne["_amount_usd"] = amt
        normalized.append(ne)

    by_coin_sorted = sorted(by_coin.items(), key=lambda kv: kv[1], reverse=True)
    by_day_sorted = sorted(by_day.items(), key=lambda kv: kv[0])

    # build cumulative series
    cumulative = []
    run = 0.0
    best_day = None
    worst_day = None
    for day, amt in by_day_sorted:
        run += amt
        cumulative.append((day, amt, run))
        if best_day is None or amt > best_day[1]:
            best_day = (day, amt)
        if worst_day is None or amt < worst_day[1]:
            worst_day = (day, amt)

    avg_rate = (rate_sum / rate_cnt) if rate_cnt > 0 else None

    return {
        "net_usd": total,
        "received_usd": pos_sum,
        "paid_usd": neg_sum,  # negative number
        "count": len(entries),
        "by_coin": by_coin_sorted,
        "by_day": by_day_sorted,
        "cumulative": cumulative,
        "best_day": best_day,
        "worst_day": worst_day,
        "largest_event_usd": largest_evt["amt"],
        "largest_event": largest_evt["entry"],
        "avg_funding_rate": avg_rate,
        "avg_funding_rate_count": rate_cnt,
        "normalized": normalized,
    }


def main():
    print("REPORT_FUNDING_3W: starting")

    client = HyperliquidTradeClient()
    address = client.address

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - int(21 * 24 * 3600 * 1000)  # 3 weeks

    entries = fetch_user_funding_by_time(address, start_ms, now_ms)
    summary = summarize_funding(entries)

    print(f"Address: {address}")
    print(f"Window: { _utc_iso_from_ms(start_ms) } -> { _utc_iso_from_ms(now_ms) }")
    print(f"Entries: {summary['count']}")
    print(f"NET FUNDING (USD): {summary['net_usd']:.6f}")
    print(f"RECEIVED (USD): {summary['received_usd']:.6f}")
    print(f"PAID (USD): {summary['paid_usd']:.6f}")

    if summary["avg_funding_rate"] is not None:
        print(f"AVG fundingRate: {summary['avg_funding_rate']:.8f} (n={summary['avg_funding_rate_count']})")

    print("\nTop coins by funding (USD):")
    for coin, amt in summary["by_coin"][:15]:
        print(f"  {coin:>8s}  {amt: .6f}")

    print("\nBest day / Worst day:")
    print(f"  BEST : {summary['best_day'][0]}  {summary['best_day'][1]: .6f}")
    print(f"  WORST: {summary['worst_day'][0]}  {summary['worst_day'][1]: .6f}")

    print(f"\nLargest single funding event: {summary['largest_event_usd']:.6f}")

    print("\nFunding by day (USD) with cumulative:")
    for day, amt, cum in summary["cumulative"]:
        print(f"  {day}  {amt: .6f}   cum={cum: .6f}")

    # Optional: write raw normalized entries for your journaling system
    out_path = "logs/reports/funding_3w_raw.json"
    try:
        import os
        os.makedirs("logs/reports", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "address": address,
                    "start_ms": start_ms,
                    "end_ms": now_ms,
                    "start_utc": _utc_iso_from_ms(start_ms),
                    "end_utc": _utc_iso_from_ms(now_ms),
                    "net_usd": summary["net_usd"],
                    "entries": summary["normalized"],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\nWrote raw funding file: {out_path}")
    except Exception as e:
        print(f"\nWARN: could not write raw funding file: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
