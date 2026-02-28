from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional, Tuple

from loguru import logger

from src.config import Config
from src.exchanges import HyperliquidPublic
from src.hyperliquid_trade_client import HyperliquidTradeClient


STATE_PATH = "configs/price_action_state.json"


@dataclass
class BreakoutState:
    side: Optional[str] = None  # LONG / SHORT / None
    entry_px: Optional[float] = None
    opened_at_ms: Optional[int] = None
    last_trade_ms: Optional[int] = None


def now_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_state(path: str = STATE_PATH) -> BreakoutState:
    try:
        if not os.path.exists(path):
            return BreakoutState()
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        s = raw.get("state", {}) if isinstance(raw, dict) else {}
        return BreakoutState(
            side=s.get("side"),
            entry_px=s.get("entry_px"),
            opened_at_ms=s.get("opened_at_ms"),
            last_trade_ms=s.get("last_trade_ms"),
        )
    except Exception as e:
        logger.warning(f"PRICE_STATE_LOAD_FAILED | err={e!r}")
        return BreakoutState()


def save_state(state: BreakoutState, path: str = STATE_PATH) -> None:
    try:
        _atomic_write_json(path, {"state": asdict(state)})
    except Exception as e:
        logger.error(f"PRICE_STATE_SAVE_FAILED | err={e!r}")


def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def get_mark_price(public: HyperliquidPublic, coin: str) -> Tuple[Optional[float], Dict[str, Any]]:
    data = public.meta_and_asset_ctxs()
    if not isinstance(data, list) or len(data) < 2:
        return None, {}
    meta, ctxs = data[0], data[1]
    if not isinstance(meta, dict):
        return None, {}
    uni = meta.get("universe") or []
    idx = None
    for i, row in enumerate(uni):
        if isinstance(row, dict) and str(row.get("name", "")).upper() == coin.upper():
            idx = i
            break
    if idx is None or idx >= len(ctxs):
        return None, {}
    ctx = ctxs[idx] if isinstance(ctxs[idx], dict) else {}
    # Try common keys.
    px = (
        _safe_float(ctx.get("markPx"))
        or _safe_float(ctx.get("midPx"))
        or _safe_float(ctx.get("oraclePx"))
        or _safe_float(ctx.get("lastPx"))
    )
    return px, ctx


def can_trade(now_ms: int, last_trade_ms: Optional[int], cooldown_sec: int) -> bool:
    if last_trade_ms is None:
        return True
    return (now_ms - int(last_trade_ms)) >= cooldown_sec * 1000


def close_position(
    client: HyperliquidTradeClient,
    coin: str,
    side: str,
    notional_usd: float,
) -> Tuple[bool, str]:
    order_side = "SELL" if side == "LONG" else "BUY"
    try:
        res = client.place_perp_order(
            coin=coin,
            side=order_side,
            notional_usd=notional_usd,
            reduce_only=True,
        )
        ok = bool(getattr(res, "ok", False)) and bool(getattr(res, "verified", False))
        return ok, str(getattr(res, "verify_reason", "unknown"))
    except Exception as e:
        return False, repr(e)


def open_position(
    client: HyperliquidTradeClient,
    coin: str,
    side: str,
    notional_usd: float,
) -> Tuple[bool, str]:
    order_side = "BUY" if side == "LONG" else "SELL"
    try:
        res = client.place_perp_order(
            coin=coin,
            side=order_side,
            notional_usd=notional_usd,
            reduce_only=False,
        )
        ok = bool(getattr(res, "ok", False)) and bool(getattr(res, "verified", False))
        return ok, str(getattr(res, "verify_reason", "unknown"))
    except Exception as e:
        return False, repr(e)


def main() -> None:
    cfg_path = os.getenv("CONFIG_PATH", "config.yaml")
    cfg = Config.load(cfg_path)
    get = cfg.get

    coin = str(os.getenv("PA_COIN", get("price_action", "COIN", "BTC"))).upper()
    poll_sec = int(os.getenv("PA_POLL_SEC", get("price_action", "POLL_SEC", 10)))
    lookback_min = int(os.getenv("PA_LOOKBACK_MIN", get("price_action", "LOOKBACK_MIN", 180)))
    breakout_buffer_pct = float(
        os.getenv("PA_BREAKOUT_BUFFER_PCT", get("price_action", "BREAKOUT_BUFFER_PCT", 0.001))
    )
    tp_pct = float(os.getenv("PA_TP_PCT", get("price_action", "TP_PCT", 0.01)))
    sl_pct = float(os.getenv("PA_SL_PCT", get("price_action", "SL_PCT", 0.005)))
    cooldown_sec = int(os.getenv("PA_COOLDOWN_SEC", get("price_action", "COOLDOWN_SEC", 900)))
    notional_usd = float(os.getenv("PA_NOTIONAL_USD", get("price_action", "NOTIONAL_USD", 12.0)))
    enable_live = str(os.getenv("PA_ENABLE_LIVE", str(get("price_action", "ENABLE_LIVE", 0)))).strip() in ("1", "true", "True")

    history_len = max(12, int(lookback_min * 60 / max(1, poll_sec)))
    prices: Deque[float] = deque(maxlen=history_len)
    state = load_state()

    logger.info(
        "PRICE_ACTION_CONFIG | "
        f"coin={coin} poll={poll_sec}s lookback_min={lookback_min} "
        f"buffer={breakout_buffer_pct:.4f} tp={tp_pct:.4f} sl={sl_pct:.4f} "
        f"cooldown={cooldown_sec}s notional=${notional_usd:.2f} ENABLE_LIVE={enable_live}"
    )

    public = HyperliquidPublic(timeout=10.0)
    trade_client: Optional[HyperliquidTradeClient] = None
    if enable_live:
        trade_client = HyperliquidTradeClient()

    try:
        while True:
            now_ms = int(time.time() * 1000)
            px, ctx = get_mark_price(public, coin)
            if px is None or px <= 0:
                logger.warning(f"PRICE_ACTION_NO_PRICE | coin={coin}")
                time.sleep(poll_sec)
                continue

            prices.append(px)
            if len(prices) < max(20, history_len // 3):
                time.sleep(poll_sec)
                continue

            hi = max(prices)
            lo = min(prices)
            up_trigger = hi * (1.0 + breakout_buffer_pct)
            dn_trigger = lo * (1.0 - breakout_buffer_pct)

            logger.info(
                "PRICE_ACTION_TICK | "
                f"t={now_iso(now_ms)} coin={coin} px={px:.2f} hi={hi:.2f} lo={lo:.2f} "
                f"up_trigger={up_trigger:.2f} dn_trigger={dn_trigger:.2f} side={state.side}"
            )

            if state.side is None:
                if not can_trade(now_ms, state.last_trade_ms, cooldown_sec):
                    time.sleep(poll_sec)
                    continue

                new_side = None
                if px >= up_trigger:
                    new_side = "LONG"
                elif px <= dn_trigger:
                    new_side = "SHORT"

                if new_side is not None:
                    if not enable_live:
                        logger.warning(
                            f"PRICE_ACTION_SIGNAL | side={new_side} px={px:.2f} action=DRY_RUN_NO_ORDER"
                        )
                        state.last_trade_ms = now_ms
                        save_state(state)
                    else:
                        assert trade_client is not None
                        ok, reason = open_position(trade_client, coin, new_side, notional_usd)
                        if ok:
                            state.side = new_side
                            state.entry_px = px
                            state.opened_at_ms = now_ms
                            state.last_trade_ms = now_ms
                            save_state(state)
                            logger.warning(
                                f"PRICE_ACTION_OPENED | side={new_side} px={px:.2f} notional=${notional_usd:.2f}"
                            )
                        else:
                            logger.error(f"PRICE_ACTION_OPEN_FAILED | side={new_side} reason={reason}")

            else:
                assert state.entry_px is not None
                if state.side == "LONG":
                    hit_tp = px >= state.entry_px * (1.0 + tp_pct)
                    hit_sl = px <= state.entry_px * (1.0 - sl_pct)
                else:
                    hit_tp = px <= state.entry_px * (1.0 - tp_pct)
                    hit_sl = px >= state.entry_px * (1.0 + sl_pct)

                if hit_tp or hit_sl:
                    cause = "TP" if hit_tp else "SL"
                    if not enable_live:
                        logger.warning(
                            f"PRICE_ACTION_EXIT_SIGNAL | side={state.side} cause={cause} px={px:.2f} action=DRY_RUN_NO_ORDER"
                        )
                        state.side = None
                        state.entry_px = None
                        state.opened_at_ms = None
                        state.last_trade_ms = now_ms
                        save_state(state)
                    else:
                        assert trade_client is not None
                        ok, reason = close_position(trade_client, coin, state.side, notional_usd)
                        if ok:
                            logger.warning(
                                f"PRICE_ACTION_CLOSED | prev_side={state.side} cause={cause} px={px:.2f}"
                            )
                            state.side = None
                            state.entry_px = None
                            state.opened_at_ms = None
                            state.last_trade_ms = now_ms
                            save_state(state)
                        else:
                            logger.error(
                                f"PRICE_ACTION_CLOSE_FAILED | side={state.side} cause={cause} reason={reason}"
                            )

            time.sleep(poll_sec)
    except KeyboardInterrupt:
        logger.info("PRICE_ACTION_STOPPED | by_user")
    finally:
        public.close()


if __name__ == "__main__":
    main()

