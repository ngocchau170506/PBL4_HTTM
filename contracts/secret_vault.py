"""Two-Mechanism Secret & Config Safety Vault for PI_AI (Chặng B Security Hardening).

Mechanism 1 — Safe Rotation Design (Zero-Downtime Dual-Token Grace Rotation):
  - Generates 48-char cryptographic tokens (secrets.token_hex(24)) for all 5 RBAC roles.
  - Moves prior tokens into a bounded grace window (default 24h) in server_ai/data/tokens.json (v2 schema)
    so Raspberry Pi 4 and ESP32 #1 NEVER suffer abrupt lockout during rollout.
  - Atomically updates ESP32 wifi_config.h (ONLY UPLINK_AUTH_TOKEN; preserves all Wi-Fi APs) and
    atomically syncs Pi 4 pi.env (ONLY RBAC token lines; preserves camera password & network settings).
  - Finalize step removes all weak/grace tokens ONLY after backup snapshot verification (fail-closed).

Mechanism 2 — Secret & Config Preservation + Verified Rollback:
  - Stores timestamped snapshots in gitignored .vault/snapshots/<id>/ with SHA-256 manifest.json.
  - Restricts directory/file permissions to owner-only (Windows NTFS icacls / POSIX 0o600/0o700).
  - Saves human-readable local credential sheet strictly inside .vault/current_credentials.local.txt
    (NEVER prints plaintext secrets to stdout, logs, or Git).
  - One-command rollback verifies SHA-256 integrity of every snapshot file before atomic restore.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_VAULT_DIR = ROOT_DIR / ".vault"
DEFAULT_TOKENS_PATH = ROOT_DIR / "server_ai" / "data" / "tokens.json"
# LCK-05 v3: Wi-Fi Survival Bridge lives on ESP32 #2 (COMMS_BLE_GPS_HUB), not ESP32 #1!
DEFAULT_WIFI_CONFIG_PATH = ROOT_DIR / "firmware" / "esp32_ble_wifi_hub" / "wifi_config.h"

ROLES_ORDER = ("admin", "operator", "viewer", "device", "driver_phone")
ROLE_TO_PI_ENV_KEY = {
    "admin": "WEB_ADMIN_TOKEN",
    "operator": "WEB_OPERATOR_TOKEN",
    "viewer": "WEB_VIEWER_TOKEN",
    "device": "DEVICE_BRIDGE_TOKEN",
}

WEAK_DENYLIST = {
    "admin", "dev", "device", "test", "viewer", "operator",
    "password", "123456", "12345678", "root", "token",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def token_fingerprint(token: str) -> str:
    return sha256_bytes((token or "").encode("utf-8"))[:12]


def mask_token(token: str) -> str:
    t = (token or "").strip()
    if len(t) <= 8:
        return f"***(len={len(t)},fp={token_fingerprint(t)[:6]})"
    return f"{t[:4]}...{t[-4:]}(fp={token_fingerprint(t)[:6]})"


def is_weak_token(token: str) -> bool:
    t = (token or "").strip()
    return len(t) < 16 or t.lower() in WEAK_DENYLIST


def restrict_owner_only(path: Path) -> None:
    """Apply owner-only permissions (best-effort NTFS icacls on Windows, chmod 600/700 on POSIX)."""
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME") or ""
            if user:
                flag = "(OI)(CI)(F)" if path.is_dir() else "(F)"
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:{flag}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
        else:
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
    except Exception:
        pass


def atomic_write_text(path: Path, text: str, owner_only: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    if owner_only:
        restrict_owner_only(tmp)
    os.replace(tmp, path)
    if owner_only:
        restrict_owner_only(path)


def _normalize_tokens_dict(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {"schema_version": "2.0", "active": {}, "grace": {}}
    if "active" in raw and isinstance(raw["active"], dict):
        return {
            "schema_version": str(raw.get("schema_version") or "2.0"),
            "active": {str(k): str(v) for k, v in raw["active"].items() if k and v},
            "grace": {
                str(k): {"role": str(m.get("role")), "expires_at": float(m.get("expires_at") or 0.0)}
                for k, m in (raw.get("grace") or {}).items()
                if k and isinstance(m, dict) and m.get("role")
            },
            **({"rotated_at": raw["rotated_at"]} if raw.get("rotated_at") else {}),
        }
    return {
        "schema_version": "1.0",
        "active": {str(k): str(v) for k, v in raw.items() if k and v},
        "grace": {},
    }


def _extract_esp_token(wifi_cfg_text: str) -> Optional[str]:
    m = re.search(r'#define\s+UPLINK_AUTH_TOKEN\s+"([^"]*)"', wifi_cfg_text)
    return m.group(1) if m else None


def _replace_esp_token(wifi_cfg_text: str, new_device_token: str) -> str:
    pattern = r'(#define\s+UPLINK_AUTH_TOKEN\s+")([^"]*)(")'
    if re.search(pattern, wifi_cfg_text):
        return re.sub(pattern, rf"\g<1>{new_device_token}\g<3>", wifi_cfg_text, count=1)
    return wifi_cfg_text.rstrip() + f'\n#define UPLINK_AUTH_TOKEN "{new_device_token}"\n'


def update_pi_env_tokens_text(pi_env_text: str, role_tokens: Dict[str, str]) -> str:
    """Replace ONLY the 5 RBAC token lines in pi.env, preserving camera password & all other settings."""
    updates = {
        "WEB_ADMIN_TOKEN": role_tokens["admin"],
        "WEB_OPERATOR_TOKEN": role_tokens["operator"],
        "WEB_VIEWER_TOKEN": role_tokens["viewer"],
        "DEVICE_BRIDGE_TOKEN": role_tokens["device"],
        "REMOTE_AUTH_TOKEN": role_tokens["device"],
    }
    seen = set()
    out_lines: List[str] = []
    for raw_line in pi_env_text.splitlines():
        matched_key = None
        for k in updates:
            if re.match(rf"^\s*{k}\s*=", raw_line):
                matched_key = k
                break
        if matched_key:
            out_lines.append(f"{matched_key}={updates[matched_key]}")
            seen.add(matched_key)
        else:
            out_lines.append(raw_line)
    for k, v in updates.items():
        if k not in seen:
            out_lines.append(f"{k}={v}")
    return "\n".join(out_lines) + "\n"


class SecretVault:
    """Manages snapshots, integrity manifests, dual-token grace rotation, and verified rollback."""

    def __init__(
        self,
        vault_dir: Path = DEFAULT_VAULT_DIR,
        tokens_path: Path = DEFAULT_TOKENS_PATH,
        wifi_config_path: Path = DEFAULT_WIFI_CONFIG_PATH,
    ):
        self.vault_dir = Path(vault_dir)
        self.snapshots_dir = self.vault_dir / "snapshots"
        self.audit_log_path = self.vault_dir / "vault_audit.jsonl"
        self.credentials_file = self.vault_dir / "current_credentials.local.txt"
        self.tokens_path = Path(tokens_path)
        self.wifi_config_path = Path(wifi_config_path)

    def _ensure_vault_dirs(self) -> None:
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        restrict_owner_only(self.vault_dir)
        restrict_owner_only(self.snapshots_dir)

    def _append_audit(self, action: str, detail: Dict[str, Any]) -> None:
        self._ensure_vault_dirs()
        rec = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "action": action,
            **detail,
        }
        with self.audit_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        restrict_owner_only(self.audit_log_path)

    def create_snapshot(self, reason: str = "manual", extra_files: Optional[Dict[str, Path]] = None) -> Dict[str, Any]:
        """Mechanism 2: Snapshot current secrets & configs with SHA-256 manifest before any change."""
        self._ensure_vault_dirs()
        safe_reason = re.sub(r"[^A-Za-z0-9_-]+", "_", reason or "manual").strip("_") or "manual"
        snap_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + f"_{secrets.token_hex(2)}_{safe_reason}"
        sdir = self.snapshots_dir / snap_id
        sdir.mkdir(parents=True, exist_ok=True)
        restrict_owner_only(sdir)

        targets: Dict[str, Path] = {
            "tokens.json": self.tokens_path,
            "wifi_config.h": self.wifi_config_path,
        }
        if extra_files:
            targets.update(extra_files)

        files_meta: Dict[str, Dict[str, Any]] = {}
        for name, src in targets.items():
            if src.exists() and src.is_file():
                dst = sdir / name
                shutil.copy2(src, dst)
                restrict_owner_only(dst)
                files_meta[name] = {
                    "original_path": str(src.resolve()),
                    "size_bytes": dst.stat().st_size,
                    "sha256": sha256_file(dst),
                }

        manifest = {
            "snapshot_id": snap_id,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "reason": safe_reason,
            "files": files_meta,
        }
        atomic_write_text(sdir / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        self._append_audit("SNAPSHOT_CREATED", {"snapshot_id": snap_id, "files": sorted(files_meta.keys())})
        return manifest

    def list_snapshots(self) -> List[Dict[str, Any]]:
        if not self.snapshots_dir.exists():
            return []
        out: List[Dict[str, Any]] = []
        for sdir in sorted(self.snapshots_dir.iterdir(), reverse=True):
            mp = sdir / "manifest.json"
            if mp.exists():
                try:
                    out.append(json.loads(mp.read_text(encoding="utf-8")))
                except Exception:
                    pass
        return out

    def verify_snapshot(self, snapshot_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if not snapshot_id or Path(snapshot_id).name != snapshot_id or snapshot_id in (".", "..") or "/" in snapshot_id or "\\" in snapshot_id:
            return False, f"Invalid snapshot_id (path traversal blocked): {snapshot_id!r}", None
        sdir = (self.snapshots_dir / snapshot_id).resolve()
        try:
            sdir.relative_to(self.snapshots_dir.resolve())
        except ValueError:
            return False, f"Snapshot path escapes vault directory: {snapshot_id!r}", None
        mp = sdir / "manifest.json"
        if not mp.exists():
            return False, f"Snapshot manifest not found: {snapshot_id}", None
        try:
            manifest = json.loads(mp.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"Corrupt manifest JSON: {exc}", None
        files = manifest.get("files") or {}
        for name, meta in files.items():
            fp = sdir / name
            if not fp.exists():
                return False, f"Missing file in snapshot: {name}", None
            actual = sha256_file(fp)
            if actual != meta.get("sha256"):
                return False, f"SHA-256 mismatch for {name} (tampered or corrupt snapshot)", None
        return True, "OK", manifest

    def rollback_snapshot(self, snapshot_id: str) -> Dict[str, Any]:
        """Fail-closed SHA-256 verified atomic rollback to a prior snapshot."""
        ok, msg, manifest = self.verify_snapshot(snapshot_id)
        if not ok or manifest is None:
            raise RuntimeError(f"[VAULT ROLLBACK BLOCKED] {msg}")

        # Safety net: snapshot current state before rolling back!
        pre_snap = self.create_snapshot(reason=f"pre_rollback_{snapshot_id[:15]}")
        sdir = self.snapshots_dir / snapshot_id
        restored: List[str] = []
        for name, meta in (manifest.get("files") or {}).items():
            src = sdir / name
            dst = Path(meta["original_path"])
            atomic_write_text(dst, src.read_text(encoding="utf-8"))
            restored.append(name)

        self._append_audit(
            "ROLLBACK_EXECUTED",
            {"target_snapshot": snapshot_id, "pre_rollback_snapshot": pre_snap["snapshot_id"], "restored": restored},
        )
        return {
            "ok": True,
            "restored_snapshot": snapshot_id,
            "pre_rollback_snapshot": pre_snap["snapshot_id"],
            "restored_files": restored,
        }

    def _write_local_credentials_sheet(self, role_tokens: Dict[str, str], state_label: str, snapshot_id: str) -> Path:
        lines = [
            "# ===========================================================================",
            "# PI_AI LOCAL CREDENTIAL VAULT (OWNER-ONLY — NEVER COMMIT TO GIT OR SHARE!)",
            f"# Updated UTC : {datetime.now(timezone.utc).isoformat()}",
            f"# State       : {state_label}",
            f"# Backup ID   : {snapshot_id}",
            "# ===========================================================================",
            "",
        ]
        for role in ROLES_ORDER:
            tok = role_tokens.get(role, "")
            if tok:
                lines.append(f"{role.upper():13s} = {tok}")
        lines.append("")
        lines.append("# Dashboard login URL (PC):    http://127.0.0.1:5000  (use ADMIN or VIEWER token)")
        lines.append("# Dashboard login URL (Phone): http://192.168.1.67:5000")
        lines.append("# Pi 4 live stream URL (LAN):  http://192.168.1.82:8080/stream?token=<ADMIN_OR_VIEWER_TOKEN>")
        lines.append("")
        atomic_write_text(self.credentials_file, "\n".join(lines), owner_only=True)
        return self.credentials_file

    def rotate_dual_window(
        self,
        grace_hours: float = 24.0,
        update_esp_header: bool = True,
        now_ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Mechanism 1: Zero-downtime dual-token rotation (active strong 48-char + bounded grace for old tokens)."""
        self._ensure_vault_dirs()
        snap = self.create_snapshot(reason="pre_dual_rotate")
        now_s = time.time() if now_ts is None else float(now_ts)
        grace_exp = now_s + max(0.05, float(grace_hours)) * 3600.0

        prior_raw = {}
        if self.tokens_path.exists():
            try:
                prior_raw = json.loads(self.tokens_path.read_text(encoding="utf-8"))
            except Exception:
                prior_raw = {}
        prior = _normalize_tokens_dict(prior_raw)

        # Generate 5 fresh 48-hex-char cryptographic tokens (192 bits entropy each)
        new_role_tokens: Dict[str, str] = {role: secrets.token_hex(24) for role in ROLES_ORDER}
        new_active = {tok: role for role, tok in new_role_tokens.items()}

        # Move prior active + still-live grace tokens into bounded grace window (no abrupt disconnect!)
        new_grace: Dict[str, Dict[str, Any]] = {}
        for tok, meta in prior.get("grace", {}).items():
            if float(meta.get("expires_at") or 0.0) >= now_s and tok not in new_active:
                new_grace[tok] = {"role": meta["role"], "expires_at": min(float(meta["expires_at"]), grace_exp)}
        for tok, role in prior.get("active", {}).items():
            if tok not in new_active:
                new_grace[tok] = {"role": role, "expires_at": grace_exp}

        new_store = {
            "schema_version": "2.0",
            "rotated_at": now_s,
            "grace_hours": float(grace_hours),
            "active": new_active,
            "grace": new_grace,
        }
        atomic_write_text(self.tokens_path, json.dumps(new_store, indent=2, ensure_ascii=False))

        esp_updated = False
        if update_esp_header and self.wifi_config_path.exists():
            orig_h = self.wifi_config_path.read_text(encoding="utf-8")
            updated_h = _replace_esp_token(orig_h, new_role_tokens["device"])
            atomic_write_text(self.wifi_config_path, updated_h)
            esp_updated = True

        cred_path = self._write_local_credentials_sheet(new_role_tokens, "DUAL_GRACE", snap["snapshot_id"])
        masked_summary = {r: mask_token(t) for r, t in new_role_tokens.items()}
        self._append_audit(
            "DUAL_ROTATION_STARTED",
            {
                "snapshot_id": snap["snapshot_id"],
                "grace_hours": float(grace_hours),
                "grace_token_count": len(new_grace),
                "esp_header_updated": esp_updated,
                "active_masked": masked_summary,
            },
        )
        return {
            "ok": True,
            "state": "DUAL_GRACE",
            "snapshot_id": snap["snapshot_id"],
            "grace_hours": float(grace_hours),
            "grace_token_count": len(new_grace),
            "esp_header_updated": esp_updated,
            "credentials_file": str(cred_path),
            "active_masked": masked_summary,
        }

    def finalize_rotation(self) -> Dict[str, Any]:
        """Revoke all legacy/grace/weak tokens atomically ONLY if a verified backup snapshot exists."""
        snaps = self.list_snapshots()
        if not snaps:
            raise RuntimeError("[VAULT FINALIZE BLOCKED] No verified backup snapshot exists. Create snapshot first!")
        ok, msg, _ = self.verify_snapshot(snaps[0]["snapshot_id"])
        if not ok:
            raise RuntimeError(f"[VAULT FINALIZE BLOCKED] Latest snapshot failed integrity check: {msg}")

        if not self.tokens_path.exists():
            raise RuntimeError(f"Missing tokens store: {self.tokens_path}")
        cur = _normalize_tokens_dict(json.loads(self.tokens_path.read_text(encoding="utf-8")))

        strong_active = {t: r for t, r in cur.get("active", {}).items() if not is_weak_token(t)}
        roles_present = set(strong_active.values())
        missing_roles = [r for r in ROLES_ORDER if r not in roles_present]
        if missing_roles:
            raise RuntimeError(
                f"[VAULT FINALIZE BLOCKED] Active strong tokens missing roles {missing_roles}. Run --rotate-dual first!"
            )

        pre_snap = self.create_snapshot(reason="pre_finalize")
        removed_weak = len(cur.get("active", {})) - len(strong_active)
        removed_grace = len(cur.get("grace", {}))

        final_store = {
            "schema_version": "2.0",
            "rotated_at": time.time(),
            "finalized_at": time.time(),
            "active": strong_active,
            "grace": {},
        }
        atomic_write_text(self.tokens_path, json.dumps(final_store, indent=2, ensure_ascii=False))

        role_to_tok = {r: t for t, r in strong_active.items()}
        cred_path = self._write_local_credentials_sheet(role_to_tok, "HARDENED_PROD", pre_snap["snapshot_id"])
        self._append_audit(
            "ROTATION_FINALIZED",
            {
                "snapshot_id": pre_snap["snapshot_id"],
                "removed_weak_active": removed_weak,
                "removed_grace": removed_grace,
                "active_count": len(strong_active),
            },
        )
        return {
            "ok": True,
            "state": "HARDENED_PROD",
            "snapshot_id": pre_snap["snapshot_id"],
            "removed_weak_active": removed_weak,
            "removed_grace": removed_grace,
            "active_count": len(strong_active),
            "credentials_file": str(cred_path),
        }

    def get_active_role_tokens(self) -> Dict[str, str]:
        """Return {role: strong_active_token} for syncing to Pi 4 (internal use only; never log!)."""
        if not self.tokens_path.exists():
            return {}
        cur = _normalize_tokens_dict(json.loads(self.tokens_path.read_text(encoding="utf-8")))
        out: Dict[str, str] = {}
        for tok, role in cur.get("active", {}).items():
            if not is_weak_token(tok):
                out[role] = tok
        return out

    def cross_check_connectivity(self, now_ts: Optional[float] = None) -> Dict[str, Any]:
        """Non-secret cross-layer token consistency check between Server tokens.json and ESP32 wifi_config.h."""
        now_s = time.time() if now_ts is None else float(now_ts)
        result: Dict[str, Any] = {
            "tokens_file_exists": self.tokens_path.exists(),
            "wifi_config_exists": self.wifi_config_path.exists(),
            "esp_token_valid_on_server": None,
            "esp_token_match_tier": "NOT_PRESENT",
            "weak_active_count": 0,
            "grace_live_count": 0,
            "rotation_state": "MISSING",
        }
        if not self.tokens_path.exists():
            return result
        store = _normalize_tokens_dict(json.loads(self.tokens_path.read_text(encoding="utf-8")))
        active = store.get("active", {})
        grace = store.get("grace", {})
        live_grace = {t: m["role"] for t, m in grace.items() if float(m.get("expires_at") or 0.0) >= now_s}
        weak_active = [t for t in active if is_weak_token(t)]
        result["weak_active_count"] = len(weak_active)
        result["grace_live_count"] = len(live_grace)
        result["active_count"] = len(active)
        result["rotation_state"] = (
            "DUAL_GRACE"
            if live_grace
            else ("HARDENED_PROD" if (len(active) >= 5 and len(weak_active) == 0) else "NEEDS_HARDENING")
        )

        if self.wifi_config_path.exists():
            esp_tok = _extract_esp_token(self.wifi_config_path.read_text(encoding="utf-8"))
            if esp_tok:
                result["esp_token_fp"] = token_fingerprint(esp_tok)
                if active.get(esp_tok) == "device":
                    result["esp_token_valid_on_server"] = True
                    result["esp_token_match_tier"] = "ACTIVE"
                elif live_grace.get(esp_tok) == "device":
                    result["esp_token_valid_on_server"] = True
                    result["esp_token_match_tier"] = "GRACE"
                else:
                    result["esp_token_valid_on_server"] = False
                    result["esp_token_match_tier"] = "MISMATCH"
        return result
