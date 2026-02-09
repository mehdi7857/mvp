from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    # Optional dependency (already in requirements.txt). We keep it optional so
    # this module doesn't hard-crash if someone runs without the venv.
    from dotenv import load_dotenv, dotenv_values  # type: ignore
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]
    dotenv_values = None  # type: ignore[assignment]


_ADDR_RE = re.compile(r"^0x[0-9a-f]{40}$", re.IGNORECASE)
_PK_RE = re.compile(r"^(0x)?[0-9a-f]{64}$", re.IGNORECASE)


def _repo_root() -> Path:
    # src/hl_keys.py -> repo root is ../
    return Path(__file__).resolve().parents[1]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def _parse_simple_kv(text: str) -> Dict[str, str]:
    """
    Parses simple KEY=VALUE lines (dotenv-like). Ignores comments/blank lines.
    """
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def _sanitize(raw: str) -> str:
    v = (raw or "").strip().strip('"').strip("'")
    return v.replace("\r", "").replace("\n", "").replace("\t", "").replace(" ", "")


def _normalize_addr(raw: str) -> str:
    v = _sanitize(raw)
    if not v:
        return ""
    if re.fullmatch(r"[0-9a-fA-F]{40}", v):
        v = "0x" + v
    if v[:2].lower() == "0x":
        v = "0x" + v[2:]
    return v


def _normalize_pk(raw: str) -> str:
    v = _sanitize(raw)
    if not v:
        return ""
    body = v[2:] if v[:2].lower() == "0x" else v
    return "0x" + body


def _is_valid_addr(addr: str) -> bool:
    return bool(_ADDR_RE.fullmatch(addr or ""))


def _is_valid_pk(pk: str) -> bool:
    # Accept 0x-prefixed or not, but ensure body is exactly 64 hex chars.
    v = _sanitize(pk)
    return bool(_PK_RE.fullmatch(v or ""))


def _maybe_load_dotenv_file(path: Path) -> None:
    if load_dotenv is None:
        return
    if path.exists():
        # Do NOT override existing process env (lets you set session env vars).
        load_dotenv(dotenv_path=path, override=False)


def _dotenv_values_file(path: Path) -> Dict[str, str]:
    if dotenv_values is None:
        return {}
    if not path.exists():
        return {}
    vals = dotenv_values(path)
    out: Dict[str, str] = {}
    for k, v in vals.items():
        if isinstance(k, str) and isinstance(v, str):
            out[k] = v
    return out


def _find_addr_in_text(text: str) -> str:
    m = re.search(r"0x[0-9a-f]{40}", text, flags=re.IGNORECASE)
    return m.group(0) if m else ""


def _find_pk_in_text(text: str) -> str:
    m = re.search(r"(0x)?[0-9a-f]{64}", text, flags=re.IGNORECASE)
    return m.group(0) if m else ""


def bootstrap_hl_env(repo_root: Optional[Path] = None) -> Dict[str, str]:
    """
    Best-effort:
    - loads .env from repo root
    - optionally reads env.txt in repo root
    - sets HL_ADDRESS / HL_PRIVATE_KEY if missing
    Returns a dict like {"HL_ADDRESS": "source", "HL_PRIVATE_KEY": "source"}.
    """
    root = repo_root or _repo_root()

    sources: Dict[str, str] = {}

    env_path = root / ".env"
    envtxt_path = root / "env.txt"

    _maybe_load_dotenv_file(env_path)

    file_vals = _dotenv_values_file(env_path)

    envtxt_text = ""
    envtxt_vals: Dict[str, str] = {}
    if envtxt_path.exists():
        envtxt_text = _read_text(envtxt_path)
        envtxt_vals = _parse_simple_kv(envtxt_text)

    # Address
    if not os.environ.get("HL_ADDRESS"):
        cand = (
            os.environ.get("HYPERLIQUID_ADDRESS")
            or os.environ.get("ADDRESS")
            or file_vals.get("HL_ADDRESS")
            or file_vals.get("HYPERLIQUID_ADDRESS")
            or file_vals.get("ADDRESS")
            or envtxt_vals.get("HL_ADDRESS")
            or envtxt_vals.get("HYPERLIQUID_ADDRESS")
            or envtxt_vals.get("ADDRESS")
        )

        if not cand and envtxt_text:
            cand = _find_addr_in_text(envtxt_text)

        cand_norm = _normalize_addr(cand or "")
        if cand_norm and _is_valid_addr(cand_norm):
            os.environ["HL_ADDRESS"] = cand_norm
            sources["HL_ADDRESS"] = ".env" if (cand in file_vals.values()) else ("env.txt" if envtxt_text else "env")

    # Private key
    if not (os.environ.get("HL_PRIVATE_KEY") or os.environ.get("HYPERLIQUID_PRIVATE_KEY")):
        cand = (
            file_vals.get("HYPERLIQUID_PRIVATE_KEY")
            or file_vals.get("HL_PRIVATE_KEY")
            or envtxt_vals.get("HYPERLIQUID_PRIVATE_KEY")
            or envtxt_vals.get("HL_PRIVATE_KEY")
        )

        if not cand and envtxt_text:
            cand = _find_pk_in_text(envtxt_text)

        if cand and _is_valid_pk(cand):
            pk_norm = _normalize_pk(cand)
            # Set both names for compatibility.
            os.environ.setdefault("HL_PRIVATE_KEY", pk_norm)
            os.environ.setdefault("HYPERLIQUID_PRIVATE_KEY", pk_norm)
            sources["HL_PRIVATE_KEY"] = ".env" if (cand in file_vals.values()) else ("env.txt" if envtxt_text else "env")

    # If we have a private key but not an address, derive it (best-effort).
    if not os.environ.get("HL_ADDRESS"):
        pk = os.environ.get("HL_PRIVATE_KEY") or os.environ.get("HYPERLIQUID_PRIVATE_KEY") or ""
        if pk and _is_valid_pk(pk):
            try:
                from eth_account import Account  # type: ignore

                acct = Account.from_key(_normalize_pk(pk))
                os.environ["HL_ADDRESS"] = str(acct.address)
                sources.setdefault("HL_ADDRESS", "derived_from_private_key")
            except Exception:
                pass

    return sources


def get_hl_address(arg: Optional[str] = None) -> str:
    """
    Returns a validated EVM address (0x + 40 hex chars).
    If arg is provided, it wins; otherwise it is taken from env/.env/env.txt.
    """
    bootstrap_hl_env()
    addr = _normalize_addr(arg) if arg is not None else _normalize_addr(os.getenv("HL_ADDRESS") or "")
    if not addr:
        raise RuntimeError("Missing HL_ADDRESS (set it in .env or pass as a CLI arg).")
    if not _is_valid_addr(addr):
        raise RuntimeError(
            "Invalid HL_ADDRESS format. Expected 42-char hex like "
            f"'0x0000000000000000000000000000000000000000' (got_len={len(addr)})."  # noqa: E501
        )
    return addr


def get_hl_private_key() -> Tuple[str, str]:
    """
    Returns (private_key, source). private_key is always 0x + 64 hex chars.
    """
    sources = bootstrap_hl_env()

    raw = os.environ.get("HYPERLIQUID_PRIVATE_KEY", "") or os.environ.get("HL_PRIVATE_KEY", "")
    raw = _sanitize(raw)
    if not raw:
        raise RuntimeError("Missing HL_PRIVATE_KEY/HYPERLIQUID_PRIVATE_KEY (set it in .env or env.txt).")
    if not _is_valid_pk(raw):
        raise RuntimeError("Invalid HYPERLIQUID_PRIVATE_KEY format (must be 64 hex chars).")

    pk = _normalize_pk(raw)
    src = sources.get("HL_PRIVATE_KEY") or ("session_env" if ("HYPERLIQUID_PRIVATE_KEY" in os.environ or "HL_PRIVATE_KEY" in os.environ) else "unknown")  # noqa: E501
    return pk, src
