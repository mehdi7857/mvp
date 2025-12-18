from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple

import httpx
from loguru import logger

from src.exchanges import HyperliquidPublic
from src.logger import setup_logger
from src.models import Snapshot
from src.strategy import FundingPremiumStrategy, StrategyDecision
from src.executor import DryRunExecutor
from src.state import load_position
from src.universe import COINS
from src.live_executor import LiveExecutorSkeleton


# --------------------------------------------------
# Utils
# --------------------------------------------------
# Small helpers for timestamp formatting, safe casting, and picking
# the most recent record from API history responses.

def now_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def safe_float(x) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def parse_latest(history):
    if not history:
        return None
    return max(history, key=lambda x: int(x.get("time", 0)))


# --------------------------------------------------
# FUNDING SIGN LOGIC (FIXED)
# --------------------------------------------------
# Normalize the funding sign so funding follows premium direction when positive
# values are reported for both longs and shorts by the upstream API.

def signed_funding(premium: float, fund_raw: float) -> float:
    """
    Funding sign normalization:

    - if fund_raw < 0        -> keep as-is
    - if fund_raw >= 0 and premium < 0 -> negative
    - if fund_raw >= 0 and premium >= 0 -> positive
    """
    if fund_raw < 0:
        return fund_raw
    return fund_raw if premium >= 0 else -abs(fund_raw)


# --------------------------------------------------
# Data Fetch (hardened)
# --------------------------------------------------
# Pull the most recent funding/premium snapshot with a bounded lookback window
# and simple retry logic to smooth over transient API/network hiccups.

def fetch_latest_snapshot(
    hl: HyperliquidPublic,
    coin: str,
    lookback_hours: int,
    max_retries: int = 3,
) -> Tuple[Optional[Snapshot], Optional[Dict[str, Any]]]:

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(timedelta(hours=lookback_hours).total_seconds() * 1000)

    for attempt in range(1, max_retries + 1):
        try:
            hist = hl.funding_history(coin, start_ms=start_ms, end_ms=end_ms)
            latest = parse_latest(hist)
            if not latest:
                return None, None

            fund_raw = safe_float(latest.get("fundingRate"))
            premium = safe_float(latest.get("premium"))
            t_ms = int(latest.get("time", 0))

            if fund_raw is None or premium is None:
                return None, latest

            fund_signed = signed_funding(premium, fund_raw)

            snap = Snapshot(
                coin=coin,
                fundingRate=fund_signed,
                premium=premium,
                time=t_ms,
            )
            return snap, latest

        except (
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
        ) as e:
            sleep_s = 0.5 * attempt
            logger.warning(
                f"API transient error | coin={coin} "
                f"attempt={attempt}/{max_retries} err={type(e).__name__} "
                f"| sleeping={sleep_s:.1f}s"
            )
            time.sleep(sleep_s)

        except Exception as e:
            logger.error(f"API unexpected error | coin={coin} err={repr(e)}")
            return None, None

    return None, None


# --------------------------------------------------
# MAIN
# --------------------------------------------------
# Wire together strategy + executors, restore any saved position, then poll all
# coins, log diagnostics, and hand decisions to the dry-run/live executor pair.

def main() -> None:
    setup_logger()

    POLL_SEC = 10
    LOOKBACK_HOURS = 24

    strat = FundingPremiumStrategy(
        prem_entry=0.00030,
        fund_entry=0.000006,
        prem_exit=0.00020,
        fund_exit=0.00000,
    )

    executor = DryRunExecutor(notional_usd=1000.0)
    live = LiveExecutorSkeleton(notional_usd=executor.notional_usd)

    restored = load_position()
    if restored is not None:
        executor.position = restored
        logger.info("RESTORED position from state")
    else:
        logger.info("No open position (FLAT)")

    hl = HyperliquidPublic()

    logger.info(
        f"Starting MULTI-COIN DRY-RUN bot (bi-directional, gated, ROTATE, AUTO-FLAT) | "
        f"coins={COINS} poll={POLL_SEC}s lookback={LOOKBACK_HOURS}h"
    )

    last_seen_time: Dict[str, Optional[int]] = {c: None for c in COINS}

    try:
        while True:
            snapshots: List[Tuple[Snapshot, Dict[str, Any]]] = []

            # ---------- FETCH ----------
            for coin in COINS:
                snap, raw = fetch_latest_snapshot(hl, coin, LOOKBACK_HOURS)
                if snap is not None and raw is not None:
                    snapshots.append((snap, raw))

            if not snapshots:
                logger.warning("No snapshots fetched this cycle")
                time.sleep(POLL_SEC)
                continue

            # ---------- PROCESS ----------
            for b_snap, raw in snapshots:
                if last_seen_time[b_snap.coin] == b_snap.time:
                    continue
                last_seen_time[b_snap.coin] = b_snap.time

                fund_raw = safe_float(raw.get("fundingRate")) or 0.0

                # ---- DIAG ----
                sign_match = (
                    (b_snap.premium > 0 and b_snap.fundingRate > 0)
                    or (b_snap.premium < 0 and b_snap.fundingRate < 0)
                )

                logger.info(
                    f"[DIAG] {b_snap.coin} "
                    f"prem={b_snap.premium:+.6f} "
                    f"fund_raw={fund_raw:+.6f} "
                    f"fund_signed={b_snap.fundingRate:+.6f} "
                    f"match={sign_match}"
                )

                logger.info(f"[RAW] {b_snap.coin} keys={list(raw.keys())}")
                logger.info(
                    f"[RAW] {b_snap.coin} view={{'time': {raw.get('time')}, "
                    f"'coin': '{raw.get('coin')}', "
                    f"'fundingRate': {raw.get('fundingRate')}, "
                    f"'premium': {raw.get('premium')}}}"
                )

                # ---------- FLAT ----------
                if executor.current_side() is None:
                    d_open: StrategyDecision = strat.decide_open(b_snap)

                    if d_open.action == "OPEN":
                        intent = live.build_open_intent(
                            b_snap, d_open.side, d_open.reason
                        )
                        live.log_intent(intent)
                        executor.on_decision(b_snap, d_open)

                    else:
                        prem_abs = abs(b_snap.premium)
                        fund_abs = abs(b_snap.fundingRate)

                        prem_gap = max(0.0, strat.prem_entry - prem_abs)
                        fund_gap = max(0.0, strat.fund_entry - fund_abs)

                        if d_open.reason == "sign_mismatch_not_a_carry":
                            prem_tag = "N/A"
                            fund_tag = "N/A"
                            prem_gap = 0.0
                            fund_gap = 0.0
                        else:
                            prem_tag = "PASS" if prem_abs >= strat.prem_entry else "FAIL"
                            fund_tag = "PASS" if fund_abs >= strat.fund_entry else "FAIL"

                        logger.info(
                            f"[{now_iso(b_snap.time)}] [{b_snap.coin}] "
                            f"HOLD | FLAT | reason={d_open.reason} | "
                            f"premium={b_snap.premium:+.6f} abs={prem_abs:.6f} "
                            f"{prem_tag} gap={prem_gap:.6f} | "
                            f"funding={b_snap.fundingRate:+.6f} abs={fund_abs:.6f} "
                            f"{fund_tag} gap={fund_gap:.6f}"
                        )

                # ---------- IN POSITION ----------
                else:
                    current = executor.current_side()
                    d_close = strat.should_close(b_snap, current)

                    if d_close.action == "CLOSE":
                        intent = live.build_close_intent(
                            b_snap, d_close.side, d_close.reason
                        )
                        live.log_intent(intent)
                        executor.on_decision(b_snap, d_close)

            time.sleep(POLL_SEC)

    except KeyboardInterrupt:
        logger.info("Stopped by user")
    finally:
        hl.close()


if __name__ == "__main__":
    main()
