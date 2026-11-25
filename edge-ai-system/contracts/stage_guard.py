"""Stage-Lock & Cross-Layer Connectivity Guard ("Chốt bảo vệ từng chặng") for PI_AI.

Prevents silent drift or broken integration between layers and stages:
  1. Verifies 7 live Cross-Layer Connectivity Links (Contracts <-> Pi Edge <-> Server of AI <-> ESP32 <-> Dashboard <-> Vault).
  2. Enforces fail-closed Stage-Lock Chain (STAGE_A -> STAGE_B -> STAGE_C -> STAGE_D) tied to exact source_hash:
     - Any 1-byte source change automatically INVALIDATES stale stage locks.
     - Stage N cannot pass/unlock unless Stage N-1 is LOCKED_PASS on the exact same source_hash.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

REPORTS_DIR = ROOT_DIR / "gate_reports"
STAGE_LOCKS_PATH = REPORTS_DIR / "stage_locks.json"


def _check_link_01_api_routes() -> Tuple[bool, str]:
    from server_ai.api_server import app

    route_paths = {getattr(r, "path", "") for r in app.routes}
    required_routes = {
        "/api/v1/health",
        "/api/v1/auth/login",
        "/api/v1/telemetry",
        "/api/v1/violation_burst",
        "/api/v1/commands/pull",
        "/api/v1/commands/ack",
        "/api/v1/events/alert_dismissed",
        "/api/v1/esp/bridge_telemetry",
        "/api/v1/violations",
        "/api/v1/violations/{event_id}/frames",
        "/api/v1/violations/{event_id}/review",
        "/api/v1/drivers/summary",
        "/api/v1/admin/security_health",
        "/evidence/{rel_path:path}",
    }
    missing = sorted(required_routes - route_paths)
    if missing:
        return False, f"Missing FastAPI routes: {missing}"
    return True, f"All {len(required_routes)} cross-system API routes verified in server_ai"


def _check_link_02_burst_and_dismiss() -> Tuple[bool, str]:
    from pi_edge.evidence.burst_collector import BurstCollector
    from server_ai.server_core import config as srv_cfg

    bc = BurstCollector()
    if bc.max_burst_frames > srv_cfg.MAX_BURST_FRAMES:
        return (
            False,
            f"BurstCollector.max_burst_frames ({bc.max_burst_frames}) > Server MAX_BURST_FRAMES ({srv_cfg.MAX_BURST_FRAMES})",
        )
    main_src = (ROOT_DIR / "pi_edge" / "app" / "main.py").read_text(encoding="utf-8")
    for src_name in ("PHYSICAL_MUTE_BUTTON", "REMOTE_OPERATOR"):
        if src_name not in srv_cfg.DISMISS_SOURCES:
            return False, f"Dismiss source {src_name} missing in server DISMISS_SOURCES"
        if src_name not in main_src:
            return False, f"Dismiss source {src_name} missing in pi_edge/app/main.py"
    return True, f"Burst cap ({bc.max_burst_frames}<={srv_cfg.MAX_BURST_FRAMES}) & dismiss sources aligned"


def _check_link_03_esp_firmware_parser() -> Tuple[bool, str]:
    from pi_edge.comms.esp_client import EspClient, classify_esp_ack_line

    ino_path = ROOT_DIR / "firmware" / "esp32_audio_gps" / "esp32_audio_gps.ino"
    if not ino_path.exists():
        return False, "Missing firmware/esp32_audio_gps/esp32_audio_gps.ino"
    ino = ino_path.read_text(encoding="utf-8", errors="ignore")
    for token in ("WHOAMI", "ROLE=AUDIO_MQ135_NODE", "VOICE:", "STOP", "MUTE", "PING", "VOL:", "EVT,ACK,STOP_INTERRUPT", "EVT,NACK", "btStop()"):
        if token not in ino:
            return False, f"ESP32 #1 firmware missing required protocol token: {token}"
    if "#include <WiFi.h>" in ino:
        return False, "ESP32 #1 (LCK-05 v3) must NOT include WiFi.h"

    hub_path = ROOT_DIR / "firmware" / "esp32_ble_wifi_hub" / "esp32_ble_wifi_hub.ino"
    if not hub_path.exists():
        return False, "Missing firmware/esp32_ble_wifi_hub/esp32_ble_wifi_hub.ino"
    hub_ino = hub_path.read_text(encoding="utf-8", errors="ignore")
    for token in ("ROLE=COMMS_BLE_GPS_HUB", "GPS_RX_PIN", "CFG_SERVER:", "WiFi.mode(WIFI_OFF)", "/api/v1/esp/bridge_telemetry"):
        if token not in hub_ino:
            return False, f"ESP32 #2 firmware missing required token: {token}"

    # Verify EspClient parser agrees with real ESP32 replies
    checks = [
        ("EVT,ROLE,AUDIO_MQ135_NODE,FW=3.0", ("ACK", "WHOAMI")),
        ("EVT,WHOAMI,ROLE=AUDIO_MQ135_NODE,FW=PI_AI_3.0", ("ACK", "WHOAMI")),
        ("EVT,ACK,STOP_INTERRUPT", ("ACK", "STOP")),
        ("EVT,ACK,MUTE_INTERRUPT", ("ACK", "MUTE")),
        ("EVT,NACK,VOICE,OUT_OF_RANGE", ("NACK", "VOICE")),
        ("EVT,MUTE_PRESSED", ("MUTE_PRESSED", "MUTE")),
        ("EVT,RESET_REASON,BROWNOUT", ("HW_EVENT", "ESP_BROWNOUT")),
    ]
    for raw, exp in checks:
        got = classify_esp_ack_line(raw)
        if got != exp:
            return False, f"Parser mismatch for '{raw}': got {got}, expected {exp}"

    cli = EspClient(mode="MOCK")
    cli.handle_incoming_line("EVT,WHOAMI,ROLE=AUDIO_MQ135_NODE,FW=PI_AI_3.0")
    if cli.device_role != "AUDIO_MQ135_NODE":
        return False, f"EspClient.device_role expected AUDIO_MQ135_NODE, got {cli.device_role}"
    return True, "Dual-ESP32 v3 firmware (AUDIO_MQ135 + BLE_GPS_HUB) & pi_edge parsers 100% aligned"


def _check_link_04_media_and_dashboard() -> Tuple[bool, str]:
    media_src = (ROOT_DIR / "server_ai" / "server_core" / "media.py").read_text(encoding="utf-8")
    js_src = (ROOT_DIR / "server_ai" / "static" / "dashboard.js").read_text(encoding="utf-8")
    html_src = (ROOT_DIR / "server_ai" / "static" / "index.html").read_text(encoding="utf-8")

    for req in ("video/mp4", "video/webm", "libx264", "yuv420p", "list_burst_frame_urls"):
        if req not in media_src:
            return False, f"media.py missing {req}"
    for req in ("attachToken", "openLiveStream", "openViolationReview", "submitHumanReview", "showStoryboardFrame", "renderDrivers"):
        if req not in js_src:
            return False, f"dashboard.js missing {req}"
    for req in ("drivers-table", "storyboard-slider", "human-review-box", "security-health-box"):
        if req not in html_src:
            return False, f"index.html missing #{req}"
    return True, "H.264/WebM media pipeline & Human-in-the-Loop Dashboard UI 100% aligned"


def _check_link_05_deploy_and_gitignore() -> Tuple[bool, str]:
    svc = (ROOT_DIR / "pi_edge" / "deploy" / "driver-monitor.service").read_text(encoding="utf-8")
    cut = (ROOT_DIR / "pi_edge" / "deploy" / "cutover_systemd.sh").read_text(encoding="utf-8")
    gi = (ROOT_DIR / ".gitignore").read_text(encoding="utf-8")

    svc_active_lines = [ln for ln in svc.splitlines() if not ln.lstrip().startswith("#")]
    if any("WEB_PORT=0" in ln for ln in svc_active_lines):
        return False, "driver-monitor.service must NOT hardcode WEB_PORT=0 (LCK-04)"
    if "--rollback" not in cut or "driver-monitor.service.iotv5.bak" not in cut:
        return False, "cutover_systemd.sh missing IOTv5 backup/rollback protection"
    for pat in (".vault/", "tokens.json", "pi.env", "wifi_config.h"):
        if pat not in gi:
            return False, f".gitignore missing secret protection rule: {pat}"
    return True, "Pi systemd deploy, IOTv5 rollback script & .gitignore secret shield verified"


def _check_link_06_vault_and_auth() -> Tuple[bool, str]:
    from server_ai.server_core import auth
    from contracts.secret_vault import SecretVault

    for fn in ("inspect_token_store_health", "is_weak_token", "_normalize_store"):
        if not hasattr(auth, fn):
            return False, f"server_ai.server_core.auth missing {fn}"
    for meth in ("create_snapshot", "verify_snapshot", "rollback_snapshot", "rotate_dual_window", "finalize_rotation"):
        if not hasattr(SecretVault, meth):
            return False, f"SecretVault missing {meth}"
    return True, "Dual-Token Grace Auth & SHA-256 SecretVault mechanisms aligned"


def _check_link_07_local_token_sync() -> Tuple[bool, str]:
    from contracts.secret_vault import SecretVault

    cc = SecretVault().cross_check_connectivity()
    if cc.get("esp_token_valid_on_server") is False:
        return (
            False,
            "ESP32 wifi_config.h UPLINK_AUTH_TOKEN does NOT match any active/grace 'device' token in server_ai/data/tokens.json!",
        )
    tier = cc.get("esp_token_match_tier", "NOT_PRESENT")
    state = cc.get("rotation_state", "UNKNOWN")
    return True, f"Local secret sync OK (rotation_state={state}, esp_token_tier={tier})"


def verify_cross_layer_connectivity() -> Dict[str, Any]:
    checks = [
        ("LINK_01_API_ROUTES", _check_link_01_api_routes),
        ("LINK_02_BURST_AND_DISMISS", _check_link_02_burst_and_dismiss),
        ("LINK_03_ESP_FIRMWARE_PARSER", _check_link_03_esp_firmware_parser),
        ("LINK_04_MEDIA_AND_DASHBOARD", _check_link_04_media_and_dashboard),
        ("LINK_05_DEPLOY_AND_GITIGNORE", _check_link_05_deploy_and_gitignore),
        ("LINK_06_VAULT_AND_AUTH", _check_link_06_vault_and_auth),
        ("LINK_07_LOCAL_TOKEN_SYNC", _check_link_07_local_token_sync),
    ]
    links: List[Dict[str, Any]] = []
    all_ok = True
    for lid, fn in checks:
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"Exception: {exc}"
        if not ok:
            all_ok = False
        links.append({"link_id": lid, "ok": ok, "detail": detail})
    return {
        "ok": all_ok,
        "passed": sum(1 for x in links if x["ok"]),
        "total": len(links),
        "links": links,
    }


def _load_locks() -> Dict[str, Any]:
    if STAGE_LOCKS_PATH.exists():
        try:
            return json.loads(STAGE_LOCKS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"stages": {}}


def _save_locks(data: Dict[str, Any]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    STAGE_LOCKS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def record_stage_a_lock(source_hash: str, preflight_verdict: str, connectivity: Dict[str, Any]) -> Dict[str, Any]:
    """Update STAGE_A lock and automatically invalidate downstream locks if source_hash changed."""
    locks = _load_locks()
    prev_hash = locks.get("source_hash")
    if prev_hash and prev_hash != source_hash:
        # Invalidate every stale downstream lock when code changes!
        for st_name, st_val in locks.get("stages", {}).items():
            if isinstance(st_val, dict) and st_val.get("source_hash") != source_hash:
                st_val["status"] = "INVALIDATED"
                st_val["invalidated_reason"] = f"source_hash changed ({prev_hash} -> {source_hash})"

    stage_a_ok = (preflight_verdict == "PASS") and bool(connectivity.get("ok"))
    locks["source_hash"] = source_hash
    locks["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    locks.setdefault("stages", {})
    locks["stages"]["STAGE_A_CONNECTIVITY_PREFLIGHT"] = {
        "status": "LOCKED_PASS" if stage_a_ok else "LOCKED_FAIL",
        "source_hash": source_hash,
        "links_passed": f"{connectivity.get('passed', 0)}/{connectivity.get('total', 0)}",
        "updated_utc": locks["updated_utc"],
    }
    _save_locks(locks)
    return locks


def evaluate_stage_chain(source_hash: str, update_stage_b: bool = False) -> Dict[str, Any]:
    """Fail-closed evaluation of STAGE_A -> STAGE_B -> STAGE_C -> STAGE_D with predecessor locks."""
    from contracts.secret_vault import SecretVault

    locks = _load_locks()
    stages_saved = locks.get("stages", {})
    stage_a = stages_saved.get("STAGE_A_CONNECTIVITY_PREFLIGHT") or {}
    stage_a_valid = stage_a.get("status") == "LOCKED_PASS" and stage_a.get("source_hash") == source_hash

    vault = SecretVault()
    cc = vault.cross_check_connectivity()
    snaps = vault.list_snapshots()
    snap_ok = False
    if snaps:
        snap_ok, _, _ = vault.verify_snapshot(snaps[0]["snapshot_id"])

    if not stage_a_valid:
        stage_b_status = "BLOCKED_BY_STAGE_A"
        stage_b_detail = "STAGE_A must be LOCKED_PASS on current source_hash before unlocking STAGE_B"
    elif cc.get("esp_token_valid_on_server") is False:
        stage_b_status = "LOCKED_FAIL"
        stage_b_detail = "ESP32 token mismatch against Server tokens.json"
    elif not snap_ok:
        stage_b_status = "BLOCKED_NO_VAULT_BACKUP"
        stage_b_detail = "No SHA-256 verified snapshot in .vault/snapshots/ (run rotate_secrets.py --rotate-dual or --backup-only)"
    elif cc.get("rotation_state") == "HARDENED_PROD":
        stage_b_status = "LOCKED_PASS_HARDENED"
        stage_b_detail = "Strong 48-char tokens active, 0 weak tokens, verified vault backup present"
    elif cc.get("rotation_state") == "DUAL_GRACE":
        stage_b_status = "LOCKED_PASS_DUAL_GRACE"
        stage_b_detail = "Zero-downtime dual-token grace active (new 48-char active + bounded grace for Pi/ESP32 rollout)"
    else:
        stage_b_status = "BLOCKED_WEAK_TOKENS"
        stage_b_detail = f"rotation_state={cc.get('rotation_state')}, weak_active={cc.get('weak_active_count')}"

    stage_b_ok = stage_b_status in ("LOCKED_PASS_HARDENED", "LOCKED_PASS_DUAL_GRACE")

    # Stage C: Real Hardware Bench (requires Stage B + real hardware_bench_evidence.json matching source_hash)
    hw_ev_path = REPORTS_DIR / "hardware_bench_evidence.json"
    if not stage_b_ok:
        stage_c_status = "BLOCKED_BY_STAGE_B"
        stage_c_detail = "STAGE_B must pass before unlocking STAGE_C"
    elif not hw_ev_path.exists():
        stage_c_status = "BLOCKED_AWAITING_REAL_HW"
        stage_c_detail = "Awaiting real hardware evidence file gate_reports/hardware_bench_evidence.json"
    else:
        try:
            hw_ev = json.loads(hw_ev_path.read_text(encoding="utf-8"))
            req_gates = ("HW1_SERVER_LAN", "HW2_ESP32_AUDIO_GPS", "HW3_PI_CAMERA_SOAK", "HW4_E2E_FULL")
            if hw_ev.get("source_hash") != source_hash:
                stage_c_status = "INVALIDATED"
                stage_c_detail = f"HW evidence source_hash mismatch ({hw_ev.get('source_hash')} != {source_hash})"
            elif all(
                (hw_ev.get("gates") or {}).get(g, {}).get("status") == "PASS"
                and bool((hw_ev.get("gates") or {}).get(g, {}).get("observed_on_real_hw"))
                for g in req_gates
            ):
                stage_c_status = "LOCKED_PASS"
                stage_c_detail = "All 4 real hardware gates (HW1..HW4) verified on current source_hash"
            else:
                stage_c_status = "PARTIAL_HW_PENDING"
                stage_c_detail = "Some HW1..HW4 gates not yet PASS"
        except Exception as exc:
            stage_c_status = "LOCKED_FAIL"
            stage_c_detail = f"Invalid hardware_bench_evidence.json: {exc}"

    stage_d_status = "UNLOCKED_READY" if stage_c_status == "LOCKED_PASS" else "LOCKED_BY_STAGE_C"
    stage_d_detail = (
        "Production Core verified — ready for Phase 6 (ESP32 #2 BLE Hub)"
        if stage_c_status == "LOCKED_PASS"
        else "Phase 6 (ESP32 #2 BLE Hub) strictly blocked until STAGE_C == LOCKED_PASS"
    )

    summary = {
        "source_hash": source_hash,
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stages": {
            "STAGE_A_CONNECTIVITY_PREFLIGHT": {
                "status": "LOCKED_PASS" if stage_a_valid else stage_a.get("status", "NOT_RUN"),
                "source_hash": stage_a.get("source_hash"),
                "links_passed": stage_a.get("links_passed", "0/7"),
            },
            "STAGE_B_SECURITY_VAULT": {
                "status": stage_b_status,
                "source_hash": source_hash if stage_b_ok else None,
                "detail": stage_b_detail,
                "token_sync": cc,
            },
            "STAGE_C_REAL_HARDWARE": {
                "status": stage_c_status,
                "source_hash": source_hash if stage_c_status == "LOCKED_PASS" else None,
                "detail": stage_c_detail,
            },
            "STAGE_D_PHASE6_BLE_HUB": {
                "status": stage_d_status,
                "detail": stage_d_detail,
            },
        },
    }
    if update_stage_b:
        _save_locks(summary)
    return summary


def main() -> int:
    from contracts.gate_runner import compute_source_tree_hash

    ap = argparse.ArgumentParser(description="PI_AI Stage-Lock & Cross-Layer Connectivity Guard")
    ap.add_argument("--check", action="store_true", help="Run 7 cross-layer connectivity checks & display stage lock chain")
    ap.add_argument("--update-locks", action="store_true", help="Re-evaluate and persist stage locks (STAGE_B/C/D)")
    ap.add_argument("--strict", action="store_true", help="Exit 1 if any connectivity link fails")
    args = ap.parse_args()

    sh = compute_source_tree_hash()
    conn = verify_cross_layer_connectivity()
    chain = evaluate_stage_chain(sh, update_stage_b=args.update_locks)
    print(f"=== PI_AI STAGE GUARD [source_hash={sh}] ===")
    print(f"Cross-Layer Connectivity: {conn['passed']}/{conn['total']} ({'PASS' if conn['ok'] else 'FAIL'})")
    for lk in conn["links"]:
        print(f"  [{'PASS' if lk['ok'] else 'FAIL'}] {lk['link_id']} — {lk['detail']}")
    print("Stage-Lock Chain:")
    for sname, sinfo in chain["stages"].items():
        print(f"  [{sinfo['status']:24s}] {sname} — {sinfo.get('detail', sinfo.get('links_passed', ''))}")
    if args.strict and not conn["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
