from __future__ import annotations

import json
import os
import tempfile
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
        save_position_or_raise(position, path=path)
    except Exception as e:
        logger.error(f"Failed to save state to {path}: {type(e).__name__}: {e}")


def save_position_or_raise(position: Optional[PositionState], path: str = DEFAULT_STATE_PATH) -> None:
    """
    Atomic + durable write:
      1) write to temp file in same dir
      2) flush + fsync
      3) os.replace(temp, path)
    """
    _ensure_parent_dir(path)
    payload = {"position": None if position is None else asdict(position)}
    parent = os.path.dirname(path) or "."
    fd = None
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".state.", suffix=".tmp", dir=parent, text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = None
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


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

        side = pos.get("side")
        if side == "LONG_PERP_SHORT_SPOT":
            pos["side"] = "LONG_PERP"
        elif side == "SHORT_PERP_LONG_SPOT":
            pos["side"] = "SHORT_PERP"

        return PositionState(**pos)

    except Exception as e:
        logger.error(f"Failed to load state from {path}: {type(e).__name__}: {e}")
        return None
