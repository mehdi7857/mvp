# src/live_runner.py
from __future__ import annotations

import argparse
import os
import time

from src.trade.config_live import LiveConfig
from src.market_data import get_ohlc, resolve_coin_for_hyperliquid
from src.trade.state_store import load_state, save_state, PositionState, now_utc_ts
from src.trade.journal import append_event
from src.trade.exit_manager import check_exit

from src.broker.paper_broker import PaperBroker
from src.broker.hl_broker import HyperliquidBroker

from src.trade.derivatives_hl import fetch_meta_and_ctx, extract_derivatives_for_coin
from src.trade.permission_layer import permission_layer
from src.trade.execution_engine import build_plan

from src.trade.indicators import atr_wilder
from src.trade.regime import compute_atr_ratio_regime
from src.trade.bias_adapter import get_htf_bias
from src.trade.triggers import rejection_candle, breakout_trigger, touched_level, range_high_low

STATE_PATH = os.path.join("logs", "state_dev.json")


def _compute_plan(cfg: LiveConfig):
    candles_15m = get_ohlc(cfg.symbol, "15m", limit=320, validate_coin=True)
    last_candle_15m = candles_15m[-1]
    last_close_15m = float(last_candle_15m["c"])
    atr_15m = atr_wilder(candles_15m, length=14)

    reg = compute_atr_ratio_regime(candles_15m)
    regime = reg["regime"]
    ratio = reg.get("ratio")

    candles_4h = get_ohlc(cfg.symbol, "4h", limit=260, validate_coin=True)
    htf_hi, htf_lo = range_high_low(candles_4h, lookback=64)

    bias, bias_dbg = get_htf_bias(cfg.symbol, tf="4h", limit=220, kijun_len=cfg.KIJUN_LEN_4H)

    kijun_level = None
    kijun_slope = None
    htf_close = None
    if isinstance(bias_dbg, dict):
        try: kijun_level = float(bias_dbg.get("kijun_now"))
        except Exception: kijun_level = None
        try: kijun_slope = float(bias_dbg.get("kijun_slope"))
        except Exception: kijun_slope = None
        try: htf_close = float(bias_dbg.get("close"))
        except Exception: htf_close = None

    # Option B (safe defaults)
    allow_flat = getattr(cfg, "ALLOW_FLAT_KIJUN_BIAS", False)
    min_ratio_flat = getattr(cfg, "MIN_RATIO_FOR_FLAT_BIAS", 0.85)
    if allow_flat and bias == "NEUTRAL":
        if (kijun_slope == 0.0) and (kijun_level is not None) and (htf_close is not None):
            if (regime != "COMPRESSION") and (ratio is not None) and (float(ratio) >= float(min_ratio_flat)):
                if htf_close > kijun_level:
                    bias = "BULL"
                elif htf_close < kijun_level:
                    bias = "BEAR"

    meta_and_ctx = fetch_meta_and_ctx()
    coin = resolve_coin_for_hyperliquid(cfg.symbol)
    deriv = extract_derivatives_for_coin(meta_and_ctx, coin)
    perm = permission_layer(deriv, regime)

    direction = "LONG" if bias == "BULL" else ("SHORT" if bias == "BEAR" else "NONE")

    tol = 0.25 * atr_15m
    levels = []
    if kijun_level is not None:
        levels.append(("kijun", kijun_level))
    if direction == "LONG":
        levels.append(("htf_lo", htf_lo))
    elif direction == "SHORT":
        levels.append(("htf_hi", htf_hi))

    touch_ok = False
    touched_name = None
    for name, lvl in levels:
        if touched_level(last_close_15m, float(lvl), tol):
            touch_ok = True
            touched_name = name
            break

    reject_ok = False
    if direction in ("LONG", "SHORT"):
        reject_ok = rejection_candle(last_candle_15m, direction=direction, wick_ratio=0.55)

    br = breakout_trigger(candles_15m, lookback=64)
    breakout_triggered = (br == "UP" and bias == "BULL") or (br == "DOWN" and bias == "BEAR")
    cvd_ok = True

    print(f"DBG | close15={last_close_15m:.2f} atr15={atr_15m:.4f} regime={regime} ratio={ratio} bias={bias} dir={direction}")
    print(f"DBG | perm_reasons={perm.get('reasons')}")
    print(f"DBG | kijun={kijun_level} slope={kijun_slope} htf_close={htf_close}")
    print(f"DBG | htf_hi={htf_hi:.2f} htf_lo={htf_lo:.2f} tol={tol:.2f}")
    print(f"DBG | levels={[(n, round(float(v),2)) for n,v in levels]} touched={touched_name} touch_ok={touch_ok} reject_ok={reject_ok}")
    print(f"DBG | breakout={br} breakout_ok={breakout_triggered}")

    if regime in ("RANGE", "TRANSITION"):
        if not (touch_ok and reject_ok):
            print("HOLD | Reactional trigger not satisfied (touch+rejection)")
            return None, last_close_15m, coin

    plan = build_plan(
        cfg=cfg,
        perm=perm,
        bias=bias,
        last_close_15m=last_close_15m,
        atr_15m=atr_15m,
        breakout_triggered=breakout_triggered,
        cvd_ok=cvd_ok,
    )

    if plan is None:
        print("HOLD | No plan (permission/bias/mode failed)")
        return None, last_close_15m, coin

    return plan, last_close_15m, coin


def main():
    parser = argparse.ArgumentParser(description="Live runner dev (safe)")
    parser.add_argument("--state-path", default=STATE_PATH, help="State file path for dev runner")
    args = parser.parse_args()
    state_path = args.state_path
    cfg = LiveConfig()
    # DEV runner safety + banner
    cfg = LiveConfig()
    cfg = cfg.__class__(**{**cfg.__dict__, "SAFE_MODE": True, "ENABLE_LIVE": False})
    print(f"LIVE_RUNNER_DEV: started | SAFE_MODE=True ENABLE_LIVE=False | state={state_path}")

    broker_live = None
    if (not cfg.SAFE_MODE) and cfg.ENABLE_LIVE:
        broker_live = HyperliquidBroker()

    last_ts = None

    while True:
        candles_15m = get_ohlc(cfg.symbol, "15m", limit=2, validate_coin=True)
        ts = int(candles_15m[-1]["t"])
        last_close = float(candles_15m[-1]["c"])

        if last_ts is None or ts != last_ts:
            last_ts = ts
            print(f"NEW 15m candle detected | ts={ts}")
            append_event({"event": "CANDLE", "symbol": cfg.symbol, "candle_ts": ts})

            state = load_state(path=state_path)

            # 1) If in position -> manage exits
            if state.in_position:
                should_exit, reason = check_exit(state, last_close)
                print(f"STATE | in_position=True side={state.side} entry={state.entry_px} sl={state.sl_px} tp={state.tp_px} last={last_close} exit?={should_exit} reason={reason}")

                if should_exit:
                    print(f"EXIT | closing position reason={reason} last={last_close}")
                    append_event({
                        "event": "EXIT",
                        "symbol": state.symbol or cfg.symbol,
                        "side": state.side,
                        "entry_px": state.entry_px,
                        "sl_px": state.sl_px,
                        "tp_px": state.tp_px,
                        "last": last_close,
                        "reason": reason,
                    })
                    if cfg.SAFE_MODE or (not cfg.ENABLE_LIVE) or broker_live is None:
                        print("PAPER_EXIT | would close position (no live orders)")
                        state = PositionState()  # reset in paper mode
                        save_state(state, path=state_path)
                    else:
                        # close reduce-only
                        close_side = "SELL" if state.side == "LONG" else "BUY"
                        # mid price from derivatives ctx
                        meta_and_ctx = fetch_meta_and_ctx()
                        coin = resolve_coin_for_hyperliquid(cfg.symbol)
                        deriv = extract_derivatives_for_coin(meta_and_ctx, coin)
                        mid = float(deriv.get("mid_price") or deriv.get("mark_price") or last_close)

                        resp = broker_live.close_reduce_only_ioc(
                            coin=coin,
                            side=close_side,
                            size=float(state.size),
                            mid=mid,
                        )
                        print("LIVE_EXIT_RESP:", resp)
                        state = PositionState()
                        save_state(state, path=state_path)

                time.sleep(1)
                continue

            # 2) Not in position -> compute plan
            plan, last_close_15m, coin = _compute_plan(cfg)
            if plan is None:
                time.sleep(1)
                continue
            append_event({
                "event": "PLAN",
                "symbol": cfg.symbol,
                "direction": plan.direction,
                "entry": float(plan.entry),
                "sl": float(plan.sl),
                "tp": float(plan.tp),
                "notional_usd": float(plan.notional_usd),
            })

            # Enforce HL min notional + cap
            min_notional = getattr(cfg, "MIN_NOTIONAL_USD", 10.0)
            plan_notional = max(float(plan.notional_usd), float(min_notional))
            plan_notional = min(plan_notional, float(cfg.MAX_NOTIONAL_USD))

            # Execute plan
            if cfg.SAFE_MODE or (not cfg.ENABLE_LIVE) or broker_live is None:
                append_event({
                    "event": "ENTRY",
                    "symbol": cfg.symbol,
                    "side": plan.direction,
                    "entry": float(plan.entry),
                    "sl": float(plan.sl),
                    "tp": float(plan.tp),
                    "notional_usd": float(plan_notional),
                    "mode": "paper",
                })
                PaperBroker().execute(plan, cfg)
                time.sleep(1)
                continue

            # Live entry IOC
            meta_and_ctx = fetch_meta_and_ctx()
            deriv = extract_derivatives_for_coin(meta_and_ctx, coin)
            mid = float(deriv.get("mid_price") or deriv.get("mark_price") or last_close_15m)

            # compute size from notional and keep what broker does for decimals in its own code
            side = "BUY" if plan.direction == "LONG" else "SELL"

            # We reuse broker's existing test method to compute size/decimals reliably,
            # but we do NOT flatten. It returns size and response.
            out = broker_live.execute_test_trade(cfg=cfg, side=side, notional_usd=plan_notional, flatten=False)
            print("LIVE_ENTRY_RESULT:", out)
            append_event({
                "event": "ENTRY",
                "symbol": cfg.symbol,
                "side": plan.direction,
                "entry": float(out.get("mid_price") or mid),
                "sl": float(plan.sl),
                "tp": float(plan.tp),
                "notional_usd": float(plan_notional),
                "size": float(out.get("size") or 0.0),
                "mode": "live",
            })

            # Persist state for exit management
            st = PositionState(
                in_position=True,
                symbol=cfg.symbol,
                side=plan.direction,
                size=float(out.get("size") or 0.0),
                entry_px=float(out.get("mid_price") or mid),
                sl_px=float(plan.sl),
                tp_px=float(plan.tp),
                opened_ts_utc=now_utc_ts(),
                last_oid=None,
            )
            save_state(st, path=state_path)
            print("STATE | saved:", st)

        time.sleep(1)


if __name__ == "__main__":
    main()
