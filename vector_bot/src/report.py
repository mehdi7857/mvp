# src/report.py
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from typing import Any, Dict, List, Tuple

from src.broker.hl_broker import HyperliquidBroker
from src.trade.hl_history import HLHistory


def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def _utc_iso_from_ms(ms: int) -> str:
    return dt.datetime.utcfromtimestamp(ms / 1000.0).isoformat() + "Z"


def _append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    _ensure_dir(os.path.dirname(path))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _period_to_hours(period: str) -> int:
    period = period.lower()
    if period == "day":
        return 24
    if period == "week":
        return 24 * 7
    if period == "month":
        return 24 * 30
    raise ValueError("period must be one of: day, week, month")


def _side_decode(side: str) -> str:
    # Hyperliquid fills commonly use "B" and "A"
    if side == "B":
        return "BUY"
    if side == "A":
        return "SELL"
    return str(side)


def _group_round_trips(fills: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Very simple grouping for flat periods:
    - Track net position per coin by fills
    - Build "segments" when position returns to 0
    """
    trades: List[Dict[str, Any]] = []
    stats = {"gross_pnl_usd_est": 0.0, "fees_usd": 0.0, "segments": 0}

    # Sort by time ascending
    fills = sorted(fills, key=lambda f: int(f.get("time", 0)))

    pos = 0.0
    vw_entry_cost = 0.0  # for longs only approximation (works if only long segments)
    current = {"fills": [], "coin": None}

    for f in fills:
        coin = f.get("coin")
        side = _side_decode(f.get("side"))
        px = float(f.get("px"))
        sz = float(f.get("sz"))
        fee = float(f.get("fee") or 0.0)
        stats["fees_usd"] += fee

        if not current["coin"]:
            current["coin"] = coin

        current["fills"].append(f)

        # Long-only approximation: BUY increases pos, SELL decreases
        # If you later do shorts, we’ll upgrade this bookkeeping.
        if side == "BUY":
            vw_entry_cost += px * sz
            pos += sz
        elif side == "SELL":
            pos -= sz
            # Realized PnL (approx) = sell_value - proportional cost basis
            # proportional cost basis:
            # if pos before sell was p_before, cost basis = (vw_entry_cost / p_before) * sz
            # but we track vw_entry_cost as total open cost; so:
            # cost_basis_per_coin = vw_entry_cost / (pos + sz)  (pos already reduced)
            p_before = pos + sz
            if p_before > 0:
                cost_basis = (vw_entry_cost / p_before) * sz
                realized = (px * sz) - cost_basis
                stats["gross_pnl_usd_est"] += realized
                vw_entry_cost -= cost_basis

        if abs(pos) < 1e-12:
            # segment complete
            stats["segments"] += 1
            trades.append(
                {
                    "coin": current["coin"],
                    "fills_count": len(current["fills"]),
                    "start_utc": _utc_iso_from_ms(int(current["fills"][0]["time"])),
                    "end_utc": _utc_iso_from_ms(int(current["fills"][-1]["time"])),
                    "fills": current["fills"],
                }
            )
            current = {"fills": [], "coin": None}

    return trades, stats


def main():
    ap = argparse.ArgumentParser(description="HL daily/weekly/monthly reporter + journal append")
    ap.add_argument("--period", required=True, choices=["day", "week", "month"])
    args = ap.parse_args()

    period = args.period.lower()
    hours = _period_to_hours(period)

    print(f"REPORT: starting | period={period} hours={hours}")

    broker = HyperliquidBroker()
    address = broker.wallet.address

    hist = HLHistory(address)
    fills = hist.user_fills_last_hours(hours)
    state = hist.user_state()

    # Add derived fields
    for f in fills:
        if "time" in f:
            f["time_utc"] = _utc_iso_from_ms(int(f["time"]))
        if "side" in f:
            f["side_decoded"] = _side_decode(str(f["side"]))

    # Append raw HL journals (truth stream)
    now = time.time()
    _append_jsonl(os.path.join("logs", "hl_state.jsonl"), {"ts": now, "ts_utc": dt.datetime.utcnow().isoformat() + "Z", "period": period, "state": state})
    for f in fills:
        _append_jsonl(os.path.join("logs", "hl_fills.jsonl"), {"ts": now, "ts_utc": dt.datetime.utcnow().isoformat() + "Z", "period": period, "fill": f})

    # Human report
    _ensure_dir(os.path.join("logs", "reports"))

    tag = dt.datetime.utcnow().strftime("%Y-%m-%d")
    if period == "week":
        tag = dt.datetime.utcnow().strftime("%Y-W%W")
    if period == "month":
        tag = dt.datetime.utcnow().strftime("%Y-%m")

    report_path = os.path.join("logs", "reports", f"{period}_{tag}.md")

    trades, stats = _group_round_trips(fills)

    lines: List[str] = []
    lines.append(f"# HL {period.capitalize()} Report — {tag}\n")
    lines.append(f"- Address: `{address}`\n")
    lines.append(f"- Fills in window: **{len(fills)}**\n")
    lines.append(f"- Flat now: **{'YES' if float(state.get('marginSummary',{}).get('totalNtlPos','0')) == 0.0 else 'NO'}**\n")
    lines.append(f"- Est. gross PnL (long-only approx): **{stats['gross_pnl_usd_est']:.4f} USD**\n")
    lines.append(f"- Fees: **{stats['fees_usd']:.4f} USD**\n")
    lines.append(f"- Est. net (gross - fees): **{(stats['gross_pnl_usd_est']-stats['fees_usd']):.4f} USD**\n")
    lines.append(f"- Completed segments (position returned to 0): **{stats['segments']}**\n\n")

    lines.append("## Fills (time-ordered)\n")
    for f in sorted(fills, key=lambda x: int(x.get("time", 0))):
        lines.append(
            f"- {f.get('time_utc')} | {f.get('coin')} | {f.get('side_decoded')} | px={f.get('px')} | sz={f.get('sz')} | fee={f.get('fee')}\n"
        )

    lines.append("\n## Segments (round trips)\n")
    if not trades:
        lines.append("- No completed segments detected in window.\n")
    else:
        for i, t in enumerate(trades, 1):
            lines.append(f"### Segment {i}: {t['coin']} | {t['start_utc']} → {t['end_utc']} | fills={t['fills_count']}\n")
            for ff in t["fills"]:
                lines.append(f"- {ff.get('time_utc')} | {ff.get('side_decoded')} px={ff.get('px')} sz={ff.get('sz')} fee={ff.get('fee')}\n")
            lines.append("\n")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("".join(lines))

    print(f"REPORT: wrote {report_path}")


if __name__ == "__main__":
    main()
