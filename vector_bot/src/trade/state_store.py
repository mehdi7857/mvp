# src/trade/state_store.py
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional


DEFAULT_STATE_PATH = os.path.join("logs", "state.json")


@dataclass
class PositionState:
    in_position: bool = False
    symbol: str = ""
    side: str = ""          # "LONG" or "SHORT"
    size: float = 0.0       # base size (e.g., BTC)
    entry_px: float = 0.0
    sl_px: float = 0.0
    tp_px: float = 0.0
    opened_ts_utc: float = 0.0
    last_oid: Optional[int] = None


def load_state(path: str = DEFAULT_STATE_PATH) -> PositionState:
    if not os.path.exists(path):
        return PositionState()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return PositionState()
        # tolerate missing/extra fields
        ps = PositionState()
        for k, v in raw.items():
            if hasattr(ps, k):
                setattr(ps, k, v)
        return ps
    except Exception:
        return PositionState()


def save_state(state: PositionState, path: str = DEFAULT_STATE_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    obj: Dict[str, Any] = asdict(state)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def clear_state(path: str = DEFAULT_STATE_PATH) -> None:
    save_state(PositionState(), path=path)


def now_utc_ts() -> float:
    return float(time.time())
