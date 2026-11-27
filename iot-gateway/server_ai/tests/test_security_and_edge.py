"""Red-Team Security, Anti-Storm, Schema, CNN Tier-2 & MP4 Stitcher Test Suite for server_ai."""
from __future__ import annotations

import base64
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient


def _make_jpeg_bytes(val: int = 140) -> bytes:
    img = np.full((120, 160, 3), val, dtype=np.uint8)
    cv2.putText(img, f"F{val}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


class TestServerAiSecurityAndCnn(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        os.environ["SERVER_DATA_DIR"] = self.tmpdir
        os.environ["SERVER_EVIDENCE_DIR"] = os.path.join(self.tmpdir, "evidence")
        os.environ["SERVER_ENV_PROFILE"] = "lab"
        os.environ["INCIDENT_COOLDOWN_S"] = "120"

        import server_ai.server_core.config as cfg
        import server_ai.server_core.database as db
        import server_ai.server_core.auth as auth
        import server_ai.server_core.analytics as analytics
        import server_ai.server_core.bluetooth_hub as bt
        import server_ai.server_core.mute_tracker as mt
        import server_ai.server_core.commands as cmds
        import server_ai.server_core.media as media
        import server_ai.server_core.deep_verifier as dv
        import server_ai.server_core.reports as reports
        import server_ai.api_server as api_server

        for m in (cfg, db, auth, analytics, bt, mt, cmds, media, dv, reports, api_server):
            importlib.reload(m)

        self.db = db
        self.auth = auth
        self.dv = dv
        self.api_server = api_server
        db.init_db()
        auth.validate_boot_tokens()
        self.client = TestClient(api_server.app)
        self.client.__enter__()

        self.admin = {"X-Dashboard-Token": "lab-admin-token"}
        self.operator = {"X-Dashboard-Token": "lab-operator-token"}
        self.viewer = {"X-Dashboard-Token": "lab-viewer-token"}
        self.device = {"X-Auth-Token": "lab-device-token"}

    def tearDown(self):
        try:
            self.client.__exit__(None, None, None)
        finally:
            self.db.close_all_connections()
            self._tmp.cleanup()

    def test_01_strict_boot_rejects_missing_tokens_in_prod(self):
        with tempfile.TemporaryDirectory() as td:
            old_data, old_prof = os.environ.get("SERVER_DATA_DIR"), os.environ.get("SERVER_ENV_PROFILE")
            try:
                os.environ["SERVER_DATA_DIR"] = td
                os.environ["SERVER_ENV_PROFILE"] = "prod"
                import server_ai.server_core.config as cfg
                import server_ai.server_core.auth as auth
                importlib.reload(cfg)
                importlib.reload(auth)
                with self.assertRaises(RuntimeError):
                    auth.validate_boot_tokens()
            finally:
                os.environ["SERVER_DATA_DIR"] = old_data or self.tmpdir
                os.environ["SERVER_ENV_PROFILE"] = old_prof or "lab"

    def test_02_real_auth_login_sets_cookie_enabling_evidence_playback(self):
        # Garbage token MUST fail login (unlike old dashboard.js checking /health!)
        bad = self.client.post("/api/v1/auth/login", json={"token": "garbage-token"})
        self.assertEqual(bad.status_code, 401)
        # Valid login issues HttpOnly web_token cookie
        ok = self.client.post("/api/v1/auth/login", json={"token": "lab-viewer-token"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["role"], "viewer")
        # Upload a violation image and verify viewer cookie can fetch /evidence/* (no 401!)
        jpg = _make_jpeg_bytes(130)
        data_uri = "data:image/jpeg;base64," + base64.b64encode(jpg).decode("ascii")
        v = self.client.post(
            "/api/v1/violation",
            headers=self.device,
            json={"vehicle_id": "XE-1", "event_id": "EVT_IMG1", "alert_type": "DROWSY", "evidence_image": data_uri},
        )
        self.assertEqual(v.status_code, 200)
        img_url = v.json()["image_url"]
        got = self.client.get(img_url)  # Uses session cookie from /auth/login!
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.content, jpg)

    def test_03_viewer_cannot_escalate_via_phone_pair_without_operator_otp(self):
        # Red-team S2: viewer tries fake pair_code -> MUST be 403!
        bad = self.client.post(
            "/api/v1/phone/pair",
            headers=self.viewer,
            json={"vehicle_id": "XE-1", "pair_code": "ANY_FAKE_CODE"},
        )
        self.assertEqual(bad.status_code, 403)
        # Operator mints real single-use OTP
        otp_res = self.client.post(
            "/api/v1/phone/pair_otp",
            headers=self.operator,
            json={"vehicle_id": "XE-1", "driver_id": "DRV-1"},
        )
        self.assertEqual(otp_res.status_code, 200)
        code = otp_res.json()["pair_code"]
        good = self.client.post(
            "/api/v1/phone/pair",
            headers=self.viewer,
            json={"vehicle_id": "XE-1", "pair_code": code},
        )
        self.assertEqual(good.status_code, 200)
        # Replay of same OTP MUST fail 403!
        replay = self.client.post(
            "/api/v1/phone/pair",
            headers=self.viewer,
            json={"vehicle_id": "XE-1", "pair_code": code},
        )
        self.assertEqual(replay.status_code, 403)

    def test_04_real_pi_schema_telemetry_populates_co2_temp_gps_map_stream_driver_no_false_tamper(self):
        payload = {
            "schema_version": "1.0.0",
            "vehicle_id": "XE-REAL",
            "driver_name": "Nguyễn Văn A",
            "timestamp_ms": 1795120000000,
            "access_info": {"local_ip": "192.168.1.7", "web_port": 8080, "stream_url": "http://192.168.1.7:8080/stream"},
            "camera": {"status": "ONLINE", "fps": 30.0, "face_detected": True, "is_stale": False},
            "gps": {"lat": 16.0685, "lon": 108.2215, "speed_kmh": 55.0, "satellites": 10, "is_valid": True},
            "mq135": {"co2_ppm": 620.5, "raw_adc": 1200},
            "pi": {"cpu_temp_c": 61.2, "thermal_level": 1, "throttled": "0x0"},
            "driver_state": {"alert_state": "NORMAL", "alert_type": "NORMAL", "verification_mode": "YOLO_BLAZEFACE_SEQ", "detected_classes": []},
            "esp": {"connected": True, "reset_count": 0, "brownout": False, "hw_events": []},
        }
        r = self.client.post("/api/v1/telemetry", headers=self.device, json=payload)
        self.assertEqual(r.status_code, 200)
        v = self.client.get("/api/v1/vehicles/XE-REAL", headers=self.viewer).json()
        self.assertEqual(v["current_driver_name"], "Nguyễn Văn A")
        self.assertEqual(v["stream_url"], "http://192.168.1.7:8080/stream")
        self.assertAlmostEqual(v["gps_lat"], 16.0685, places=4)
        self.assertAlmostEqual(v["gps_lon"], 108.2215, places=4)
        self.assertEqual(v["gps_valid"], 1)

        hist = self.db.get_conn().execute("SELECT co2_ppm, pi_temp_c FROM telemetry_history WHERE vehicle_id='XE-REAL'").fetchone()
        self.assertAlmostEqual(hist["co2_ppm"], 620.5, places=1)
        self.assertAlmostEqual(hist["pi_temp_c"], 61.2, places=1)

        incs = self.client.get("/api/v1/incidents?vehicle_id=XE-REAL", headers=self.viewer).json()["incidents"]
        self.assertEqual(len([x for x in incs if x["category"] == "CAMERA_TAMPER_SUSPECTED"]), 0)

    def test_05_anti_storm_watchdog_deduplicates_repeated_overheat(self):
        for _ in range(5):
            r = self.client.post(
                "/api/v1/telemetry",
                headers=self.device,
                json={"vehicle_id": "XE-HOT", "pi": {"cpu_temp_c": 76.0, "thermal_level": 3, "throttled": "0x0"}},
            )
            self.assertEqual(r.status_code, 200)
        incs = self.client.get("/api/v1/incidents?vehicle_id=XE-HOT", headers=self.viewer).json()["incidents"]
        overheat = [x for x in incs if x["category"] == "PI_OVERHEAT"]
        self.assertEqual(len(overheat), 1, "Cooldown must prevent incident storm!")

    def test_06_two_way_command_preserves_cmd_uid_and_acks_cleanly(self):
        enq = self.client.post("/api/v1/server/command", headers=self.operator, json={"vehicle_id": "XE-CMD", "cmd": "VOICE:2"})
        self.assertEqual(enq.status_code, 200)
        t = self.client.post("/api/v1/telemetry", headers=self.device, json={"vehicle_id": "XE-CMD"}).json()
        self.assertEqual(len(t["commands"]), 1)
        self.assertIn("cmd_uid", t["commands"][0])
        uid = t["commands"][0]["cmd_uid"]
        ack = self.client.post(
            "/api/v1/commands/ack",
            headers=self.device,
            json={"cmd_uid": uid, "vehicle_id": "XE-CMD", "result": "OK", "detail": "EVT,ACK,VOICE,2"},
        )
        self.assertEqual(ack.status_code, 200)
        self.assertEqual(ack.json()["status"], "EXECUTED_OK")

    def test_07_remote_config_persists_in_sqlite(self):
        r = self.client.post("/api/v1/config/XE-CFG", headers=self.admin, json={"allow_smoking": False, "allow_phone": True})
        self.assertEqual(r.status_code, 200)
        got = self.client.get("/api/v1/config/XE-CFG", headers=self.viewer).json()
        self.assertFalse(got["allow_smoking"])
        self.assertTrue(got["allow_phone"])

    def test_08_dismissal_attaches_real_active_trip_id_and_increments_mute_count(self):
        self.client.post("/api/v1/telemetry", headers=self.device, json={"vehicle_id": "XE-MUTE", "gps": {"speed_kmh": 20}})
        d = self.client.post(
            "/api/v1/events/alert_dismissed",
            headers=self.device,
            json={
                "vehicle_id": "XE-MUTE",
                "dismiss_source": "PHYSICAL_MUTE_BUTTON",
                "dismissed_alert_type": "DROWSY",
                "alert_started_at_ms": 1000,
                "dismissed_at_ms": 2200,
                "gps": {"speed_kmh": 20},
            },
        ).json()
        self.assertIsNotNone(d["trip_id"])
        self.assertEqual(d["reaction_grade"], "FAST")
        trips = self.client.get("/api/v1/trips?vehicle_id=XE-MUTE", headers=self.viewer).json()["trips"]
        self.assertEqual(trips[0]["mute_count"], 1)

    def test_09_trip_haversine_distance_updates_on_real_gps_movement(self):
        self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={"vehicle_id": "XE-GPS", "gps": {"lat": 16.0685, "lon": 108.2215, "speed_kmh": 40, "is_valid": True}},
        )
        self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={"vehicle_id": "XE-GPS", "gps": {"lat": 16.0705, "lon": 108.2215, "speed_kmh": 40, "is_valid": True}},
        )
        trips = self.client.get("/api/v1/trips?vehicle_id=XE-GPS", headers=self.viewer).json()["trips"]
        self.assertGreater(trips[0]["distance_km"], 0.15)

    def test_10_tier2_deep_verifier_confirms_real_burst_stitches_mp4_and_serves_http_206(self):
        self.client.post("/api/v1/telemetry", headers=self.device, json={"vehicle_id": "XE-CNN", "gps": {"speed_kmh": 45}})
        meta = {
            "schema_version": "1.0.0",
            "event_id": "EVT_BURST_OK",
            "idempotency_key": "IDEM_BURST_OK",
            "vehicle_id": "XE-CNN",
            "driver_name": "Tài xế Minh",
            "alert_state": "EDGE_TRIGGERED",
            "alert_type": "DROWSY",
            "verification_mode": "YOLO_BLAZEFACE_SEQ",
            "started_at": 100.0,
            "confirmed_at": 102.5,
            "duration_seconds": 2.5,
            "confidence": 0.88,
        }
        files = [("frames", (f"k{i:02d}.jpg", _make_jpeg_bytes(100 + i * 10), "image/jpeg")) for i in range(6)]
        r = self.client.post(
            "/api/v1/violation_burst",
            headers={**self.device, "Idempotency-Key": "IDEM_BURST_OK"},
            data={"meta": json.dumps(meta, ensure_ascii=False)},
            files=files,
        )
        self.assertEqual(r.status_code, 200)
        # Wait briefly for async DeepVerifier worker
        self.dv.verifier_engine._queue.join()

        rows = self.client.get("/api/v1/violations?vehicle_id=XE-CNN", headers=self.viewer).json()["violations"]
        rec = [x for x in rows if x["event_id"] == "EVT_BURST_OK"][0]
        self.assertEqual(rec["cnn_status"], "SERVER_CNN_CONFIRMED")
        self.assertIsNotNone(rec["video_url"])

        # Verify HTTP 206 Byte-Range on Server-stitched MP4!
        r206 = self.client.get(rec["video_url"], headers={**self.viewer, "Range": "bytes=0-31"})
        self.assertEqual(r206.status_code, 206)
        self.assertEqual(len(r206.content), 32)

        # Trip penalty applied ONLY after CNN confirmed!
        trips = self.client.get("/api/v1/trips?vehicle_id=XE-CNN", headers=self.viewer).json()["trips"]
        self.assertLess(trips[0]["safety_score"], 100.0)

    def test_11_tier2_deep_verifier_rejects_black_false_alarm_no_penalty_and_archives_hard_negative(self):
        self.client.post("/api/v1/telemetry", headers=self.device, json={"vehicle_id": "XE-FP", "gps": {"speed_kmh": 30}})
        meta = {
            "schema_version": "1.0.0",
            "event_id": "EVT_BURST_BLACK",
            "idempotency_key": "IDEM_BURST_BLACK",
            "vehicle_id": "XE-FP",
            "alert_state": "EDGE_TRIGGERED",
            "alert_type": "DROWSY",
            "verification_mode": "SINGLE_MODEL_YOLO",
            "started_at": 100.0,
            "confirmed_at": 102.0,
            "duration_seconds": 2.0,
        }
        black = np.zeros((120, 160, 3), dtype=np.uint8)
        _, bbuf = cv2.imencode(".jpg", black)
        files = [("frames", (f"b{i}.jpg", bbuf.tobytes(), "image/jpeg")) for i in range(4)]
        r = self.client.post(
            "/api/v1/violation_burst",
            headers={**self.device, "Idempotency-Key": "IDEM_BURST_BLACK"},
            data={"meta": json.dumps(meta)},
            files=files,
        )
        self.assertEqual(r.status_code, 200)
        self.dv.verifier_engine._queue.join()

        rows = self.client.get("/api/v1/violations?vehicle_id=XE-FP", headers=self.viewer).json()["violations"]
        rec = [x for x in rows if x["event_id"] == "EVT_BURST_BLACK"][0]
        self.assertEqual(rec["cnn_status"], "SERVER_CNN_REJECTED")
        # False alarm rejected -> driver safety score MUST stay 100!
        trips = self.client.get("/api/v1/trips?vehicle_id=XE-FP", headers=self.viewer).json()["trips"]
        self.assertEqual(trips[0]["safety_score"], 100.0)
        # Hard negative archived for retraining best.pt!
        hn_dir = Path(self.tmpdir) / "hard_negatives" / rec["burst_dir"]
        self.assertTrue((hn_dir / "verdict.json").exists())

    def test_12_oversized_content_length_rejected_413_and_audit_logs_401(self):
        r413 = self.client.post(
            "/api/v1/telemetry",
            headers={**self.device, "Content-Length": "999999999"},
            content=b"{}",
        )
        self.assertEqual(r413.status_code, 413)
        r401 = self.client.get("/api/v1/vehicles")
        self.assertEqual(r401.status_code, 401)
        aud = self.client.get("/api/v1/audit?limit=10", headers=self.admin).json()["audit_logs"]
        self.assertTrue(any(a["status_code"] == 401 for a in aud))

    def test_13_health_exposes_honest_baseline_verifier_mode_when_no_onnx(self):
        h = self.client.get("/api/v1/health").json()
        self.assertFalse(h["deep_verifier_is_real_cnn"])
        self.assertEqual(h["deep_verifier_mode"], "HEURISTIC_BASELINE")

    def test_14_login_cookie_sets_secure_behind_https_proxy_and_sse_has_no_url_token(self):
        res_https = self.client.post(
            "/api/v1/auth/login",
            headers={"X-Forwarded-Proto": "https"},
            json={"token": "lab-viewer-token"},
        )
        self.assertEqual(res_https.status_code, 200)
        set_cookie = res_https.headers.get("set-cookie", "")
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("Secure", set_cookie)

        js_src = (Path(__file__).resolve().parent.parent / "static" / "dashboard.js").read_text(encoding="utf-8")
        self.assertNotIn("/api/v1/stream/events?token=", js_src)

    def test_15_pbl4_bridge_maps_alerts_telemetry_heartbeat_and_multipart_mp4(self):
        from server_ai.server_core import config as scfg
        from server_ai.server_core.pbl4_bridge import (
            PBL4BridgeClient,
            build_pbl4_heartbeat_body,
            build_pbl4_telemetry_body,
            map_alert_to_pbl4_code,
        )

        self.assertEqual(map_alert_to_pbl4_code("DROWSY"), "MICROSLEEP")
        self.assertEqual(map_alert_to_pbl4_code("YAWNING"), "YAWNING_FATIGUE")
        self.assertEqual(map_alert_to_pbl4_code("DISTRACTED"), "PHONE_DISTRACTION")
        self.assertEqual(map_alert_to_pbl4_code("PHONE"), "PHONE_DISTRACTION")
        self.assertEqual(map_alert_to_pbl4_code("AUTO_CO2_CRIT"), "HIGH_CO2")

        tb = build_pbl4_telemetry_body(
            "XE-01",
            {"gps": {"lat": 16.07, "lon": 108.22, "speed_kmh": 42.0, "is_valid": True}, "mq135": {"co2_ppm": 710.0}},
        )
        self.assertIsNone(tb["aiViolationFlag"])
        self.assertAlmostEqual(tb["vi_do_gps"], 16.07, places=2)
        self.assertAlmostEqual(tb["co2_ppm"], 710.0, places=1)

        tb_nogps = build_pbl4_telemetry_body("XE-01", {"gps": {"lat": 0.0, "lon": 0.0, "is_valid": False}})
        self.assertNotIn("vi_do_gps", tb_nogps)

        hb = build_pbl4_heartbeat_body("XE-01", {"pi": {"cpu_temp_c": 61.5}, "camera": {"status": "OFFLINE"}})
        self.assertEqual(hb["trang_thai_ket_noi"], "ERROR_CAMERA")
        self.assertAlmostEqual(hb["cpu_temp_c"], 61.5, places=1)

        sent = []

        def fake_sender(url, body, headers, timeout_s):
            sent.append((url, body, headers))
            return 201, '{"success":true}'

        old_url, old_tok = scfg.PBL4_BACKEND_URL, scfg.PBL4_DEVICE_TOKEN
        try:
            scfg.PBL4_BACKEND_URL = "http://127.0.0.1:3000"
            scfg.PBL4_DEVICE_TOKEN = "pbl4-secret-token-123"
            vid_file = Path(self.tmpdir) / "evidence" / "sample_bridge.mp4"
            vid_file.parent.mkdir(parents=True, exist_ok=True)
            vid_file.write_bytes(b"\x00\x00\x00\x18ftypmp42FAKEVIDEO")

            br = PBL4BridgeClient(sender=fake_sender)
            ok = br.process_job_sync(
                {
                    "kind": "violation",
                    "idempotency_key": "IDEM_PBL4_1",
                    "payload": {"client_event_id": "IDEM_PBL4_1", "ma_loai": "MICROSLEEP", "ai_confidence_score": 0.88},
                    "video_path": str(vid_file),
                }
            )
            self.assertTrue(ok)
            self.assertEqual(len(sent), 1)
            url, raw_body, hdrs = sent[0]
            self.assertTrue(url.endswith("/api/v1/edge/violations"))
            self.assertEqual(hdrs["x-device-token"], "pbl4-secret-token-123")
            self.assertEqual(hdrs["Idempotency-Key"], "IDEM_PBL4_1")
            self.assertIn(b'name="payload"', raw_body)
            self.assertIn(b'name="video"; filename="sample_bridge.mp4"', raw_body)
            self.assertIn(b"MICROSLEEP", raw_body)
        finally:
            scfg.PBL4_BACKEND_URL = old_url
            scfg.PBL4_DEVICE_TOKEN = old_tok


if __name__ == "__main__":
    unittest.main()
