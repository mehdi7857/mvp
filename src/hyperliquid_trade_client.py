from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union
import math

from loguru import logger
from eth_account import Account

from src.hl_keys import get_hl_private_key


def _resolve_hyperliquid_classes():
    try:
        from hyperliquid.info import Info
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants
        return Info, Exchange, constants
    except Exception as e1:  # pragma: no cover - fallback for alt SDK layouts
        try:
            from hyperliquid.api import Info  # type: ignore
            from hyperliquid.exchange import Exchange  # type: ignore
            from hyperliquid.utils import constants  # type: ignore
            return Info, Exchange, constants
        except Exception as e2:
            raise RuntimeError(
                "Failed to import Hyperliquid SDK entrypoints. "
                f"info/exchange import errors: {e1!r}, {e2!r}"
            )


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    raw: Dict[str, Any]
    mid_price: float
    size: float
    verified: bool
    before_position: Optional[Dict[str, Any]]
    after_position: Optional[Dict[str, Any]]
    verify_reason: str
    cloid: Optional[str]


class HyperliquidTradeClient:
    """
    PERP-ONLY trade client for Hyperliquid.

    Loads wallet private key from process env / .env / .venv.json / env.txt
    via src.hl_keys.get_hl_private_key().

    Places *market* perp orders sized by notional_usd using mid price.
    """

    def __init__(self, base_url: Optional[str] = None) -> None:
        Info, Exchange, constants = _resolve_hyperliquid_classes()
        if base_url is None:
            base_url = constants.MAINNET_API_URL

        pk, pk_source = get_hl_private_key()
        self.account = Account.from_key(pk)
        self.address = self.account.address

        self.base_url = base_url
        self.info = Info(base_url, skip_ws=True)
        self.exchange = Exchange(self.account, base_url)

        logger.info(
            "HyperliquidTradeClient initialized | "
            f"base_url={base_url} | address={self.address} | pk_source={pk_source}"
        )

    @staticmethod
    def _normalize_private_key(raw: str) -> str:
        pk = raw.strip()
        if pk.startswith('"') and pk.endswith('"'):
            pk = pk[1:-1].strip()

        body = pk[2:] if pk.lower().startswith("0x") else pk
        is_hex = all(c in "0123456789abcdefABCDEF" for c in body)
        if len(body) == 64 and is_hex:
            return "0x" + body

        raise RuntimeError(
            "Invalid HYPERLIQUID_PRIVATE_KEY format. Expected 64 hex characters "
            "(32 bytes), with or without a 0x prefix."
        )

    @staticmethod
    def _safe_float(x: Any) -> Optional[float]:
        try:
            return float(x)
        except Exception:
            return None

    def _get_mid(self, coin: str) -> float:
        mids = self.info.all_mids()
        m = mids.get(coin)
        if m is None:
            raise RuntimeError(f"Mid price not available for coin={coin}. all_mids keys={list(mids.keys())[:10]}")
        return float(m)

    def _get_sz_decimals(self, coin: str) -> int:
        try:
            name = self.info.name_to_coin[coin]
            asset = self.info.coin_to_asset[name]
            return int(self.info.asset_to_sz_decimals[asset])
        except Exception:
            return 6

    def _parse_positions(self, state: Dict[str, Any]) -> List[Dict[str, Any]]:
        positions = state.get("assetPositions") or state.get("positions") or []
        parsed: List[Dict[str, Any]] = []
        for p in positions:
            if isinstance(p, dict) and isinstance(p.get("position"), dict):
                pos = p["position"]
            elif isinstance(p, dict):
                pos = p
            else:
                continue

            coin = pos.get("coin")
            if not coin:
                continue

            parsed.append(
                {
                    "coin": coin,
                    "szi": self._safe_float(pos.get("szi")),
                    "entry_px": self._safe_float(pos.get("entryPx")),
                    "liq_px": self._safe_float(pos.get("liquidationPx")),
                    "position_value": self._safe_float(pos.get("positionValue")),
                    "margin_used": self._safe_float(pos.get("marginUsed")),
                    "unrealized_pnl": self._safe_float(pos.get("unrealizedPnl")),
                }
            )
        return parsed

    def get_positions(self, coin: Optional[str] = None) -> List[Dict[str, Any]]:
        state = self.info.user_state(self.address)
        if isinstance(state, dict):
            positions = state.get("assetPositions") or state.get("positions") or []
            logger.info(
                f"GET_POSITIONS_RAW | keys={list(state.keys())} positions_len={len(positions)}"
            )
        else:
            logger.warning(f"GET_POSITIONS_RAW | unexpected_state_type={type(state).__name__}")
        parsed = self._parse_positions(state if isinstance(state, dict) else {})
        if coin is None:
            return parsed
        return [p for p in parsed if p.get("coin") == coin]

    def _find_position(self, positions: List[Dict[str, Any]], coin: str) -> Optional[Dict[str, Any]]:
        for p in positions:
            if p.get("coin") == coin:
                return p
        return None

    def cancel_order(self, coin: str, oid: Optional[int] = None, cloid: Optional[str] = None) -> Dict[str, Any]:
        if oid is not None and hasattr(self.exchange, "cancel"):
            return self.exchange.cancel(coin, oid)  # type: ignore
        if cloid is not None and hasattr(self.exchange, "cancel_by_cloid"):
            return self.exchange.cancel_by_cloid(coin, cloid)  # type: ignore
        raise RuntimeError("Exchange SDK does not support cancel/cancel_by_cloid or missing order id.")

    def _response_ok(self, resp: Any) -> bool:
        if isinstance(resp, dict):
            if resp.get("status") in ("err", "error", "rejected", "failed"):
                return False
            if "error" in resp or "err" in resp:
                return False
            statuses = (
                resp.get("response", {})
                .get("data", {})
                .get("statuses")
            )
            if isinstance(statuses, list):
                for st in statuses:
                    if isinstance(st, dict) and st.get("error"):
                        return False
        return True

    def _verify_position_change(
        self,
        coin: str,
        is_buy: bool,
        reduce_only: bool,
        before: Optional[Dict[str, Any]],
        after: Optional[Dict[str, Any]],
    ) -> Tuple[bool, str]:
        before_szi = self._safe_float(before.get("szi") if before else 0.0) or 0.0
        after_szi = self._safe_float(after.get("szi") if after else 0.0) or 0.0
        delta = after_szi - before_szi

        if reduce_only:
            if before_szi == 0.0:
                return False, "reduce_only_with_no_position"
            if abs(after_szi) < abs(before_szi):
                return True, "reduced_position"
            return False, "position_not_reduced"

        if is_buy and delta > 0:
            return True, "increased_long"
        if not is_buy and delta < 0:
            return True, "increased_short"
        return False, "position_not_changed"

    def _log_position(self, tag: str, pos: Optional[Dict[str, Any]]) -> None:
        if not pos:
            logger.info(f"{tag} | position=None")
            return
        notional = pos.get("position_value")
        margin_used = pos.get("margin_used")
        leverage = None
        try:
            if notional is not None and margin_used not in (None, 0):
                leverage = float(notional) / float(margin_used)
        except Exception:
            leverage = None
        logger.info(
            f"{tag} | coin={pos.get('coin')} szi={pos.get('szi')} entry_px={pos.get('entry_px')} "
            f"liq_px={pos.get('liq_px')} notional={notional} margin_used={margin_used} "
            f"leverage={leverage} pnl={pos.get('unrealized_pnl')}"
        )

    def _make_cloid(self, client_order_id: Optional[str]) -> Optional[Any]:
        try:
            from hyperliquid.utils.types import Cloid  # type: ignore
        except Exception:
            return None

        raw = client_order_id
        if not raw:
            raw = "0x" + secrets.token_hex(16)
        return Cloid.from_str(str(raw))

    def place_order(
        self,
        coin: str,
        side: Union[str, bool],
        notional_usd: float,
        reduce_only: bool = False,
        slippage: float = 0.01,
        client_order_id: Optional[str] = None,
    ) -> OrderResult:
        """
        Places a MARKET order on perps sized by USD notional.
        reduce_only=True is used for CLOSE leg.
        """
        if isinstance(side, bool):
            is_buy = side
            side_txt = "BUY" if is_buy else "SELL"
        else:
            side_txt = str(side).upper()
            if side_txt not in ("BUY", "SELL"):
                raise ValueError(f"side must be BUY or SELL, got {side!r}")
            is_buy = side_txt == "BUY"

        mid = self._get_mid(coin)
        if mid <= 0:
            raise RuntimeError(f"Invalid mid price for {coin}: {mid}")

        sz = notional_usd / mid
        sz_decimals = self._get_sz_decimals(coin)
        scale = 10 ** max(sz_decimals, 0)
        sz = math.floor(sz * scale) / scale
        if sz <= 0:
            raise RuntimeError(f"Order size too small after rounding | coin={coin} sz={sz}")

        before_positions = self.get_positions(coin=coin)
        before_pos = self._find_position(before_positions, coin)
        self._log_position("VERIFY_BEFORE", before_pos)

        # For reduce-only CLOSE, always close the full current position size.
        # Closing by notional can leave residual exposure when price moved.
        if reduce_only:
            before_szi = self._safe_float(before_pos.get("szi") if before_pos else 0.0) or 0.0
            if before_szi == 0.0:
                raise RuntimeError(f"reduce_only close requested but no open position for coin={coin}")
            sz = abs(before_szi)
            sz = math.floor(sz * scale) / scale
            if sz <= 0:
                raise RuntimeError(f"Close size too small after rounding | coin={coin} sz={sz}")
            logger.info(f"CLOSE_FULL_SIZE | coin={coin} before_szi={before_szi} sz={sz}")

        cloid = self._make_cloid(client_order_id)
        cloid_txt = str(cloid) if cloid else None
        logger.warning(
            f"LIVE ORDER CALL | {coin} {side_txt} notional=${notional_usd:.2f} "
            f"sz={sz} reduce_only={reduce_only} cloid={cloid_txt}"
        )

        resp: Dict[str, Any] = {}
        if hasattr(self.exchange, "market_open") and not reduce_only:
            resp = self.exchange.market_open(coin, is_buy, sz, slippage=slippage, cloid=cloid)  # type: ignore
        elif hasattr(self.exchange, "market_close") and reduce_only:
            resp = self.exchange.market_close(coin, sz, slippage=slippage, cloid=cloid)  # type: ignore
        elif hasattr(self.exchange, "order"):
            limit_px = None
            if hasattr(self.exchange, "_slippage_price"):
                try:
                    limit_px = self.exchange._slippage_price(coin, is_buy, slippage)  # type: ignore
                except Exception:
                    limit_px = None
            if limit_px is None:
                limit_px = mid * (1 + slippage) if is_buy else mid * (1 - slippage)
            order_type = {"limit": {"tif": "Ioc"}}
            resp = self.exchange.order(
                coin,
                is_buy,
                sz,
                limit_px,
                order_type=order_type,
                reduce_only=reduce_only,
                cloid=cloid,
            )  # type: ignore
        else:
            raise RuntimeError("Your hyperliquid Exchange SDK has no supported order method (market_open/market_close/order).")

        logger.info(f"HL_RESP | coin={coin} raw={resp}")

        ok = self._response_ok(resp)

        after_positions = self.get_positions(coin=coin)
        after_pos = self._find_position(after_positions, coin)
        self._log_position("VERIFY_AFTER", after_pos)

        verified, verify_reason = self._verify_position_change(
            coin=coin,
            is_buy=is_buy,
            reduce_only=reduce_only,
            before=before_pos,
            after=after_pos,
        )

        if verified:
            logger.info(f"VERIFY_OK | coin={coin} reason={verify_reason}")
        else:
            logger.warning(f"VERIFY_FAIL | coin={coin} reason={verify_reason}")
            ok = False

        return OrderResult(
            ok=ok,
            raw=resp if isinstance(resp, dict) else {"resp": resp},
            mid_price=mid,
            size=sz,
            verified=verified,
            before_position=before_pos,
            after_position=after_pos,
            verify_reason=verify_reason,
            cloid=cloid_txt,
        )

    def place_perp_order(
        self,
        coin: str,
        side: Union[str, bool],
        notional_usd: float,
        reduce_only: bool = False,
        slippage: float = 0.01,
        client_order_id: Optional[str] = None,
    ) -> OrderResult:
        return self.place_order(
            coin=coin,
            side=side,
            notional_usd=notional_usd,
            reduce_only=reduce_only,
            slippage=slippage,
            client_order_id=client_order_id,
        )
