from typing import Dict, Any, Optional
from .plan_types import TradePlan
from .config_live import LiveConfig

def build_plan(
    cfg: LiveConfig,
    perm: Dict[str, Any],
    bias: str,
    last_close_15m: float,
    atr_15m: float,
    breakout_triggered: bool,
    cvd_ok: bool,
) -> Optional[TradePlan]:
    """
    Execution Engine:
    - Only if Permission YES
    - 4H Bias is Judge (BULL/BEAR only)
    - 15m is Executor
    Locked rules:
      Reactional: SL=0.5*ATR(15m), RR=3, entry on 15m close
      Breakout: entry on break close, RR=3.5, SL=1.5*ATR(15m)
    """
    if not perm.get("trade_allowed", False):
        return None

    if bias not in ("BULL", "BEAR"):
        return None

    direction = "LONG" if bias == "BULL" else "SHORT"
    regime = perm.get("regime", "UNKNOWN")

    # Mode selection per your official framework
    if regime in ("RANGE", "TRANSITION"):
        mode = "REACTIONAL"
    elif regime == "EXPANSION" and breakout_triggered and cvd_ok:
        mode = "BREAKOUT"
    else:
        return None

    entry = float(last_close_15m)  # locked: on close

    if mode == "REACTIONAL":
        sl_dist = cfg.SL_ATR_REACTIONAL * float(atr_15m)
        rr = cfg.RR_REACTIONAL
        reasons = perm.get("reasons", []) + [f"MODE_REACTIONAL:{regime}", f"BIAS_{bias}"]
    else:
        sl_dist = cfg.SL_ATR_BREAKOUT * float(atr_15m)
        rr = cfg.RR_BREAKOUT
        reasons = perm.get("reasons", []) + ["MODE_BREAKOUT:EXPANSION", "TRIGGER_BREAK_CLOSE", "FLOW_CVD_OK", f"BIAS_{bias}"]

    tp_dist = rr * sl_dist

    if direction == "LONG":
        sl = entry - sl_dist
        tp = entry + tp_dist
    else:
        sl = entry + sl_dist
        tp = entry - tp_dist

    return TradePlan(
        symbol=cfg.symbol,
        mode=mode,
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp,
        notional_usd=cfg.MAX_NOTIONAL_USD,
        reasons=reasons,
        extra={"atr_15m": float(atr_15m), "regime": regime}
    )
