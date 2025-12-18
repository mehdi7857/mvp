from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Optional

from loguru import logger

from .models import PositionState


DEFAULT_STATE_PATH = "configs/state.json"


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def save_position(position: Optional[PositionState], path: str = DEFAULT_STATE_PATH) -> None:
    """
    Persist position safely.
    Format:
      {"position": null}
      OR
      {"position": {...PositionState fields...}}
    """
    try:
        _ensure_parent_dir(path)
        payload = {"position": None if position is None else asdict(position)}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save state to {path}: {type(e).__name__}: {e}")


def load_position(path: str = DEFAULT_STATE_PATH) -> Optional[PositionState]:
    """
    Load position safely. Returns None if missing/corrupt/old format.
    """
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        pos = data.get("position", None)
        if pos is None:
            return None

        # Old/partial formats -> fail gracefully to FLAT
        required = {"coin", "side", "is_open", "opened_at_ms"}
        if not isinstance(pos, dict) or not required.issubset(pos.keys()):
            logger.warning(f"State file format invalid/old -> FLAT | path={path}")
            return None

        return PositionState(**pos)

    except Exception as e:
        logger.error(f"Failed to load state from {path}: {type(e).__name__}: {e}")
        return None
