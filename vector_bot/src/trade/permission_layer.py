from typing import Dict, Any

# Hard requirements for HL-only data (safe + feasible)
HARD_REQUIRED = ["open_interest", "funding", "basis"]

# Soft requirements (logged; can be enforced later when you add sources)
SOFT_REQUIRED = ["long_short_ratio", "taker_buy_sell", "oi_over_mcap"]

def permission_layer(deriv: Dict[str, Any], regime: str) -> Dict[str, Any]:
    """
    Permission Layer (YES/NO).
    - HARD_REQUIRED must exist (HL-native).
    - SOFT_REQUIRED are warnings for now (since HL ctx doesn't provide them).
    If you later add sources, you can promote SOFT -> HARD.
    """
    reasons = []
    allowed = True

    hard_missing = [k for k in HARD_REQUIRED if deriv.get(k) is None]
    if hard_missing:
        allowed = False
        reasons.append(f"PERM_NO:MISSING_HARD_DERIV:{','.join(hard_missing)}")

    soft_missing = [k for k in SOFT_REQUIRED if deriv.get(k) is None]
    if soft_missing:
        reasons.append(f"PERM_WARN:MISSING_SOFT_DERIV:{','.join(soft_missing)}")

    if regime not in ("COMPRESSION", "EXPANSION", "TRANSITION", "RANGE"):
        allowed = False
        reasons.append(f"PERM_NO:BAD_REGIME:{regime}")

    return {"trade_allowed": allowed, "reasons": reasons, "regime": regime}
