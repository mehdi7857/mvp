# src/journal.py
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys


def run_account_report(hours: float) -> str:
    # call the existing module so we don't change your working code
    cmd = [sys.executable, "-m", "src.account_report", "--hours", str(hours)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    return out


def main():
    ap = argparse.ArgumentParser(description="Daily/weekly/monthly journal wrapper for account_report")
    ap.add_argument("--period", choices=["day", "week", "month"], required=True)
    args = ap.parse_args()

    period = args.period
    if period == "day":
        hours = 24
        tag = dt.datetime.utcnow().strftime("%Y-%m-%d")
    elif period == "week":
        hours = 168
        tag = dt.datetime.utcnow().strftime("%Y-W%W")
    else:
        hours = 24 * 30
        tag = dt.datetime.utcnow().strftime("%Y-%m")

    out = run_account_report(hours)

    os.makedirs("logs/journals", exist_ok=True)
    path = os.path.join("logs", "journals", f"{period}_{tag}.log")

    with open(path, "w", encoding="utf-8") as f:
        f.write(out)

    print(f"JOURNAL: wrote {path}")


if __name__ == "__main__":
    main()
