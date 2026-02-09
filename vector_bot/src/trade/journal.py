# src/trade/journal.py
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

JOURNAL_PATH_DEFAULT = os.path.join("logs", "trade_journal.jsonl")

def now_ts() -> float:
    return time.time()

def iso_utc(ts: Optional[float] = None) -> str:
    ts = now_ts() if ts is None else ts
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

def append_event(event: Dict[str, Any], path: str = JOURNAL_PATH_DEFAULT) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "ts": now_ts(),
        "ts_utc": iso_utc(),
        **event,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
