"""CLI for PI_AI Two-Mechanism Secret Rotation, Preservation & Verified Rollback.

Examples:
  python contracts/rotate_secrets.py --status
  python contracts/rotate_secrets.py --backup-only
  python contracts/rotate_secrets.py --rotate-dual --grace-hours 24
  python contracts/rotate_secrets.py --sync-pi --pi-host raspberrypi@192.168.1.82
  python contracts/rotate_secrets.py --finalize
  python contracts/rotate_secrets.py --rollback <snapshot_id>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.secret_vault import (  # noqa: E402
    ROLES_ORDER,
    SecretVault,
    atomic_write_text,
    update_pi_env_tokens_text,
)


def _sync_pi_env_atomic(vault: SecretVault, pi_host: str, remote_env_path: str) -> dict:
    role_tokens = vault.get_active_role_tokens()
    missing = [r for r in ("admin", "operator", "viewer", "device") if r not in role_tokens]
    if missing:
        raise RuntimeError(f"Thieu strong active token cho vai tro {missing}. Chay --rotate-dual truoc!")

    # 1. Pull current pi.env from Pi 4 cleanly
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", pi_host, f"cat {remote_env_path}"],
        capture_output=True,
        text=True,
        timeout=12,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"Khong ket noi/doc duoc {pi_host}:{remote_env_path}: {proc.stderr.strip()}")

    # 2. Archive pulled pi.env inside local .vault snapshot BEFORE modifying!
    with tempfile.TemporaryDirectory() as td:
        cached_pi_env = Path(td) / "pi.env.remote_before"
        cached_pi_env.write_text(proc.stdout, encoding="utf-8", newline="\n")
        snap = vault.create_snapshot(reason="pre_pi_env_sync", extra_files={"pi.env": cached_pi_env})

        new_text = update_pi_env_tokens_text(proc.stdout, role_tokens)
        tmp_new = Path(td) / "pi.env.new"
        atomic_write_text(tmp_new, new_text, owner_only=True)

        remote_tmp = f"{remote_env_path}.new.tmp"
        scp_res = subprocess.run(
            ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", str(tmp_new), f"{pi_host}:{remote_tmp}"],
            capture_output=True,
            text=True,
            timeout=12,
        )
        if scp_res.returncode != 0:
            raise RuntimeError(f"SCP that bai: {scp_res.stderr.strip()}")

    # 3. Backup remote pi.env + chmod 600 + atomic mv on Pi 4
    remote_cmd = (
        f"cp -p {remote_env_path} {remote_env_path}.bak.$(date +%Y%m%d_%H%M%S) && "
        f"chmod 600 {remote_tmp} && mv {remote_tmp} {remote_env_path} && echo PI_ENV_ATOMIC_SYNC_OK"
    )
    mv_res = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", pi_host, remote_cmd],
        capture_output=True,
        text=True,
        timeout=12,
    )
    if mv_res.returncode != 0 or "PI_ENV_ATOMIC_SYNC_OK" not in mv_res.stdout:
        raise RuntimeError(f"Atomic mv tren Pi that bai: {mv_res.stderr.strip()}")

    return {"ok": True, "pi_host": pi_host, "snapshot_id": snap["snapshot_id"], "status": "PI_ENV_ATOMIC_SYNC_OK"}


def main() -> int:
    ap = argparse.ArgumentParser(description="PI_AI Zero-Downtime Secret Vault & Rotation CLI")
    ap.add_argument("--status", action="store_true", help="Show masked security & cross-layer token sync status")
    ap.add_argument("--backup-only", action="store_true", help="Create SHA-256 verified snapshot of current secrets")
    ap.add_argument("--rotate-dual", action="store_true", help="Start zero-downtime dual-token grace rotation")
    ap.add_argument("--grace-hours", type=float, default=24.0, help="Grace window hours for legacy tokens (default 24)")
    ap.add_argument("--no-esp-header", action="store_true", help="Do not touch firmware/esp32_audio_gps/wifi_config.h")
    ap.add_argument("--sync-pi", action="store_true", help="Atomically sync new active tokens to Pi 4 pi.env")
    ap.add_argument("--pi-host", default="raspberrypi@192.168.1.82", help="Pi SSH target")
    ap.add_argument("--remote-env", default="~/pi_ai_edge/pi_edge/deploy/pi.env", help="Remote pi.env path")
    ap.add_argument("--finalize", action="store_true", help="Revoke all weak/grace tokens after verified backup")
    ap.add_argument("--list-snapshots", action="store_true", help="List available SHA-256 snapshots")
    ap.add_argument("--rollback", default="", help="Rollback to a verified snapshot_id")
    args = ap.parse_args()

    vault = SecretVault()
    try:
        if args.backup_only:
            out = vault.create_snapshot(reason="manual_backup")
        elif args.rotate_dual:
            out = vault.rotate_dual_window(
                grace_hours=args.grace_hours,
                update_esp_header=not args.no_esp_header,
            )
        elif args.sync_pi:
            out = _sync_pi_env_atomic(vault, args.pi_host, args.remote_env)
        elif args.finalize:
            out = vault.finalize_rotation()
        elif args.list_snapshots:
            out = {"snapshots": vault.list_snapshots()}
        elif args.rollback:
            out = vault.rollback_snapshot(args.rollback)
        else:
            out = {
                "cross_check": vault.cross_check_connectivity(),
                "snapshot_count": len(vault.list_snapshots()),
                "credentials_file_exists": vault.credentials_file.exists(),
            }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
