import httpx
from typing import Any, Dict


class HyperliquidPublic:
    def __init__(self, timeout: float = 10.0):
        self._client = httpx.Client(timeout=timeout)

    def info(self, payload: Dict[str, Any]) -> Any:
        r = self._client.post("https://api.hyperliquid.xyz/info", json=payload)
        r.raise_for_status()
        return r.json()

    def funding_history(self, coin: str, start_ms: int, end_ms: int) -> Any:
        payload = {
            "type": "fundingHistory",
            "coin": coin,
            "startTime": int(start_ms),
            "endTime": int(end_ms),
        }
        return self.info(payload)

    def meta_and_asset_ctxs(self) -> Any:
        # Returns [meta, assetCtxs]
        return self.info({"type": "metaAndAssetCtxs"})

    def close(self):
        self._client.close()
