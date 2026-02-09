from dataclasses import dataclass
from typing import List, Literal, Optional, Dict, Any

Direction = Literal["LONG", "SHORT"]
Mode = Literal["REACTIONAL", "BREAKOUT"]

@dataclass
class TradePlan:
    symbol: str
    mode: Mode
    direction: Direction
    entry: float
    sl: float
    tp: float
    notional_usd: float
    reasons: List[str]
    extra: Optional[Dict[str, Any]] = None
