"""Strict RBAC (5 roles) with fail-closed boot, zero-downtime dual-token grace window, and full audit logging."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request

from server_ai.server_core import config, database

ROLE_RANK = {"viewer": 0, "driver_phone": 1, "device": 2, "operator": 3, "admin": 4}
WEAK_TOKENS_DENYLIST = {
    "admin",
    "dev",
    "device",
    "test",
    "viewer",
    "operator",
    "password",
    "123456",
    "12345678",
    "root",
    "token",
}

_TOKENS_LOCK = threading.RLock()
_STORE_CACHE: Optional[Dict[str, Any]] = None
_TOKENS_MTIME: float = -1.0
_TOKENS_PATH_CACHED: Optional[Path] = None


def tokens_path() -> Path:
    return config.DATA_DIR / "tokens.json"


def reset_token_cache() -> None:
    global _STORE_CACHE, _TOKENS_MTIME, _TOKENS_PATH_CACHED
    with _TOKENS_LOCK:
        _STORE_CACHE = None
        _TOKENS_MTIME = -1.0
        _TOKENS_PATH_CACHED = None


def token_fingerprint(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()[:12]


def is_weak_token(token: str) -> bool:
    t = (token or "").strip()
    if len(t) < 16:
        return True
    if t.lower() in WEAK_TOKENS_DENYLIST:
        return True
    return False


def _normalize_store(raw: Any) -> Dict[str, Any]:
    """Normalize both legacy flat {tok: role} and v2 {active: {tok: role}, grace: {tok: {role, expires_at}}}."""
    if not isinstance(raw, dict):
        return {"schema_version": "2.0", "active": {}, "grace": {}}

    if "active" in raw and isinstance(raw["active"], dict):
        active = {str(k): str(v) for k, v in raw["active"].items() if k and str(v) in ROLE_RANK}
        grace_raw = raw.get("grace") if isinstance(raw.get("grace"), dict) else {}
        grace: Dict[str, Dict[str, Any]] = {}
        for k, meta in grace_raw.items():
            if not k or not isinstance(meta, dict):
                continue
            r = str(meta.get("role") or "")
            try:
                exp = float(meta.get("expires_at") or 0.0)
            except (TypeError, ValueError):
                exp = 0.0
            if r in ROLE_RANK and exp > 0:
                grace[str(k)] = {"role": r, "expires_at": exp}
        out = {
            "schema_version": str(raw.get("schema_version") or "2.0"),
            "active": active,
            "grace": grace,
        }
        if raw.get("rotated_at"):
            out["rotated_at"] = raw["rotated_at"]
        return out

    # Legacy flat format: {token: role}
    active = {str(k): str(v) for k, v in raw.items() if k and str(v) in ROLE_RANK}
    return {"schema_version": "1.0", "active": active, "grace": {}}


def _load_store() -> Dict[str, Any]:
    global _STORE_CACHE, _TOKENS_MTIME, _TOKENS_PATH_CACHED
    p = tokens_path()
    with _TOKENS_LOCK:
        if not p.exists():
            if config.ENV_PROFILE == "lab":
                _atomic_write_tokens(dict(config.LAB_BOOTSTRAP_TOKENS))
            else:
                return {"schema_version": "2.0", "active": {}, "grace": {}}
        try:
            st = p.stat()
            if _STORE_CACHE is not None and _TOKENS_PATH_CACHED == p and st.st_mtime == _TOKENS_MTIME:
                return {
                    "schema_version": _STORE_CACHE["schema_version"],
                    "active": dict(_STORE_CACHE["active"]),
                    "grace": {k: dict(v) for k, v in _STORE_CACHE["grace"].items()},
                    **({"rotated_at": _STORE_CACHE["rotated_at"]} if "rotated_at" in _STORE_CACHE else {}),
                }
            raw = json.loads(p.read_text(encoding="utf-8"))
            norm = _normalize_store(raw)
            _STORE_CACHE = norm
            _TOKENS_MTIME = st.st_mtime
            _TOKENS_PATH_CACHED = p
            return {
                "schema_version": norm["schema_version"],
                "active": dict(norm["active"]),
                "grace": {k: dict(v) for k, v in norm["grace"].items()},
                **({"rotated_at": norm["rotated_at"]} if "rotated_at" in norm else {}),
            }
        except Exception:
            # NEVER silently fall back to hardcoded bootstrap admin tokens on corrupt file!
            return {"schema_version": "2.0", "active": {}, "grace": {}}


def _atomic_write_store(store: Dict[str, Any]) -> None:
    p = tokens_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    reset_token_cache()


def _atomic_write_tokens(tokens: Dict[str, str]) -> None:
    """Backward-compatible flat writer used by lab bootstrap & legacy callers."""
    p = tokens_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tokens, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    reset_token_cache()


def _load_tokens(now_ts: Optional[float] = None) -> Dict[str, str]:
    """Return all currently valid {token: role} including unexpired grace tokens (zero-downtime rotation)."""
    store = _load_store()
    now_s = time.time() if now_ts is None else float(now_ts)
    merged: Dict[str, str] = {}
    # Load valid unexpired grace tokens first; active tokens take precedence on key match
    for tok, meta in store.get("grace", {}).items():
        if float(meta.get("expires_at") or 0.0) >= now_s:
            merged[tok] = str(meta["role"])
    for tok, role in store.get("active", {}).items():
        merged[tok] = str(role)
    return merged


def validate_boot_tokens() -> Dict[str, str]:
    """Fail-closed startup check (LCK-06): prod MUST have non-empty tokens.json; lab bootstraps."""
    p = tokens_path()
    with _TOKENS_LOCK:
        if not p.exists():
            if config.ENV_PROFILE == "lab":
                _atomic_write_tokens(dict(config.LAB_BOOTSTRAP_TOKENS))
                return _load_tokens()
            raise RuntimeError(
                f"[RBAC STRICT BOOT FAIL] Missing token store at {p}. "
                "Provision tokens.json before starting production server (or set SERVER_ENV_PROFILE=lab for localhost-only dev)."
            )
        tokens = _load_tokens()
        if not tokens:
            raise RuntimeError(f"[RBAC STRICT BOOT FAIL] Token store {p} is empty or all grace tokens expired.")
        return tokens


def inspect_token_store_health(now_ts: Optional[float] = None) -> Dict[str, Any]:
    """Non-secret security audit of tokens.json (never exposes plaintext tokens)."""
    store = _load_store()
    now_s = time.time() if now_ts is None else float(now_ts)
    active = store.get("active", {})
    grace = store.get("grace", {})
    weak_active = [
        {"role": r, "suffix": t[-4:], "fp": token_fingerprint(t), "len": len(t)}
        for t, r in active.items()
        if is_weak_token(t)
    ]
    grace_live = []
    grace_expired = 0
    for t, meta in grace.items():
        exp = float(meta.get("expires_at") or 0.0)
        if exp >= now_s:
            grace_live.append(
                {
                    "role": meta["role"],
                    "suffix": t[-4:],
                    "fp": token_fingerprint(t),
                    "expires_in_s": int(exp - now_s),
                    "weak": is_weak_token(t),
                }
            )
        else:
            grace_expired += 1
    roles_covered = sorted(set(active.values()))
    return {
        "schema_version": store.get("schema_version", "1.0"),
        "active_count": len(active),
        "roles_covered": roles_covered,
        "weak_active_count": len(weak_active),
        "weak_active": weak_active,
        "grace_live_count": len(grace_live),
        "grace_live": grace_live,
        "grace_expired_count": grace_expired,
        "rotation_state": (
            "DUAL_GRACE"
            if grace_live
            else ("HARDENED_PROD" if (len(active) >= 5 and len(weak_active) == 0) else "NEEDS_HARDENING")
        ),
    }


def issue_token(token: str, role: str) -> None:
    if role not in config.ROLES:
        raise ValueError(f"Invalid role: {role}")
    if not token or len(token) < 8:
        raise ValueError("Token must be at least 8 characters")
    with _TOKENS_LOCK:
        store = _load_store()
        store["active"][token] = role
        if store.get("schema_version") == "2.0" or store.get("grace"):
            _atomic_write_store(store)
        else:
            _atomic_write_tokens(store["active"])


def revoke_token(token: str) -> bool:
    with _TOKENS_LOCK:
        store = _load_store()
        existed_a = store["active"].pop(token, None) is not None
        existed_g = store["grace"].pop(token, None) is not None
        if store.get("schema_version") == "2.0" or store.get("grace"):
            _atomic_write_store(store)
        else:
            _atomic_write_tokens(store["active"])
        return existed_a or existed_g


def list_token_summaries() -> List[Dict[str, Any]]:
    store = _load_store()
    now_s = time.time()
    out: List[Dict[str, Any]] = []
    for t, r in store.get("active", {}).items():
        out.append({"token_suffix": t[-6:], "role": r, "state": "active", "weak": is_weak_token(t)})
    for t, meta in store.get("grace", {}).items():
        exp = float(meta.get("expires_at") or 0.0)
        if exp >= now_s:
            out.append(
                {
                    "token_suffix": t[-6:],
                    "role": meta["role"],
                    "state": "grace",
                    "expires_in_s": int(exp - now_s),
                    "weak": is_weak_token(t),
                }
            )
    return out


def _extract_token(request: Request) -> Optional[str]:
    for kind, key in config.TOKEN_HEADER_CANDIDATES:
        if kind == "header":
            val = request.headers.get(key)
            if val:
                if key == "authorization":
                    if val.lower().startswith("bearer "):
                        return val[7:].strip()
                    continue
                return val.strip()
        elif kind == "cookie":
            val = request.cookies.get(key)
            if val:
                return val.strip()
        elif kind == "query":
            val = request.query_params.get(key)
            if val:
                return val.strip()
    return None


def _lookup_role_constant_time(token: str, tokens: Dict[str, str]) -> Optional[str]:
    matched_role: Optional[str] = None
    for stored_tok, role in tokens.items():
        if hmac.compare_digest(token, stored_tok):
            matched_role = role
    return matched_role


def audit(
    request: Request,
    status_code: int,
    vehicle_id: Optional[str] = None,
    detail: str = "",
) -> None:
    role = getattr(request.state, "role", "anonymous")
    token = getattr(request.state, "token", "") or ""
    try:
        with database.tx() as conn:
            conn.execute(
                "INSERT INTO audit_logs (actor_role, token_suffix, method, path, vehicle_id, status_code, remote_ip, detail) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    role,
                    token[-6:] if token else "",
                    request.method,
                    request.url.path,
                    vehicle_id,
                    status_code,
                    request.client.host if request.client else "",
                    detail[:300],
                ),
            )
    except Exception:
        pass


def authenticate(request: Request) -> Tuple[str, str]:
    token = _extract_token(request)
    if not token:
        audit(request, 401, detail="missing_token")
        raise HTTPException(status_code=401, detail="Thieu token xac thuc (RBAC)")
    tokens = _load_tokens()
    role = _lookup_role_constant_time(token, tokens)
    if not role:
        request.state.token = token
        audit(request, 401, detail="invalid_token")
        raise HTTPException(status_code=401, detail="Token khong hop le")
    request.state.role = role
    request.state.token = token
    return role, token


def require_role(min_role: str):
    def _dep(request: Request) -> str:
        role, _ = authenticate(request)
        if ROLE_RANK.get(role, -1) < ROLE_RANK.get(min_role, 99):
            audit(request, 403, detail=f"need>={min_role},got={role}")
            raise HTTPException(status_code=403, detail=f"Can vai tro >= {min_role}, hien tai: {role}")
        return role

    return _dep
