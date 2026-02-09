print("LIVE_MAIN: started")

from src.trade.config_live import LiveConfig
from src.trade.permission_layer import permission_layer
from src.trade.execution_engine import build_plan

from src.broker.paper_broker import PaperBroker
from src.broker.hl_broker import HyperliquidBroker

from src.market_data import get_ohlc, resolve_coin_for_hyperliquid
from src.trade.indicators import atr_wilder
from src.trade.regime import compute_atr_ratio_regime
from src.trade.bias_adapter import get_htf_bias

from src.trade.derivatives_hl import fetch_meta_and_ctx, extract_derivatives_for_coin
from src.trade.triggers import (
    rejection_candle,
    breakout_trigger,
    touched_level,
    range_high_low,
)


def run_once():
    cfg = LiveConfig()

    # ---------- 15m (execution TF) ----------
    candles_15m = get_ohlc(cfg.symbol, "15m", limit=320, validate_coin=True)
    last_candle_15m = candles_15m[-1]
    last_close_15m = float(last_candle_15m["c"])
    atr_15m = atr_wilder(candles_15m, length=14)

    reg = compute_atr_ratio_regime(candles_15m)
    regime = reg["regime"]
    ratio = reg.get("ratio")

    # ---------- 4h (HTF structure for C) ----------
    candles_4h = get_ohlc(cfg.symbol, "4h", limit=260, validate_coin=True)
    htf_hi, htf_lo = range_high_low(candles_4h, lookback=64)  # ✅ C: range edges included

    # --- Near-edge alert (LOG ONLY, not a trigger) ---
    EDGE_PCT = 0.003  # 0.3% of price; tune later (0.2%–0.6% typical)
    near_hi = abs(last_close_15m - float(htf_hi)) <= (EDGE_PCT * last_close_15m)
    near_lo = abs(last_close_15m - float(htf_lo)) <= (EDGE_PCT * last_close_15m)

    if near_hi or near_lo:
        side = "HTF_HI" if near_hi else "HTF_LO"
        dist = abs(last_close_15m - (float(htf_hi) if near_hi else float(htf_lo)))
        print(f"ALERT | NEAR_EDGE side={side} dist={dist:.2f} edge_pct={EDGE_PCT}")

    # ---------- HTF Bias (Kijun 52 by your spec) ----------
    bias, bias_dbg = get_htf_bias(
        cfg.symbol,
        tf="4h",
        limit=220,
        kijun_len=cfg.KIJUN_LEN_4H
    )

    # Extract kijun debug values
    kijun_level = None
    kijun_slope = None
    htf_close = None
    if isinstance(bias_dbg, dict):
        try:
            kijun_level = float(bias_dbg.get("kijun_now"))
        except Exception:
            kijun_level = None
        try:
            kijun_slope = float(bias_dbg.get("kijun_slope"))
        except Exception:
            kijun_slope = None
        try:
            htf_close = float(bias_dbg.get("close"))
        except Exception:
            htf_close = None

    # ---------- Option B: allow flat kijun bias when volatility is "awake" ----------
    # If bias is NEUTRAL because slope=0, permit direction by close vs kijun,
    # but only when regime is not compression and ratio >= threshold.
    allow_flat = getattr(cfg, "ALLOW_FLAT_KIJUN_BIAS", False)
    min_ratio_flat = getattr(cfg, "MIN_RATIO_FOR_FLAT_BIAS", 0.85)

    if allow_flat and bias == "NEUTRAL":
        if (kijun_slope == 0.0) and (kijun_level is not None) and (htf_close is not None):
            if (regime != "COMPRESSION") and (ratio is not None) and (float(ratio) >= float(min_ratio_flat)):
                if htf_close > kijun_level:
                    bias = "BULL"
                elif htf_close < kijun_level:
                    bias = "BEAR"

    # ---------- Derivatives -> Permission layer ----------
    meta_and_ctx = fetch_meta_and_ctx()
    coin = resolve_coin_for_hyperliquid(cfg.symbol)
    deriv = extract_derivatives_for_coin(meta_and_ctx, coin)
    perm = permission_layer(deriv, regime)

    # ---------- Determine direction from bias ----------
    direction = "LONG" if bias == "BULL" else ("SHORT" if bias == "BEAR" else "NONE")

    # ---------- Triggers ----------
    tol = 0.25 * atr_15m  # touch tolerance around levels

    # ✅ C: Reactional levels = Kijun + HTF range edge
    levels = []
    if kijun_level is not None:
        levels.append(("kijun", kijun_level))
    if direction == "LONG":
        levels.append(("htf_lo", htf_lo))
    elif direction == "SHORT":
        levels.append(("htf_hi", htf_hi))

    # --- diagnostics: distance to levels ---
    distances = []
    for name, lvl in levels:
        distances.append(
            (
                name,
                float(lvl),
                round(last_close_15m - float(lvl), 2),
                round(abs(last_close_15m - float(lvl)), 2),
            )
        )
    print(f"DBG | level_distances(name,level,delta,abs_delta)={distances} tol={round(tol,2)}")

    touch_ok = False
    touched_name = None
    touched_level_price = None
    for name, lvl in levels:
        if touched_level(last_close_15m, float(lvl), tol):
            touch_ok = True
            touched_name = name
            touched_level_price = float(lvl)
            break

    # Candle structure for rejection diagnostics
    c = last_candle_15m
    o, h, l, cl = float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"])
    body = abs(cl - o)
    upper = h - max(o, cl)
    lower = min(o, cl) - l
    print(f"DBG | candle o={o:.2f} h={h:.2f} l={l:.2f} c={cl:.2f} body={body:.2f} upper_wick={upper:.2f} lower_wick={lower:.2f}")

    reject_ok = False
    if direction in ("LONG", "SHORT"):
        reject_ok = rejection_candle(last_candle_15m, direction=direction, wick_ratio=0.55)

    # Breakout trigger (HTF-aligned): break & close beyond 4h range edge
    br = None
    if last_close_15m > htf_hi:
        br = "UP"
    elif last_close_15m < htf_lo:
        br = "DOWN"

    breakout_triggered = (br == "UP" and bias == "BULL") or (br == "DOWN" and bias == "BEAR")

    if breakout_triggered and regime not in ("TRANSITION", "EXPANSION"):
        print("HOLD | breakout_triggered but regime not allowed")
        return

    # --- NO-TOUCH-ZONE guard ---
    abs_deltas = [abs(last_close_15m - float(lvl)) for _, lvl in levels]
    min_abs_delta = min(abs_deltas) if abs_deltas else None

    FAR_MULT = 3.0  # >=3x tol means we are not "reacting", we are drifting
    if (min_abs_delta is not None) and (min_abs_delta > FAR_MULT * tol):
        print(f"HOLD | NO-TOUCH-ZONE min_abs_delta={min_abs_delta:.2f} tol={tol:.2f} FAR_MULT={FAR_MULT}")
        # In this zone, only breakouts are allowed to trigger a plan.
        if not breakout_triggered:
            print("HOLD | NO-TOUCH-ZONE and no breakout trigger")
            return
    print(f"SUMMARY | regime={regime} bias={bias} dir={direction} touch={touch_ok} reject={reject_ok} breakout={br} allowed={perm.get('allowed')}")

    # Placeholder until you add real CVD / taker delta feed
    cvd_ok = True

    # ---------- Debug logs (include HTF range edges to confirm C) ----------
    print(f"DBG | close15={last_close_15m:.2f} atr15={atr_15m:.4f} regime={regime} ratio={ratio} bias={bias} dir={direction}")
    print(f"DBG | deriv={deriv}")
    print(f"DBG | perm_reasons={perm.get('reasons')}")

    print(f"DBG | kijun={kijun_level} slope={kijun_slope} htf_close={htf_close}")
    print(f"DBG | htf_hi={htf_hi:.2f} htf_lo={htf_lo:.2f} tol={tol:.2f}")
    print(f"DBG | levels={[(n, round(float(v), 2)) for n, v in levels]} touched={touched_name} touched_lvl={touched_level_price} touch_ok={touch_ok} reject_ok={reject_ok}")
    print(f"DBG | breakout={br} breakout_ok={breakout_triggered}")

    # ---------- Reactional trigger rules ----------
    # Reactional requires touch + rejection in RANGE/TRANSITION.
    # If far from levels, only allow breakout in TRANSITION/EXPANSION when triggered.
    if regime in ("RANGE", "TRANSITION"):
        if not (touch_ok and reject_ok):
            if not (regime in ("TRANSITION", "EXPANSION") and breakout_triggered):
                if not touch_ok:
                    print("HOLD | NO-TOUCH (not near levels)")
                print("HOLD | Reactional trigger not satisfied (touch+rejection) and no valid breakout")
                return

    # ---------- Build plan ----------
    plan = build_plan(
        cfg=cfg,
        perm=perm,
        bias=bias,
        last_close_15m=last_close_15m,
        atr_15m=atr_15m,
        breakout_triggered=breakout_triggered,
        cvd_ok=cvd_ok
    )

    if plan is None:
        print("HOLD | No plan (permission/bias/mode failed)")
        return

    # ---------- Broker routing ----------
    if cfg.SAFE_MODE or (not cfg.ENABLE_LIVE):
        PaperBroker().execute(plan, cfg)
        return

    # Live mode: enforce HL minimum notional ($10) + cap by MAX_NOTIONAL_USD
    min_notional = getattr(cfg, "MIN_NOTIONAL_USD", 10.0)
    notional = max(float(plan.notional_usd), float(min_notional))
    notional = min(notional, float(cfg.MAX_NOTIONAL_USD))

    side = "BUY" if plan.direction == "LONG" else "SELL"

    broker = HyperliquidBroker()
    out = broker.execute_test_trade(
        cfg=cfg,
        side=side,
        notional_usd=notional,
        flatten=False  # real bot should manage exits separately (SL/TP manager)
    )
    print("LIVE_EXEC_RESULT:", out)


if __name__ == "__main__":
    run_once()
