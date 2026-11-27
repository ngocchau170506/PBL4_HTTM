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

    def test_15_bluetooth_hub_does_not_mark_phone_connected_from_usb_hub_alone(self):
        from server_ai.server_core import bluetooth_hub, database

        bluetooth_hub.ingest_bluetooth_block(
            "XE-BT-01",
            {"hub_connected": True, "phone_connected": False, "survival_wifi_on": False, "free_heap": 111000},
        )
        v1 = database.get_vehicle("XE-BT-01")
        self.assertIsNotNone(v1)
        self.assertEqual(v1["bt_phone_connected"], 0)

        bluetooth_hub.ingest_bluetooth_block(
            "XE-BT-01",
            {"hub_connected": True, "phone_connected": True, "survival_wifi_on": False},
        )
        v2 = database.get_vehicle("XE-BT-01")
        self.assertEqual(v2["bt_phone_connected"], 1)

    def test_16_onnx_cnn_verifier_supports_2class_softmax_and_binary_logit(self):
        import cv2
        import numpy as np
        from server_ai.server_core.deep_verifier import OnnxCnnVerifier, TemporalBaselineVerifier

        class _FakeNet:
            def __init__(self, out_vec):
                self._out = np.asarray(out_vec, dtype=np.float32)

            def setInput(self, _blob):
                pass

            def forward(self):
                return self._out

        with tempfile.TemporaryDirectory() as td:
            fp = Path(td) / "f0.jpg"
            cv2.imwrite(str(fp), np.full((64, 64, 3), 120, dtype=np.uint8))

            ver = object.__new__(OnnxCnnVerifier)
            ver.model_version = "onnx-cnn:test.onnx"
            ver._fallback = TemporalBaselineVerifier()

            # 2-class softmax: violation logit >> normal logit -> CONFIRMED
            ver._net = _FakeNet([[-1.5, 2.5]])
            v_pos = ver.verify_burst([fp], {})
            self.assertEqual(v_pos.status, "SERVER_CNN_CONFIRMED")
            self.assertGreater(v_pos.confidence, 0.9)

            # 2-class softmax: normal logit >> violation logit -> REJECTED
            ver._net = _FakeNet([[2.5, -1.5]])
            v_neg = ver.verify_burst([fp], {})
            self.assertEqual(v_neg.status, "SERVER_CNN_REJECTED")
            self.assertLess(v_neg.confidence, 0.1)

            # 1-class binary logit -> CONFIRMED
            ver._net = _FakeNet([[2.0]])
            v_bin = ver.verify_burst([fp], {})
            self.assertEqual(v_bin.status, "SERVER_CNN_CONFIRMED")
            self.assertGreater(v_bin.confidence, 0.8)

    def test_17_fault_software_mute_no_double_record_or_false_abuse(self):
        # Start active trip
        r0 = self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={"vehicle_id": "XE-MUTE-DEDUP", "driver_name": "Tài xế Dedupe", "gps": {"speed_kmh": 30.0, "is_valid": False}},
        )
        self.assertEqual(r0.status_code, 200)
        # 1. Explicit PHONE_BLE dismissal arrives
        r1 = self.client.post(
            "/api/v1/events/alert_dismissed",
            headers=self.device,
            json={"vehicle_id": "XE-MUTE-DEDUP", "dismiss_source": "PHONE_BLE", "dismissed_alert_type": "DROWSY"},
        )
        self.assertEqual(r1.status_code, 200)
        # 2. Telemetry arrives <5s later echoing mute_state + MUTE_ACTIVE -> MUST deduplicate!
        r2 = self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={
                "vehicle_id": "XE-MUTE-DEDUP",
                "mute_state": {"active": True, "newly_triggered": True, "mute_duration_s": 30},
                "esp": {"connected": True, "hw_events": ["MUTE_ACTIVE"]},
            },
        )
        self.assertEqual(r2.status_code, 200)
        dism = self.client.get("/api/v1/dismissals?vehicle_id=XE-MUTE-DEDUP", headers=self.viewer).json()["dismissals"]
        self.assertEqual(len(dism), 1)
        incs = self.client.get("/api/v1/incidents?vehicle_id=XE-MUTE-DEDUP", headers=self.viewer).json()["incidents"]
        self.assertEqual([i for i in incs if i["category"] == "MUTE_ABUSE_ESCALATION"], [])
        # Also verify driver summary merges mute_count into the same driver row (no phantom XE-MUTE-DEDUP split!)
        drivers = self.client.get("/api/v1/drivers/summary", headers=self.viewer).json()["drivers"]
        matched = [d for d in drivers if "XE-MUTE-DEDUP" in d["vehicles"]]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["mute_count"], 1)

        # QC-1 Reverse order: telemetry mute arrives FIRST, then /events/alert_dismissed 10ms later -> STILL deduped!
        self.client.post("/api/v1/telemetry", headers=self.device, json={"vehicle_id": "XE-MUTE-REV"})
        self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={"vehicle_id": "XE-MUTE-REV", "mute_state": {"active": True, "newly_triggered": True, "mute_duration_s": 30}},
        )
        self.client.post(
            "/api/v1/events/alert_dismissed",
            headers=self.device,
            json={"vehicle_id": "XE-MUTE-REV", "dismiss_source": "PHONE_BLE", "dismissed_alert_type": "DROWSY"},
        )
        dism_rev = self.client.get("/api/v1/dismissals?vehicle_id=XE-MUTE-REV", headers=self.viewer).json()["dismissals"]
        self.assertEqual(len(dism_rev), 1)
        incs_rev = self.client.get("/api/v1/incidents?vehicle_id=XE-MUTE-REV", headers=self.viewer).json()["incidents"]
        self.assertEqual([i for i in incs_rev if i["category"] == "MUTE_ABUSE_ESCALATION"], [])

    def test_18_fault_sticky_throttled_esp2_survival_and_sos_no_penalty(self):
        # 1. Sticky 0x50000 must NOT spam PI_OVERHEAT; active 0x50005 MUST trigger PI_OVERHEAT
        self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={"vehicle_id": "XE-PWR", "pi": {"cpu_temp_c": 52.0, "thermal_level": 0, "throttled": "0x50000"}},
        )
        incs1 = self.client.get("/api/v1/incidents?vehicle_id=XE-PWR", headers=self.viewer).json()["incidents"]
        self.assertEqual([i for i in incs1 if i["category"] == "PI_OVERHEAT"], [])
        self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={"vehicle_id": "XE-PWR", "pi": {"cpu_temp_c": 52.0, "thermal_level": 0, "throttled": "0x50005"}},
        )
        incs2 = self.client.get("/api/v1/incidents?vehicle_id=XE-PWR", headers=self.viewer).json()["incidents"]
        self.assertEqual(len([i for i in incs2 if i["category"] == "PI_OVERHEAT"]), 1)

        # 2. Real ESP32 #2 survival payload -> status SURVIVAL_ESP2 + PI_OFFLINE_SURVIVAL_MODE incident
        surv_res = self.client.post(
            "/api/v1/esp/bridge_telemetry",
            headers=self.device,
            json={
                "schema_version": "1.0.0",
                "vehicle_id": "XE-SURV2",
                "timestamp_ms": 42000,
                "camera": {"status": "OFFLINE", "fps": 0.0, "face_detected": False, "is_stale": True},
                "pi": {"thermal_level": 0, "throttled": "0x0"},
                "driver_state": {"alert_state": "NORMAL", "alert_type": "NORMAL", "verification_mode": "ESP32_SURVIVAL", "detected_classes": []},
                "esp": {"connected": True, "direct_wifi": True, "hub_role": "COMMS_BLE_GPS_HUB", "seq": 1, "ble_connected": True},
                "gps": {"lat": 16.06, "lon": 108.21, "speed_kmh": 20.0, "satellites": 8, "source": "esp32_hub_gps_survival", "is_valid": True},
                "mq135": {"co2_ppm": 450.0, "raw_adc": 0},
            },
        )
        self.assertEqual(surv_res.status_code, 200)
        v_surv = self.client.get("/api/v1/vehicles/XE-SURV2", headers=self.viewer).json()
        self.assertEqual(v_surv["status"], "SURVIVAL_ESP2")
        self.assertEqual(v_surv["bt_phone_connected"], 1)
        incs_s = self.client.get("/api/v1/incidents?vehicle_id=XE-SURV2", headers=self.viewer).json()["incidents"]
        self.assertTrue(any(i["category"] == "PI_OFFLINE_SURVIVAL_MODE" for i in incs_s))

        # 3. SOS burst raises CRITICAL SOS_PRESSED and does NOT deduct driver safety score
        meta_sos = json.dumps({"vehicle_id": "XE-SURV2", "event_id": "EVT_SOS_1", "alert_type": "SOS", "duration_seconds": 1.0})
        r_sos = self.client.post("/api/v1/violation_burst", headers=self.device, data={"meta": meta_sos})
        self.assertEqual(r_sos.status_code, 200)
        from server_ai.server_core.deep_verifier import verifier_engine
        verifier_engine.process_job_sync({"event_id": "EVT_SOS_1", "vehicle_id": "XE-SURV2", "trip_id": surv_res.json()["trip_id"], "burst_dir": None, "meta": {"alert_type": "SOS"}})
        incs_sos = self.client.get("/api/v1/incidents?vehicle_id=XE-SURV2", headers=self.viewer).json()["incidents"]
        self.assertTrue(any(i["category"] == "SOS_PRESSED" and i["severity"] == "CRITICAL" for i in incs_sos))
        trips_s = self.client.get("/api/v1/trips?vehicle_id=XE-SURV2", headers=self.viewer).json()["trips"]
        self.assertAlmostEqual(trips_s[0]["safety_score"], 100.0, places=1)

    def test_19_fault_concurrent_sqlite_offline_last_seen_range_head_and_onnx_lock(self):
        import threading
        import time
        from server_ai.server_core import analytics, database
        from server_ai.server_core.deep_verifier import OnnxCnnVerifier, TemporalBaselineVerifier

        # 1. 8 concurrent threads upserting the same new vehicle_id -> 0 OperationalError / IntegrityError
        errs = []
        def _w(idx):
            try:
                database.upsert_vehicle("XE-CONC", vehicle_name=f"Name-{idx}", thermal_level=idx % 3)
            except Exception as exc:
                errs.append(exc)
        threads = [threading.Thread(target=_w, args=(i,)) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errs, [])

        # 2. Offline watchdog must NOT overwrite old last_seen with time.time()!
        old_ts = time.time() - 120.0
        database.upsert_vehicle("XE-OFF", status="ONLINE", last_seen=old_ts)
        analytics.check_offline_watchdog()
        v_off = database.get_vehicle("XE-OFF")
        self.assertEqual(v_off["status"], "OFFLINE")
        self.assertAlmostEqual(float(v_off["last_seen"]), old_ts, places=1)

        # 3. Malformed JSON / non-dict -> 400 across endpoints (never 500!); invalid export fmt -> 400; Range bytes=- -> 416; HEAD /evidence -> 200
        self.assertEqual(self.client.post("/api/v1/telemetry", headers=self.device, content=b"{not_json").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/auth/login", content=b"{not_json").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/violation", headers=self.device, content=b"[1,2,3]").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/server/command", headers=self.operator, content=b"[1]").status_code, 400)
        bad_fmt = self.client.get("/api/v1/reports/export?kind=trips&fmt=evil", headers=self.operator)
        self.assertEqual(bad_fmt.status_code, 400)
        # QC-4 + MAIN Policy: HUMAN_CONFIRMED & CNN_CONFIRMED on SOS and AUTO_CO2_CRIT/WARN must NOT deduct safety score!
        self.client.post("/api/v1/telemetry", headers=self.device, json={"vehicle_id": "XE-SOS-HR"})
        self.client.post("/api/v1/violation_burst", headers=self.device, data={"meta": json.dumps({"vehicle_id": "XE-SOS-HR", "event_id": "EVT_SOS_HR", "alert_type": "SOS"})})
        hr = self.client.post("/api/v1/violations/EVT_SOS_HR/review", headers=self.operator, json={"decision": "HUMAN_CONFIRMED", "note": "Verified emergency"})
        self.assertEqual(hr.status_code, 200)
        self.assertFalse(hr.json()["penalty_applied"])
        for co2_idx, co2_type in enumerate(("AUTO_CO2_CRIT", "AUTO_CO2_WARN")):
            eid = f"EVT_CO2_{co2_idx}"
            r_c = self.client.post("/api/v1/violation_burst", headers=self.device, data={"meta": json.dumps({"vehicle_id": "XE-SOS-HR", "event_id": eid, "alert_type": co2_type, "duration_seconds": 1.0})})
            self.assertEqual(r_c.status_code, 200)
            t_id = self.client.get("/api/v1/trips?vehicle_id=XE-SOS-HR", headers=self.viewer).json()["trips"][0]["trip_id"]
            from server_ai.server_core.deep_verifier import verifier_engine
            verifier_engine.process_job_sync({"event_id": eid, "vehicle_id": "XE-SOS-HR", "trip_id": t_id, "burst_dir": None, "meta": {"alert_type": co2_type}})
            hr_c = self.client.post(f"/api/v1/violations/{eid}/review", headers=self.operator, json={"decision": "HUMAN_CONFIRMED", "note": "Cabin air advisory"})
            self.assertEqual(hr_c.status_code, 200)
            self.assertFalse(hr_c.json()["penalty_applied"])
            analytics.apply_violation_penalty(t_id, co2_type)
        trips_co2 = self.client.get("/api/v1/trips?vehicle_id=XE-SOS-HR", headers=self.viewer).json()["trips"]
        self.assertAlmostEqual(trips_co2[0]["safety_score"], 100.0, places=1)
        self.assertEqual(trips_co2[0]["violation_count"], 0)
        # QC-3: SSEBroker FIFO eviction sends None sentinel so evicted stream exits cleanly
        import asyncio
        from server_ai.server_core.sse_broker import SSEBroker, MAX_SSE_SUBSCRIBERS
        async def _test_evict():
            b = SSEBroker()
            first = await b.subscribe()
            for _ in range(MAX_SSE_SUBSCRIBERS):
                await b.subscribe()
            return first.get_nowait()
        self.assertIsNone(asyncio.run(_test_evict()))
        jpg = _make_jpeg_bytes(110)
        ev_r = self.client.post(
            "/api/v1/violation",
            headers=self.device,
            json={"vehicle_id": "XE-OFF", "event_id": "EVT_HDR", "alert_type": "DROWSY", "evidence_image": base64.b64encode(jpg).decode()},
        )
        img_url = ev_r.json()["image_url"]
        self.assertEqual(self.client.head(img_url, headers=self.viewer).status_code, 200)
        self.assertEqual(self.client.get(img_url, headers={**self.viewer, "Range": "bytes=-"}).status_code, 416)

        # 4. Concurrent OnnxCnnVerifier lock guarantees zero overlapping forward() calls
        import cv2
        import numpy as np
        in_flight = {"n": 0, "overlap": False}
        class _SpyNet:
            def setInput(self, _b):
                in_flight["n"] += 1
                if in_flight["n"] > 1:
                    in_flight["overlap"] = True
                time.sleep(0.01)
            def forward(self):
                in_flight["n"] -= 1
                return np.asarray([[-1.0, 2.0]], dtype=np.float32)

        with tempfile.TemporaryDirectory() as td:
            fp = Path(td) / "f0.jpg"
            cv2.imwrite(str(fp), np.full((32, 32, 3), 100, dtype=np.uint8))
            ver = object.__new__(OnnxCnnVerifier)
            ver.model_version = "onnx-cnn:spy.onnx"
            ver._net = _SpyNet()
            ver._net_lock = threading.Lock()
            ver._fallback = TemporalBaselineVerifier()
            ts = [threading.Thread(target=ver.verify_burst, args=([fp], {})) for _ in range(4)]
            for t in ts: t.start()
            for t in ts: t.join()
            self.assertFalse(in_flight["overlap"])

    def test_20_qc_polish_co2_driver_summary_and_pbl4_bridge_a2(self):
        from server_ai.server_core import analytics, config as srv_cfg
        from server_ai.server_core.deep_verifier import verifier_engine
        from server_ai.server_core.pbl4_bridge import PBL4BridgeClient, map_alert_to_pbl4_code

        # 1. CO2_CRIT / CO2_WARN / whitespace-padded AUTO_CO2_CRIT:
        #    - NEVER deduct safety score or inflate driver summary violation counts
        #    - Telemetry watchdog STILL raises CO2_CRIT incident at >= 1500 ppm
        self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={"vehicle_id": "XE-CO2-QC", "driver_name": "TaiXeCO2", "mq135": {"co2_ppm": 1650.0, "raw_adc": 900}},
        )
        incs = self.client.get("/api/v1/incidents?vehicle_id=XE-CO2-QC", headers=self.viewer).json()["incidents"]
        self.assertTrue(any(i["category"] == "CO2_CRIT" for i in incs))

        t_id = self.client.get("/api/v1/trips?vehicle_id=XE-CO2-QC", headers=self.viewer).json()["trips"][0]["trip_id"]
        for idx, atype in enumerate(("CO2_CRIT", "CO2_WARN", " AUTO_CO2_CRIT ")):
            eid = f"EVT_QC_CO2_{idx}"
            r = self.client.post(
                "/api/v1/violation_burst",
                headers=self.device,
                data={"meta": json.dumps({"vehicle_id": "XE-CO2-QC", "driver_name": "TaiXeCO2", "event_id": eid, "alert_type": atype})},
            )
            self.assertEqual(r.status_code, 200)
            verifier_engine.process_job_sync({"event_id": eid, "vehicle_id": "XE-CO2-QC", "trip_id": t_id, "burst_dir": None, "meta": {"alert_type": atype}})
            hr = self.client.post(f"/api/v1/violations/{eid}/review", headers=self.operator, json={"decision": "HUMAN_CONFIRMED"})
            self.assertEqual(hr.status_code, 200)
            self.assertFalse(hr.json()["penalty_applied"])

        trips = self.client.get("/api/v1/trips?vehicle_id=XE-CO2-QC", headers=self.viewer).json()["trips"]
        self.assertAlmostEqual(trips[0]["safety_score"], 100.0, places=1)
        self.assertEqual(trips[0]["violation_count"], 0)
        drivers = self.client.get("/api/v1/drivers/summary", headers=self.viewer).json()["drivers"]
        d_co2 = next(d for d in drivers if d["driver_name"] == "TaiXeCO2")
        self.assertEqual(d_co2["ai_confirmed"], 0)
        self.assertEqual(d_co2["human_confirmed"], 0)

        # 1b. No-face tamper auto-alert is disabled by default (ENABLE_NO_FACE_TAMPER_ALERT=False)
        from server_ai.server_core import database as srv_db
        self.assertFalse(srv_cfg.ENABLE_NO_FACE_TAMPER_ALERT)
        srv_db.upsert_vehicle("XE-NOFACE", last_face_seen_at=1.0)
        self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={"vehicle_id": "XE-NOFACE", "gps": {"speed_kmh": 50.0}, "camera": {"face_detected": False}},
        )
        nf_incs = self.client.get("/api/v1/incidents?vehicle_id=XE-NOFACE", headers=self.viewer).json()["incidents"]
        self.assertEqual([i for i in nf_incs if i["category"] == "CAMERA_TAMPER_SUSPECTED"], [])

        # 1c. BLE ACK_AWAKE & REST_BREAK_START right after MUTE must NOT trigger false MUTE_ABUSE_ESCALATION!
        self.client.post("/api/v1/telemetry", headers=self.device, json={"vehicle_id": "XE-BLE-ACK", "driver_name": "TaiXeBLE"})
        self.assertEqual(
            self.client.post("/api/v1/events/alert_dismissed", headers=self.device, json={"vehicle_id": "XE-BLE-ACK", "dismiss_source": "PHONE_BLE", "dismissed_alert_type": "DROWSY"}).status_code,
            200,
        )
        self.assertEqual(
            self.client.post("/api/v1/events/alert_dismissed", headers=self.device, json={"vehicle_id": "XE-BLE-ACK", "dismiss_source": "PHONE_BLE", "dismissed_alert_type": "ACK_AWAKE"}).status_code,
            200,
        )
        self.assertEqual(
            self.client.post("/api/v1/events/alert_dismissed", headers=self.device, json={"vehicle_id": "XE-BLE-ACK", "dismiss_source": "PHONE_BLE", "dismissed_alert_type": "REST_BREAK_START"}).status_code,
            200,
        )
        ble_incs = self.client.get("/api/v1/incidents?vehicle_id=XE-BLE-ACK", headers=self.viewer).json()["incidents"]
        self.assertEqual([i for i in ble_incs if i["category"] == "MUTE_ABUSE_ESCALATION"], [])
        ble_trips = self.client.get("/api/v1/trips?vehicle_id=XE-BLE-ACK", headers=self.viewer).json()["trips"]
        self.assertEqual(ble_trips[0]["mute_count"], 1)

        # 2. A2 PBL4 Bridge: disabled by default, retries transient 503->200, skips non-penalty CO2 violation
        self.assertEqual(map_alert_to_pbl4_code("DROWSY"), "MICROSLEEP")
        self.assertEqual(map_alert_to_pbl4_code("YAWNING"), "YAWNING_FATIGUE")
        self.assertEqual(map_alert_to_pbl4_code("PHONE"), "PHONE_DISTRACTION")
        old_url = srv_cfg.PBL4_BACKEND_URL
        try:
            srv_cfg.PBL4_BACKEND_URL = ""
            br_off = PBL4BridgeClient()
            self.assertFalse(br_off.enabled)
            self.assertFalse(br_off.forward_violation(event_id="E0", idempotency_key="K0", vehicle_id="XE-1", alert_type="DROWSY", confidence=0.9, detail="x"))

            srv_cfg.PBL4_BACKEND_URL = "http://127.0.0.1:3000"
            calls = []
            def _flaky_sender(url, body, headers, timeout):
                calls.append((url, headers))
                return (503, "busy") if len(calls) == 1 else (200, '{"ok":true}')

            br = PBL4BridgeClient(sender=_flaky_sender, max_retries=3, retry_backoff_s=0.0)
            self.assertFalse(br.forward_violation(event_id="E1", idempotency_key="K1", vehicle_id="XE-1", alert_type="AUTO_CO2_CRIT", confidence=0.9, detail="co2"))
            self.assertTrue(br.forward_violation(event_id="E2", idempotency_key="K2", vehicle_id="XE-1", alert_type="DROWSY", confidence=0.92, detail="eyes closed"))
            job = br._queue.get_nowait()
            self.assertTrue(br.process_job_sync(job))
            self.assertEqual(len(calls), 2)
            self.assertTrue(calls[0][0].endswith("/api/v1/edge/violations"))
            self.assertEqual(calls[0][1]["Idempotency-Key"], "K2")
        finally:
            srv_cfg.PBL4_BACKEND_URL = old_url

    def test_21_security_hardening_stream_url_idor_otp_public_and_logout(self):
        # 1. Public attacker stream_url MUST be rejected/stripped; private LAN /stream accepted; null clears stale URL
        r_evil = self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={"vehicle_id": "XE-SEC", "access_info": {"local_ip": "192.168.1.9", "stream_url": "https://attacker.tld/steal"}},
        )
        self.assertEqual(r_evil.status_code, 200)
        v_evil = self.client.get("/api/v1/vehicles/XE-SEC", headers=self.viewer).json()
        self.assertIsNone(v_evil["stream_url"])

        self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={"vehicle_id": "XE-SEC", "access_info": {"local_ip": "192.168.1.9", "stream_url": "http://192.168.1.9:8080/stream"}},
        )
        self.assertEqual(
            self.client.get("/api/v1/vehicles/XE-SEC", headers=self.viewer).json()["stream_url"],
            "http://192.168.1.9:8080/stream",
        )
        self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={"vehicle_id": "XE-SEC", "access_info": {"local_ip": "192.168.1.9", "stream_url": None}},
        )
        self.assertIsNone(self.client.get("/api/v1/vehicles/XE-SEC", headers=self.viewer).json()["stream_url"])

        # 2. Unauthenticated driver phone on /driver can redeem valid operator OTP (both /pair_otp & /pair/otp)
        #    AND paired driver_phone token MUST be bound to its vehicle_id (blocks cross-vehicle IDOR!)
        otp_r = self.client.post("/api/v1/phone/pair/otp", headers=self.operator, json={"vehicle_id": "XE-A", "driver_id": "DRV-A"})
        self.assertEqual(otp_r.status_code, 200)
        pair_r = self.client.post("/api/v1/phone/pair", json={"vehicle_id": "XE-A", "pair_code": otp_r.json()["pair_code"]})
        self.assertEqual(pair_r.status_code, 200)
        drv_tok = pair_r.json()["driver_phone_token"]
        self.assertEqual(
            self.client.post(
                "/api/v1/phone/action",
                headers={"Authorization": f"Bearer {drv_tok}"},
                json={"vehicle_id": "XE-A", "action": "ACK_AWAKE"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/phone/action",
                headers={"Authorization": f"Bearer {drv_tok}"},
                json={"vehicle_id": "XE-B", "action": "MUTE_ALERT"},
            ).status_code,
            403,
        )

        # 3. REMOTE_OPERATOR mute must NOT accuse driver of MUTE_ABUSE_ESCALATION
        for _ in range(4):
            self.assertEqual(
                self.client.post(
                    "/api/v1/events/alert_dismissed",
                    headers=self.device,
                    json={"vehicle_id": "XE-OPMUTE", "dismiss_source": "REMOTE_OPERATOR", "dismissed_alert_type": "DROWSY", "gps": {"speed_kmh": 80.0}},
                ).status_code,
                200,
            )
        op_incs = self.client.get("/api/v1/incidents?vehicle_id=XE-OPMUTE", headers=self.viewer).json()["incidents"]
        self.assertEqual([i for i in op_incs if i["category"] == "MUTE_ABUSE_ESCALATION"], [])

        # 4. Query ?token= must NOT work on mutating POST; safe revoke by fingerprint & logout work cleanly
        self.assertEqual(
            self.client.post("/api/v1/server/command?token=lab-admin-token", json={"vehicle_id": "XE-1", "cmd": "PING"}).status_code,
            401,
        )
        issued = self.client.post("/api/v1/admin/tokens", headers=self.admin, json={"role": "viewer"}).json()
        self.assertGreaterEqual(len(issued["token"]), 48)
        rev = self.client.post("/api/v1/admin/tokens/revoke", headers=self.admin, json={"fingerprint": issued["fp"]})
        self.assertEqual(rev.status_code, 200)
        self.assertTrue(rev.json()["revoked"])
        self.assertEqual(self.client.post("/api/v1/auth/logout").status_code, 200)

        # 5. Dashboard static guard: no unescaped esp1.vol / esp2.free_heap and never attach TOKEN to external http URLs
        dash_js = (Path(__file__).resolve().parent.parent / "static" / "dashboard.js").read_text(encoding="utf-8")
        self.assertNotIn("Vol:${esp1.vol", dash_js)
        self.assertNotIn("Heap:${esp2.free_heap", dash_js)
        self.assertIn("isPrivateLanStreamUrl", dash_js)

    def test_21_real_gps_positioning_engine_no_sample_anchor_track_and_phone_gnss(self):
        """Verify REAL WGS-84/ECEF/UTM/ENU GPS calculation, track history, phone GNSS backup, and NO sample coords."""
        # 1. Legacy sample anchor injection MUST be disabled (HTTP 410 Gone)
        r_inj = self.client.post(
            "/api/v1/geo/inject_xyz",
            headers=self.operator,
            json={"vehicle_id": "XE-GEO", "lat": 10.0, "lon": 106.0},
        )
        self.assertEqual(r_inj.status_code, 410)

        # 2. Unfixed ESP32 #2 telemetry -> honest NO_FIX with UART2 diagnostics (zero fabricated lat/lon!)
        self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={
                "vehicle_id": "XE-GEO",
                "gps": {
                    "is_valid": False,
                    "lat": 0.0,
                    "lon": 0.0,
                    "speed_kmh": 0.0,
                    "satellites": 0,
                    "sats_in_view": 5,
                    "gps_rx_bytes": 1024,
                    "nmea_ok": 12,
                },
            },
        )
        loc0 = self.client.get("/api/v1/vehicles/XE-GEO/location", headers=self.viewer).json()
        self.assertEqual(loc0["fix_status"], "NO_FIX")
        self.assertFalse(loc0["has_position"])
        self.assertIsNone(loc0["lat"])
        self.assertEqual(loc0["diagnostics"]["sats_in_view"], 5)
        self.assertIn("ACQUIRING_FIX_5", loc0["diagnostics"]["wiring_status"])

        # 3. Pair driver phone & send REAL phone GNSS fallback -> LIVE_PHONE_GNSS with ECEF + UTM + DMS!
        otp = self.client.post("/api/v1/phone/pair/otp", headers=self.operator, json={"vehicle_id": "XE-GEO"}).json()["pair_code"]
        ptok = self.client.post("/api/v1/phone/pair", json={"vehicle_id": "XE-GEO", "pair_code": otp}).json()["driver_phone_token"]
        r_ph = self.client.post(
            "/api/v1/phone/telemetry",
            headers={"Authorization": f"Bearer {ptok}"},
            json={
                "vehicle_id": "XE-GEO",
                "phone_gps": {
                    "is_valid": True,
                    "lat": 16.068500,
                    "lon": 108.221500,
                    "altitude_m": 12.0,
                    "accuracy_m": 6.5,
                    "speed_kmh": 18.0,
                    "heading_deg": 45.0,
                },
            },
        )
        self.assertEqual(r_ph.status_code, 200)
        self.assertTrue(r_ph.json()["gps_result"]["accepted"])

        loc1 = self.client.get("/api/v1/vehicles/XE-GEO/location", headers=self.viewer).json()
        self.assertEqual(loc1["fix_status"], "LIVE_PHONE_GNSS")
        self.assertTrue(loc1["is_live"])
        self.assertAlmostEqual(loc1["lat"], 16.0685, places=4)
        self.assertEqual(loc1["utm"]["zone_label"], "49Q")
        self.assertEqual(loc1["compass"], "Đông Bắc")
        self.assertIsNotNone(loc1["ecef"]["x_m"])

        # 4. Real ESP32 #2 GNSS fix takes priority over Phone GNSS & builds metric ENU track!
        for lat, lon, spd in (
            (16.070000, 108.221500, 35.0),
            (16.071500, 108.221500, 40.0),
        ):
            self.client.post(
                "/api/v1/telemetry",
                headers=self.device,
                json={
                    "vehicle_id": "XE-GEO",
                    "gps": {
                        "is_valid": True,
                        "lat": lat,
                        "lon": lon,
                        "altitude_m": 15.0,
                        "speed_kmh": spd,
                        "heading_deg": 0.0,
                        "hdop": 1.2,
                        "fix_quality": 1,
                        "satellites": 10,
                        "source": "esp32_hub_gps",
                    },
                },
            )

        loc2 = self.client.get("/api/v1/vehicles/XE-GEO/location", headers=self.viewer).json()
        self.assertEqual(loc2["fix_status"], "LIVE_ESP2_GNSS")
        self.assertEqual(loc2["accuracy_m"], 3.0)
        self.assertAlmostEqual(loc2["lat"], 16.0715, places=4)

        fleet = self.client.get("/api/v1/fleet/positions", headers=self.viewer).json()["positions"]
        self.assertTrue(any(p["vehicle_id"] == "XE-GEO" and p["fix_status"] == "LIVE_ESP2_GNSS" for p in fleet))

        trk = self.client.get("/api/v1/vehicles/XE-GEO/track?limit=50", headers=self.viewer).json()
        self.assertEqual(trk["point_count"], 3)
        self.assertGreater(trk["total_distance_km"], 0.30)
        self.assertAlmostEqual(trk["points"][-1]["north_m"], 333.0, delta=15.0)

    def test_22_calibrate_whitelist_phone_action_idor_and_thermal_80c(self):
        """Verify CALIBRATE command whitelist, phone_action CALIBRATE_LOCK auto-enqueue + IDOR block, and 75/80°C thermal thresholds."""
        import server_ai.server_core.commands as srv_cmds
        import server_ai.server_core.config as srv_cfg

        # 1. Thermal thresholds updated per MAIN approval (75.0°C / 80.0°C)
        self.assertEqual(srv_cfg.PI_THERMAL_CRIT1_C, 75.0)
        self.assertEqual(srv_cfg.PI_THERMAL_CRIT2_C, 80.0)

        # 76.0°C with thermal_level=2 & throttled=0x0 MUST NOT raise PI_OVERHEAT under new 80°C threshold
        self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={"vehicle_id": "XE-TH80", "pi": {"cpu_temp_c": 76.0, "thermal_level": 2, "throttled": "0x0"}},
        )
        incs_warm = self.client.get("/api/v1/incidents?vehicle_id=XE-TH80", headers=self.viewer).json()["incidents"]
        self.assertEqual([i for i in incs_warm if i["category"] == "PI_OVERHEAT"], [])

        # >= 80.0°C MUST raise PI_OVERHEAT
        self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={"vehicle_id": "XE-TH80", "pi": {"cpu_temp_c": 80.5, "thermal_level": 2, "throttled": "0x0"}},
        )
        incs_crit = self.client.get("/api/v1/incidents?vehicle_id=XE-TH80", headers=self.viewer).json()["incidents"]
        self.assertEqual(len([i for i in incs_crit if i["category"] == "PI_OVERHEAT"]), 1)

        # 2. CALIBRATE command accepted by operator /api/v1/server/command
        self.assertTrue(srv_cmds.validate_command("CALIBRATE"))
        r_op = self.client.post("/api/v1/server/command", headers=self.operator, json={"vehicle_id": "XE-CAL-OP", "cmd": "CALIBRATE"})
        self.assertEqual(r_op.status_code, 200)
        self.assertEqual(r_op.json()["command"], "CALIBRATE")

        # 3. Paired driver phone CALIBRATE_LOCK auto-enqueues CALIBRATE for its bound vehicle & blocks cross-vehicle IDOR
        otp_a = self.client.post("/api/v1/phone/pair_otp", headers=self.operator, json={"vehicle_id": "XE-CAL-A", "driver_id": "DRV-CAL"}).json()["pair_code"]
        drv_tok = self.client.post("/api/v1/phone/pair", json={"vehicle_id": "XE-CAL-A", "pair_code": otp_a}).json()["driver_phone_token"]

        # Cross-vehicle IDOR attempt onto XE-CAL-B MUST be rejected 403 with zero queued commands
        r_idor = self.client.post(
            "/api/v1/phone/action",
            headers={"Authorization": f"Bearer {drv_tok}"},
            json={"vehicle_id": "XE-CAL-B", "action": "CALIBRATE_LOCK"},
        )
        self.assertEqual(r_idor.status_code, 403)
        self.assertEqual(self.client.get("/api/v1/commands?vehicle_id=XE-CAL-B", headers=self.viewer).json()["commands"], [])

        # Legitimate CALIBRATE_LOCK on XE-CAL-A enqueues CALIBRATE immediately & records cabin_sensor_events
        r_cal = self.client.post(
            "/api/v1/phone/action",
            headers={"Authorization": f"Bearer {drv_tok}"},
            json={"vehicle_id": "XE-CAL-A", "action": "CALIBRATE_LOCK", "detail": {"source": "PWA_HTTP"}},
        )
        self.assertEqual(r_cal.status_code, 200)
        cmd_id = r_cal.json().get("command_id")
        self.assertTrue(cmd_id and cmd_id.startswith("CMD_"))

        ev_row = self.db.get_conn().execute(
            "SELECT sensor_type, payload_json FROM cabin_sensor_events WHERE vehicle_id='XE-CAL-A' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(ev_row)
        self.assertEqual(ev_row["sensor_type"], "PHONE_CALIBRATE_LOCK")
        self.assertIn(cmd_id, ev_row["payload_json"])

        # Next telemetry cycle delivers CALIBRATE to XE-CAL-A and accepts optional driver_state.calibration
        t_resp = self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={
                "vehicle_id": "XE-CAL-A",
                "driver_state": {
                    "alert_state": "NORMAL",
                    "alert_type": "NORMAL",
                    "verification_mode": "YOLO_BLAZEFACE_SEQ",
                    "detected_classes": [],
                    "calibration": {
                        "state": "RUNNING",
                        "mode": "APP_LOCK",
                        "progress_s": 3.5,
                        "duration_s": 10.0,
                        "locked": False,
                        "profile_ts": None,
                        "ear_closed_threshold": None,
                        "glare_mode": False,
                    },
                },
            },
        ).json()
        self.assertEqual(t_resp["commands"], [{"cmd_uid": cmd_id, "cmd": "CALIBRATE"}])

        ack_r = self.client.post(
            "/api/v1/commands/ack",
            headers=self.device,
            json={"cmd_uid": cmd_id, "vehicle_id": "XE-CAL-A", "result": "OK", "detail": "CALIBRATION_STARTED"},
        )
        self.assertEqual(ack_r.status_code, 200)
        self.assertEqual(ack_r.json()["status"], "EXECUTED_OK")

    def test_23_phone_status_idor_mute_enqueue_and_lan_cors(self):
        """Verify GET /api/v1/phone/status (with cross-vehicle IDOR 403), MUTE/REST enqueue via phone_action, and safe LAN CORS."""
        # 1. Seed telemetry on XE-PH-1 with calibration + GPS + Pi temp
        self.client.post(
            "/api/v1/telemetry",
            headers=self.device,
            json={
                "vehicle_id": "XE-PH-1",
                "camera": {"status": "ONLINE", "fps": 28.5, "face_detected": True},
                "gps": {"lat": 16.0685, "lon": 108.2215, "speed_kmh": 42.0, "satellites": 9, "is_valid": True},
                "pi": {"cpu_temp_c": 58.4, "thermal_level": 0},
                "driver_state": {
                    "alert_state": "NORMAL",
                    "alert_type": "NORMAL",
                    "calibration": {"state": "DONE", "locked": True, "progress_s": 10.0, "duration_s": 10.0, "ear_closed_threshold": 0.19},
                },
            },
        )
        otp = self.client.post("/api/v1/phone/pair_otp", headers=self.operator, json={"vehicle_id": "XE-PH-1"}).json()["pair_code"]
        drv_tok = self.client.post("/api/v1/phone/pair", json={"vehicle_id": "XE-PH-1", "pair_code": otp}).json()["driver_phone_token"]
        drv_hdr = {"Authorization": f"Bearer {drv_tok}"}

        # 2. Bound vehicle gets live status; another vehicle gets 403 IDOR blocked
        st_ok = self.client.get("/api/v1/phone/status?vehicle_id=XE-PH-1", headers=drv_hdr)
        self.assertEqual(st_ok.status_code, 200)
        body = st_ok.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["source"], "server_http")
        self.assertEqual(body["vehicle_id"], "XE-PH-1")
        self.assertEqual(body["vehicle_status"], "ONLINE")
        self.assertEqual(body["calibration"]["state"], "DONE")
        self.assertTrue(body["calibration"]["locked"])
        self.assertAlmostEqual(body["gps"]["speed_kmh"], 42.0, places=1)
        self.assertAlmostEqual(body["pi"]["cpu_temp_c"], 58.4, places=1)

        st_idor = self.client.get("/api/v1/phone/status?vehicle_id=XE-OTHER", headers=drv_hdr)
        self.assertEqual(st_idor.status_code, 403)

        # 3. MUTE_ALERT enqueues MUTE & REST_BREAK_START enqueues REST_BREAK_REQUEST
        r_mute = self.client.post("/api/v1/phone/action", headers=drv_hdr, json={"vehicle_id": "XE-PH-1", "action": "MUTE_ALERT"})
        self.assertEqual(r_mute.status_code, 200)
        mute_uid = r_mute.json().get("command_id")
        self.assertTrue(mute_uid and mute_uid.startswith("CMD_"))

        r_rest = self.client.post("/api/v1/phone/action", headers=drv_hdr, json={"vehicle_id": "XE-PH-1", "action": "REST_BREAK_START"})
        self.assertEqual(r_rest.status_code, 200)
        rest_uid = r_rest.json().get("command_id")
        self.assertTrue(rest_uid and rest_uid.startswith("CMD_"))

        pulled = self.client.get("/api/v1/commands/pull?vehicle_id=XE-PH-1", headers=self.device).json()["commands"]
        self.assertEqual([c["cmd"] for c in pulled], ["MUTE", "REST_BREAK_REQUEST"])

        # 4. LAN CORS allows private LAN origin, blocks evil public origin
        lan_ok = self.client.get("/api/v1/health", headers={"Origin": "http://192.168.1.9:8080"})
        self.assertEqual(lan_ok.status_code, 200)
        self.assertEqual(lan_ok.headers.get("access-control-allow-origin"), "http://192.168.1.9:8080")

        lan_pre = self.client.options(
            "/api/v1/phone/status",
            headers={"Origin": "http://192.168.1.9:8080", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "Authorization"},
        )
        self.assertEqual(lan_pre.status_code, 200)
        self.assertEqual(lan_pre.headers.get("access-control-allow-origin"), "http://192.168.1.9:8080")

        evil_get = self.client.get("/api/v1/health", headers={"Origin": "https://evil.example.com"})
        self.assertEqual(evil_get.status_code, 200)
        self.assertNotIn("access-control-allow-origin", {k.lower() for k in evil_get.headers.keys()})


if __name__ == "__main__":
    unittest.main()
