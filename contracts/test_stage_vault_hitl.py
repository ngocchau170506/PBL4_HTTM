"""Tests for Stage-Lock Guard, 2-Mechanism SecretVault, H.264 MP4, HITL Human Review, 10s Window & ESP32 Role."""
from __future__ import annotations

import importlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

from contracts.secret_vault import SecretVault, is_weak_token, update_pi_env_tokens_text
from contracts.stage_guard import verify_cross_layer_connectivity
from pi_edge.comms.esp_client import EspClient, classify_esp_ack_line
from pi_edge.evidence.burst_collector import BurstCollector


def _make_jpg(val: int) -> bytes:
    img = np.full((240, 320, 3), val, dtype=np.uint8)
    cv2.putText(img, f"F{val}", (40, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    assert ok
    return buf.tobytes()


class TestStageVaultAndHitl(unittest.TestCase):
    def test_01_cross_layer_connectivity_all_links_pass(self):
        res = verify_cross_layer_connectivity()
        self.assertTrue(res["ok"], f"Connectivity failures: {[x for x in res['links'] if not x['ok']]}")
        self.assertEqual(res["passed"], res["total"])
        self.assertGreaterEqual(res["total"], 7)

    def test_02_esp32_role_whoami_parser(self):
        self.assertEqual(classify_esp_ack_line("EVT,ROLE,AUDIO_GPS,FW=2.0"), ("ACK", "WHOAMI"))
        cli = EspClient(mode="MOCK")
        cli.handle_incoming_line("EVT,ROLE,AUDIO_GPS,FW=2.0")
        self.assertEqual(cli.device_role, "AUDIO_GPS")

    def test_03_burst_collector_10s_continuous_window_dedup(self):
        bc = BurstCollector(ring_seconds=4.0, sample_fps=4.0, target_size=(320, 180), pre_event_s=3.0, post_event_s=7.0)
        img = np.full((360, 640, 3), 120, dtype=np.uint8)
        # Feed 4 seconds of pre-event frames (t=10.0 -> 14.0)
        t = 10.0
        for fid in range(1, 17):
            bc.push_clean_frame(fid, img, now_mono=t)
            t += 0.26
        # Trigger violation at t=14.1
        self.assertTrue(bc.start_window({"alert_type": "DROWSY", "duration_seconds": 2.1}, now_mono=14.1))
        # Second trigger during active 10s window MUST be suppressed (no duplicate clip spam!)
        self.assertFalse(bc.start_window({"alert_type": "DROWSY", "duration_seconds": 2.5}, now_mono=15.0))
        self.assertIsNone(bc.poll_completed_window(now_mono=16.0))
        # Feed post-event frames up to t=21.2 (> 14.1 + 7.0)
        for fid in range(17, 48):
            bc.push_clean_frame(fid, img, now_mono=t)
            t += 0.26
        completed = bc.poll_completed_window(now_mono=t)
        self.assertIsNotNone(completed)
        meta, jpegs, eff_fps = completed
        self.assertEqual(meta["alert_type"], "DROWSY")
        self.assertGreaterEqual(len(jpegs), 25)
        self.assertGreaterEqual(eff_fps, 2.0)
        self.assertFalse(bc.has_active_window)

    def test_04_secret_vault_dual_rotation_preserves_wifi_and_allows_grace_then_finalize(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tok_p = root / "tokens.json"
            wifi_p = root / "wifi_config.h"
            vault_p = root / ".vault"

            tok_p.write_text(json.dumps({"admin": "admin", "dev": "device"}), encoding="utf-8")
            wifi_p.write_text(
                '#define UPLINK_VEHICLE_ID "XE-01"\n'
                '#define UPLINK_AUTH_TOKEN "dev"\n'
                'static const WifiCred UPLINK_WIFI_LIST[] = { {"CKMT", "secret123"} };\n',
                encoding="utf-8",
            )

            vault = SecretVault(vault_dir=vault_p, tokens_path=tok_p, wifi_config_path=wifi_p)
            rot = vault.rotate_dual_window(grace_hours=12.0, update_esp_header=True)
            self.assertTrue(rot["ok"])
            self.assertEqual(rot["state"], "DUAL_GRACE")
            self.assertTrue(rot["esp_header_updated"])
            self.assertTrue(vault.credentials_file.exists())

            # Wi-Fi SSIDs/passwords MUST remain untouched while token became 48-char strong
            new_wifi = wifi_p.read_text(encoding="utf-8")
            self.assertIn('{"CKMT", "secret123"}', new_wifi)
            self.assertNotIn('"dev"', new_wifi)

            # Cross-check sees active match & DUAL_GRACE
            cc = vault.cross_check_connectivity()
            self.assertTrue(cc["esp_token_valid_on_server"])
            self.assertEqual(cc["esp_token_match_tier"], "ACTIVE")
            self.assertEqual(cc["rotation_state"], "DUAL_GRACE")

            # Finalize revokes weak/grace tokens -> HARDENED_PROD
            fin = vault.finalize_rotation()
            self.assertEqual(fin["state"], "HARDENED_PROD")
            self.assertGreaterEqual(fin["removed_grace"], 2)
            cc2 = vault.cross_check_connectivity()
            self.assertEqual(cc2["rotation_state"], "HARDENED_PROD")
            self.assertEqual(cc2["weak_active_count"], 0)
            self.assertEqual(cc2["grace_live_count"], 0)

    def test_05_secret_vault_tamper_detection_and_verified_rollback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tok_p = root / "tokens.json"
            wifi_p = root / "wifi_config.h"
            vault = SecretVault(vault_dir=root / ".vault", tokens_path=tok_p, wifi_config_path=wifi_p)
            tok_p.write_text('{"admin": "admin", "dev": "device"}', encoding="utf-8")
            wifi_p.write_text('#define UPLINK_AUTH_TOKEN "dev"\n', encoding="utf-8")

            snap = vault.create_snapshot(reason="baseline")
            snap_id = snap["snapshot_id"]
            vault.rotate_dual_window(grace_hours=24.0)
            # Rollback succeeds on untampered snapshot
            rb = vault.rollback_snapshot(snap_id)
            self.assertTrue(rb["ok"])
            self.assertIn('"admin": "admin"', tok_p.read_text(encoding="utf-8"))

            # Tamper 1 byte in snapshot -> rollback MUST fail-closed!
            snap_tok = root / ".vault" / "snapshots" / snap_id / "tokens.json"
            snap_tok.write_text('{"admin": "hacked"}', encoding="utf-8")
            with self.assertRaises(RuntimeError):
                vault.rollback_snapshot(snap_id)

    def test_06_pi_env_token_updater_preserves_camera_and_network_settings(self):
        orig = (
            "RUN_MODE=server\n"
            "FD_CAMERA_PASSWORD=super_cam_pass\n"
            "CAMERA_WIDTH=800\n"
            "CAMERA_ROTATION=180\n"
            "WEB_ADMIN_TOKEN=admin\n"
            "REMOTE_AUTH_TOKEN=dev\n"
        )
        roles = {
            "admin": "a" * 48,
            "operator": "b" * 48,
            "viewer": "c" * 48,
            "device": "d" * 48,
        }
        updated = update_pi_env_tokens_text(orig, roles)
        self.assertIn("FD_CAMERA_PASSWORD=super_cam_pass", updated)
        self.assertIn("CAMERA_WIDTH=800", updated)
        self.assertIn("CAMERA_ROTATION=180", updated)
        self.assertIn(f"WEB_ADMIN_TOKEN={'a' * 48}", updated)
        self.assertIn(f"REMOTE_AUTH_TOKEN={'d' * 48}", updated)
        self.assertIn(f"DEVICE_BRIDGE_TOKEN={'d' * 48}", updated)
        self.assertFalse(is_weak_token("a" * 48))
        self.assertTrue(is_weak_token("admin"))

    def test_07_dual_token_grace_auth_accepts_live_and_rejects_expired(self):
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as td:
            old_data = os.environ.get("SERVER_DATA_DIR")
            old_ev = os.environ.get("SERVER_EVIDENCE_DIR")
            old_prof = os.environ.get("SERVER_ENV_PROFILE")
            try:
                os.environ["SERVER_DATA_DIR"] = str(Path(td) / "data")
                os.environ["SERVER_EVIDENCE_DIR"] = str(Path(td) / "evidence")
                os.environ["SERVER_ENV_PROFILE"] = "prod"

                import server_ai.server_core.config as scfg
                import server_ai.server_core.database as sdb
                import server_ai.server_core.auth as sauth
                import server_ai.api_server as sapi

                importlib.reload(scfg)
                sdb.close_all_connections()
                importlib.reload(sdb)
                importlib.reload(sauth)
                sauth.reset_token_cache()
                importlib.reload(sapi)

                now_s = time.time()
                strong_adm = "1" * 48
                live_grace = "2" * 48
                expired_grace = "3" * 48
                sauth._atomic_write_store(
                    {
                        "schema_version": "2.0",
                        "active": {strong_adm: "admin"},
                        "grace": {
                            live_grace: {"role": "viewer", "expires_at": now_s + 3600},
                            expired_grace: {"role": "admin", "expires_at": now_s - 60},
                        },
                    }
                )
                with TestClient(sapi.app) as tc:
                    self.assertEqual(tc.get("/api/v1/vehicles", headers={"X-Auth-Token": strong_adm}).status_code, 200)
                    self.assertEqual(tc.get("/api/v1/vehicles", headers={"X-Auth-Token": live_grace}).status_code, 200)
                    # Expired grace token MUST be rejected fail-closed!
                    self.assertEqual(tc.get("/api/v1/vehicles", headers={"X-Auth-Token": expired_grace}).status_code, 401)
                    sh = tc.get("/api/v1/admin/security_health", headers={"X-Auth-Token": strong_adm})
                    self.assertEqual(sh.status_code, 200)
                    self.assertEqual(sh.json()["token_health"]["rotation_state"], "DUAL_GRACE")
                sdb.close_all_connections()
            finally:
                for k, v in (("SERVER_DATA_DIR", old_data), ("SERVER_EVIDENCE_DIR", old_ev), ("SERVER_ENV_PROFILE", old_prof)):
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_08_h264_mp4_stitching_and_human_in_the_loop_review_refund(self):
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as td:
            old_data = os.environ.get("SERVER_DATA_DIR")
            old_ev = os.environ.get("SERVER_EVIDENCE_DIR")
            old_prof = os.environ.get("SERVER_ENV_PROFILE")
            try:
                os.environ["SERVER_DATA_DIR"] = str(Path(td) / "data")
                os.environ["SERVER_EVIDENCE_DIR"] = str(Path(td) / "evidence")
                os.environ["SERVER_ENV_PROFILE"] = "lab"

                import server_ai.server_core.config as scfg
                import server_ai.server_core.database as sdb
                import server_ai.server_core.auth as sauth
                import server_ai.server_core.media as smedia
                import server_ai.server_core.deep_verifier as sdv
                import server_ai.api_server as sapi

                importlib.reload(scfg)
                sdb.close_all_connections()
                importlib.reload(sdb)
                importlib.reload(sauth)
                sauth.reset_token_cache()
                importlib.reload(smedia)
                importlib.reload(sdv)
                importlib.reload(sapi)

                with TestClient(sapi.app) as tc:
                    dev_h = {"X-Auth-Token": "lab-device-token"}
                    op_h = {"X-Auth-Token": "lab-operator-token"}
                    vwr_h = {"X-Auth-Token": "lab-viewer-token"}

                    # 1. Upload violation burst -> CNN confirms -> penalty applied -> H.264 MP4 stitched
                    meta = {
                        "event_id": "EVT_HITL_001",
                        "vehicle_id": "XE-HITL-01",
                        "driver_name": "Nguyen Van A",
                        "alert_type": "DROWSY",
                        "duration_seconds": 2.4,
                        "burst_fps": 4.0,
                    }
                    files = [("frames", (f"k{i:02d}.jpg", _make_jpg(90 + i * 10), "image/jpeg")) for i in range(6)]
                    r_up = tc.post("/api/v1/violation_burst", headers=dev_h, data={"meta": json.dumps(meta)}, files=files)
                    self.assertEqual(r_up.status_code, 200)
                    sdv.verifier_engine._queue.join()

                    # Verify stitched video is universal H.264 MP4 (when imageio-ffmpeg present) & HTTP 206 works
                    rows = tc.get("/api/v1/violations?vehicle_id=XE-HITL-01", headers=vwr_h).json()["violations"]
                    self.assertEqual(len(rows), 1)
                    vurl = rows[0]["video_url"]
                    self.assertTrue(vurl.endswith(("_stitched.mp4", "_stitched.webm")))
                    r_206 = tc.get(vurl, headers={**vwr_h, "Range": "bytes=0-31"})
                    self.assertEqual(r_206.status_code, 206)
                    if smedia._resolve_ffmpeg_bin():
                        self.assertTrue(vurl.endswith("_stitched.mp4"))
                        self.assertEqual(r_206.headers.get("content-type"), "video/mp4")
                        self.assertIn(b"ftyp", r_206.content)

                    # 2. Storyboard frames endpoint returns all 6 keyframes
                    r_fr = tc.get("/api/v1/violations/EVT_HITL_001/frames", headers=vwr_h)
                    self.assertEqual(r_fr.status_code, 200)
                    self.assertEqual(len(r_fr.json()["frames"]), 6)

                    # 3. Check Safety Score dropped by 6.0 (100 -> 94)
                    trip_before = tc.get("/api/v1/trips?vehicle_id=XE-HITL-01", headers=vwr_h).json()["trips"][0]
                    self.assertAlmostEqual(trip_before["safety_score"], 94.0, places=1)

                    # 4. Human operator marks HUMAN_FALSE_POSITIVE -> Safety Score refunded to 100.0 & hard_negative saved!
                    r_rev = tc.post(
                        "/api/v1/violations/EVT_HITL_001/review",
                        headers=op_h,
                        json={"decision": "HUMAN_FALSE_POSITIVE", "note": "Choi nang kinh chieu hau"},
                    )
                    self.assertEqual(r_rev.status_code, 200)
                    self.assertAlmostEqual(r_rev.json()["safety_score"], 100.0, places=1)
                    self.assertFalse(r_rev.json()["penalty_applied"])

                    # 5. Human-centric driver summary reflects refunded false positive & AN_TOAN coaching level
                    drv = tc.get("/api/v1/drivers/summary", headers=vwr_h).json()["drivers"]
                    self.assertTrue(any(d["driver_name"] == "Nguyen Van A" and d["human_false_positive"] == 1 for d in drv))
                    self.assertTrue(any(p.name.startswith("human_fp_") for p in scfg.HARD_NEGATIVES_DIR.iterdir()))
                sdb.close_all_connections()
            finally:
                for k, v in (("SERVER_DATA_DIR", old_data), ("SERVER_EVIDENCE_DIR", old_ev), ("SERVER_ENV_PROFILE", old_prof)):
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
