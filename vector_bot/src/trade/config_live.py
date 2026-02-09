from dataclasses import dataclass

@dataclass(frozen=True)
class LiveConfig:
    # execution
    symbol: str = "BTC"
    tf_bias: str = "4h"
    tf_exec: str = "15m"

    # safety gates
    SAFE_MODE: bool = True       # True => never place live orders
    ENABLE_LIVE: bool = False   # must be True to allow live broker

    # risk caps
    RISK_PCT: float = 0.01
    MAX_NOTIONAL_USD: float = 100.0
    MAX_POSITIONS: int = 1

    # locked rules (official framework)
    KIJUN_LEN_4H: int = 52
    ALLOW_FLAT_KIJUN_BIAS: bool = True
    MIN_RATIO_FOR_FLAT_BIAS: float = 0.85

    # Reactional
    SL_ATR_REACTIONAL: float = 0.5
    RR_REACTIONAL: float = 3.0

    # Breakout
    SL_ATR_BREAKOUT: float = 1.5
    RR_BREAKOUT: float = 3.5

    # Exchange constraints
    MIN_NOTIONAL_USD: float = 10.0
