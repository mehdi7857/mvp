from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Literal


# --- Types used across the project ---

Action = Literal["OPEN", "HOLD", "CLOSE"]

Side = Literal[
    "SHORT_PERP_LONG_SPOT",   # short perp, long spot
    "LONG_PERP_SHORT_SPOT",   # long perp, short spot
]


# --- Market snapshot used by strategy/main ---

@dataclass(frozen=True)
class Snapshot:
    coin: str
    fundingRate: Optional[float]
    premium: Optional[float]
    time: int  # ms


# --- Persisted position state used by executor/state ---

@dataclass
class PositionState:
    # required (your loader previously expected these)
    coin: str
    side: Side
    is_open: bool
    opened_at_ms: int

    # optional extras (added for richer logs / PnL bookkeeping)
    entry_premium: float = 0.0
    entry_funding: float = 0.0
    funding_pnl_usd: float = 0.0
