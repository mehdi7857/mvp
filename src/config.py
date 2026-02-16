from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


@dataclass(frozen=True)
class Config:
    raw: Dict[str, Any]

    @staticmethod
    def load(path: str = "config.yaml") -> "Config":
        if yaml is None:
            raise RuntimeError("PyYAML is not installed; cannot load YAML config.")
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {p.resolve()}")
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Config root must be a mapping, got: {type(data).__name__}")
        return Config(raw=data)

    def get(self, *keys: str, default: Any = None) -> Any:
        d: Any = self.raw
        for k in keys:
            if not isinstance(d, dict) or k not in d:
                return default
            d = d[k]
        return d

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
