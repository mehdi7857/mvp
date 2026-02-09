# src/trade/exit_manager.py
from __future__ import annotations

from typing import Optional, Tuple

from src.trade.state_store import PositionState


def check_exit(state: PositionState, last_price: float) -> Tuple[bool, Optional[str]]:
    """
    Returns (should_exit, reason)
    """
    if not state.in_position:
        return False, None

    side = (state.side or "").upper()
    if side not in ("LONG", "SHORT"):
        return True, "BAD_STATE_SIDE"

    if side == "LONG":
        if last_price <= float(state.sl_px):
            return True, "HIT_SL"
        if last_price >= float(state.tp_px):
            return True, "HIT_TP"
    else:
        if last_price >= float(state.sl_px):
            return True, "HIT_SL"
        if last_price <= float(state.tp_px):
            return True, "HIT_TP"

    return False, None
