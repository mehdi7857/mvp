from __future__ import annotations

import os
import time
from pathlib import Path
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
from src.config import Config
from src.hedge_preflight import run_hedge_preflight


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


def is_retryable_http_status(code: Optional[int]) -> bool:
    if code is None:
        return False
    return code == 429 or 500 <= code <= 599


def calc_expected_funding_and_fees(
    funding_rate: float,
    notional_usd: float,
    horizon_hours: float,
    round_trip_fee_rate: float,
) -> Tuple[float, float]:
    expected_funding_usd = abs(funding_rate) * notional_usd * horizon_hours
    est_fees_usd = notional_usd * round_trip_fee_rate
    return expected_funding_usd, est_fees_usd


def compute_next_funding_ms(snapshot_ms: int, interval_sec: int) -> int:
    interval_ms = max(1, interval_sec) * 1000
    return ((int(snapshot_ms) // interval_ms) + 1) * interval_ms


def funding_interpretation(rate: float) -> str:
    if rate > 0:
        return "long_pays_short_receives"
    if rate < 0:
        return "long_receives_short_pays"
    return "flat_or_zero"


def side_expected_receive(side: str, rate: float) -> bool:
    if side == "SHORT_PERP":
        return rate > 0
    if side == "LONG_PERP":
        return rate < 0
    return False


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
    backoff_base_seconds: float = 0.5,
    backoff_max_seconds: float = 5.0,
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
            httpx.NetworkError,
            httpx.PoolTimeout,
        ) as e:
            last_err = e
            sleep_s = min(backoff_max_seconds, backoff_base_seconds * (2 ** (attempt - 1)))
            logger.warning(
                f"API transient error | coin={coin} attempt={attempt}/{max_retries} "
                f"err={type(e).__name__} | sleeping={sleep_s:.1f}s"
            )
            time.sleep(sleep_s)
        except httpx.HTTPStatusError as e:
            last_err = e
            code = e.response.status_code if e.response is not None else None
            if is_retryable_http_status(code):
                sleep_s = min(backoff_max_seconds, backoff_base_seconds * (2 ** (attempt - 1)))
                logger.warning(
                    f"API transient status | coin={coin} code={code} "
                    f"attempt={attempt}/{max_retries} | sleeping={sleep_s:.1f}s"
                )
                time.sleep(sleep_s)
                continue
            logger.error(f"API non-retryable status | coin={coin} code={code}")
            return None, None

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

    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    cfg: Optional[Config] = None
    try:
        cfg = Config.load(str(cfg_path))
        logger.info(f"CONFIG_LOADED | path={cfg_path}")
    except Exception as e:
        logger.warning(f"CONFIG_LOAD_FAILED | path={cfg_path} err={e!r} | using defaults/env")

    def cfg_get(*keys: str, default: Any) -> Any:
        if cfg is None:
            return default
        return cfg.get(*keys, default=default)

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

    def env_int(name: str, default: int) -> int:
        val = os.getenv(name)
        if val is None or not str(val).strip():
            return int(default)
        try:
            return int(str(val).strip())
        except Exception:
            logger.warning(f"Invalid int env var {name}={val!r}; using default={default}")
            return int(default)

    POLL_SEC = env_int("POLL_SEC", int(cfg_get("runtime", "POLL_SEC", default=10)))
    LOOKBACK_HOURS = env_int("LOOKBACK_HOURS", int(cfg_get("runtime", "LOOKBACK_HOURS", default=24)))
    REQUEST_TIMEOUT_SECONDS = env_float(
        "REQUEST_TIMEOUT_SECONDS",
        float(cfg_get("networking", "REQUEST_TIMEOUT_SECONDS", default=10.0)),
    )
    FETCH_RETRY_ATTEMPTS = max(
        1,
        env_int(
            "FETCH_RETRY_ATTEMPTS",
            int(cfg_get("networking", "FETCH_RETRY_ATTEMPTS", default=3)),
        ),
    )
    FETCH_BACKOFF_BASE_SECONDS = max(
        0.1,
        env_float(
            "FETCH_BACKOFF_BASE_SECONDS",
            float(cfg_get("networking", "FETCH_BACKOFF_BASE_SECONDS", default=0.5)),
        ),
    )
    FETCH_BACKOFF_MAX_SECONDS = max(
        FETCH_BACKOFF_BASE_SECONDS,
        env_float(
            "FETCH_BACKOFF_MAX_SECONDS",
            float(cfg_get("networking", "FETCH_BACKOFF_MAX_SECONDS", default=5.0)),
        ),
    )
    COOLDOWN_SEC = env_int(
        "ENTRY_COOLDOWN_SEC",
        int(cfg_get("cooldowns", "ENTRY_COOLDOWN_SEC", default=120)),
    )
    ALERT_CONSECUTIVE_EMPTY_CYCLES = max(
        1,
        env_int(
            "ALERT_CONSECUTIVE_EMPTY_CYCLES",
            int(cfg_get("alerts", "CONSECUTIVE_EMPTY_CYCLES", default=3)),
        ),
    )
    ALERT_EMPTY_CYCLE_COOLDOWN_SEC = max(
        30,
        env_int(
            "ALERT_EMPTY_CYCLE_COOLDOWN_SEC",
            int(cfg_get("alerts", "EMPTY_CYCLE_COOLDOWN_SEC", default=300)),
        ),
    )
    ALERT_MISSED_OPEN_OPP_CYCLES = max(
        1,
        env_int(
            "ALERT_MISSED_OPEN_OPP_CYCLES",
            int(cfg_get("alerts", "MISSED_OPEN_OPP_CYCLES", default=2)),
        ),
    )
    ALERT_MISSED_OPEN_COOLDOWN_SEC = max(
        30,
        env_int(
            "ALERT_MISSED_OPEN_COOLDOWN_SEC",
            int(cfg_get("alerts", "MISSED_OPEN_COOLDOWN_SEC", default=180)),
        ),
    )
    REQUIRE_SPOT_HEDGE_PREFLIGHT = env_bool(
        "REQUIRE_SPOT_HEDGE_PREFLIGHT",
        default=bool(cfg_get("runtime", "REQUIRE_SPOT_HEDGE_PREFLIGHT", default=1)),
    )
    PREFLIGHT_STRICT_ON_ERROR = env_bool(
        "PREFLIGHT_STRICT_ON_ERROR",
        default=bool(cfg_get("runtime", "PREFLIGHT_STRICT_ON_ERROR", default=1)),
    )
    PREFLIGHT_SPOT_QUOTE = str(
        os.getenv("PREFLIGHT_SPOT_QUOTE", str(cfg_get("runtime", "PREFLIGHT_SPOT_QUOTE", default="USDC")))
    ).strip()
    PREFLIGHT_TIMEOUT_SECONDS = env_float(
        "PREFLIGHT_TIMEOUT_SECONDS",
        float(cfg_get("runtime", "PREFLIGHT_TIMEOUT_SECONDS", default=12.0)),
    )
    FUNDING_INTERVAL_SECONDS = max(
        60,
        env_int(
            "FUNDING_INTERVAL_SECONDS",
            int(cfg_get("runtime", "FUNDING_INTERVAL_SECONDS", default=3600)),
        ),
    )
    POST_FUNDING_VALIDATE_DELAY_SECONDS = max(
        0,
        env_int(
            "POST_FUNDING_VALIDATE_DELAY_SECONDS",
            int(cfg_get("runtime", "POST_FUNDING_VALIDATE_DELAY_SECONDS", default=60)),
        ),
    )
    ENFORCE_POST_FUNDING_VALIDATION = env_bool(
        "ENFORCE_POST_FUNDING_VALIDATION",
        default=bool(cfg_get("runtime", "ENFORCE_POST_FUNDING_VALIDATION", default=0)),
    )
    TEST_FORCE_ENTRY_ONCE = env_bool(
        "TEST_FORCE_ENTRY_ONCE",
        default=bool(cfg_get("runtime", "TEST_FORCE_ENTRY_ONCE", default=0)),
    )

    # Single gate switch
    # Safe-by-default: requires explicit ENABLE_LIVE=1 to place real orders.
    ENABLE_LIVE = env_bool("ENABLE_LIVE", default=bool(cfg_get("runtime", "ENABLE_LIVE", default=0)))
    live_enabled = ENABLE_LIVE

    # Entry gate (break-even): only enter if expected funding covers round-trip fees with buffer.
    # IMPORTANT: this is a conservative filter; it prevents fee-churn from bleeding the account.
    FUNDING_HORIZON_HOURS = env_float(
        "FUNDING_HORIZON_HOURS",
        float(cfg_get("funding_gate", "FUNDING_HORIZON_HOURS", default=24.0)),
    )
    FEE_RATE_OPEN = env_float(
        "FEE_RATE_OPEN",
        float(cfg_get("funding_gate", "FEE_RATE_OPEN", default=0.00045)),
    )
    FEE_RATE_CLOSE = env_float(
        "FEE_RATE_CLOSE",
        float(cfg_get("funding_gate", "FEE_RATE_CLOSE", default=0.00045)),
    )
    funding_edge_default = float(cfg_get("funding_gate", "FUNDING_EDGE_MULTIPLIER", default=2.0))
    if os.getenv("FUNDING_EDGE_MULTIPLIER") is not None:
        FUNDING_FEE_MULTIPLE = env_float("FUNDING_EDGE_MULTIPLIER", funding_edge_default)
        funding_multiplier_source = "env:FUNDING_EDGE_MULTIPLIER"
    elif os.getenv("FUNDING_FEE_MULTIPLE") is not None:
        # Backward-compat with old env var name used in previous version.
        FUNDING_FEE_MULTIPLE = env_float("FUNDING_FEE_MULTIPLE", funding_edge_default)
        funding_multiplier_source = "env:FUNDING_FEE_MULTIPLE"
    else:
        FUNDING_FEE_MULTIPLE = funding_edge_default
        funding_multiplier_source = "config:funding_gate.FUNDING_EDGE_MULTIPLIER"
    EST_ROUND_TRIP_FEE_RATE = max(0.0, FEE_RATE_OPEN + FEE_RATE_CLOSE)
    SLIPPAGE_RATE_EST = env_float(
        "SLIPPAGE_RATE_EST",
        float(cfg_get("funding_gate", "SLIPPAGE_RATE_EST", default=0.0)),
    )
    BASIS_BUFFER_RATE = env_float(
        "BASIS_BUFFER_RATE",
        float(cfg_get("funding_gate", "BASIS_BUFFER_RATE", default=0.0)),
    )

    PREM_ENTRY = env_float("PREM_ENTRY", float(cfg_get("strategy", "PREM_ENTRY", default=0.00030)))
    FUND_ENTRY = env_float("FUND_ENTRY", float(cfg_get("strategy", "FUND_ENTRY", default=0.000006)))
    PREM_EXIT = env_float("PREM_EXIT", float(cfg_get("strategy", "PREM_EXIT", default=0.00020)))
    FUND_EXIT = env_float("FUND_EXIT", float(cfg_get("strategy", "FUND_EXIT", default=0.000005)))
    ALLOW_LONG_CARRY = env_bool(
        "ALLOW_LONG_CARRY",
        default=bool(cfg_get("strategy", "ALLOW_LONG_CARRY", default=0)),
    )

    strat = FundingPremiumStrategy(
        prem_entry=PREM_ENTRY,
        fund_entry=FUND_ENTRY,
        prem_exit=PREM_EXIT,
        fund_exit=FUND_EXIT,
        allow_long_carry=ALLOW_LONG_CARRY,
    )

    base_notional_default = float(cfg_get("sizing", "BASE_NOTIONAL_X_USD", default=100.0))
    base_notional_usd = env_float("BASE_NOTIONAL_X_USD", base_notional_default)
    base_notional_source = (
        "env:BASE_NOTIONAL_X_USD" if os.getenv("BASE_NOTIONAL_X_USD") is not None else "config:sizing.BASE_NOTIONAL_X_USD"
    )
    executor = DryRunExecutor(notional_usd=base_notional_usd)

    logger.info(
        "EFFECTIVE_CONFIG | "
        f"ENABLE_LIVE={ENABLE_LIVE} "
        f"POLL_SEC={POLL_SEC} LOOKBACK_HOURS={LOOKBACK_HOURS} ENTRY_COOLDOWN_SEC={COOLDOWN_SEC} "
        f"REQUEST_TIMEOUT_SECONDS={REQUEST_TIMEOUT_SECONDS:.1f} "
        f"FETCH_RETRY_ATTEMPTS={FETCH_RETRY_ATTEMPTS} "
        f"FETCH_BACKOFF_BASE_SECONDS={FETCH_BACKOFF_BASE_SECONDS:.1f} "
        f"FETCH_BACKOFF_MAX_SECONDS={FETCH_BACKOFF_MAX_SECONDS:.1f} "
        f"ALERT_CONSECUTIVE_EMPTY_CYCLES={ALERT_CONSECUTIVE_EMPTY_CYCLES} "
        f"ALERT_EMPTY_CYCLE_COOLDOWN_SEC={ALERT_EMPTY_CYCLE_COOLDOWN_SEC} "
        f"ALERT_MISSED_OPEN_OPP_CYCLES={ALERT_MISSED_OPEN_OPP_CYCLES} "
        f"ALERT_MISSED_OPEN_COOLDOWN_SEC={ALERT_MISSED_OPEN_COOLDOWN_SEC} "
        f"REQUIRE_SPOT_HEDGE_PREFLIGHT={REQUIRE_SPOT_HEDGE_PREFLIGHT} "
        f"PREFLIGHT_STRICT_ON_ERROR={PREFLIGHT_STRICT_ON_ERROR} "
        f"PREFLIGHT_SPOT_QUOTE={PREFLIGHT_SPOT_QUOTE} "
        f"PREFLIGHT_TIMEOUT_SECONDS={PREFLIGHT_TIMEOUT_SECONDS:.1f} "
        f"FUNDING_INTERVAL_SECONDS={FUNDING_INTERVAL_SECONDS} "
        f"POST_FUNDING_VALIDATE_DELAY_SECONDS={POST_FUNDING_VALIDATE_DELAY_SECONDS} "
        f"ENFORCE_POST_FUNDING_VALIDATION={ENFORCE_POST_FUNDING_VALIDATION} "
        f"TEST_FORCE_ENTRY_ONCE={TEST_FORCE_ENTRY_ONCE} "
        f"PREM_ENTRY={PREM_ENTRY:.6f} FUND_ENTRY={FUND_ENTRY:.6f} "
        f"PREM_EXIT={PREM_EXIT:.6f} FUND_EXIT={FUND_EXIT:.6f} "
        f"ALLOW_LONG_CARRY={ALLOW_LONG_CARRY} "
        f"BASE_NOTIONAL_X_USD={executor.notional_usd:.2f} "
        f"FUNDING_HORIZON_HOURS={FUNDING_HORIZON_HOURS:.2f} "
        f"FEE_RATE_OPEN={FEE_RATE_OPEN:.6f} FEE_RATE_CLOSE={FEE_RATE_CLOSE:.6f} "
        f"FUNDING_EDGE_MULTIPLIER={FUNDING_FEE_MULTIPLE:.3f}"
    )
    logger.info(
        "EFFECTIVE_CONFIG_DEBUG | "
        f"funding_multiplier_source={funding_multiplier_source} "
        f"base_notional_source={base_notional_source} "
        f"x_usd={executor.notional_usd:.2f} "
        f"fee_open={FEE_RATE_OPEN:.6f} fee_close={FEE_RATE_CLOSE:.6f} "
        f"slippage_rate_est={SLIPPAGE_RATE_EST:.6f} basis_buffer_rate={BASIS_BUFFER_RATE:.6f} "
        f"round_trip_fee_rate_in_gate={EST_ROUND_TRIP_FEE_RATE:.6f}"
    )

    # LiveExecutor: safe_mode flips based on ENABLE_LIVE
    live = LiveExecutor(
        notional_usd=executor.notional_usd,
        safe_mode=not ENABLE_LIVE,
        spot_quote=PREFLIGHT_SPOT_QUOTE,
    )

    if live_enabled and REQUIRE_SPOT_HEDGE_PREFLIGHT:
        for coin in COINS:
            try:
                preflight = run_hedge_preflight(
                    coin=coin,
                    quote=PREFLIGHT_SPOT_QUOTE,
                    timeout=PREFLIGHT_TIMEOUT_SECONDS,
                )
                exec_supports_spot = live.spot_hedge_capability(coin)
                logger.info(
                    "HEDGE_PREFLIGHT | "
                    f"coin={coin} quote={PREFLIGHT_SPOT_QUOTE} "
                    f"market_hedgeable={preflight.market_hedgeable} "
                    f"carry_pos={preflight.carry_positive_status} "
                    f"carry_neg={preflight.carry_negative_status} "
                    f"spot_candidates={preflight.spot_pair_candidates[:3]} "
                    f"execution_supports_spot={exec_supports_spot}"
                )
                if preflight.market_hedgeable and not exec_supports_spot:
                    raise RuntimeError(
                        "HARD_PREFLIGHT_FAIL | market is hedgeable on HL (perp+spot) "
                        "but executor cannot trade required spot pair for hedge. "
                        "Check spot symbol mapping / SDK support before live start."
                    )
            except Exception as e:
                if PREFLIGHT_STRICT_ON_ERROR:
                    logger.error(f"HEDGE_PREFLIGHT_ABORT | coin={coin} err={e!r}")
                    raise
                logger.warning(f"HEDGE_PREFLIGHT_WARN_ONLY | coin={coin} err={e!r}")

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

    hl = HyperliquidPublic(timeout=REQUEST_TIMEOUT_SECONDS)

    # One-shot pre-start check: market suitability before bot loop.
    for coin in COINS:
        pre_snap, _ = fetch_latest_snapshot(
            hl,
            coin,
            LOOKBACK_HOURS,
            max_retries=FETCH_RETRY_ATTEMPTS,
            backoff_base_seconds=FETCH_BACKOFF_BASE_SECONDS,
            backoff_max_seconds=FETCH_BACKOFF_MAX_SECONDS,
        )
        if pre_snap is None:
            logger.warning(f"PRESTART_MARKET_CHECK | coin={coin} status=no_data")
            continue
        pre_decision = strat.decide_open(pre_snap)
        pre_expected_funding_usd, pre_est_fees_usd = calc_expected_funding_and_fees(
            pre_snap.fundingRate,
            executor.notional_usd,
            FUNDING_HORIZON_HOURS,
            EST_ROUND_TRIP_FEE_RATE,
        )
        pre_gate_ok = pre_expected_funding_usd >= (FUNDING_FEE_MULTIPLE * pre_est_fees_usd)
        pre_candidate = pre_decision.action == "OPEN" and pre_gate_ok
        logger.info(
            "PRESTART_MARKET_CHECK | "
            f"coin={coin} action={pre_decision.action} reason={pre_decision.reason} "
            f"premium={pre_snap.premium:+.6f} funding={pre_snap.fundingRate:+.6f} "
            f"gate_pass={pre_gate_ok} market_open_candidate={pre_candidate}"
        )

    logger.info(
        f"Starting MULTI-COIN bot | coins={COINS} poll={POLL_SEC}s lookback={LOOKBACK_HOURS}h | ENABLE_LIVE={ENABLE_LIVE}"
    )

    last_seen_time: Dict[str, Optional[int]] = {c: None for c in COINS}
    last_trade_ms: Dict[str, Optional[int]] = {c: None for c in COINS}
    fail_counts: Dict[str, int] = {}
    last_fail_log_ms: Optional[int] = None
    consecutive_empty_cycles = 0
    last_empty_alert_ms: Optional[int] = None
    missed_open_opportunity_cycles: Dict[str, int] = {c: 0 for c in COINS}
    last_missed_open_alert_ms: Dict[str, Optional[int]] = {c: None for c in COINS}
    pending_funding_validation: Dict[str, Dict[str, Any]] = {}
    test_force_entry_once_available = TEST_FORCE_ENTRY_ONCE
    test_force_gate_once_available = TEST_FORCE_ENTRY_ONCE
    branch_guard_logged: Dict[str, bool] = {c: False for c in COINS}

    try:
        while True:
            snapshots: List[Tuple[Snapshot, Dict[str, Any]]] = []

            # ---------- FETCH ----------
            for coin in COINS:
                snap, raw = fetch_latest_snapshot(
                    hl,
                    coin,
                    LOOKBACK_HOURS,
                    max_retries=FETCH_RETRY_ATTEMPTS,
                    backoff_base_seconds=FETCH_BACKOFF_BASE_SECONDS,
                    backoff_max_seconds=FETCH_BACKOFF_MAX_SECONDS,
                )
                if snap is not None and raw is not None:
                    snapshots.append((snap, raw))

            if not snapshots:
                consecutive_empty_cycles += 1
                logger.warning(f"No snapshots fetched this cycle | consecutive={consecutive_empty_cycles}")
                now_ms = int(time.time() * 1000)
                if consecutive_empty_cycles >= ALERT_CONSECUTIVE_EMPTY_CYCLES:
                    should_alert = (
                        last_empty_alert_ms is None
                        or (now_ms - last_empty_alert_ms) >= ALERT_EMPTY_CYCLE_COOLDOWN_SEC * 1000
                    )
                    if should_alert:
                        logger.error(
                            "ALERT_SNAPSHOT_STALL | "
                            f"consecutive_empty_cycles={consecutive_empty_cycles} "
                            f"threshold={ALERT_CONSECUTIVE_EMPTY_CYCLES} "
                            f"poll_sec={POLL_SEC} retries={FETCH_RETRY_ATTEMPTS}"
                        )
                        last_empty_alert_ms = now_ms
                time.sleep(POLL_SEC)
                continue
            consecutive_empty_cycles = 0

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
                    next_funding_ms = compute_next_funding_ms(b_snap.time, FUNDING_INTERVAL_SECONDS)
                    logger.info(
                        "[FUNDING_SRC] "
                        f"coin={b_snap.coin} rate={b_snap.fundingRate:+.6f} premium={b_snap.premium:+.6f} "
                        f"snapshot_time={now_iso(b_snap.time)} next_funding_time={now_iso(next_funding_ms)} "
                        f"funding_interval_sec={FUNDING_INTERVAL_SECONDS} "
                        f"interpretation={funding_interpretation(b_snap.fundingRate)}"
                    )

                    # Post-funding validation checks whether side is still aligned with funding direction.
                    pending = pending_funding_validation.get(b_snap.coin)
                    if pending is not None and b_snap.time >= int(pending["next_funding_ms"]) + (POST_FUNDING_VALIDATE_DELAY_SECONDS * 1000):
                        curr_side = str(pending.get("side", ""))
                        validation_pass = side_expected_receive(curr_side, b_snap.fundingRate)
                        if validation_pass:
                            logger.info(
                                "[POST_FUNDING_VALIDATION] "
                                f"coin={b_snap.coin} side={curr_side} expected_next_funding={now_iso(int(pending['next_funding_ms']))} "
                                f"check_time={now_iso(b_snap.time)} funding_rate={b_snap.fundingRate:+.6f} "
                                "action=VALIDATED_OK"
                            )
                            pending_funding_validation.pop(b_snap.coin, None)
                        else:
                            action_txt = "WARN_ONLY"
                            logger.error(
                                "[POST_FUNDING_VALIDATION] "
                                f"coin={b_snap.coin} side={curr_side} expected_next_funding={now_iso(int(pending['next_funding_ms']))} "
                                f"check_time={now_iso(b_snap.time)} funding_rate={b_snap.fundingRate:+.6f} "
                                "action=CLOSE_ALL+DISABLE_LIVE reason=unexpected_funding_direction"
                            )
                            if ENFORCE_POST_FUNDING_VALIDATION:
                                action_txt = "ENFORCED_CLOSE_ALL+DISABLE_LIVE"
                                if executor.current_side() is not None:
                                    forced = StrategyDecision(
                                        action="CLOSE",
                                        side=executor.current_side(),
                                        reason="post_funding_validation_failed",
                                    )
                                    plan = live.preview(b_snap, "CLOSE", forced.side, forced.reason)
                                    if live_enabled:
                                        _ = live.execute(plan)
                                    _ = executor.on_decision(b_snap, forced)
                                live_enabled = False
                                logger.error("EXECUTION_DESYNC_ABORT | live trading disabled until restart")
                            logger.warning(
                                "[POST_FUNDING_VALIDATION] "
                                f"coin={b_snap.coin} enforcement={ENFORCE_POST_FUNDING_VALIDATION} result={action_txt}"
                            )
                            pending_funding_validation.pop(b_snap.coin, None)

                    # ---------- FLAT ----------
                    if executor.current_side() is None:
                        d_open: StrategyDecision = strat.decide_open(b_snap)
                        if (
                            test_force_entry_once_available
                            and d_open.action == "HOLD"
                            and d_open.reason in (
                                "short_carry_but_below_entry_thresholds",
                                "long_carry_but_below_entry_thresholds",
                            )
                        ):
                            if d_open.reason.startswith("long_"):
                                if not branch_guard_logged.get(b_snap.coin, False):
                                    logger.warning(
                                        "[BRANCH_GUARD] "
                                        f"coin={b_snap.coin} unsupported_branch_requires_spot_borrow "
                                        "force_entry_skipped=True"
                                    )
                                    branch_guard_logged[b_snap.coin] = True
                                d_open = StrategyDecision(
                                    action="HOLD",
                                    side=None,
                                    score=d_open.score,
                                    reason="long_carry_disabled_one_sided_mode",
                                )
                            else:
                                forced_side = "SHORT_PERP"
                                d_open = StrategyDecision(
                                    action="OPEN",
                                    side=forced_side,
                                    score=d_open.score,
                                    reason=f"{d_open.reason}_test_force_entry_once",
                                )
                                test_force_entry_once_available = False
                                logger.warning(
                                    "[TEST_FORCE_ENTRY] "
                                    f"used=True coin={b_snap.coin} forced_side={forced_side} "
                                    "bypassed_thresholds=[PREM_ENTRY,FUND_ENTRY]"
                                )

                        if d_open.action == "OPEN":
                            precheck_pass = side_expected_receive(d_open.side, b_snap.fundingRate)
                            precheck_reason = (
                                "aligned_with_funding_direction"
                                if precheck_pass
                                else "side_not_receiver_under_current_funding_sign"
                            )
                            logger.info(
                                "[PRECHECK_FUNDING_DIRECTION] "
                                f"coin={b_snap.coin} side={d_open.side} funding_rate={b_snap.fundingRate:+.6f} "
                                f"pass={precheck_pass} reason={precheck_reason}"
                            )
                            if not precheck_pass:
                                missed_open_opportunity_cycles[b_snap.coin] = 0
                                logger.warning(
                                    f"[{now_iso(b_snap.time)}] [{b_snap.coin}] HOLD | PRECHECK_FUNDING_DIRECTION | "
                                    f"reason={precheck_reason}"
                                )
                                continue

                            # Break-even gate: funding must cover estimated fees.
                            expected_funding_usd, est_fees_usd = calc_expected_funding_and_fees(
                                b_snap.fundingRate,
                                executor.notional_usd,
                                FUNDING_HORIZON_HOURS,
                                EST_ROUND_TRIP_FEE_RATE,
                            )
                            gate_ok = expected_funding_usd >= (FUNDING_FEE_MULTIPLE * est_fees_usd)
                            logger.info(
                                f"[GATE] {b_snap.coin} exp_funding_{FUNDING_HORIZON_HOURS:.0f}h=${expected_funding_usd:.6f} "
                                f"est_round_trip_fees=${est_fees_usd:.6f} mult={FUNDING_FEE_MULTIPLE:.2f} pass={gate_ok}"
                            )
                            ratio = (expected_funding_usd / est_fees_usd) if est_fees_usd > 0 else float("inf")
                            required_funding = FUNDING_FEE_MULTIPLE * est_fees_usd
                            logger.info(
                                f"[GATE_DEBUG] {b_snap.coin} "
                                f"mult_source={funding_multiplier_source} "
                                f"x_usd={executor.notional_usd:.2f} "
                                f"fee_open={FEE_RATE_OPEN:.6f} fee_close={FEE_RATE_CLOSE:.6f} "
                                f"slippage_rate_est={SLIPPAGE_RATE_EST:.6f} basis_buffer_rate={BASIS_BUFFER_RATE:.6f} "
                                f"fee_rate_used_in_gate={EST_ROUND_TRIP_FEE_RATE:.6f} "
                                f"exp_funding_usd={expected_funding_usd:.6f} "
                                f"fees_usd={est_fees_usd:.6f} "
                                f"required_funding_usd={required_funding:.6f} "
                                f"funding_to_fee_ratio={ratio:.6f}"
                            )
                            if not gate_ok and test_force_gate_once_available and d_open.side == "SHORT_PERP":
                                logger.warning(
                                    "[TEST_FORCE_ENTRY] "
                                    f"used=True coin={b_snap.coin} side={d_open.side} "
                                    "bypassed_gate=True reason=integration_test_only"
                                )
                                gate_ok = True
                                test_force_gate_once_available = False
                                test_force_entry_once_available = False

                            if not gate_ok:
                                missed_open_opportunity_cycles[b_snap.coin] = 0
                                logger.warning(
                                    f"[{now_iso(b_snap.time)}] [{b_snap.coin}] HOLD | BREAK_EVEN_GATE | "
                                    f"exp_funding=${expected_funding_usd:.6f} fees=${est_fees_usd:.6f} "
                                    f"mult={FUNDING_FEE_MULTIPLE:.2f}"
                                )
                                continue

                            if live_enabled and d_open.side == "LONG_PERP":
                                missed_open_opportunity_cycles[b_snap.coin] = 0
                                logger.warning(
                                    f"[{now_iso(b_snap.time)}] [{b_snap.coin}] HOLD | "
                                    "UNSUPPORTED_BRANCH long_perp_short_spot_requires_spot_borrow"
                                )
                                continue

                            last_trade = last_trade_ms.get(b_snap.coin)
                            cooldown_active = last_trade is not None and (b_snap.time - last_trade) < COOLDOWN_SEC * 1000
                            if cooldown_active:
                                missed_open_opportunity_cycles[b_snap.coin] = 0
                                logger.info(
                                    f"[{now_iso(b_snap.time)}] [{b_snap.coin}] HOLD | COOLDOWN_ACTIVE | "
                                    f"cooldown_sec={COOLDOWN_SEC}"
                                )
                                continue

                            missed_open_opportunity_cycles[b_snap.coin] += 1
                            plan = live.preview(b_snap, "OPEN", d_open.side, d_open.reason)

                            if live_enabled:
                                result = live.execute(plan)
                                if not result or not getattr(result, "ok", False) or not getattr(result, "verified", False):
                                    logger.warning(
                                        f"[{now_iso(b_snap.time)}] [{b_snap.coin}] OPEN | LIVE_FAILED | "
                                        f"ok={getattr(result, 'ok', None)} verified={getattr(result, 'verified', None)} "
                                        f"reason={getattr(result, 'verify_reason', None)}"
                                    )
                                    now_ms = int(time.time() * 1000)
                                    should_alert = (
                                        missed_open_opportunity_cycles[b_snap.coin] >= ALERT_MISSED_OPEN_OPP_CYCLES
                                        and (
                                            last_missed_open_alert_ms[b_snap.coin] is None
                                            or (now_ms - (last_missed_open_alert_ms[b_snap.coin] or 0))
                                            >= ALERT_MISSED_OPEN_COOLDOWN_SEC * 1000
                                        )
                                    )
                                    if should_alert:
                                        logger.error(
                                            "ALERT_MISSED_OPEN_WHEN_MARKET_OK | "
                                            f"coin={b_snap.coin} cycles={missed_open_opportunity_cycles[b_snap.coin]} "
                                            f"reason={d_open.reason} gate_pass={gate_ok} cooldown_active={cooldown_active} "
                                            f"live_enabled={live_enabled}"
                                        )
                                        last_missed_open_alert_ms[b_snap.coin] = now_ms
                                    live_enabled = False
                                    logger.error("EXECUTION_DESYNC_ABORT | live trading disabled until restart")
                                    continue
                                last_trade_ms[b_snap.coin] = b_snap.time

                            status = executor.on_decision(b_snap, d_open)
                            if status.startswith("OPENED"):
                                missed_open_opportunity_cycles[b_snap.coin] = 0
                                pending_funding_validation[b_snap.coin] = {
                                    "side": d_open.side,
                                    "opened_at_ms": b_snap.time,
                                    "next_funding_ms": compute_next_funding_ms(b_snap.time, FUNDING_INTERVAL_SECONDS),
                                }
                                logger.info(
                                    "[POST_FUNDING_VALIDATION_ARMED] "
                                    f"coin={b_snap.coin} side={d_open.side} "
                                    f"opened_at={now_iso(b_snap.time)} "
                                    f"next_funding_time={now_iso(int(pending_funding_validation[b_snap.coin]['next_funding_ms']))}"
                                )
                            logger.info(f"[{now_iso(b_snap.time)}] [{b_snap.coin}] OPEN | {status}")

                        else:
                            missed_open_opportunity_cycles[b_snap.coin] = 0
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
                            if status.startswith("CLOSED"):
                                pending_funding_validation.pop(b_snap.coin, None)
                                logger.info(
                                    "[ACCOUNTING_SPLIT] "
                                    f"coin={b_snap.coin} side={d_close.side} "
                                    "funding_pnl=NA overlay_pnl=NA basis_pnl=NA fees=NA net=NA "
                                    "note=v1_placeholder_no_ledger_attribution"
                                )
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
