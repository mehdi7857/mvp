from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Literal


# --- Types used across the project ---

Action = Literal["OPEN", "HOLD", "CLOSE"]

Side = Literal[
    "SHORT_PERP",
    "LONG_PERP",
    "SHORT_PERP_LONG_SPOT",   # deprecated legacy value
    "LONG_PERP_SHORT_SPOT",   # deprecated legacy value
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
    # optional (for sync/diagnostics)
    size: Optional[float] = None
    entry_px: Optional[float] = None


