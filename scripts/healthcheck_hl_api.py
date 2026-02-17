from __future__ import annotations

import argparse
import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import List

import httpx

# Allow running this script directly from the repo root or scripts folder.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.exchanges import HyperliquidPublic


def is_transient_error(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.NetworkError,
            httpx.PoolTimeout,
        ),
    ):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        code = exc.response.status_code
        return code == 429 or 500 <= code <= 599
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Hyperliquid API connectivity healthcheck")
    parser.add_argument("--coin", default="BTC")
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    attempts = max(1, int(args.attempts))
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(timedelta(hours=max(1, int(args.lookback_hours))).total_seconds() * 1000)

    hl = HyperliquidPublic(timeout=float(args.timeout))
    ok_latencies_ms: List[float] = []
    fail_count = 0
    transient_count = 0

    print(
        f"HEALTHCHECK_HL_API start | coin={args.coin} attempts={attempts} "
        f"timeout={args.timeout}s lookback_h={args.lookback_hours}",
        flush=True,
    )

    try:
        for i in range(1, attempts + 1):
            t0 = time.perf_counter()
            try:
                hist = hl.funding_history(args.coin, start_ms=start_ms, end_ms=end_ms)
                dt_ms = (time.perf_counter() - t0) * 1000.0
                count = len(hist) if isinstance(hist, list) else -1
                ok_latencies_ms.append(dt_ms)
                print(f"OK attempt={i}/{attempts} latency_ms={dt_ms:.1f} rows={count}", flush=True)
            except Exception as exc:  # noqa: BLE001
                fail_count += 1
                transient = is_transient_error(exc)
                if transient:
                    transient_count += 1
                kind = "TRANSIENT" if transient else "NON_TRANSIENT"
                print(f"ERR attempt={i}/{attempts} kind={kind} type={type(exc).__name__} msg={exc}", flush=True)

    finally:
        hl.close()

    if ok_latencies_ms:
        p50 = statistics.median(ok_latencies_ms)
        p95 = max(ok_latencies_ms) if len(ok_latencies_ms) < 2 else sorted(ok_latencies_ms)[int(0.95 * (len(ok_latencies_ms) - 1))]
        print(
            f"SUMMARY ok={len(ok_latencies_ms)} fail={fail_count} transient_fail={transient_count} "
            f"lat_ms_p50={p50:.1f} lat_ms_p95={p95:.1f}",
            flush=True,
        )
    else:
        print(f"SUMMARY ok=0 fail={fail_count} transient_fail={transient_count}", flush=True)

    if fail_count == 0:
        print("STATUS=HEALTHY", flush=True)
        return 0
    if transient_count == fail_count:
        print("STATUS=DEGRADED (all failures transient)", flush=True)
        return 1
    print("STATUS=UNHEALTHY (non-transient failures present)", flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
