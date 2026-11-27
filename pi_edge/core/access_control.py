"""Strict RBAC & Fail-Closed Audit Logger for pi_edge (fixes F-007, F-008, tunnel bypass, audit leak)."""
from __future__ import annotations

import hmac
import json
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

ROLE_RANK = {"viewer": 0, "device": 1, "operator": 2, "admin": 3}
ALERT_CMD_RE = re.compile(r"^ALERT:[12]:(\d{2,4})$")
ALLOWED_STATIC_CMDS = {
    "STOP",
    "MUTE",
    "PING",
    "WHOAMI",
    "BT_UNPAIR",
    "BT_FORCE_RESCAN",
    "CONFIG_SET",
    "REST_BREAK_REQUEST",
    "CALIBRATE",
}


def is_allowed_control_command(cmd: str) -> bool:
    c = (cmd or "").strip()
    if c in ALLOWED_STATIC_CMDS:
        return True
    if c.startswith("VOICE:"):
        try:
            n = int(c.split(":", 1)[1])
            return 1 <= n <= 9
        except ValueError:
            return False
    if c.startswith("VOL:"):
        try:
            v = int(c.split(":", 1)[1])
            return 10 <= v <= 100
        except ValueError:
            return False
    m = ALERT_CMD_RE.match(c)
    if m:
        ms = int(m.group(1))
        return 50 <= ms <= 5000
    return False


class AuditLogger:
    """Thread-safe rotating JSONL audit logger stored in logs/audit/ (NEVER inside violations/)."""

    def __init__(self, audit_dir: Path, max_bytes: int = 10 * 1024 * 1024):
        self.audit_dir = Path(audit_dir)
        self.log_path = self.audit_dir / "audit_permissions.jsonl"
        self.max_bytes = max(65536, int(max_bytes))
        self._lock = threading.Lock()
        self._recent: deque = deque(maxlen=200)
        self.audit_degraded: bool = False
        self.last_error: str = ""

    def record(
        self,
        *,
        actor_role: str,
        method: str,
        path: str,
        allowed: bool,
        reason: str,
        remote_ip: str = "",
        detail: str = "",
        required: bool = False,
    ) -> bool:
        entry = {
            "ts": round(time.time(), 3),
            "role": actor_role,
            "method": method,
            "path": path,
            "allowed": bool(allowed),
            "reason": reason,
            "remote_ip": remote_ip,
            "detail": detail[:200],
        }
        with self._lock:
            self._recent.append(entry)
            try:
                self.audit_dir.mkdir(parents=True, exist_ok=True)
                if self.log_path.exists() and self.log_path.stat().st_size >= self.max_bytes:
                    rotated = self.log_path.with_suffix(".jsonl.1")
                    os.replace(self.log_path, rotated)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self.audit_degraded = False
                self.last_error = ""
                return True
            except Exception as exc:
                # NEVER silently swallow! Surface degraded state & fail closed if audit is required.
                self.audit_degraded = True
                self.last_error = f"{type(exc).__name__}: {exc}"
                if required:
                    return False
                return False

    def recent(self, limit: int = 50) -> list:
        with self._lock:
            return list(self._recent)[-max(1, int(limit)):]


@dataclass(frozen=True)
class AuthDecision:
    allowed: bool
    role: str
    reason: str


class EdgeAccessController:
    """Strict RBAC for Pi Edge HTTP server: NO unauthenticated open-lab default in prod, NO 127.0.0.1 tunnel bypass."""

    def __init__(self, tokens_by_role: Dict[str, str], env_profile: str = "prod"):
        self.env_profile = env_profile
        self._tokens: Dict[str, str] = {tok: role for tok, role in tokens_by_role.items() if tok and role in ROLE_RANK}

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, env_profile: str = "prod") -> "EdgeAccessController":
        e = env if env is not None else os.environ
        mapping: Dict[str, str] = {}
        pairs = [
            ("WEB_ADMIN_TOKEN", "admin"),
            ("WEB_OPERATOR_TOKEN", "operator"),
            ("WEB_VIEWER_TOKEN", "viewer"),
            ("DEVICE_BRIDGE_TOKEN", "device"),
            ("REMOTE_AUTH_TOKEN", "device"),
        ]
        for k, role in pairs:
            v = (e.get(k) or "").strip()
            if v:
                mapping[v] = role
        # Legacy single token maps to admin ONLY if explicitly provided
        legacy = (e.get("WEB_AUTH_TOKEN") or "").strip()
        if legacy and legacy not in mapping:
            mapping[legacy] = "admin"

        if not mapping:
            if env_profile == "lab":
                mapping = {
                    "lab-pi-admin-token": "admin",
                    "lab-pi-viewer-token": "viewer",
                    "lab-pi-device-token": "device",
                }
            else:
                raise RuntimeError(
                    "[PI RBAC STRICT BOOT FAIL] No RBAC tokens configured in pi.env "
                    "(set WEB_ADMIN_TOKEN / WEB_VIEWER_TOKEN / DEVICE_BRIDGE_TOKEN, or use ENV_PROFILE=lab for 127.0.0.1-only dev)."
                )
        return cls(mapping, env_profile=env_profile)

    def extract_token(self, headers: Mapping[str, str], query_params: Mapping[str, str], cookies: Mapping[str, str]) -> Optional[str]:
        low = {k.lower(): v for k, v in headers.items()}
        auth_h = low.get("authorization", "")
        if auth_h.lower().startswith("bearer "):
            t = auth_h[7:].strip()
            if t:
                return t
        for h in ("x-api-token", "x-dashboard-token", "x-auth-token"):
            v = (low.get(h) or "").strip()
            if v:
                return v
        ck = (cookies.get("web_token") or "").strip()
        if ck:
            return ck
        qt = (query_params.get("token") or "").strip()
        return qt or None

    def authorize(
        self,
        *,
        min_role: str,
        headers: Mapping[str, str],
        query_params: Optional[Mapping[str, str]] = None,
        cookies: Optional[Mapping[str, str]] = None,
    ) -> AuthDecision:
        # NOTE: Deliberately NO 127.0.0.1 exemption (closes PUBLIC_TUNNEL_URL bypass!)
        token = self.extract_token(headers, query_params or {}, cookies or {})
        if not token:
            return AuthDecision(False, "anonymous", "MISSING_TOKEN")
        matched_role: Optional[str] = None
        for stored, role in self._tokens.items():
            if hmac.compare_digest(token, stored):
                matched_role = role
        if not matched_role:
            return AuthDecision(False, "anonymous", "INVALID_TOKEN")
        if ROLE_RANK.get(matched_role, -1) < ROLE_RANK.get(min_role, 99):
            return AuthDecision(False, matched_role, f"INSUFFICIENT_ROLE:{matched_role}<{min_role}")
        return AuthDecision(True, matched_role, "OK")
