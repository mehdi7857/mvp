from __future__ import annotations

import os
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
from src.state import load_position, save_position
from src.models import PositionState
from src.universe import COINS
from src.live_executor import LiveExecutor


# --------------------------------------------------
# Utils
# --------------------------------------------------

def now_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


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

def signed_funding(premium: float, fund_raw: float) -> float:
    """
    Funding sign normalization:

    - if fund_raw < 0 -> keep as-is
    - else            -> follow premium sign
    """
    if fund_raw < 0:
        return fund_raw
    return fund_raw if premium >= 0 else -abs(fund_raw)


# --------------------------------------------------
# Data Fetch (hardened)
# --------------------------------------------------

def fetch_latest_snapshot(
    hl: HyperliquidPublic,
    coin: str,
    lookback_hours: int,
    max_retries: int = 3,
) -> Tuple[Optional[Snapshot], Optional[Dict[str, Any]]]:

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(timedelta(hours=lookback_hours).total_seconds() * 1000)

    last_err: Optional[Exception] = None

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
            last_err = e
            sleep_s = 0.5 * attempt
            logger.warning(
                f"API transient error | coin={coin} attempt={attempt}/{max_retries} "
                f"err={type(e).__name__} | sleeping={sleep_s:.1f}s"
            )
            time.sleep(sleep_s)

        except Exception as e:
            last_err = e
            logger.error(f"API unexpected error | coin={coin} err={repr(e)}")
            return None, None

    logger.warning(
        f"API failed after retries | coin={coin} last_err={type(last_err).__name__ if last_err else 'None'}"
    )
    return None, None


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main() -> None:
    setup_logger()

    POLL_SEC = 10
    LOOKBACK_HOURS = 24
    COOLDOWN_SEC = 120

    def env_bool(name: str, default: bool = False) -> bool:
        val = os.getenv(name)
        if val is None:
            return default
        return val.strip().lower() in ("1", "true", "yes", "y", "on")

    def env_float(name: str, default: float) -> float:
        val = os.getenv(name)
        if val is None or not str(val).strip():
            return float(default)
        try:
            return float(str(val).strip())
        except Exception:
            logger.warning(f"Invalid float env var {name}={val!r}; using default={default}")
            return float(default)

    # Single gate switch
    # Safe-by-default: requires explicit ENABLE_LIVE=1 to place real orders.
    ENABLE_LIVE = env_bool("ENABLE_LIVE", default=False)
    live_enabled = ENABLE_LIVE

    # Entry gate (break-even): only enter if expected funding covers round-trip fees with buffer.
    # IMPORTANT: this is a conservative filter; it prevents fee-churn from bleeding the account.
    FUNDING_HORIZON_HOURS = env_float("FUNDING_HORIZON_HOURS", 24.0)
    FEE_RATE_OPEN = env_float("FEE_RATE_OPEN", 0.00045)    # 4.5 bps default (override per your tier)
    FEE_RATE_CLOSE = env_float("FEE_RATE_CLOSE", 0.00045)  # 4.5 bps default (override per your tier)
    FUNDING_FEE_MULTIPLE = env_float("FUNDING_FEE_MULTIPLE", 1.5)  # buffer vs estimation error
    EST_ROUND_TRIP_FEE_RATE = max(0.0, FEE_RATE_OPEN + FEE_RATE_CLOSE)

    strat = FundingPremiumStrategy(
        prem_entry=0.00030,
        fund_entry=0.000006,
        prem_exit=0.00020,
        fund_exit=0.000005,
    )

    executor = DryRunExecutor(notional_usd=100.0)

    # LiveExecutor: safe_mode flips based on ENABLE_LIVE
    live = LiveExecutor(
        notional_usd=executor.notional_usd,
        safe_mode=not ENABLE_LIVE,
    )

    restored = load_position()
    if restored is not None:
        executor.position = restored
        logger.info("RESTORED position from state")
    else:
        logger.info("No open position (FLAT)")

    if live_enabled:
        try:
            live.ensure_client()
            live_positions = live.client.get_positions()  # type: ignore[union-attr]
            if not live_positions:
                if restored is not None:
                    logger.warning("STATE_SYNC_FLAT | exchange has no positions -> resetting state to FLAT")
                executor.position = None
                save_position(None)
            else:
                if len(live_positions) > 1:
                    logger.warning(f"STATE_SYNC_MULTI | exchange returned {len(live_positions)} positions; using first")
                pos = live_positions[0]
                szi = pos.get("szi") or 0.0
                side = "LONG_PERP" if float(szi) > 0 else "SHORT_PERP"
                synced = PositionState(
                    coin=str(pos.get("coin")),
                    side=side,
                    is_open=True,
                    opened_at_ms=int(time.time() * 1000),
                    size=abs(float(szi)),
                    entry_px=pos.get("entry_px"),
                )
                if restored is not None and (
                    restored.coin != synced.coin or restored.side != synced.side or not restored.is_open
                ):
                    logger.warning("STATE_SYNC_OVERRIDE | state differs from exchange; using exchange truth")
                executor.position = synced
                save_position(synced)
                logger.info(
                    f"STATE_SYNC_EXCHANGE | coin={synced.coin} side={synced.side} size={synced.size} entry_px={synced.entry_px}"
                )
        except Exception as e:
            logger.warning(f"STATE_SYNC_FAILED | err={e!r}")
    else:
        logger.info("LIVE_DISABLED | skipping exchange state sync (no private key required)")

    hl = HyperliquidPublic()

    logger.info(
        f"Starting MULTI-COIN bot | coins={COINS} poll={POLL_SEC}s lookback={LOOKBACK_HOURS}h | ENABLE_LIVE={ENABLE_LIVE}"
    )

    last_seen_time: Dict[str, Optional[int]] = {c: None for c in COINS}
    last_trade_ms: Dict[str, Optional[int]] = {c: None for c in COINS}
    fail_counts: Dict[str, int] = {}
    last_fail_log_ms: Optional[int] = None

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
                try:
                    if last_seen_time[b_snap.coin] == b_snap.time:
                        continue
                    last_seen_time[b_snap.coin] = b_snap.time

                    fund_raw = safe_float(raw.get("fundingRate")) or 0.0

                    sign_match = (
                        (b_snap.premium > 0 and b_snap.fundingRate > 0)
                        or (b_snap.premium < 0 and b_snap.fundingRate < 0)
                    )

                    logger.info(
                        f"[DIAG] {b_snap.coin} prem={b_snap.premium:+.6f} "
                        f"fund_raw={fund_raw:+.6f} fund_signed={b_snap.fundingRate:+.6f} match={sign_match}"
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
                            # Break-even gate: funding must cover estimated fees.
                            expected_funding_usd = abs(b_snap.fundingRate) * executor.notional_usd * FUNDING_HORIZON_HOURS
                            est_fees_usd = executor.notional_usd * EST_ROUND_TRIP_FEE_RATE
                            gate_ok = expected_funding_usd >= (FUNDING_FEE_MULTIPLE * est_fees_usd)
                            logger.info(
                                f"[GATE] {b_snap.coin} exp_funding_{FUNDING_HORIZON_HOURS:.0f}h=${expected_funding_usd:.6f} "
                                f"est_round_trip_fees=${est_fees_usd:.6f} mult={FUNDING_FEE_MULTIPLE:.2f} pass={gate_ok}"
                            )
                            if not gate_ok:
                                logger.warning(
                                    f"[{now_iso(b_snap.time)}] [{b_snap.coin}] HOLD | BREAK_EVEN_GATE | "
                                    f"exp_funding=${expected_funding_usd:.6f} fees=${est_fees_usd:.6f} "
                                    f"mult={FUNDING_FEE_MULTIPLE:.2f}"
                                )
                                continue

                            last_trade = last_trade_ms.get(b_snap.coin)
                            if last_trade is not None and (b_snap.time - last_trade) < COOLDOWN_SEC * 1000:
                                logger.info(
                                    f"[{now_iso(b_snap.time)}] [{b_snap.coin}] HOLD | COOLDOWN_ACTIVE | "
                                    f"cooldown_sec={COOLDOWN_SEC}"
                                )
                                continue
                            plan = live.preview(b_snap, "OPEN", d_open.side, d_open.reason)

                            if live_enabled:
                                result = live.execute(plan)
                                if not result or not getattr(result, "ok", False) or not getattr(result, "verified", False):
                                    logger.warning(
                                        f"[{now_iso(b_snap.time)}] [{b_snap.coin}] OPEN | LIVE_FAILED | "
                                        f"ok={getattr(result, 'ok', None)} verified={getattr(result, 'verified', None)} "
                                        f"reason={getattr(result, 'verify_reason', None)}"
                                    )
                                    live_enabled = False
                                    logger.error("EXECUTION_DESYNC_ABORT | live trading disabled until restart")
                                    continue
                                last_trade_ms[b_snap.coin] = b_snap.time

                            status = executor.on_decision(b_snap, d_open)
                            logger.info(f"[{now_iso(b_snap.time)}] [{b_snap.coin}] OPEN | {status}")

                        else:
                            prem_abs = abs(b_snap.premium)
                            fund_abs = abs(b_snap.fundingRate)

                            # Track which open condition fails most often
                            fail_counts[d_open.reason] = fail_counts.get(d_open.reason, 0) + 1
                            now_ms = int(time.time() * 1000)
                            if last_fail_log_ms is None or (now_ms - last_fail_log_ms) >= 60_000:
                                top_reason = max(fail_counts.items(), key=lambda kv: kv[1])
                                logger.info(
                                    f"[DEBUG] OPEN_FAIL_MOST | reason={top_reason[0]} count={top_reason[1]}"
                                )
                                last_fail_log_ms = now_ms

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
                                f"[{now_iso(b_snap.time)}] [{b_snap.coin}] HOLD | FLAT | reason={d_open.reason} | "
                                f"premium={b_snap.premium:+.6f} abs={prem_abs:.6f} {prem_tag} gap={prem_gap:.6f} | "
                                f"funding={b_snap.fundingRate:+.6f} abs={fund_abs:.6f} {fund_tag} gap={fund_gap:.6f}"
                            )

                    # ---------- IN POSITION ----------
                    else:
                        current = executor.current_side()
                        d_close = strat.should_close(b_snap, current)

                        if d_close.action == "CLOSE":
                            last_trade = last_trade_ms.get(b_snap.coin)
                            if last_trade is not None and (b_snap.time - last_trade) < COOLDOWN_SEC * 1000:
                                logger.info(
                                    f"[{now_iso(b_snap.time)}] [{b_snap.coin}] HOLD | COOLDOWN_ACTIVE | "
                                    f"cooldown_sec={COOLDOWN_SEC}"
                                )
                                continue
                            plan = live.preview(b_snap, "CLOSE", d_close.side, d_close.reason)

                            if live_enabled:
                                result = live.execute(plan)
                                if not result or not getattr(result, "ok", False) or not getattr(result, "verified", False):
                                    logger.warning(
                                        f"[{now_iso(b_snap.time)}] [{b_snap.coin}] CLOSE | LIVE_FAILED | "
                                        f"ok={getattr(result, 'ok', None)} verified={getattr(result, 'verified', None)} "
                                        f"reason={getattr(result, 'verify_reason', None)}"
                                    )
                                    live_enabled = False
                                    logger.error("EXECUTION_DESYNC_ABORT | live trading disabled until restart")
                                    continue
                                last_trade_ms[b_snap.coin] = b_snap.time

                            status = executor.on_decision(b_snap, d_close)
                            logger.info(f"[{now_iso(b_snap.time)}] [{b_snap.coin}] CLOSE | {status}")

                        else:
                            # IMPORTANT: without this, you see DIAG/RAW and nothing else while in a position
                            prem_abs = abs(b_snap.premium)
                            fund_abs = abs(b_snap.fundingRate)

                            prem_headroom = prem_abs - strat.prem_exit
                            fund_headroom = fund_abs - strat.fund_exit
                            prem_needs_exit = prem_abs <= strat.prem_exit
                            fund_needs_exit = fund_abs <= strat.fund_exit

                            logger.info(
                                f"[{now_iso(b_snap.time)}] [{b_snap.coin}] HOLD | IN_POSITION | reason={d_close.reason} | "
                                f"premium={b_snap.premium:+.6f} abs={prem_abs:.6f} exit_thr={strat.prem_exit:.6f} "
                                f"headroom={prem_headroom:+.6f} needs_exit={prem_needs_exit} | "
                                f"funding={b_snap.fundingRate:+.6f} abs={fund_abs:.6f} "
                                f"exit_thr={strat.fund_exit:.6f} headroom={fund_headroom:+.6f} "
                                f"needs_exit={fund_needs_exit}"
                            )

                except Exception as e:
                    # Golden rule: never let one coin crash the loop
                    logger.error(f"PROCESS error | coin={b_snap.coin} err={repr(e)}")
                    continue

            time.sleep(POLL_SEC)

    except KeyboardInterrupt:
        logger.info("Stopped by user")
    finally:
        hl.close()


if __name__ == "__main__":
    main()
