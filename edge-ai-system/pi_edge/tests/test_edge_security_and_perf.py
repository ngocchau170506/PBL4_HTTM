"""Red-Team Security, Performance, Protocol & Unit Test Suite for pi_edge."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from pi_edge.ai.sensor_fusion import SensorFusion
from pi_edge.camera.frame_source import CameraFrameSource, resize_then_rotate
from pi_edge.comms.esp_client import EspClient, classify_esp_ack_line
from pi_edge.comms.server_sync import ServerSyncClient
from pi_edge.core.access_control import AuditLogger, EdgeAccessController
from pi_edge.core.config import EdgeConfig, resolve_run_profile
from pi_edge.core.telemetry import ThermalPolicy
from pi_edge.evidence.burst_collector import BurstCollector
from pi_edge.evidence.spool_store import UnifiedSpoolStore
from pi_edge.web.mini_server import MiniWebServer


class TestPiEdgeSecurityAndPerf(unittest.TestCase):
    def test_01_strict_rbac_boot_rejects_empty_tokens_in_prod(self):
        with self.assertRaises(RuntimeError):
            EdgeAccessController.from_env(env={}, env_profile="prod")

    def test_02_tunnel_127_0_0_1_has_no_admin_exemption(self):
        ac = EdgeAccessController.from_env(env={"WEB_ADMIN_TOKEN": "adm-secret-123"}, env_profile="prod")
        dec = ac.authorize(min_role="viewer", headers={"Host": "127.0.0.1:8080"})
        self.assertFalse(dec.allowed)
        self.assertEqual(dec.reason, "MISSING_TOKEN")

    def test_03_single_run_mode_local_has_zero_egress(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = EdgeConfig.from_env(
                env={"RUN_MODE": "local", "ENV_PROFILE": "lab", "VIOLATION_STORAGE_DIR": td}
            )
            self.assertFalse(cfg.remote_enabled)
            self.assertEqual(cfg.web_mode, "full_local")
            spool = UnifiedSpoolStore(Path(td))
            try:
                sync = ServerSyncClient(cfg, auth_token="tok", spool=spool)
                sync.start()
                img = np.full((240, 320, 3), 120, dtype=np.uint8)
                bc = BurstCollector()
                bc.push_clean_frame(1, img, now_mono=1.0)
                sync.queue_violation_burst("DROWSY", "SINGLE_MODEL_YOLO", 2.1, bc.extract_jpeg_burst(img))
                res = sync.send_telemetry_once({"test": 1})
                drained = sync.drain_spool_once()
                self.assertIsNone(res)
                self.assertEqual(drained, 0)
                self.assertEqual(sync.egress_attempts, 0)
                sync.close()
            finally:
                spool.close()

    def test_04_server_mode_mini_web_only_exposes_3_routes_and_blocks_oom_and_db_leak(self):
        with tempfile.TemporaryDirectory() as td:
            vdir = Path(td) / "violations"
            vdir.mkdir()
            (vdir / "edge_spool.db").write_bytes(b"SECRET_DB")
            (vdir / "audit_permissions.jsonl").write_text('{"secret":1}\n', encoding="utf-8")
            (vdir / "sample.jpg").write_bytes(b"\xff\xd8\xff\xd9")

            ac = EdgeAccessController({"adm-12345678": "admin", "vwr-12345678": "viewer", "dev-12345678": "device"})
            audit = AuditLogger(Path(td) / "logs" / "audit")

            # A. minimal_server mode: /api/control & /api/status must be 404
            srv = MiniWebServer("127.0.0.1", 0, "minimal_server", ac, audit, vdir)
            port = srv.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=2) as r:
                    self.assertEqual(r.status, 200)
                # /api/status disabled in server mode
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status", headers={"X-Auth-Token": "adm-12345678"})
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=2)
                self.assertEqual(ctx.exception.code, 404)
                # Oversized POST Content-Length rejected 413 BEFORE body read
                req_big = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/esp_bridge",
                    method="POST",
                    headers={"Content-Length": "999999999"},
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx2:
                    urllib.request.urlopen(req_big, timeout=2)
                self.assertEqual(ctx2.exception.code, 413)
            finally:
                srv.close()

            # B. full_local mode: viewer CANNOT download .db or .jsonl!
            srv_loc = MiniWebServer("127.0.0.1", 0, "full_local", ac, audit, vdir)
            port2 = srv_loc.start()
            try:
                for forbidden in ("edge_spool.db", "audit_permissions.jsonl"):
                    req_f = urllib.request.Request(
                        f"http://127.0.0.1:{port2}/violations/{forbidden}",
                        headers={"X-Auth-Token": "vwr-12345678"},
                    )
                    with self.assertRaises(urllib.error.HTTPError) as ctx3:
                        urllib.request.urlopen(req_f, timeout=2)
                    self.assertEqual(ctx3.exception.code, 403)
                req_ok = urllib.request.Request(
                    f"http://127.0.0.1:{port2}/violations/sample.jpg",
                    headers={"X-Auth-Token": "vwr-12345678"},
                )
                with urllib.request.urlopen(req_ok, timeout=2) as r_ok:
                    self.assertEqual(r_ok.status, 200)
            finally:
                srv_loc.close()

    def test_05_esp_nack_never_classified_as_ack_and_correlated_waiter(self):
        self.assertEqual(classify_esp_ack_line("EVT,NACK,VOICE,9"), ("NACK", "VOICE"))
        self.assertEqual(classify_esp_ack_line("EVT,ACK,VOICE,1,i2s=1"), ("ACK", "VOICE"))
        self.assertEqual(classify_esp_ack_line("EVT,ACK,STOP_INTERRUPT"), ("ACK", "STOP"))
        self.assertEqual(classify_esp_ack_line("EVT,ACK,MUTE_INTERRUPT"), ("ACK", "MUTE"))
        self.assertEqual(classify_esp_ack_line("EVT,NACK,FDFRAME,type=1,seq=4,ENQUEUE_FAILED"), ("NACK", "FDFRAME"))
        self.assertEqual(classify_esp_ack_line("EVT,WHOAMI,ROLE=AUDIO_GPS_NODE,FW=PI_AI_1.0"), ("ACK", "WHOAMI"))

        # Verify concurrent FDFRAME ACK does NOT satisfy VOICE waiter, and VOICE NACK fails!
        class FakeSerial:
            def __init__(self):
                self.written = []
            def write(self, b):
                self.written.append(b)
            def flush(self):
                pass
            def close(self):
                pass

        esp = EspClient(mode="SERIAL", require_ack=True)
        esp._serial = FakeSerial()
        esp.connected = True

        def feed():
            time.sleep(0.05)
            esp.handle_incoming_line("EVT,ACK,FDFRAME,type=2,seq=1,len=50,queued=1")  # Wrong key!
            time.sleep(0.05)
            esp.handle_incoming_line("EVT,NACK,VOICE,9")  # Real NACK for VOICE!

        t = threading.Thread(target=feed)
        t.start()
        res = esp.send_command("VOICE:9", wait_ack=True, timeout_s=0.6)
        t.join()
        self.assertFalse(res.ok)
        self.assertEqual(res.error, "ESP32_NACK")

    def test_06_esp_hw_events_canonical_names_and_mute_callback(self):
        seen_hw = []
        seen_mute = []
        esp = EspClient(
            mode="MOCK",
            on_hw_event=lambda name, raw: seen_hw.append(name),
            on_mute_pressed=lambda snap: seen_mute.append(snap),
        )
        esp.handle_incoming_line("EVT,AUTO_ALERT,CO2_CRIT,1650.0")
        esp.handle_incoming_line("EVT,WARN,BROWNOUT_SUT_AP_NGUON_LOA")
        esp.handle_incoming_line("EVT,MUTE_PRESSED")
        self.assertIn("AUTO_CO2_CRIT", seen_hw)
        self.assertIn("ESP_BROWNOUT", seen_hw)
        self.assertEqual(len(seen_mute), 1)
        blk = esp.get_esp_status_block()
        self.assertTrue(blk["brownout"])
        self.assertIn("MUTE_ACTIVE", blk["hw_events"])

    def test_07_sensor_fusion_hard_clamps_blind_hold_prevents_single_frame_false_alarm(self):
        fusion = SensorFusion(eye_threshold_s=2.0, max_blind_hold_s=5.0)  # Attempt excessive hold!
        self.assertLessEqual(fusion.max_blind_hold_s, 0.8)

        # Single positive frame at t=0.0, then None gaps through t=2.5 -> MUST NEVER trigger EDGE_TRIGGERED!
        d0, _ = fusion.update({"eye": True}, now_mono=0.0, verification_mode="SINGLE_MODEL_YOLO")
        self.assertEqual(d0.alert_state, "NORMAL")
        for t in (0.3, 0.7, 1.2, 2.1, 2.5):
            d, _ = fusion.update({"eye": None}, now_mono=t, verification_mode="SINGLE_MODEL_YOLO")
            self.assertEqual(d.alert_state, "NORMAL", f"Must not false-trigger during blind gap at t={t}")

        # Sustained real detections >= 2.0s with >= 2 hits MUST trigger EDGE_TRIGGERED with separated verification_mode
        fusion.update({"eye": True}, now_mono=10.0, verification_mode="YOLO_BLAZEFACE_SEQ")
        fusion.update({"eye": True}, now_mono=11.0, verification_mode="YOLO_BLAZEFACE_SEQ")
        d_trig, _ = fusion.update({"eye": True}, now_mono=12.1, verification_mode="YOLO_BLAZEFACE_SEQ")
        self.assertEqual(d_trig.alert_state, "EDGE_TRIGGERED")
        self.assertEqual(d_trig.alert_type, "DROWSY")
        self.assertEqual(d_trig.verification_mode, "YOLO_BLAZEFACE_SEQ")

    def test_08_resize_then_rotate_and_camera_is_opened_method(self):
        raw = np.zeros((1080, 1920, 3), dtype=np.uint8)
        raw[0:20, 0:20] = (0, 255, 0)  # top-left green marker
        out = resize_then_rotate(raw, (640, 480), rotation_deg=180)
        self.assertEqual(out.shape, (480, 640, 3))
        # After 180 deg rotation, green marker must be at bottom-right
        self.assertGreater(int(out[-2, -2, 1]), 200)
        cam = CameraFrameSource(open_fn=lambda: None)
        self.assertFalse(cam.is_opened())

    def test_09_thermal_policy_actually_drops_imgsz_to_256_at_l3(self):
        tp = ThermalPolicy(base_imgsz=320, warm_c=58.0, hot_c=65.0, crit_c=72.0)
        p0 = tp.plan(50.0, 0)
        p3 = tp.plan(74.0, 0)
        self.assertEqual(p0.thermal_level, 0)
        self.assertEqual(p0.yolo_imgsz, 320)
        self.assertEqual(p3.thermal_level, 3)
        self.assertEqual(p3.yolo_imgsz, 256)
        self.assertFalse(p3.allow_face_geometry)
        self.assertGreater(p3.ai_min_interval_s, p0.ai_min_interval_s)

    def test_10_burst_collector_and_spool_quota_fifo_eviction(self):
        bc = BurstCollector(sample_fps=5.0, max_burst_frames=10, jpeg_quality=70)
        for i in range(15):
            img = np.full((480, 640, 3), (i * 15) % 255, dtype=np.uint8)
            bc.push_clean_frame(i + 1, img, now_mono=i * 0.25)
        jpegs = bc.extract_jpeg_burst()
        self.assertEqual(len(jpegs), 10)
        self.assertTrue(all(raw[:2] == b"\xff\xd8" for _, raw in jpegs))

        with tempfile.TemporaryDirectory() as td:
            # Tiny 1 MB quota -> must FIFO evict cleanly without crashing
            spool = UnifiedSpoolStore(Path(td), max_storage_mb=1.0, max_attempts=3)
            try:
                big_jpg = b"\xff\xd8" + (b"\x01" * 250_000) + b"\xff\xd9"
                for k in range(6):
                    spool.enqueue_violation_burst(f"EVT_{k}", f"IDEM_{k}", {"k": k}, [(f"f{k}.jpg", big_jpg)])
                st = spool.stats()
                self.assertLessEqual(st["disk_bytes"], 1.1 * 1024 * 1024)
            finally:
                spool.close()

    def test_11_audit_logger_surfaces_degraded_state_on_write_failure(self):
        with tempfile.TemporaryDirectory() as td:
            # Point audit_dir to a regular file so mkdir/write fails!
            blocker = Path(td) / "not_a_dir"
            blocker.write_text("file", encoding="utf-8")
            al = AuditLogger(blocker)
            ok = al.record(actor_role="admin", method="POST", path="/api/control", allowed=True, reason="OK", required=True)
            self.assertFalse(ok)
            self.assertTrue(al.audit_degraded)
            self.assertNotEqual(al.last_error, "")

    def test_12_spool_resets_monotonic_retry_timer_after_pi_reboot(self):
        with tempfile.TemporaryDirectory() as td:
            spool1 = UnifiedSpoolStore(Path(td), max_storage_mb=10.0)
            try:
                spool1.enqueue_json_event("EVT_REBOOT", "IDEM_REBOOT", "alert_dismissed", {"ok": 1})
                # Simulate Pi running for 10 hours (monotonic=36000s) when upload failed with backoff
                spool1.mark_failed("EVT_REBOOT", "network_down", permanent=False, now_mono=36000.0)
                # Before reboot (at monotonic=5.0s after restart), fetch_due would return 0 if not reset!
                self.assertEqual(len(spool1.fetch_due(limit=5, now_mono=5.0)), 0)
            finally:
                spool1.close()

            # Simulate Pi reboot: reopening UnifiedSpoolStore must reset PENDING retry deadlines to 0
            spool2 = UnifiedSpoolStore(Path(td), max_storage_mb=10.0)
            try:
                due = spool2.fetch_due(limit=5, now_mono=5.0)
                self.assertEqual(len(due), 1)
                self.assertEqual(due[0]["event_id"], "EVT_REBOOT")
                self.assertEqual(due[0]["attempts"], 1)  # attempts preserved!
            finally:
                spool2.close()

    def test_13_stale_camera_frame_does_not_trigger_false_alert(self):
        from pi_edge.ai.pipeline import EdgeAiPipeline, InferenceOutput
        from pi_edge.core.telemetry import ThrottlePlan

        cfg = EdgeConfig.from_env(env={"RUN_MODE": "server", "ENV_PROFILE": "lab", "DISABLE_AI": "1"})
        pipe = EdgeAiPipeline(cfg)
        # Seed a single real positive eye_closed inference at t=10.0
        pipe._latest_infer = InferenceOutput(
            ts_mono=10.0,
            detected_classes=["Eye_Closed"],
            face_detected=True,
            eye_closed=True,
            latency_ms=40.0,
            verification_mode="SINGLE_MODEL_YOLO",
        )
        submitted = []
        pipe.submit_if_due = lambda *a, **kw: submitted.append(True) or True  # type: ignore[method-assign]
        plan = ThrottlePlan(0, 0.1, 320, True, "L0_NORMAL")
        img = np.zeros((240, 320, 3), dtype=np.uint8)

        # Fresh tick at t=10.0 registers first hit
        st0 = pipe.evaluate_tick(img, plan, now_mono=10.0, is_stale=False)
        self.assertEqual(st0["alert_state"], "NORMAL")
        self.assertEqual(len(submitted), 1)

        # Camera freezes for 3 seconds (is_stale=True) -> MUST NOT re-submit frozen frame or trigger DROWSY!
        for t in (10.5, 11.2, 12.2, 13.0):
            st = pipe.evaluate_tick(img, plan, now_mono=t, is_stale=True)
            self.assertEqual(st["alert_state"], "NORMAL", f"Stale frame caused false alert at t={t}")
        self.assertEqual(len(submitted), 1)

    def test_14_server_mode_disables_live_stream_by_default_and_local_enables_it(self):
        cfg_srv = EdgeConfig.from_env(env={"RUN_MODE": "server", "ENV_PROFILE": "lab"})
        cfg_loc = EdgeConfig.from_env(env={"RUN_MODE": "local", "ENV_PROFILE": "lab"})
        self.assertFalse(cfg_srv.enable_live_stream)
        self.assertTrue(cfg_loc.enable_live_stream)

        with tempfile.TemporaryDirectory() as td:
            ac = EdgeAccessController({"vwr-12345678": "viewer"})
            audit = AuditLogger(Path(td) / "audit")
            srv = MiniWebServer("127.0.0.1", 0, "minimal_server", ac, audit, Path(td), enable_live_stream=False)
            port = srv.start()
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/stream",
                    headers={"X-Auth-Token": "vwr-12345678"},
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=2)
                self.assertEqual(ctx.exception.code, 404)
            finally:
                srv.close()

    def test_15_blazeface_decodes_face_box_with_ssd_anchor(self):
        from pi_edge.ai.face_geometry import BlazeFaceGeometry

        class _FakeBlazeNet:
            def setInput(self, _blob):
                pass

            def forward(self, out_names):
                clf = np.full((1, 896, 1), -10.0, dtype=np.float32)
                reg = np.zeros((1, 896, 16), dtype=np.float32)
                # Anchor #686 in 8x8x6 layer has center (0.6875, 0.4375)
                clf[0, 686, 0] = 1.28
                reg[0, 686, 0:4] = [0.2, 2.3, 49.1, 49.1]
                reg[0, 686, 4:12] = [-10.0, -8.0, 10.0, -8.0, 0.0, 2.0, 0.0, 14.0]
                if isinstance(out_names, list):
                    return [clf, reg]
                return clf if out_names == "classificators" else reg

        geo = BlazeFaceGeometry("nonexistent.task")
        geo._net = _FakeBlazeNet()
        geo.available = True
        pose = geo.detect_pose(np.zeros((240, 320, 3), dtype=np.uint8))
        self.assertTrue(pose.valid)
        self.assertIsNotNone(pose.face_box)
        x1, y1, x2, y2 = pose.face_box  # type: ignore[misc]
        # Must sit near anchor (220, 109) rather than legacy top-left (0, 0, 61, 50)
        self.assertGreater(x1, 140)
        self.assertLess(x2, 300)
        self.assertGreater(y1, 50)
        self.assertLess(y2, 170)

    def test_16_esp_ble_hub_parser_actions_and_nack_handling(self):
        from pi_edge.comms.esp_ble_client import EspBleHubClient, classify_ble_hub_line

        self.assertEqual(classify_ble_hub_line("EVT,WHOAMI,ROLE=COMMS_BLE_WIFI_HUB,FW=PI_AI_2.0"), ("ACK", "WHOAMI"))
        self.assertEqual(classify_ble_hub_line("EVT,NACK,CFG_SERVER,INVALID_URL"), ("NACK", "CFG_SERVER"))
        self.assertEqual(classify_ble_hub_line("EVT,ACK,HB"), ("ACK", "HB"))
        self.assertEqual(classify_ble_hub_line("EVT,BLE_ACTION,MUTE_ALERT"), ("BLE_ACTION", "MUTE_ALERT"))
        self.assertEqual(classify_ble_hub_line("EVT,BLE_STATE,PHONE_CONNECTED"), ("BLE_STATE", "PHONE_CONNECTED"))

        actions = []
        states = []
        hub = EspBleHubClient(
            mode="MOCK",
            on_ble_action=lambda a: actions.append(a),
            on_ble_state=lambda s: states.append(s),
        )
        self.assertTrue(hub.start())
        hub.handle_incoming_line("EVT,WHOAMI,ROLE=COMMS_BLE_WIFI_HUB,FW=PI_AI_2.0")
        hub.handle_incoming_line("EVT,BLE_STATE,PHONE_CONNECTED")
        hub.handle_incoming_line("EVT,BLE_ACTION,MUTE_ALERT")
        hub.handle_incoming_line("EVT,BLE_ACTION,SOS")
        hub.handle_incoming_line('FDBLE:{"role":"COMMS_BLE_WIFI_HUB","ble_connected":true,"wifi_survival_on":false}')
        self.assertEqual(hub.device_role, "COMMS_BLE_WIFI_HUB")
        self.assertTrue(hub.ble_phone_connected)
        self.assertEqual(actions, ["MUTE_ALERT", "SOS"])
        self.assertEqual(states, [True])
        blk = hub.get_bluetooth_block()
        self.assertIsNotNone(blk)
        self.assertTrue(blk["phone_connected"])
        self.assertTrue(hub.send_heartbeat_if_due({"co2_ppm": 500.0, "gps": {"is_valid": False}}, now_mono=10.0))
        hub.close()

    def test_17_main_loop_boots_and_shuts_down_cleanly_with_dual_mock_esp(self):
        import os
        from pi_edge.app.main import run as run_edge

        with tempfile.TemporaryDirectory() as td:
            env_patch = {
                "RUN_MODE": "local",
                "ENV_PROFILE": "lab",
                "WEB_PORT": "18089",
                "DISABLE_CAMERA": "1",
                "DISABLE_AI": "1",
                "ESP_MODE": "MOCK",
                "ESP_BLE_MODE": "MOCK",
                "VIOLATION_STORAGE_DIR": str(Path(td) / "v"),
                "LOGS_DIR": str(Path(td) / "l"),
                "AUDIT_DIR": str(Path(td) / "a"),
            }
            old = {k: os.environ.get(k) for k in env_patch}
            try:
                os.environ.update(env_patch)
                rc = run_edge(cli_mode="local", max_ticks=2)
                self.assertEqual(rc, 0)
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_18_thermal_policy_triggers_critical_at_70c_and_ble_exposes_heap(self):
        from pi_edge.comms.esp_ble_client import EspBleHubClient
        from pi_edge.core.telemetry import ThermalPolicy

        tp = ThermalPolicy()
        self.assertEqual(tp.plan(69.5, 0).thermal_level, 2)
        p70 = tp.plan(70.0, 0)
        self.assertEqual(p70.thermal_level, 3)
        self.assertFalse(p70.allow_face_geometry)

        hub = EspBleHubClient(mode="MOCK")
        hub.start()
        hub.handle_incoming_line('FDBLE:{"role":"COMMS_BLE_WIFI_HUB","ble_connected":false,"wifi_survival_on":false,"free_heap":111096}')
        hub.handle_incoming_line("EVT,BLE_ACTION,ACK_AWAKE")
        blk = hub.get_bluetooth_block()
        self.assertIsNotNone(blk)
        self.assertEqual(blk["free_heap"], 111096)
        self.assertEqual(blk["last_action"], "ACK_AWAKE")
        self.assertFalse(blk["phone_connected"])
        hub.close()

    def test_19_lck05_v3_esp2_gps_telemetry_and_vol_command(self):
        from pi_edge.comms.esp_ble_client import EspBleHubClient
        from pi_edge.core.access_control import is_allowed_control_command

        # 1. VOL:<10..100> validation + ACK/NACK classification
        self.assertTrue(is_allowed_control_command("VOL:65"))
        self.assertTrue(is_allowed_control_command("VOL:10"))
        self.assertTrue(is_allowed_control_command("VOL:100"))
        self.assertFalse(is_allowed_control_command("VOL:5"))
        self.assertFalse(is_allowed_control_command("VOL:101"))
        self.assertFalse(is_allowed_control_command("VOL:abc"))
        self.assertEqual(classify_esp_ack_line("EVT,ACK,VOL,80"), ("ACK", "VOL"))
        self.assertEqual(classify_esp_ack_line("EVT,NACK,VOL,RANGE_10_100"), ("NACK", "VOL"))
        self.assertEqual(
            classify_esp_ack_line("EVT,WHOAMI,ROLE=AUDIO_MQ135_NODE,FW=PI_AI_3.0,BAUD=115200"),
            ("ACK", "WHOAMI"),
        )

        # 2. ESP32 #2 direct UART2 GPS telemetry parsing in FDBLE:
        hub = EspBleHubClient(mode="MOCK")
        hub.start()
        hub.handle_incoming_line(
            'FDBLE:{"role":"COMMS_BLE_GPS_HUB","fw":"PI_AI_3.0","ble_connected":true,'
            '"wifi_survival_on":false,"gps_fix":true,"lat":16.054400,"lon":108.202200,'
            '"speed_kmh":42.5,"alt_m":18.2,"sats":9,"free_heap":124000}'
        )
        gps = hub.get_gps_snapshot()
        self.assertTrue(gps["is_valid"])
        self.assertAlmostEqual(gps["lat"], 16.0544, places=4)
        self.assertAlmostEqual(gps["lon"], 108.2022, places=4)
        self.assertAlmostEqual(gps["speed_kmh"], 42.5, places=1)
        self.assertEqual(gps["satellites"], 9)
        blk = hub.get_bluetooth_block()
        self.assertTrue(blk["gps_fix"])
        self.assertEqual(blk["gps_sats"], 9)

        # Invalid/lost GPS fix must clear lat/lon to None
        hub.handle_incoming_line(
            'FDBLE:{"role":"COMMS_BLE_GPS_HUB","ble_connected":false,"gps_fix":false,"sats":0,"free_heap":124000}'
        )
        gps2 = hub.get_gps_snapshot()
        self.assertFalse(gps2["is_valid"])
        self.assertIsNone(gps2["lat"])
        self.assertIsNone(gps2["lon"])
        hub.close()

    def test_20_fault_serial_unplug_ioerror_does_not_crash_and_clears_stale_gps(self):
        from pi_edge.comms.esp_ble_client import EspBleHubClient

        class _BrokenSerial:
            def write(self, _data):
                raise OSError("Input/output error (USB unplugged)")

            def flush(self):
                raise OSError("flush failed")

            def close(self):
                pass

        # ESP32 #1 unplug during VOICE:1 must NOT raise uncaught OSError!
        esp1 = EspClient(mode="SERIAL", port="/dev/nonexistent")
        esp1._serial = _BrokenSerial()
        esp1.connected = True
        r1 = esp1.send_command("VOICE:1", wait_ack=False)
        self.assertFalse(r1.ok)
        self.assertIn("serial_io_error", r1.error)
        self.assertFalse(esp1.connected)
        self.assertIsNone(esp1._serial)

        # ESP32 #2 unplug during HB must NOT crash and MUST invalidate stale GPS & BLE phone state!
        hub = EspBleHubClient(mode="SERIAL", port="/dev/nonexistent")
        hub._serial = _BrokenSerial()
        hub.connected = True
        hub.ble_phone_connected = True
        hub.latest_gps.update({"is_valid": True, "lat": 16.05, "lon": 108.2})
        r2 = hub.send_command('HB:{"co2_ppm":420}', wait_ack=False)
        self.assertFalse(r2.ok)
        self.assertIn("serial_io_error", r2.error)
        self.assertFalse(hub.connected)
        self.assertFalse(hub.ble_phone_connected)
        self.assertFalse(hub.get_gps_snapshot()["is_valid"])

    def test_21_fault_single_frame_spike_respects_blind_hold_and_ai_worker_survives_exception(self):
        from pi_edge.ai.pipeline import EdgeAiPipeline, InferenceOutput
        from pi_edge.core.telemetry import ThrottlePlan

        class _FlakyYolo:
            def __init__(self):
                self.calls = 0

            def predict(self, *_a, **_kw):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("Simulated OOM on frame 1")
                return []

        with tempfile.TemporaryDirectory() as td:
            cfg = EdgeConfig.from_env(
                env={
                    "RUN_MODE": "local",
                    "ENV_PROFILE": "lab",
                    "VIOLATION_STORAGE_DIR": td,
                    "DISABLE_AI": "0",
                    "DROWSY_THRESHOLD_SECONDS": "1.0",
                    "BLIND_HOLD_MAX_S": "0.75",
                }
            )
            yolo = _FlakyYolo()
            pipe = EdgeAiPipeline(cfg, yolo_model=yolo)
            pipe.start()
            try:
                plan = ThrottlePlan(0, 0.01, 320, False, "L0")
                frm = np.zeros((120, 160, 3), dtype=np.uint8)
                # Frame 1 triggers simulated OOM -> worker MUST stay alive and handle Frame 2!
                pipe.submit_if_due(frm, plan, now_mono=10.0)
                time.sleep(0.15)
                self.assertEqual(pipe.worker_errors, 1)
                self.assertTrue(pipe._worker is not None and pipe._worker.is_alive())
                pipe.submit_if_due(frm, plan, now_mono=11.0)
                time.sleep(0.15)
                self.assertGreaterEqual(yolo.calls, 2)

                # Single Eye_Closed inference across 30 ticks at 30 Hz MUST count as 1 hit (never bypass 2-hit guard!)
                t0 = 100.0
                with pipe._lock:
                    pipe._latest_infer = InferenceOutput(
                        ts_mono=t0,
                        detected_classes=["Eye_Closed"],
                        face_detected=True,
                        eye_closed=True,
                    )
                    pipe._last_consumed_infer_ts = 0.0
                for step in range(45):
                    out = pipe.evaluate_tick(None, plan, now_mono=t0 + step * 0.033)
                    self.assertEqual(out["alert_state"], "NORMAL")
                self.assertEqual(pipe.fusion.channels["eye"]._positive_hits, 0)
            finally:
                pipe.close()

    def test_22_fault_spool_deletes_uploaded_quarantines_corrupt_and_sync_latches_hw_events(self):
        with tempfile.TemporaryDirectory() as td:
            spool = UnifiedSpoolStore(Path(td), max_storage_mb=2.0)
            try:
                rel = spool.enqueue_violation_burst(
                    "EVT_DEL_1", "IDEM_DEL_1", {"alert_type": "DROWSY"}, [("k0.jpg", b"\xff\xd8" + b"0" * 500)]
                )
                self.assertTrue((spool.bursts_dir / rel).exists())
                spool.mark_uploaded("EVT_DEL_1")
                self.assertFalse((spool.bursts_dir / rel).exists(), "Uploaded burst JPEGs must be deleted from microSD!")

                # Inject corrupt JSON directly into SQLite -> fetch_due must quarantine to DEAD_LETTER without crashing!
                spool._conn.execute(
                    "INSERT INTO spool_items (event_id, idempotency_key, kind, meta_json, status) VALUES ('EVT_BAD','IDEM_BAD','alert_dismissed','{corrupt','PENDING')"
                )
                spool._conn.commit()
                due = spool.fetch_due(limit=5)
                self.assertEqual(due, [])
                self.assertEqual(spool.stats()["dead_letter"], 1)

                # Verify 30Hz update_state latches hw_events so none are lost before 1.5s sync
                cfg = EdgeConfig.from_env(
                    env={
                        "RUN_MODE": "server",
                        "ENV_PROFILE": "lab",
                        "VIOLATION_STORAGE_DIR": td,
                        "REMOTE_SERVER_URL": "http://127.0.0.1:9",
                    }
                )
                sync = ServerSyncClient(cfg=cfg, auth_token="tok", spool=spool)
                sync.update_state({"esp": {"connected": True, "hw_events": ["ESP_RESET"]}, "mute_state": {"newly_triggered": True}})
                # Next 30 ticks overwrite with empty hw_events
                for _ in range(30):
                    sync.update_state({"esp": {"connected": True, "hw_events": []}, "mute_state": {"newly_triggered": False}})
                self.assertIn("ESP_RESET", sync._pending_hw_events)
                self.assertTrue(sync._pending_mute_newly)
            finally:
                spool.close()

    def test_23_audit_fixes_spool_transient_audit_strip_cfg_wifi_and_ai_geometry(self):
        from pi_edge.ai.face_geometry import evaluate_mouth_yawn_geometry, unrotate_bbox_in_roi
        from pi_edge.ai.pipeline import EdgeAiPipeline
        from pi_edge.comms.esp_ble_client import EspBleHubClient
        from pi_edge.core.access_control import AuditLogger, EdgeAccessController
        from pi_edge.web.mini_server import MiniWebServer

        # 1. Transient network failures (50 retries > max_attempts=20) must NEVER dead-letter offline spool (F-004)
        with tempfile.TemporaryDirectory() as td:
            spool = UnifiedSpoolStore(Path(td), max_storage_mb=2.0, max_attempts=5)
            try:
                spool.enqueue_violation_burst("EVT_OFF_1", "IDEM_OFF_1", {"alert_type": "DROWSY"}, [])
                for i in range(30):
                    spool.mark_failed("EVT_OFF_1", "ConnectionError: offline", permanent=False, transient=True, now_mono=float(i * 100))
                st = spool.stats()
                self.assertEqual(st["pending"], 1)
                self.assertEqual(st["dead_letter"], 0)
            finally:
                spool.close()

        # 2. MiniWebServer MUST strip ?token= before writing audit_permissions.jsonl (F-003)
        with tempfile.TemporaryDirectory() as td:
            audit = AuditLogger(Path(td) / "audit")
            acc = EdgeAccessController(tokens_by_role={"secret_tok_xyz123": "operator"})
            srv = MiniWebServer(
                host="127.0.0.1",
                port=0,
                web_mode="full_local",
                access=acc,
                audit=audit,
                violations_dir=Path(td),
                enable_live_stream=False,
            )
            port = srv.start()
            try:
                import urllib.error
                import urllib.request
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/control?token=secret_tok_xyz123",
                    data=b'{"cmd":"PING"}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=2.0)
                self.assertEqual(ctx.exception.code, 401)
                log_txt = audit.log_path.read_text(encoding="utf-8")
                self.assertNotIn("secret_tok_xyz123", log_txt)
            finally:
                srv.close()

        # 3. ESP32 #2 sync_runtime_config pushes CFG_WIFI when provided (F-007)
        sent_cmds = []
        hub = EspBleHubClient(mode="MOCK")
        hub.start()
        orig_send = hub.send_command
        def _spy_send(cmd, wait_ack=True, timeout_s=0.8):
            sent_cmds.append(cmd)
            return orig_send(cmd, wait_ack=wait_ack, timeout_s=timeout_s)
        hub.send_command = _spy_send  # type: ignore[method-assign]
        hub.sync_runtime_config("XE-01", "https://srv.example.com", "a" * 32, wifi_ssid="Driver4G", wifi_pass="pass999")
        self.assertIn("CFG_WIFI:Driver4G|pass999", sent_cmds)

        # 4. Yawn geometry coherent threshold (ar=0.70 -> True, smile ar=0.25 -> False) & unrotate_bbox_in_roi
        ok_yawn, _ = evaluate_mouth_yawn_geometry((10, 10, 50, 38), (0, 0, 200, 200), yaw_deg=0.0)
        ok_smile, _ = evaluate_mouth_yawn_geometry((10, 10, 50, 20), (0, 0, 200, 200), yaw_deg=0.0)
        self.assertTrue(ok_yawn)
        self.assertFalse(ok_smile)
        ux1, uy1, ux2, uy2 = unrotate_bbox_in_roi((20, 20, 40, 40), 100, 100, 20.0)
        self.assertGreater(ux2, ux1)
        self.assertGreater(uy2, uy1)

        # 5. Tier-1 Eye_Closed vs Eye_Open conflict resolution by max_conf + real confidence + 2-frame hysteresis
        class _FakeBox:
            def __init__(self, cid, conf, xyxy):
                self.cls = [cid]
                self.conf = [conf]
                self.xyxy = [np.array(xyxy, dtype=np.float32)]

        class _FakeRes:
            def __init__(self, boxes):
                self.names = {1: "Eye_Closed", 2: "Eye_Open"}
                self.boxes = boxes

        class _FakeYolo:
            def __init__(self, boxes):
                self._boxes = boxes
            def predict(self, *args, **kwargs):
                return [_FakeRes(self._boxes)]

        cfg = EdgeConfig.from_env(env={"RUN_MODE": "local", "ENV_PROFILE": "lab", "DISABLE_AI": "0"})
        pipe = EdgeAiPipeline(cfg, yolo_model=_FakeYolo([_FakeBox(1, 0.36, [0, 0, 20, 20]), _FakeBox(2, 0.91, [0, 0, 20, 20])]))
        plan = ThermalPolicy().plan(50.0, 0)
        out = pipe._run_inference(np.zeros((120, 160, 3), dtype=np.uint8), plan)
        self.assertFalse(out.eye_closed, "Strong Eye_Open (0.91) must beat weak Eye_Closed (0.36)!")

        # 5b. Empty cabin / no face detected: stray background 'phone'/'Smoking' MUST NEVER trigger alert!
        class _FakePhoneOnlyRes:
            def __init__(self):
                self.names = {5: "Smoking", 6: "phone"}
                self.boxes = [_FakeBox(6, 0.85, [10, 10, 40, 80]), _FakeBox(5, 0.82, [50, 10, 80, 40])]

        class _FakePhoneOnlyYolo:
            def predict(self, *args, **kwargs):
                return [_FakePhoneOnlyRes()]

        pipe_empty = EdgeAiPipeline(cfg, yolo_model=_FakePhoneOnlyYolo())
        out_empty = pipe_empty._run_inference(np.zeros((120, 160, 3), dtype=np.uint8), plan)
        self.assertFalse(out_empty.face_detected)
        self.assertFalse(out_empty.phone, "Empty room without face MUST NOT trigger PHONE!")
        self.assertFalse(out_empty.smoking, "Empty room without face MUST NOT trigger SMOKING!")

        # 2-frame negative hysteresis holds through 1 noisy False frame within <=0.8s, resets on 2nd False
        f = SensorFusion(eye_threshold_s=1.0, max_blind_hold_s=0.75)
        f.update({"eye": True}, now_mono=0.0)
        f.update({"eye": True}, now_mono=0.5)
        d_noise, _ = f.update({"eye": False}, now_mono=0.7)
        self.assertTrue(d_noise.in_blind_hold)
        d_rec, _ = f.update({"eye": True}, now_mono=1.1)
        self.assertEqual(d_rec.alert_state, "EDGE_TRIGGERED")
        f.update({"eye": False}, now_mono=1.2)
        d_clear, _ = f.update({"eye": False}, now_mono=1.3)
        self.assertEqual(d_clear.alert_state, "NORMAL")

    def test_25_live_camera_viewer_cookie_auth_hud_overlay_and_real_geo_math(self):
        """Verify Pi live viewer page (/ + /api/web_login + on-demand HUD overlay) and real WGS-84 geo math."""
        from contracts.geo_math import (
            classify_gps_wiring,
            ecef_to_geodetic,
            filter_kinematic_fix,
            format_dms,
            geodetic_to_ecef,
            geodetic_to_enu,
            geodetic_to_utm,
            haversine_m,
            initial_bearing_deg,
            is_valid_wgs84,
        )
        from pi_edge.comms.esp_ble_client import EspBleHubClient

        # 1. Pure WGS-84 geodetic math verification (Da Nang real coordinates, ZERO mock/sample fallback)
        self.assertFalse(is_valid_wgs84(0.0, 0.0))
        self.assertFalse(is_valid_wgs84(95.0, 108.0))
        self.assertTrue(is_valid_wgs84(16.0685, 108.2215))

        x, y, z = geodetic_to_ecef(16.0685, 108.2215, 25.0)
        lat2, lon2, alt2 = ecef_to_geodetic(x, y, z)
        self.assertAlmostEqual(lat2, 16.0685, places=6)
        self.assertAlmostEqual(lon2, 108.2215, places=6)
        self.assertAlmostEqual(alt2, 25.0, places=3)

        utm = geodetic_to_utm(16.0685, 108.2215)
        self.assertIsNotNone(utm)
        self.assertEqual(utm["zone"], 49)
        self.assertEqual(utm["band"], "Q")  # 16°N..24°N = MGRS band Q (Da Nang 16.0685°N = 49Q)
        self.assertIn("N", format_dms(16.0685, 108.2215))

        # ENU & Haversine consistency: ~0.009 deg North is ~1000 m due North (bearing ~0 deg)
        d_m = haversine_m(16.0685, 108.2215, 16.0775, 108.2215)
        e_m, n_m, _ = geodetic_to_enu(16.0775, 108.2215, 0.0, 16.0685, 108.2215, 0.0)
        self.assertAlmostEqual(d_m, 1000.7, delta=10.0)
        self.assertAlmostEqual(n_m, d_m, delta=10.0)
        self.assertLess(abs(e_m), 2.0)
        self.assertAlmostEqual(initial_bearing_deg(16.0685, 108.2215, 16.0775, 108.2215), 0.0, delta=0.5)

        # Kinematic filter: slow walk 12m at 0.5 km/h MUST update (fixes <1.8 km/h freeze bug!), teleport >300 km/h rejected!
        walk = filter_kinematic_fix(
            prev_lat=16.068500, prev_lon=108.221500, prev_ts=100.0,
            lat=16.068610, lon=108.221500, now_ts=110.0, reported_speed_kmh=0.5, accuracy_m=5.0,
        )
        self.assertTrue(walk["accepted"])
        self.assertAlmostEqual(walk["lat"], 16.068610, places=6)
        self.assertGreater(walk["delta_m"], 10.0)

        glitch = filter_kinematic_fix(
            prev_lat=16.068500, prev_lon=108.221500, prev_ts=100.0,
            lat=16.200000, lon=108.221500, now_ts=101.0, reported_speed_kmh=20.0,
        )
        self.assertFalse(glitch["accepted"])
        self.assertIn("OUTLIER_JUMP", glitch["reason"])

        self.assertEqual(classify_gps_wiring(is_valid=False, gps_rx_bytes=0), "NO_UART2_DATA_CHECK_WIRING")
        self.assertIn("ACQUIRING_FIX_6", classify_gps_wiring(is_valid=False, sats_in_view=6, gps_rx_bytes=512, nmea_ok=8))

        # 2. EspBleHubClient parses extended FDBLE fields (heading, hdop, sats_view, gps_rx_bytes, wiring_status)
        hub = EspBleHubClient(mode="MOCK")
        hub.handle_incoming_line(
            'FDBLE:{"role":"COMMS_BLE_GPS_HUB","gps_fix":true,"lat":16.0685,"lon":108.2215,'
            '"speed_kmh":32.4,"alt_m":15.2,"heading":88.5,"hdop":1.4,"fix_q":1,"sats":9,"sats_view":12,'
            '"gps_rx_bytes":4096,"nmea_ok":40,"nmea_bad":0,"free_heap":110000}'
        )
        gps = hub.get_gps_snapshot()
        self.assertTrue(gps["is_valid"])
        self.assertEqual(gps["heading_deg"], 88.5)
        self.assertEqual(gps["hdop"], 1.4)
        self.assertEqual(gps["accuracy_m"], 3.5)
        self.assertEqual(gps["wiring_status"], "LOCKED_3D")

        # 3. MiniWebServer live viewer page (GET /, POST /api/web_login cookie, on-demand overlay does NOT mutate clean frame)
        with tempfile.TemporaryDirectory() as td:
            ac = EdgeAccessController({"vwr-live-token-123": "viewer"})
            audit = AuditLogger(Path(td) / "audit")
            overlay_calls = []

            def _overlay(img_copy, st):
                overlay_calls.append(True)
                img_copy[0:10, 0:10] = (0, 0, 255)
                return img_copy

            srv = MiniWebServer("127.0.0.1", 0, "minimal_server", ac, audit, Path(td), overlay_fn=_overlay, enable_live_stream=True)
            port = srv.start()
            try:
                clean = np.zeros((120, 160, 3), dtype=np.uint8)
                srv.update_frame(1, clean, {"driver_state": {"alert_type": "NORMAL"}}, is_stale=False)
                # Viewer HTML served at /
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r_html:
                    self.assertEqual(r_html.status, 200)
                    self.assertIn("Camera Trực Tiếp", r_html.read().decode("utf-8"))
                # Login issues HttpOnly cookie web_token enabling /api/live_meta
                req_login = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/web_login",
                    data=json.dumps({"token": "vwr-live-token-123"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req_login, timeout=2) as r_log:
                    self.assertEqual(r_log.status, 200)
                    sc = r_log.headers.get("Set-Cookie", "")
                    self.assertIn("web_token=vwr-live-token-123", sc)
                    self.assertIn("HttpOnly", sc)
                req_meta = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/live_meta",
                    headers={"Cookie": "web_token=vwr-live-token-123"},
                )
                with urllib.request.urlopen(req_meta, timeout=2) as r_meta:
                    self.assertEqual(r_meta.status, 200)
                    self.assertTrue(json.loads(r_meta.read().decode("utf-8"))["ok"])
                # On-demand JPEG render uses overlay without mutating original clean numpy frame!
                fid, jpg = srv._get_jpeg()
                self.assertEqual(fid, 1)
                self.assertTrue(jpg.startswith(b"\xff\xd8"))
                self.assertEqual(len(overlay_calls), 1)
                self.assertEqual(int(clean[0, 0, 2]), 0, "Clean BurstCollector frame MUST NEVER be mutated by live HUD!")
            finally:
                srv.close()

            # When enable_live_stream=False (default server mode), / and /api/live_meta MUST return 404
            srv_off = MiniWebServer("127.0.0.1", 0, "minimal_server", ac, audit, Path(td), enable_live_stream=False)
            port_off = srv_off.start()
            try:
                with self.assertRaises(urllib.error.HTTPError) as ctx_off:
                    urllib.request.urlopen(f"http://127.0.0.1:{port_off}/", timeout=2)
                self.assertEqual(ctx_off.exception.code, 404)
            finally:
                srv_off.close()


if __name__ == "__main__":
    unittest.main()
