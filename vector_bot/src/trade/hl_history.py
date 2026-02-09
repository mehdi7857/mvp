# src/trade/hl_history.py
from __future__ import annotations

import time
from typing import Any, Dict, List

from hyperliquid.info import Info


class HLHistory:
    def __init__(self, address: str):
        self.info = Info("https://api.hyperliquid.xyz")
        self.info.timeout = 10
        self.address = address

    def user_fills_last_24h(self) -> List[Dict[str, Any]]:
        """
        Returns all fills in the last 24h.
        """
        now_ms = int(time.time() * 1000)
        since_ms = now_ms - 24 * 60 * 60 * 1000

        # HL expects seconds in some SDK versions, ms in others.
        # SDK normalizes internally, so we pass ms safely.
        fills = self.info.user_fills_by_time(
            self.address,
            since_ms,
            now_ms,
        )
        return fills or []

    def user_fills_last_hours(self, hours: float) -> List[Dict[str, Any]]:
        now_ms = int(time.time() * 1000)
        since_ms = now_ms - int(hours * 3600 * 1000)
        fills = self.info.user_fills_by_time(self.address, since_ms, now_ms)
        return fills or []

    def user_state(self) -> Dict[str, Any]:
        """
        Current positions, margin, etc.
        """
        return self.info.user_state(self.address)
