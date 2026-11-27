"""Cross-System Contract Integration Tests: REAL pi_edge client -> REAL server_ai FastAPI.

Prevents confirmation bias (no synthetic simulator guessing schemas!). Every payload generated
by pi_edge.comms.server_sync.ServerSyncClient and firmware survival JSON is validated against
contracts/pi_server_schema.json and ingested by the real server_ai FastAPI app.
"""
from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from contracts.schema_validator import validate_telemetry_payload, validate_violation_burst_meta
from pi_edge.comms.esp_client import EspClient
from pi_edge.comms.server_sync import ServerSyncClient
from pi_edge.core.config import EdgeConfig
from pi_edge.evidence.burst_collector import BurstCollector
from pi_edge.evidence.spool_store import UnifiedSpoolStore


class _TestClientRequestsAdapter:
    """Bridges ServerSyncClient's requests.Session interface directly onto FastAPI TestClient."""

    def __init__(self, tc: TestClient):
        self._tc = tc

    def post(self, url: str, json=None, data=None, files=None, headers=None, timeout=None):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        return self._tc.post(path, json=json, data=data, files=files, headers=headers)

    def close(self):
        pass


class TestCrossSystemContract(unittest.TestCase):
    def setUp(self):
        self._srv_tmp = tempfile.TemporaryDirectory()
        self._pi_tmp = tempfile.TemporaryDirectory()
        os.environ["SERVER_DATA_DIR"] = self._srv_tmp.name
        os.environ["SERVER_EVIDENCE_DIR"] = os.path.join(self._srv_tmp.name, "evidence")
        os.environ["SERVER_ENV_PROFILE"] = "lab"

        import server_ai.server_core.config as scfg
        import server_ai.server_core.database as sdb
        import server_ai.server_core.auth as sauth
        import server_ai.server_core.analytics as sanal
        import server_ai.server_core.bluetooth_hub as sbt
        import server_ai.server_core.mute_tracker as smt
        import server_ai.server_core.commands as scmds
        import server_ai.server_core.media as smedia
        import server_ai.server_core.deep_verifier as sdv
        import server_ai.server_core.reports as srep
        import server_ai.api_server as sapi

        for m in (scfg, sdb, sauth, sanal, sbt, smt, scmds, smedia, sdv, srep, sapi):
            importlib.reload(m)

        self.sdb = sdb
        self.sdv = sdv
        sdb.init_db()
        sauth.validate_boot_tokens()
        self.tc = TestClient(sapi.app)
        self.tc.__enter__()

        self.admin_h = {"X-Dashboard-Token": "lab-admin-token"}
        self.op_h = {"X-Dashboard-Token": "lab-operator-token"}
        self.vwr_h = {"X-Dashboard-Token": "lab-viewer-token"}

        self.pi_cfg = EdgeConfig.from_env(
            env={
                "RUN_MODE": "server",
                "ENV_PROFILE": "lab",
                "VEHICLE_ID": "XE-CROSS-01",
                "DRIVER_NAME": "Nguyễn Văn Tài Xế",
                "REMOTE_SERVER_URL": "http://testserver",
                "VIOLATION_STORAGE_DIR": os.path.join(self._pi_tmp.name, "violations"),
                "LOGS_DIR": os.path.join(self._pi_tmp.name, "logs"),
            }
        )
        self.spool = UnifiedSpoolStore(self.pi_cfg.storage_dir)

    def tearDown(self):
        try:
            self.spool.close()
        finally:
            try:
                self.tc.__exit__(None, None, None)
            finally:
                self.sdb.close_all_connections()
                self._srv_tmp.cleanup()
                self._pi_tmp.cleanup()

    def test_01_pi_telemetry_conforms_to_contract_and_ingests_cleanly_on_server(self):
        sync = ServerSyncClient(
            self.pi_cfg,
            auth_token="lab-device-token",
            spool=self.spool,
            session=_TestClientRequestsAdapter(self.tc),
        )
        payload = sync.build_telemetry_payload(
            ai_state={"alert_state": "NORMAL", "alert_type": "NORMAL", "verification_mode": "YOLO_BLAZEFACE_SEQ", "face_detected": True, "thermal_level": 1},
            hw_snap={"temp_c": 59.5, "cpu_percent": 42.0, "ram_mb": 420.0, "throttled": "0x0"},
            fps_snap={"camera_fps": 30.0, "loop_fps": 30.1, "ai_fps": 4.2, "stream_fps": 0.0, "alert_latency_p95_ms": 2.4},
            esp_sensor={"co2_ppm": 710.0, "raw_adc": 1450, "gps": {"lat": 16.0685, "lon": 108.2215, "speed_kmh": 48.0, "satellites": 11, "is_valid": True, "source": "esp32_gps"}},
            esp_status={"connected": True, "reset_count": 0, "brownout": False, "hw_events": []},
            mute_state={"active": False, "newly_triggered": False},
        )
        ok, errs = validate_telemetry_payload(payload)
        self.assertTrue(ok, f"Schema errors: {errs}")

        resp = sync.send_telemetry_once(payload)
        self.assertIsNotNone(resp)
        self.assertEqual(resp["ack"], "TELEMETRY_RECEIVED")

        veh = self.tc.get("/api/v1/vehicles/XE-CROSS-01", headers=self.vwr_h).json()
        self.assertEqual(veh["current_driver_name"], "Nguyễn Văn Tài Xế")
        self.assertAlmostEqual(veh["gps_lat"], 16.0685, places=4)
        # LCK-04 v2: live /stream is OFF by default in RUN_MODE=server to save Pi CPU & 4G bandwidth
        self.assertIsNone(veh["stream_url"])

    def test_02_pi_vietnamese_jpeg_burst_upload_idempotency_and_server_cnn_stitch(self):
        sync = ServerSyncClient(
            self.pi_cfg,
            auth_token="lab-device-token",
            spool=self.spool,
            session=_TestClientRequestsAdapter(self.tc),
        )
        bc = BurstCollector(sample_fps=5.0, max_burst_frames=8)
        for i in range(8):
            img = np.full((240, 320, 3), 100 + i * 10, dtype=np.uint8)
            cv2.circle(img, (160, 120), 40, (255, 255, 255), 2)
            bc.push_clean_frame(i + 1, img, now_mono=i * 0.25)
        jpegs = bc.extract_jpeg_burst()

        eid = sync.queue_violation_burst(
            alert_type="DROWSY",
            verification_mode="YOLO_BLAZEFACE_SEQ",
            duration_seconds=2.3,
            jpeg_frames=jpegs,
            gps={"lat": 16.0685, "lon": 108.2215, "speed_kmh": 50.0},
            co2_ppm=680.0,
            behavior_detail="Nhắm mắt 2.3s/2.0s",
        )
        due = self.spool.fetch_due(limit=5)
        ok_m, errs_m = validate_violation_burst_meta(due[0]["meta"])
        self.assertTrue(ok_m, f"Burst meta errors: {errs_m}")

        uploaded = sync.drain_spool_once()
        self.assertEqual(uploaded, 1)
        self.sdv.verifier_engine._queue.join()

        rows = self.tc.get("/api/v1/violations?vehicle_id=XE-CROSS-01", headers=self.vwr_h).json()["violations"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], eid)
        self.assertEqual(rows[0]["cnn_status"], "SERVER_CNN_CONFIRMED")
        self.assertTrue(rows[0]["video_url"].endswith(("_stitched.mp4", "_stitched.webm")))
        r_vid = self.tc.get(rows[0]["video_url"], headers={**self.vwr_h, "Range": "bytes=0-63"})
        self.assertEqual(r_vid.status_code, 206)
        self.assertIn(r_vid.headers.get("content-type"), ("video/mp4", "video/webm"))

        # Re-sending identical idempotency key MUST NOT duplicate violation row!
        r_dup = self.tc.post(
            "/api/v1/violation_burst",
            headers={"X-Auth-Token": "lab-device-token", "Idempotency-Key": due[0]["idempotency_key"]},
            data={"meta": json.dumps(due[0]["meta"], ensure_ascii=False)},
            files=[("frames", (jpegs[0][0], jpegs[0][1], "image/jpeg"))],
        )
        self.assertEqual(r_dup.status_code, 200)
        self.assertTrue(r_dup.json().get("duplicate"))
        rows2 = self.tc.get("/api/v1/violations?vehicle_id=XE-CROSS-01", headers=self.vwr_h).json()["violations"]
        self.assertEqual(len(rows2), 1)

    def test_03_server_command_and_remote_config_roundtrip_to_pi_with_ack(self):
        executed = []
        applied_cfg = {}

        sync = ServerSyncClient(
            self.pi_cfg,
            auth_token="lab-device-token",
            spool=self.spool,
            on_command=lambda cmd: (executed.append(cmd) or True, f"ACK:{cmd}"),
            on_remote_config=lambda cfg: applied_cfg.update(cfg),
            session=_TestClientRequestsAdapter(self.tc),
        )
        self.tc.post("/api/v1/config/XE-CROSS-01", headers=self.admin_h, json={"allow_smoking": False, "allow_phone": True})
        enq = self.tc.post("/api/v1/server/command", headers=self.op_h, json={"vehicle_id": "XE-CROSS-01", "cmd": "VOICE:3"}).json()

        payload = sync.build_telemetry_payload(
            ai_state={"alert_state": "NORMAL", "alert_type": "NORMAL", "verification_mode": "SINGLE_MODEL_YOLO", "face_detected": True},
            hw_snap={"temp_c": 52.0, "throttled": "0x0"},
            fps_snap={},
            esp_sensor={"co2_ppm": 450.0, "gps": {"speed_kmh": 0.0, "is_valid": False}},
            esp_status={"connected": True},
            mute_state={},
        )
        sync.send_telemetry_once(payload)

        self.assertEqual(executed, ["VOICE:3"])
        self.assertFalse(applied_cfg["allow_smoking"])
        cmds = self.tc.get("/api/v1/commands?vehicle_id=XE-CROSS-01", headers=self.vwr_h).json()["commands"]
        matched = [c for c in cmds if c["cmd_uid"] == enq["cmd_uid"]][0]
        self.assertEqual(matched["status"], "EXECUTED_OK")

    def test_04_esp32_survival_bridge_json_conforms_to_contract_and_ingests(self):
        # Exact payload structure emitted by firmware/esp32_audio_gps/esp32_audio_gps.ino
        esp_json = {
            "schema_version": "1.0.0",
            "vehicle_id": "XE-ESP-SURV",
            "timestamp_ms": 1795120001000,
            "camera": {"status": "OFFLINE", "fps": 0.0, "face_detected": True, "is_stale": True},
            "pi": {"thermal_level": 0, "throttled": "0x0"},
            "driver_state": {"alert_state": "NORMAL", "alert_type": "NORMAL", "verification_mode": "ESP32_SURVIVAL", "detected_classes": []},
            "esp": {"connected": True, "direct_wifi": True, "seq": 12, "co2_ppm": 820.0, "adc": 1680},
            "gps": {"lat": 16.071, "lon": 108.222, "speed_kmh": 35.0, "altitude_m": 12.0, "heading_deg": 90.0, "satellites": 9, "status": "FIX_3D", "source": "esp32_direct", "is_valid": True},
            "mq135": {"co2_ppm": 820.0, "raw_adc": 1680, "gas_level": 0},
        }
        ok, errs = validate_telemetry_payload(esp_json)
        self.assertTrue(ok, f"ESP32 survival schema error: {errs}")
        r = self.tc.post("/api/v1/esp/bridge_telemetry", headers={"X-Auth-Token": "lab-device-token"}, json=esp_json)
        self.assertEqual(r.status_code, 200)
        veh = self.tc.get("/api/v1/vehicles/XE-ESP-SURV", headers=self.vwr_h).json()
        self.assertAlmostEqual(veh["gps_lat"], 16.071, places=3)

    def test_05_esp32_mute_pressed_flows_end_to_end_to_server_trip(self):
        sync = ServerSyncClient(
            self.pi_cfg,
            auth_token="lab-device-token",
            spool=self.spool,
            session=_TestClientRequestsAdapter(self.tc),
        )
        # Start active trip first
        p = sync.build_telemetry_payload(
            ai_state={"alert_state": "NORMAL", "alert_type": "NORMAL", "verification_mode": "SINGLE_MODEL_YOLO", "face_detected": True},
            hw_snap={"temp_c": 50.0, "throttled": "0x0"},
            fps_snap={},
            esp_sensor={"co2_ppm": 500.0, "gps": {"speed_kmh": 25.0, "is_valid": False}},
            esp_status={"connected": True},
            mute_state={},
        )
        sync.send_telemetry_once(p)

        esp = EspClient(
            mode="MOCK",
            on_mute_pressed=lambda snap: sync.queue_alert_dismissed("PHYSICAL_MUTE_BUTTON", "DROWSY", gps=snap.get("gps")),
        )
        esp.handle_incoming_line("EVT,MUTE_PRESSED")
        self.assertEqual(sync.drain_spool_once(), 1)
        dism = self.tc.get("/api/v1/dismissals?vehicle_id=XE-CROSS-01", headers=self.vwr_h).json()["dismissals"]
        self.assertEqual(len(dism), 1)
        self.assertIsNotNone(dism[0]["trip_id"])

        # Verify 0-frame ServerSyncClient SOS burst reaches FastAPI cleanly (multipart 0-frame fix!)
        sync.queue_violation_burst(alert_type="SOS", verification_mode="SINGLE_MODEL_YOLO", duration_seconds=1.0, jpeg_frames=[])
        self.assertEqual(sync.drain_spool_once(), 1)
        incs = self.tc.get("/api/v1/incidents?vehicle_id=XE-CROSS-01", headers=self.vwr_h).json()["incidents"]
        self.assertTrue(any(i["category"] == "SOS_PRESSED" for i in incs))

    def test_06_firmware_ino_contains_required_safety_fixes(self):
        ino = (Path(__file__).resolve().parent.parent / "firmware" / "esp32_audio_gps" / "esp32_audio_gps.ino").read_text(encoding="utf-8")
        self.assertIn("pollDuringAudio(-1);", ino)
        self.assertIn("EVT,WHOAMI,ROLE=AUDIO_MQ135_NODE,FW=PI_AI_3.0", ino)
        self.assertIn("isBeforeDeadline", ino)
        self.assertIn("btStop()", ino)
        self.assertIn("MQ_WARMUP_MS", ino)
        self.assertIn("#define SERIAL_BAUD        115200", ino)
        # LCK-05 v3: ESP32 #1 must have ZERO Wi-Fi, ZERO GPS, ZERO physical MUTE pin
        self.assertNotIn("#include <WiFi.h>", ino)
        self.assertNotIn("MUTE_PIN", ino)
        self.assertNotIn("gpsSerial", ino)

    def test_07_esp32_hub_firmware_phase6_protocol_and_secret_safety(self):
        import re
        hub_ino = (
            Path(__file__).resolve().parent.parent / "firmware" / "esp32_ble_wifi_hub" / "esp32_ble_wifi_hub.ino"
        ).read_text(encoding="utf-8")
        for rx in (
            r"#define\s+HUB_SERIAL_BAUD\s+115200",
            r"#define\s+GPS_RX_PIN\s+16",
            r"#define\s+GPS_TX_PIN\s+17",
        ):
            self.assertRegex(hub_ino, rx)
        for req in (
            "ROLE=COMMS_BLE_GPS_HUB",
            "FW=PI_AI_3.1",
            "HardwareSerial gpsSerial(2);",
            "6E400001-B5A3-F393-E0A9-E50E24DCCA9E",
            "pChar->getData()",
            "Preferences",
            "WiFiClientSecure",
            "WiFi.mode(WIFI_OFF)",
            "EVT,BLE_ACTION,MUTE_ALERT",
            "EVT,BLE_ACTION,SOS",
            "EVT,BLE_ACTION,CALIBRATE_LOCK",
            "OK:CALIBRATE_LOCK",
            "EVT,ACK,HB",
            "EVT,NACK,CFG_SERVER,INVALID_URL",
            "/api/v1/esp/bridge_telemetry",
        ):
            self.assertIn(req, hub_ino)
        # Ensure NVS secrets are never echoed back to Serial
        self.assertNotIn("Serial.println(cfgAuthToken)", hub_ino)
        self.assertNotIn("Serial.println(cfgWifiPass)", hub_ino)

    def test_08_calibrate_command_phone_action_ble_hub_and_calibration_schema(self):
        from contracts.schema_validator import load_contract
        from pi_edge.comms.esp_ble_client import EspBleHubClient, classify_ble_hub_line
        from pi_edge.core.access_control import is_allowed_control_command
        from server_ai.server_core import bluetooth_hub as srv_bt, commands as srv_cmds

        # 1. Schema ControlCommands aligned with server_ai command whitelist & phone ACTION_TYPES
        ctrl = load_contract()["schemas"]["ControlCommands"]
        for exact_cmd in ctrl["server_to_pi_exact"]:
            self.assertTrue(srv_cmds.validate_command(exact_cmd), f"Server missing command {exact_cmd}")
        self.assertEqual(set(ctrl["phone_actions"]), set(srv_bt.ACTION_TYPES))

        # 2. ESP32 #2 BLE hub line classifier & callback for CALIBRATE_LOCK + NOTIFY formatting
        self.assertEqual(classify_ble_hub_line("EVT,BLE_ACTION,CALIBRATE_LOCK"), ("BLE_ACTION", "CALIBRATE_LOCK"))
        ble_actions = []
        sent_serial = []
        hub = EspBleHubClient(mode="MOCK", on_ble_action=lambda act: ble_actions.append(act))
        hub.start()
        hub.handle_incoming_line("EVT,BLE_ACTION,CALIBRATE_LOCK")
        self.assertEqual(ble_actions, ["CALIBRATE_LOCK"])
        self.assertEqual(hub.get_bluetooth_block()["last_action"], "CALIBRATE_LOCK")

        orig_send = hub.send_command
        hub.send_command = lambda cmd, wait_ack=True, timeout_s=0.8: (sent_serial.append(cmd) or orig_send(cmd, wait_ack=wait_ack, timeout_s=timeout_s))
        self.assertTrue(hub.send_notify("CALIB_START|10s"))
        self.assertTrue(hub.send_notify("ALERT:CALIB_DONE|locked"))
        self.assertEqual(sent_serial, ["NOTIFY:CALIB_START|10s", "NOTIFY:CALIB_DONE|locked"])

        # 3. Validate optional driver_state.calibration both absent and present
        executed = []
        sync = ServerSyncClient(
            self.pi_cfg,
            auth_token="lab-device-token",
            spool=self.spool,
            on_command=lambda cmd: (executed.append(cmd) or True, "CALIBRATION_STARTED"),
            session=_TestClientRequestsAdapter(self.tc),
        )
        payload = sync.build_telemetry_payload(
            ai_state={"alert_state": "NORMAL", "alert_type": "NORMAL", "verification_mode": "YOLO_BLAZEFACE_SEQ", "face_detected": True},
            hw_snap={"temp_c": 62.0, "throttled": "0x0"},
            fps_snap={},
            esp_sensor={"co2_ppm": 500.0, "gps": {"speed_kmh": 0.0, "is_valid": False}},
            esp_status={"connected": True},
            mute_state={},
        )
        ok0, errs0 = validate_telemetry_payload(payload)
        self.assertTrue(ok0, f"Schema errors without calibration: {errs0}")

        payload_with_cal = json.loads(json.dumps(payload))
        payload_with_cal["driver_state"]["calibration"] = {
            "state": "DONE",
            "mode": "APP_LOCK",
            "progress_s": 10.0,
            "duration_s": 10.0,
            "locked": True,
            "profile_ts": 1795120005.0,
            "ear_closed_threshold": 0.19,
            "glare_mode": True,
        }
        ok1, errs1 = validate_telemetry_payload(payload_with_cal)
        self.assertTrue(ok1, f"Schema errors with calibration: {errs1}")

        bad_cal = json.loads(json.dumps(payload_with_cal))
        bad_cal["driver_state"]["calibration"]["state"] = "BOGUS_STATE"
        ok_bad, _ = validate_telemetry_payload(bad_cal)
        self.assertFalse(ok_bad)

        # 4. Phone HTTP action CALIBRATE_LOCK -> Server queues CALIBRATE -> delivered on /api/v1/telemetry & ACKed
        otp = self.tc.post("/api/v1/phone/pair_otp", headers=self.op_h, json={"vehicle_id": "XE-CROSS-01"}).json()["pair_code"]
        ptok = self.tc.post("/api/v1/phone/pair", json={"vehicle_id": "XE-CROSS-01", "pair_code": otp}).json()["driver_phone_token"]
        act_r = self.tc.post(
            "/api/v1/phone/action",
            headers={"Authorization": f"Bearer {ptok}"},
            json={"vehicle_id": "XE-CROSS-01", "action": "CALIBRATE_LOCK"},
        )
        self.assertEqual(act_r.status_code, 200)
        cmd_id = act_r.json()["command_id"]

        if is_allowed_control_command("CALIBRATE"):
            sync.send_telemetry_once(payload_with_cal)
            self.assertEqual(executed, ["CALIBRATE"])
        else:
            t_res = self.tc.post(
                "/api/v1/telemetry",
                headers={"X-Auth-Token": "lab-device-token"},
                json=payload_with_cal,
            ).json()
            self.assertEqual(t_res["commands"], [{"cmd_uid": cmd_id, "cmd": "CALIBRATE"}])
            self.tc.post(
                "/api/v1/commands/ack",
                headers={"X-Auth-Token": "lab-device-token"},
                json={"cmd_uid": cmd_id, "vehicle_id": "XE-CROSS-01", "result": "OK", "detail": "CALIBRATION_STARTED"},
            )

        cmds = self.tc.get("/api/v1/commands?vehicle_id=XE-CROSS-01", headers=self.vwr_h).json()["commands"]
        matched = [c for c in cmds if c["cmd_uid"] == cmd_id][0]
        self.assertEqual(matched["status"], "EXECUTED_OK")
        self.assertEqual(matched["ack_detail"], "CALIBRATION_STARTED")


if __name__ == "__main__":
    unittest.main()
