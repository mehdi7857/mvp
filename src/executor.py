from __future__ import annotations

from dataclasses import replace
from typing import Optional

from loguru import logger

from .models import Snapshot, PositionState, Side
from .strategy import StrategyDecision
from .state import save_position


class DryRunExecutor:
    """
    Simulated executor (no real trading).
    Keeps a minimal PositionState compatible with current models.py.
    """

    def __init__(self, notional_usd: float = 1000.0, state_path: str = "configs/state.json") -> None:
        self.notional_usd = float(notional_usd)
        self.state_path = state_path
        self.position: Optional[PositionState] = None

    def current_side(self) -> Optional[Side]:
        if self.position is None:
            return None
        return self.position.side if getattr(self.position, "is_open", False) else None

    def _persist(self) -> None:
        # state.py supports path=...
        save_position(self.position, path=self.state_path)

    def open_position(self, snap: Snapshot, side: Side) -> str:
        # IMPORTANT: keep only fields that PositionState surely accepts
        self.position = PositionState(
            coin=snap.coin,
            side=side,
            is_open=True,
            opened_at_ms=int(snap.time),
        )
        self._persist()
        return (
            f"OPENED | coin={snap.coin} side={side} "
            f"premium={snap.premium:+.6f} funding={snap.fundingRate:+.6f}"
        )

    def close_position(self, snap: Snapshot) -> str:
        if self.position is None or not getattr(self.position, "is_open", False):
            return "CLOSE_SKIPPED | no open position"

        self.position = replace(self.position, is_open=False)
        self._persist()
        return f"CLOSED | coin={snap.coin} side={self.position.side}"

    def on_decision(self, snap: Snapshot, d: StrategyDecision) -> str:
        if d.action == "OPEN":
            if self.current_side() is not None:
                return "OPEN_SKIPPED | already in position"
            if d.side is None:
                return "OPEN_SKIPPED | missing side"
            return self.open_position(snap, d.side)

        if d.action == "CLOSE":
            if self.current_side() is None:
                return "CLOSE_SKIPPED | already flat"
            return self.close_position(snap)

        return "HOLD | no action"
