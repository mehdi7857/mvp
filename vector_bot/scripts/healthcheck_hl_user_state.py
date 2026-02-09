import os
import sys
import time
import re
from pathlib import Path
from hyperliquid.info import Info

# When running `python scripts/healthcheck_*.py`, Python's import path starts at
# `scripts/`, so `import src...` fails unless we add the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from src.hl_keys import get_hl_address
except Exception:  # pragma: no cover
    get_hl_address = None  # type: ignore


_HEX_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$", re.IGNORECASE)


def _load_env_from_repo_root() -> None:
    """Best-effort .env loading from the repo root (one level above /scripts)."""
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return

    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env", override=False)


def _normalize_evm_address(addr: str) -> str:
    addr = (addr or "").strip()
    if not addr:
        return ""
    # Normalize "0X" -> "0x" so downstream logging is consistent.
    if addr[:2].lower() == "0x":
        addr = "0x" + addr[2:]
    # Accept 40-hex without 0x prefix.
    if re.fullmatch(r"[a-fA-F0-9]{40}", addr):
        addr = "0x" + addr
    return addr


def _is_valid_evm_address(addr: str) -> bool:
    return bool(_HEX_ADDR_RE.fullmatch(addr or ""))


def main():
    _load_env_from_repo_root()

    try:
        if get_hl_address is not None:
            # arg wins; otherwise load from .env/env.txt
            address = get_hl_address(sys.argv[1] if len(sys.argv) >= 2 else None)
        else:
            address = os.getenv("HL_ADDRESS") or os.getenv("HYPERLIQUID_ADDRESS") or os.getenv("ADDRESS") or ""
            if len(sys.argv) >= 2:
                address = sys.argv[1]

            address = _normalize_evm_address(address)
            if not address:
                raise RuntimeError("Missing HL_ADDRESS")
            if not _is_valid_evm_address(address):
                raise RuntimeError(f"Invalid address len={len(address)}")
    except Exception as e:
        msg = str(e) or type(e).__name__
        print(f"ERR: {msg}. Provide a valid 42-char address (0x + 40 hex).", flush=True)
        sys.exit(2)

    timeout = float(os.getenv("HL_TIMEOUT", "10"))
    start = time.time()

    # Avoid websocket threads; this script is a single HTTP call healthcheck.
    try:
        i = Info("https://api.hyperliquid.xyz", skip_ws=True)
    except TypeError:
        i = Info("https://api.hyperliquid.xyz")
    # hyperliquid SDK uses requests; it reads i.timeout in api.py
    i.timeout = timeout

    print(f"HEALTHCHECK: user_state | address={address} timeout={timeout}s", flush=True)

    try:
        # This triggers /info {type: clearinghouseState, user: address, dex: ...}
        us = i.user_state(address)
        elapsed = time.time() - start
        # keep output compact but informative
        keys = list(us.keys()) if isinstance(us, dict) else []
        print(f"OK: user_state in {elapsed:.2f}s | top_keys={keys[:10]}", flush=True)

        # optional: show a tiny summary if present
        if isinstance(us, dict):
            # common fields (safe if missing)
            margin = us.get("marginSummary", {})
            asset_positions = us.get("assetPositions", [])
            print(f"SUMMARY: assetPositions={len(asset_positions)} marginKeys={list(margin.keys())[:10]}", flush=True)

        sys.exit(0)

    except Exception as e:
        elapsed = time.time() - start
        print(f"ERR: {type(e).__name__} after {elapsed:.2f}s | {e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
