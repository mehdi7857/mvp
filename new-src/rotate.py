from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import Side


@dataclass(frozen=True)
class BestPick:
    coin: str
    side: Side
    score: float


def should_rotate(
    current: Optional[BestPick],
    candidate: BestPick,
    ratio: float,
    abs_delta: float,
) -> bool:
    if current is None:
        return True

    if candidate.coin == current.coin and candidate.side == current.side:
        return False

    # Candidate must beat both ratio and absolute delta to rotate
    if current.score <= 0:
        return True

    return (candidate.score >= current.score * ratio) and ((candidate.score - current.score) >= abs_delta)
