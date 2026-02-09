from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

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
    Parse simple KEY=VALUE lines (dotenv-like). Ignores comments/blank lines.
    Supports optional leading 'export '.
    """
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
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
    v = _sanitize(pk)
    return bool(_PK_RE.fullmatch(v or ""))


def _find_addr_in_text(text: str) -> str:
    m = re.search(r"0x[0-9a-f]{40}", text, flags=re.IGNORECASE)
    return m.group(0) if m else ""


def _find_pk_in_text(text: str) -> str:
    m = re.search(r"(0x)?[0-9a-f]{64}", text, flags=re.IGNORECASE)
    return m.group(0) if m else ""


def _load_json_file(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def bootstrap_hl_env(repo_root: Optional[Path] = None) -> Dict[str, str]:
    """
    Best-effort key bootstrap for this repo.

    Sources (in order, only if env vars are missing/invalid):
    - process env
    - .env (repo root)
    - .venv.json (repo root)
    - env.txt (repo root)

    Sets (if missing):
    - HYPERLIQUID_PRIVATE_KEY (and HL_PRIVATE_KEY for compatibility)
    - HYPERLIQUID_ADDRESS / HL_ADDRESS (if present, or derived from private key)

    Returns a dict like {"HYPERLIQUID_PRIVATE_KEY": "source", "HYPERLIQUID_ADDRESS": "source"}.
    """
    root = repo_root or _repo_root()
    sources: Dict[str, str] = {}

    # Read files once (best-effort).
    env_path = root / ".env"
    envtxt_path = root / "env.txt"
    dotvenv_path = root / ".venv.json"

    env_vals: Dict[str, str] = {}
    if env_path.exists():
        env_vals = _parse_simple_kv(_read_text(env_path))

    envtxt_text = _read_text(envtxt_path) if envtxt_path.exists() else ""
    envtxt_vals = _parse_simple_kv(envtxt_text) if envtxt_text else {}

    dotvenv = _load_json_file(dotvenv_path)

    # ---- Private key
    def _valid_pk_from_env() -> str:
        for name in ("HYPERLIQUID_PRIVATE_KEY", "HL_PRIVATE_KEY", "PRIVATE_KEY"):
            v = os.environ.get(name, "")
            if v and _is_valid_pk(v):
                return _normalize_pk(v)
        return ""

    pk_norm = _valid_pk_from_env()
    if pk_norm:
        # Canonicalize to HYPERLIQUID_PRIVATE_KEY so downstream code has one stable name.
        os.environ["HYPERLIQUID_PRIVATE_KEY"] = pk_norm
        # Keep a secondary name for compatibility (only override if missing/invalid).
        if not _is_valid_pk(os.environ.get("HL_PRIVATE_KEY", "")):
            os.environ["HL_PRIVATE_KEY"] = pk_norm
        sources.setdefault("HYPERLIQUID_PRIVATE_KEY", "session_env")
    else:
        candidates = [
            (env_vals.get("HYPERLIQUID_PRIVATE_KEY"), ".env"),
            (env_vals.get("HL_PRIVATE_KEY"), ".env"),
            (env_vals.get("PRIVATE_KEY"), ".env"),
            (dotvenv.get("HYPERLIQUID_PRIVATE_KEY"), ".venv.json"),
            (dotvenv.get("private_key"), ".venv.json"),
            (dotvenv.get("PRIVATE_KEY"), ".venv.json"),
            (envtxt_vals.get("HYPERLIQUID_PRIVATE_KEY"), "env.txt"),
            (envtxt_vals.get("HL_PRIVATE_KEY"), "env.txt"),
            (envtxt_vals.get("PRIVATE_KEY"), "env.txt"),
        ]
        for v, src in candidates:
            s = str(v or "").strip()
            if s and _is_valid_pk(s):
                pk_norm = _normalize_pk(s)
                os.environ["HYPERLIQUID_PRIVATE_KEY"] = pk_norm
                if not _is_valid_pk(os.environ.get("HL_PRIVATE_KEY", "")):
                    os.environ["HL_PRIVATE_KEY"] = pk_norm
                sources["HYPERLIQUID_PRIVATE_KEY"] = src
                break
        else:
            if envtxt_text:
                s = _find_pk_in_text(envtxt_text)
                if s and _is_valid_pk(s):
                    pk_norm = _normalize_pk(s)
                    os.environ["HYPERLIQUID_PRIVATE_KEY"] = pk_norm
                    if not _is_valid_pk(os.environ.get("HL_PRIVATE_KEY", "")):
                        os.environ["HL_PRIVATE_KEY"] = pk_norm
                    sources["HYPERLIQUID_PRIVATE_KEY"] = "env.txt"

    # ---- Address (public)
    def _valid_addr_from_env() -> str:
        for name in ("HYPERLIQUID_ADDRESS", "HL_ADDRESS", "ADDRESS"):
            v = os.environ.get(name, "")
            if not v:
                continue
            norm = _normalize_addr(v)
            if _is_valid_addr(norm):
                return norm
        return ""

    addr_norm = _valid_addr_from_env()
    if addr_norm:
        os.environ["HYPERLIQUID_ADDRESS"] = addr_norm
        if not _is_valid_addr(_normalize_addr(os.environ.get("HL_ADDRESS", ""))):
            os.environ["HL_ADDRESS"] = addr_norm
        sources.setdefault("HYPERLIQUID_ADDRESS", "session_env")
    else:
        candidates_addr = [
            (env_vals.get("HYPERLIQUID_ADDRESS"), ".env"),
            (env_vals.get("HL_ADDRESS"), ".env"),
            (env_vals.get("ADDRESS"), ".env"),
            (envtxt_vals.get("HYPERLIQUID_ADDRESS"), "env.txt"),
            (envtxt_vals.get("HL_ADDRESS"), "env.txt"),
            (envtxt_vals.get("ADDRESS"), "env.txt"),
        ]
        for v, src in candidates_addr:
            s = str(v or "").strip()
            norm = _normalize_addr(s)
            if norm and _is_valid_addr(norm):
                os.environ["HYPERLIQUID_ADDRESS"] = norm
                if not _is_valid_addr(_normalize_addr(os.environ.get("HL_ADDRESS", ""))):
                    os.environ["HL_ADDRESS"] = norm
                sources["HYPERLIQUID_ADDRESS"] = src
                addr_norm = norm
                break
        else:
            if envtxt_text:
                s = _find_addr_in_text(envtxt_text)
                norm = _normalize_addr(s)
                if norm and _is_valid_addr(norm):
                    os.environ["HYPERLIQUID_ADDRESS"] = norm
                    if not _is_valid_addr(_normalize_addr(os.environ.get("HL_ADDRESS", ""))):
                        os.environ["HL_ADDRESS"] = norm
                    sources["HYPERLIQUID_ADDRESS"] = "env.txt"
                    addr_norm = norm

    # If we have a private key but no valid address, derive it.
    if not addr_norm:
        pk = os.environ.get("HYPERLIQUID_PRIVATE_KEY") or os.environ.get("HL_PRIVATE_KEY") or ""
        if pk and _is_valid_pk(pk):
            try:
                from eth_account import Account  # type: ignore

                acct = Account.from_key(_normalize_pk(pk))
                addr = str(acct.address)
                os.environ["HYPERLIQUID_ADDRESS"] = addr
                if not _is_valid_addr(_normalize_addr(os.environ.get("HL_ADDRESS", ""))):
                    os.environ["HL_ADDRESS"] = addr
                sources.setdefault("HYPERLIQUID_ADDRESS", "derived_from_private_key")
            except Exception:
                pass

    return sources


def get_hl_private_key() -> Tuple[str, str]:
    """
    Returns (private_key, source). private_key is always 0x + 64 hex chars.
    """
    sources = bootstrap_hl_env()

    raw = (
        os.environ.get("HYPERLIQUID_PRIVATE_KEY")
        or os.environ.get("HL_PRIVATE_KEY")
        or os.environ.get("PRIVATE_KEY")
        or ""
    )
    raw = _sanitize(raw)
    if not raw:
        raise RuntimeError(
            "Missing HYPERLIQUID_PRIVATE_KEY. Set it in one of: "
            "process env, .env, .venv.json, env.txt."
        )
    if not _is_valid_pk(raw):
        raise RuntimeError("Invalid HYPERLIQUID_PRIVATE_KEY format (must be 64 hex chars, with or without 0x).")

    pk = _normalize_pk(raw)
    src = sources.get("HYPERLIQUID_PRIVATE_KEY") or ("session_env" if "HYPERLIQUID_PRIVATE_KEY" in os.environ else "unknown")
    return pk, src


def get_hl_address(arg: Optional[str] = None) -> str:
    """
    Returns a validated EVM address (0x + 40 hex chars).
    If arg is provided, it wins; otherwise it is taken from env/.env/.venv.json/env.txt (or derived from PK).
    """
    bootstrap_hl_env()
    addr = _normalize_addr(arg) if arg is not None else _normalize_addr(os.getenv("HYPERLIQUID_ADDRESS") or os.getenv("HL_ADDRESS") or "")
    if not addr:
        raise RuntimeError("Missing wallet address (set HYPERLIQUID_ADDRESS/HL_ADDRESS, or provide a private key to derive it).")
    if not _is_valid_addr(addr):
        raise RuntimeError(
            "Invalid address format. Expected 42-char hex like "
            f"'0x0000000000000000000000000000000000000000' (got_len={len(addr)})."
        )
    return addr
