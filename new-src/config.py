from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BotConfig:
    # polling
    poll_seconds: int = 10
    lookback_hours: int = 24

    # strategy thresholds (example defaults)
    prem_entry: float = 0.000400  # abs(premium) must be >= this to open
    fund_entry: float = 0.000020  # abs(funding) must be >= this to open
    prem_exit: float = 0.000150   # abs(premium) <= this to consider closing
    fund_exit: float = 0.000008   # abs(funding) <= this to consider closing

    # rotate
    rotate_ratio: float = 1.25
    rotate_abs_delta: float = 0.000050

    # safety
    auto_flat_enabled: bool = True
    auto_flat_cooldown_seconds: int = 60

    # networking hardening
    request_timeout_seconds: float = 6.0
    retry_attempts: int = 4
    backoff_base_seconds: float = 0.6  # exponential backoff base
