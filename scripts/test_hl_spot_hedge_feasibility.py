from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running directly from repo root or scripts folder.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.hedge_preflight import run_hedge_preflight


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only feasibility check for HL delta-neutral hedge directions."
    )
    parser.add_argument("--coin", default="BTC", help="Perp/base coin symbol (default: BTC)")
    parser.add_argument("--quote", default="USDC", help="Spot quote symbol (default: USDC)")
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args()

    base = args.coin.upper().strip()
    quote = args.quote.upper().strip()

    print(f"CHECK_START | base={base} quote={quote} timeout={args.timeout}s", flush=True)

    try:
        result = run_hedge_preflight(coin=base, quote=quote, timeout=args.timeout)
    except Exception as e:  # noqa: BLE001
        print(f"ERR: preflight failed | {type(e).__name__}: {e}", flush=True)
        return 2

    print(f"KEY_SOURCES | {result.key_sources}", flush=True)
    print(f"ADDRESS | present={result.address_present}", flush=True)
    print(f"PERP_MARKET | {base} exists={result.perp_market_exists}", flush=True)
    print(
        f"SPOT_MARKET | {base}/{quote} exists={result.spot_pair_exists} "
        f"candidates={result.spot_pair_candidates[:5]}",
        flush=True,
    )
    print(
        f"SPOT_ACCOUNT | state_ok={result.spot_state_ok} "
        f"{quote}_bal={result.quote_balance:.8f} {base}_bal={result.base_balance:.8f} "
        f"borrow_signals={result.has_borrow_signals}",
        flush=True,
    )
    print(
        "RESULT | funding>0 hedge (SHORT perp + LONG spot) => "
        f"{result.carry_positive_status} | reason={result.carry_positive_reason}",
        flush=True,
    )
    print(
        "RESULT | funding<0 hedge (LONG perp + SHORT spot) => "
        f"{result.carry_negative_status} | reason={result.carry_negative_reason}",
        flush=True,
    )

    if result.carry_positive_status in ("FEASIBLE", "CONDITIONAL"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
