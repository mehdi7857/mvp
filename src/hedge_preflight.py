from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.hl_keys import bootstrap_hl_env


INFO_URL = "https://api.hyperliquid.xyz/info"
_TOKEN_ALIASES: Dict[str, List[str]] = {
    "BTC": ["BTC", "UBTC", "WBTC"],
    "ETH": ["ETH", "WETH"],
}


def _safe_upper(x: Any) -> str:
    return str(x or "").strip().upper()


def _try_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def _token_matches(target: str, actual: str) -> bool:
    t = _safe_upper(target)
    a = _safe_upper(actual)
    if not t or not a:
        return False
    if a == t:
        return True
    for alias in _TOKEN_ALIASES.get(t, []):
        if a == alias:
            return True
    if a in (f"U{t}", f"W{t}"):
        return True
    return False


def _call_info(payload: Dict[str, Any], timeout: float) -> Any:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        INFO_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _detect_perp_coin(meta_and_ctxs: Any, coin: str) -> bool:
    if not isinstance(meta_and_ctxs, list) or not meta_and_ctxs:
        return False
    meta = meta_and_ctxs[0] if isinstance(meta_and_ctxs[0], dict) else {}
    universe = meta.get("universe", []) if isinstance(meta, dict) else []
    wanted = _safe_upper(coin)
    for u in universe:
        if not isinstance(u, dict):
            continue
        if _safe_upper(u.get("name")) == wanted:
            return True
    return False


def _find_spot_pair_candidates(spot_meta: Any, base: str, quote: str) -> List[str]:
    out: List[str] = []
    base_u = _safe_upper(base)
    quote_u = _safe_upper(quote)

    if not isinstance(spot_meta, dict):
        return out

    token_names: Dict[int, str] = {}
    for i, tok in enumerate(spot_meta.get("tokens", [])):
        if isinstance(tok, dict):
            nm = _safe_upper(tok.get("name") or tok.get("coin") or tok.get("symbol"))
            if nm:
                token_names[i] = nm

    for u in spot_meta.get("universe", []):
        if not isinstance(u, dict):
            continue
        nm = _safe_upper(u.get("name") or u.get("coin") or u.get("symbol"))
        if quote_u in nm and (
            base_u in nm
            or any(alias in nm for alias in _TOKEN_ALIASES.get(base_u, []))
            or f"U{base_u}" in nm
            or f"W{base_u}" in nm
        ):
            out.append(nm)
            continue

        toks = u.get("tokens") or u.get("tokenIds") or []
        if isinstance(toks, list) and len(toks) >= 2:
            names: List[str] = []
            for t in toks[:2]:
                idx = None
                try:
                    idx = int(t)
                except Exception:
                    idx = None
                if idx is not None and idx in token_names:
                    names.append(token_names[idx])
            if quote_u in names and any(_token_matches(base_u, n) for n in names):
                out.append(f"{base_u}/{quote_u} (tokens={names})")

    if not out:
        raw = _safe_upper(json.dumps(spot_meta, separators=(",", ":")))
        probe = f"{base_u}/{quote_u}"
        if probe in raw:
            out.append(probe)

    return sorted(set(out))


def _extract_balances(spot_state: Any) -> Dict[str, float]:
    balances: Dict[str, float] = {}
    if not isinstance(spot_state, dict):
        return balances

    rows = spot_state.get("balances")
    if not isinstance(rows, list):
        return balances

    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _safe_upper(row.get("coin") or row.get("token") or row.get("name") or row.get("symbol"))
        if not name:
            continue
        amt = (
            _try_float(row.get("total"))
            or _try_float(row.get("balance"))
            or _try_float(row.get("sz"))
            or _try_float(row.get("amount"))
            or 0.0
        )
        balances[name] = float(amt)
    return balances


@dataclass(frozen=True)
class HedgePreflightResult:
    coin: str
    quote: str
    address_present: bool
    perp_market_exists: bool
    spot_pair_exists: bool
    spot_pair_candidates: List[str]
    spot_state_ok: bool
    quote_balance: float
    base_balance: float
    has_borrow_signals: bool
    carry_positive_status: str
    carry_positive_reason: str
    carry_negative_status: str
    carry_negative_reason: str
    key_sources: Dict[str, str]

    @property
    def market_hedgeable(self) -> bool:
        return self.perp_market_exists and self.spot_pair_exists


def run_hedge_preflight(coin: str, quote: str = "USDC", timeout: float = 12.0) -> HedgePreflightResult:
    base = _safe_upper(coin)
    q = _safe_upper(quote)

    key_sources = bootstrap_hl_env()
    address = os.environ.get("HYPERLIQUID_ADDRESS") or os.environ.get("HL_ADDRESS") or ""

    meta_and_ctxs = _call_info({"type": "metaAndAssetCtxs"}, timeout=timeout)
    spot_meta = _call_info({"type": "spotMeta"}, timeout=timeout)

    perp_ok = _detect_perp_coin(meta_and_ctxs, base)
    spot_pairs = _find_spot_pair_candidates(spot_meta, base, q)
    spot_ok = len(spot_pairs) > 0

    spot_state: Dict[str, Any] = {}
    spot_state_ok = False
    balances: Dict[str, float] = {}
    if address:
        try:
            maybe = _call_info({"type": "spotClearinghouseState", "user": address}, timeout=timeout)
            if isinstance(maybe, dict):
                spot_state = maybe
                spot_state_ok = True
                balances = _extract_balances(spot_state)
        except Exception:
            # Spot account state is optional for market feasibility.
            spot_state_ok = False

    base_bal = balances.get(base, 0.0)
    quote_bal = balances.get(q, 0.0)
    spot_state_blob = json.dumps(spot_state, separators=(",", ":")).lower()
    has_borrow_signals = any(k in spot_state_blob for k in ("borrow", "debt", "liabil", "margin"))

    carry_a = "NOT_FEASIBLE"
    carry_a_reason: List[str] = []
    if perp_ok and spot_ok:
        if spot_state_ok and quote_bal <= 0:
            carry_a = "CONDITIONAL"
            carry_a_reason.append(f"need {q} balance to buy spot")
        else:
            carry_a = "FEASIBLE"
            carry_a_reason.append("short perp + long spot is structurally supported")
    else:
        if not perp_ok:
            carry_a_reason.append("perp coin missing")
        if not spot_ok:
            carry_a_reason.append("spot pair missing")

    carry_b = "NOT_FEASIBLE"
    carry_b_reason: List[str] = []
    if perp_ok and spot_ok:
        if has_borrow_signals:
            carry_b = "CONDITIONAL"
            carry_b_reason.append("possible if spot borrow/margin is truly enabled")
        elif base_bal > 0:
            carry_b = "CONDITIONAL"
            carry_b_reason.append(f"can only sell existing {base} inventory")
        else:
            carry_b_reason.append("no borrow signals and no base inventory for spot short")
    else:
        if not perp_ok:
            carry_b_reason.append("perp coin missing")
        if not spot_ok:
            carry_b_reason.append("spot pair missing")

    return HedgePreflightResult(
        coin=base,
        quote=q,
        address_present=bool(address),
        perp_market_exists=perp_ok,
        spot_pair_exists=spot_ok,
        spot_pair_candidates=spot_pairs,
        spot_state_ok=spot_state_ok,
        quote_balance=quote_bal,
        base_balance=base_bal,
        has_borrow_signals=has_borrow_signals,
        carry_positive_status=carry_a,
        carry_positive_reason="; ".join(carry_a_reason),
        carry_negative_status=carry_b,
        carry_negative_reason="; ".join(carry_b_reason),
        key_sources=key_sources,
    )
