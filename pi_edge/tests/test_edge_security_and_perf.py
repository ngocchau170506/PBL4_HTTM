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
        # Must sit near anchor (220, 106) with aspect-preserving 1:1 letterbox (no 1.77x stretch!)
        self.assertGreater(x1, 140)
        self.assertLess(x2, 300)
        self.assertGreater(y1, 35)
        self.assertLess(y2, 175)
        self.assertLessEqual(abs((x2 - x1) - (y2 - y1)), 2)

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
        self.assertEqual(tp.plan(69.5, 0).thermal_level, 0)
        self.assertEqual(tp.plan(72.0, 0).thermal_level, 1)
        self.assertEqual(tp.plan(76.5, 0).thermal_level, 2)
        p80 = tp.plan(80.0, 0)
        self.assertEqual(p80.thermal_level, 3)
        self.assertFalse(p80.allow_face_geometry)

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

    def test_26_calibration_10s_boot_waits_for_real_frame_and_app_lock_persists_atomically(self):
        from pi_edge.ai.calibration_10s import DriverCalibrator10s, MeshSample, load_driver_profile
        from pi_edge.ai.face_geometry import BlazeFaceGeometry, HeadPoseEstimate
        from pi_edge.ai.pipeline import EdgeAiPipeline

        class _SpyYolo:
            def __init__(self):
                self.calls = 0

            def predict(self, *args, **kwargs):
                self.calls += 1
                return []

        sample_idx = {"n": 0}

        def _fake_extractor(_frame):
            sample_idx["n"] += 1
            # Inject 1 blink frame (0.09) amidst normal open-eye EAR=0.30 samples
            ear_val = 0.09 if sample_idx["n"] == 3 else 0.30
            pose = HeadPoseEstimate(valid=True, yaw_deg=4.5, pitch_deg=-3.2, roll_deg=1.0, face_box=(100, 60, 220, 180), score=0.92)
            ms = MeshSample(valid=True, presence=0.96, ear=ear_val, mar=0.06, left_eye_box=(120, 95, 148, 112), right_eye_box=(172, 95, 200, 112))
            return pose, ms, True

        with tempfile.TemporaryDirectory() as td:
            cal_dir = Path(td) / "calibration"
            cfg = EdgeConfig.from_env(
                env={
                    "RUN_MODE": "local",
                    "ENV_PROFILE": "lab",
                    "CALIBRATION_DIR": str(cal_dir),
                    "VIOLATION_STORAGE_DIR": str(Path(td) / "v"),
                }
            )
            geo = BlazeFaceGeometry("nonexistent.task")
            cal = DriverCalibrator10s(cal_dir, geo, duration_s=10.0, auto_boot=True, sample_extractor=_fake_extractor)
            yolo = _SpyYolo()
            pipe = EdgeAiPipeline(cfg, yolo_model=yolo, face_geo=geo, calibrator=cal)
            plan = ThermalPolicy().plan(55.0, 0)
            frm = np.zeros((240, 320, 3), dtype=np.uint8)

            # 1. While NO_CAMERA (frame=None) or camera is_stale=True across 8 seconds -> timer MUST NOT start!
            for t_wait in (0.0, 2.5, 5.0, 8.0):
                st_none = pipe.evaluate_tick(None, plan, now_mono=t_wait, is_stale=True)
                st_stale = pipe.evaluate_tick(frm, plan, now_mono=t_wait + 0.1, is_stale=True)
                self.assertEqual(st_none["calibration"]["state"], "IDLE")
                self.assertEqual(st_stale["calibration"]["state"], "IDLE")
                self.assertEqual(st_stale["calibration"]["progress_s"], 0.0)

            # 2. First real fresh frame at t=20.0 starts Mode 1 (BOOT) 10.0s countdown!
            st_start = pipe.evaluate_tick(frm, plan, now_mono=20.0, is_stale=False)
            self.assertEqual(st_start["calibration"]["state"], "RUNNING")
            self.assertEqual(st_start["calibration"]["mode"], "BOOT")

            # Feed frames during 10s BOOT -> YOLO must rest (0 calls) and no false alerts trigger
            for step in range(1, 10):
                t_cur = 20.0 + step * 1.0
                out = pipe._run_inference(frm, plan, now_mono=t_cur)
                pipe._latest_infer = out
                tick_st = pipe.evaluate_tick(frm, plan, now_mono=t_cur, is_stale=False)
                self.assertEqual(tick_st["alert_state"], "NORMAL")
                self.assertIn(tick_st["verification_mode"], ("SINGLE_MODEL_YOLO", "YOLO_BLAZEFACE_SEQ"))
            self.assertEqual(yolo.calls, 0, "YOLO must rest during 10s MediaPipe calibration!")

            # Finalize BOOT at t=30.1 -> temporary baseline (locked=False)
            pipe._run_inference(frm, plan, now_mono=30.1)
            boot_done = pipe.get_calibration_status(now_mono=30.1)
            self.assertEqual(boot_done["state"], "DONE")
            self.assertFalse(boot_done["locked"])
            evt_boot = pipe.pop_calibration_event()
            self.assertIsNotNone(evt_boot)
            self.assertFalse(evt_boot["locked"])

            # 3. Mode 2 (APP_LOCK): driver triggers CALIBRATE -> busy guard on duplicate -> atomic locked=True save
            ok1, msg1 = pipe.request_calibration(source="APP_BLE", lock=True, now_mono=50.0)
            self.assertTrue(ok1)
            self.assertEqual(msg1, "CALIBRATION_STARTED")
            ok2, msg2 = pipe.request_calibration(source="CONTROL_CMD", lock=True, now_mono=51.0)
            self.assertFalse(ok2)
            self.assertEqual(msg2, "CALIBRATION_BUSY")

            for step in range(1, 11):
                pipe._run_inference(frm, plan, now_mono=50.0 + step * 1.0)
            lock_st = pipe.get_calibration_status(now_mono=60.1)
            self.assertEqual(lock_st["state"], "DONE")
            self.assertEqual(lock_st["mode"], "APP_LOCK")
            self.assertTrue(lock_st["locked"])
            self.assertTrue(lock_st["glare_mode"])
            # 0.30 * 0.70 = 0.210 (blink 0.09 rejected!)
            self.assertAlmostEqual(lock_st["ear_closed_threshold"], 0.210, places=3)

            saved = load_driver_profile(cal_dir / "driver_profile.json")
            self.assertIsNotNone(saved)
            self.assertTrue(saved["locked"])
            self.assertEqual(saved["source"], "APP_BLE")
            self.assertGreaterEqual(saved["samples"], 8)
            self.assertAlmostEqual(saved["neutral_yaw_deg"], 4.5, places=2)

    def test_27_calibration_10s_locked_profile_survives_next_boot_and_failed_run_preserves_profile(self):
        from pi_edge.ai.calibration_10s import DriverCalibrator10s, MeshSample, load_driver_profile, save_driver_profile_atomic
        from pi_edge.ai.face_geometry import BlazeFaceGeometry, HeadPoseEstimate

        with tempfile.TemporaryDirectory() as td:
            cal_dir = Path(td) / "calibration"
            prof_path = cal_dir / "driver_profile.json"
            locked_prof = {
                "locked": True,
                "created_ts": 1700000123.45,
                "source": "APP_BLE",
                "ear_open_median": 0.31,
                "ear_closed_threshold": 0.2232,
                "mar_rest_median": 0.05,
                "neutral_yaw_deg": 6.0,
                "neutral_pitch_deg": -4.0,
                "neutral_roll_deg": 0.5,
                "glare_mode": True,
                "samples": 42,
            }
            save_driver_profile_atomic(prof_path, locked_prof)

            # Next Pi boot: even when 10s BOOT runs with different face values, locked=True profile MUST NOT be overwritten!
            def _different_driver_extractor(_frame):
                return (
                    HeadPoseEstimate(valid=True, yaw_deg=-15.0, pitch_deg=10.0, roll_deg=0.0, face_box=(50, 50, 150, 150), score=0.9),
                    MeshSample(valid=True, presence=0.95, ear=0.20, mar=0.08),
                    False,
                )

            geo = BlazeFaceGeometry("nonexistent.task")
            cal_boot = DriverCalibrator10s(cal_dir, geo, duration_s=10.0, auto_boot=True, sample_extractor=_different_driver_extractor)
            self.assertTrue(cal_boot.get_status_dict()["locked"])
            self.assertAlmostEqual(cal_boot.get_ear_closed_threshold(), 0.2232, places=4)

            frm = np.zeros((160, 160, 3), dtype=np.uint8)
            cal_boot.notify_real_camera_frame(now_mono=10.0)
            self.assertEqual(cal_boot.get_status_dict()["state"], "RUNNING")
            for k in range(1, 11):
                cal_boot.step_frame(frm, now_mono=10.0 + k * 1.0)
            st_after_boot = cal_boot.get_status_dict(now_mono=20.2)
            self.assertEqual(st_after_boot["state"], "DONE")
            self.assertTrue(st_after_boot["locked"])
            disk_after_boot = load_driver_profile(prof_path)
            self.assertEqual(disk_after_boot["created_ts"], 1700000123.45, "BOOT must NEVER overwrite locked driver_profile.json!")
            self.assertAlmostEqual(disk_after_boot["ear_closed_threshold"], 0.2232, places=4)

            # Explicit APP_LOCK with NO_FACE (0 samples) -> FAILED(NO_FACE) & previous locked profile preserved!
            def _no_face_extractor(_frame):
                return HeadPoseEstimate(valid=False, yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0), MeshSample(valid=False, presence=0.0, ear=0.0, mar=0.0), False

            cal_fail = DriverCalibrator10s(cal_dir, geo, duration_s=10.0, auto_boot=False, sample_extractor=_no_face_extractor)
            cal_fail.pop_event()
            cal_fail.request_calibration(source="CONTROL_CMD", lock=True, now_mono=100.0)
            for k in range(1, 11):
                cal_fail.step_frame(frm, now_mono=100.0 + k * 1.0)
            st_fail = cal_fail.get_status_dict(now_mono=110.1)
            self.assertEqual(st_fail["state"], "FAILED")
            evt_fail = cal_fail.pop_event()
            self.assertEqual(evt_fail["reason"], "NO_FACE")
            # Disk profile still intact!
            self.assertEqual(load_driver_profile(prof_path)["created_ts"], 1700000123.45)

            # Explicit APP_LOCK with 3 samples (< 5) -> FAILED(LOW_SAMPLES) & previous profile still preserved!
            cnt = {"i": 0}
            def _few_samples_extractor(_frame):
                cnt["i"] += 1
                if cnt["i"] <= 3:
                    return (
                        HeadPoseEstimate(valid=True, yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0, face_box=(40, 40, 120, 120), score=0.9),
                        MeshSample(valid=True, presence=0.9, ear=0.28, mar=0.05),
                        False,
                    )
                return _no_face_extractor(_frame)

            cal_low = DriverCalibrator10s(cal_dir, geo, duration_s=10.0, auto_boot=False, sample_extractor=_few_samples_extractor)
            cal_low.request_calibration(source="APP_HTTP", lock=True, now_mono=200.0)
            for k in range(1, 11):
                cal_low.step_frame(frm, now_mono=200.0 + k * 1.0)
            self.assertEqual(cal_low.get_status_dict(now_mono=210.1)["state"], "FAILED")
            self.assertEqual(cal_low.pop_event()["reason"], "LOW_SAMPLES")
            self.assertEqual(load_driver_profile(prof_path)["created_ts"], 1700000123.45)

    def test_28_eye_closed_not_overridden_by_normal_and_smart_roi_clahe_works_at_roll_zero(self):
        from pi_edge.ai.calibration_10s import DriverCalibrator10s, MeshSample
        from pi_edge.ai.face_geometry import HeadPoseEstimate
        from pi_edge.ai.pipeline import EdgeAiPipeline

        class _FakeBox:
            def __init__(self, cid, conf, xyxy):
                self.cls = [cid]
                self.conf = [conf]
                self.xyxy = [np.array(xyxy, dtype=np.float32)]

        class _FakeRes:
            def __init__(self, boxes):
                self.names = {0: "Normal", 1: "Eye_Closed", 2: "Eye_Open"}
                self.boxes = boxes

        # Case A: Full-face "Normal" (0.76) MUST NOT override "Eye_Closed" (0.38)!
        class _NormalPlusClosedYolo:
            def predict(self, *args, **kwargs):
                return [_FakeRes([_FakeBox(0, 0.76, [100, 60, 240, 200]), _FakeBox(1, 0.38, [120, 100, 155, 118])])]

        class _UprightFaceGeo:
            available = True

            def detect_pose(self, _frame):
                return HeadPoseEstimate(valid=True, yaw_deg=1.0, pitch_deg=-1.0, roll_deg=0.0, face_box=(100, 60, 240, 200), score=0.95)

        with tempfile.TemporaryDirectory() as td:
            cfg = EdgeConfig.from_env(
                env={"RUN_MODE": "local", "ENV_PROFILE": "lab", "CALIBRATION_DIR": str(Path(td) / "cal")}
            )
            geo = _UprightFaceGeo()
            cal = DriverCalibrator10s(Path(td) / "cal", geo, auto_boot=False)  # type: ignore[arg-type]
            plan = ThermalPolicy().plan(55.0, 0)

            pipe_a = EdgeAiPipeline(cfg, yolo_model=_NormalPlusClosedYolo(), face_geo=geo, calibrator=cal)  # type: ignore[arg-type]
            out_a = pipe_a._run_inference(np.zeros((280, 360, 3), dtype=np.uint8), plan)
            self.assertTrue(out_a.eye_closed, "Full-face 'Normal' box MUST NEVER suppress 'Eye_Closed'!")

            # Case B: Upright head (roll=0°!) with glare where full-frame misses eyes -> Smart Face-ROI + CLAHE recovers BOTH eye boxes!
            class _RoiOnlyYolo:
                def __init__(self):
                    self.calls = []

                def predict(self, img, *args, **kwargs):
                    self.calls.append(img.shape[:2])
                    if len(self.calls) == 1:
                        # Pass 1 (full-frame): only sees "Normal", 0 eye boxes!
                        return [_FakeRes([_FakeBox(0, 0.68, [100, 60, 240, 200])])]
                    # Pass 2 (Smart Face-ROI crop): detects BOTH closed eyes with distinct coordinates!
                    return [
                        _FakeRes(
                            [
                                _FakeBox(1, 0.74, [40, 62, 76, 80]),
                                _FakeBox(1, 0.71, [108, 62, 144, 80]),
                            ]
                        )
                    ]

            roi_yolo = _RoiOnlyYolo()
            pipe_b = EdgeAiPipeline(cfg, yolo_model=roi_yolo, face_geo=geo, calibrator=cal)  # type: ignore[arg-type]
            pipe_b._mesh = None
            glare_img = np.full((280, 360, 3), 90, dtype=np.uint8)
            # Add bright eyeglass reflection patch in upper half of face_box (100..240, 60..130)
            glare_img[80:115, 120:210] = 250
            out_b = pipe_b._run_inference(glare_img, plan)
            self.assertEqual(len(roi_yolo.calls), 2, "Smart Face-ROI must run even when head is upright (roll=0°)!")
            self.assertTrue(out_b.eye_closed)
            closed_boxes = [box for lbl, _, box in out_b.detections if lbl == "Eye_Closed"]
            self.assertEqual(len(closed_boxes), 2, "Must map BOTH distinct Eye_Closed boxes onto full frame!")
            self.assertNotEqual(closed_boxes[0], closed_boxes[1])
            self.assertNotEqual(closed_boxes[0], (100, 60, 240, 200), "Eye box must NOT be overwritten with face_box!")

            # Case C: Severe lens glare where BOTH full-frame and ROI see 0 eyes -> Hybrid 478-Mesh EAR rescues!
            class _BlindEyeYolo:
                def predict(self, *args, **kwargs):
                    return [_FakeRes([_FakeBox(0, 0.60, [100, 60, 240, 200])])]

            class _FakeMeshClosed:
                available = True

                def infer_ear_mar(self, _frame, _fbox):
                    return MeshSample(
                        valid=True,
                        presence=0.91,
                        ear=0.11,
                        mar=0.05,
                        left_eye_box=(122, 98, 152, 114),
                        right_eye_box=(186, 98, 216, 114),
                    )

            pipe_c = EdgeAiPipeline(cfg, yolo_model=_BlindEyeYolo(), face_geo=geo, calibrator=cal)  # type: ignore[arg-type]
            pipe_c._mesh = _FakeMeshClosed()  # type: ignore[assignment]
            out_c = pipe_c._run_inference(glare_img, plan)
            self.assertTrue(out_c.eye_closed, "Hybrid 478-Mesh EAR must rescue when YOLO sees 0 eyes under glare!")
            self.assertIn("MeshEAR", out_c.behavior_detail)

    def test_29_calibrate_command_whitelist_80c_thermal_levels_hud_and_no_mediapipe_import(self):
        from contracts.schema_validator import validate_telemetry_payload
        from pi_edge.app.main import _render_live_hud_overlay
        from pi_edge.core.access_control import is_allowed_control_command

        # 1. Whitelist CALIBRATE
        self.assertTrue(is_allowed_control_command("CALIBRATE"))
        self.assertTrue(is_allowed_control_command("  CALIBRATE  "))
        self.assertFalse(is_allowed_control_command("CALIBRATE_HACK"))

        # 2. Thermal defaults 70 / 75 / 80°C + intervals 0.08 / 0.15 / 0.25 / 0.40s
        cfg = EdgeConfig.from_env(env={"RUN_MODE": "server", "ENV_PROFILE": "lab"})
        self.assertEqual((cfg.thermal_warm_c, cfg.thermal_hot_c, cfg.thermal_crit_c), (70.0, 75.0, 80.0))
        tp = ThermalPolicy(cfg.yolo_imgsz, cfg.thermal_warm_c, cfg.thermal_hot_c, cfg.thermal_crit_c)
        p0 = tp.plan(68.0, 0)
        p1 = tp.plan(72.0, 0)
        p2 = tp.plan(77.0, 0)
        p3 = tp.plan(80.0, 0)
        p_uv = tp.plan(55.0, 0x1)
        self.assertEqual((p0.thermal_level, p0.ai_min_interval_s, p0.allow_face_geometry), (0, 0.08, True))
        self.assertEqual((p1.thermal_level, p1.ai_min_interval_s, p1.allow_face_geometry), (1, 0.15, True))
        self.assertEqual((p2.thermal_level, p2.ai_min_interval_s, p2.allow_face_geometry), (2, 0.25, True))
        self.assertEqual((p3.thermal_level, p3.ai_min_interval_s, p3.yolo_imgsz, p3.allow_face_geometry), (3, 0.40, 256, False))
        self.assertEqual((p_uv.thermal_level, p_uv.allow_face_geometry), (3, False))

        # 3. Telemetry exports driver_state.calibration & HUD draws CALIBRATING 10s banner + 2 eye boxes
        with tempfile.TemporaryDirectory() as td:
            spool = UnifiedSpoolStore(Path(td))
            try:
                sync = ServerSyncClient(cfg, auth_token="tok", spool=spool)
                cal_dict = {
                    "state": "RUNNING",
                    "mode": "APP_LOCK",
                    "progress_s": 4.2,
                    "duration_s": 10.0,
                    "locked": False,
                    "profile_ts": None,
                    "ear_closed_threshold": None,
                    "glare_mode": False,
                }
                payload = sync.build_telemetry_payload(
                    ai_state={
                        "alert_state": "NORMAL",
                        "alert_type": "NORMAL",
                        "verification_mode": "YOLO_BLAZEFACE_SEQ",
                        "face_detected": True,
                        "thermal_level": 0,
                        "calibration": cal_dict,
                    },
                    hw_snap={"temp_c": 62.0, "throttled": "0x0"},
                    fps_snap={"camera_fps": 25.0, "ai_fps": 10.5},
                    esp_sensor={"co2_ppm": 450.0, "gps": {"speed_kmh": 0.0, "is_valid": False}},
                    esp_status={"connected": True},
                    mute_state={},
                )
                ok_s, errs_s = validate_telemetry_payload(payload)
                self.assertTrue(ok_s, f"Schema validation failed: {errs_s}")
                self.assertEqual(payload["driver_state"]["calibration"], cal_dict)

                canvas = np.zeros((240, 320, 3), dtype=np.uint8)
                rendered = _render_live_hud_overlay(
                    canvas.copy(),
                    payload,
                    {
                        "pose": {"valid": True, "yaw_deg": 0.0, "pitch_deg": 0.0, "roll_deg": 0.0, "face_box": (80, 70, 220, 200)},
                        "detections": [("Eye_Closed", 0.73, (100, 110, 130, 124)), ("Eye_Closed", 0.71, (165, 110, 195, 124))],
                        "calibration": cal_dict,
                    },
                )
                # Calibration banner region (y=35..60) must be drawn
                self.assertGreater(int(np.max(rendered[35:60, :])), 0)
            finally:
                spool.close()

        # 4. Zero `import mediapipe` anywhere in pi_edge/ (ARM64 Cortex-A72 SIGILL invariant!)
        pi_edge_root = Path(__file__).resolve().parent.parent
        for py_file in pi_edge_root.rglob("*.py"):
            if py_file.resolve() == Path(__file__).resolve():
                continue
            src = py_file.read_text(encoding="utf-8")
            for raw_line in src.splitlines():
                code_line = raw_line.split("#", 1)[0].strip()
                self.assertNotIn("import mediapipe", code_line, f"Forbidden mediapipe import in {py_file}")
                self.assertNotIn("from mediapipe", code_line, f"Forbidden mediapipe import in {py_file}")

    def test_30_main_control_calibrate_never_hits_esp1_and_returns_no_camera_when_disabled(self):
        import os
        import socket
        from pi_edge.app.main import run as run_edge

        with tempfile.TemporaryDirectory() as td:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                free_port = s.getsockname()[1]
            env_patch = {
                "RUN_MODE": "local",
                "ENV_PROFILE": "lab",
                "WEB_PORT": str(free_port),
                "DISABLE_CAMERA": "1",
                "DISABLE_AI": "1",
                "ESP_MODE": "MOCK",
                "ESP_BLE_MODE": "MOCK",
                "VIOLATION_STORAGE_DIR": str(Path(td) / "v"),
                "LOGS_DIR": str(Path(td) / "l"),
                "AUDIT_DIR": str(Path(td) / "a"),
                "CALIBRATION_DIR": str(Path(td) / "c"),
                "CAMERA_FPS": "4",
            }
            old = {k: os.environ.get(k) for k in env_patch}
            resp_holder = {"json": None}

            def _client():
                deadline = time.monotonic() + 2.5
                while time.monotonic() < deadline:
                    try:
                        req = urllib.request.Request(
                            f"http://127.0.0.1:{free_port}/api/control",
                            data=b'{"cmd":"CALIBRATE"}',
                            headers={"Content-Type": "application/json", "X-Auth-Token": "lab-pi-admin-token"},
                            method="POST",
                        )
                        with urllib.request.urlopen(req, timeout=1.0) as r:
                            resp_holder["json"] = json.loads(r.read().decode("utf-8"))
                            return
                    except Exception:
                        time.sleep(0.05)

            try:
                os.environ.update(env_patch)
                t = threading.Thread(target=_client, daemon=True)
                t.start()
                rc = run_edge(cli_mode="local", max_ticks=5)
                t.join(timeout=2.0)
                self.assertEqual(rc, 0)
                self.assertIsNotNone(resp_holder["json"])
                self.assertFalse(resp_holder["json"]["ok"])
                self.assertEqual(resp_holder["json"]["detail"], "NO_CAMERA")
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_31_round2_mesh_gate_pitch_guard_calib_clamp_roi_256_and_calib_stop_order(self):
        import os
        import socket
        from unittest import mock
        from pi_edge.ai.calibration_10s import (
            DriverCalibrator10s,
            FaceMesh478TFLite,
            MeshSample,
            load_driver_profile,
            save_driver_profile_atomic,
        )
        from pi_edge.ai.face_geometry import BlazeFaceGeometry, HeadPoseEstimate
        from pi_edge.ai.pipeline import EdgeAiPipeline
        from pi_edge.app.main import run as run_edge

        # (a) FaceMesh478TFLite rejects black/flat frame AND anatomically degenerate 478-point output (even if Identity_2=0.99)
        class _FakeBadMeshNet:
            def setInput(self, _blob):
                pass

            def forward(self, name):
                if name == "Identity":
                    # Collapsed cluster (bw=5 < 48, bh=5 < 48)
                    pts = np.full((1, 478, 3), 128.0, dtype=np.float32)
                    pts[0, 0, :2] = [133.0, 133.0]
                    return pts
                return np.array([[0.99]], dtype=np.float32)

        mesh_fake = FaceMesh478TFLite("nonexistent.task")
        mesh_fake._net = _FakeBadMeshNet()
        mesh_fake.available = True
        black = np.zeros((256, 256, 3), dtype=np.uint8)
        self.assertFalse(mesh_fake.infer_ear_mar(black, (0, 0, 256, 256)).valid)

        rng = np.random.default_rng(7)
        noisy = rng.integers(0, 255, size=(256, 256, 3), dtype=np.uint8)
        self.assertFalse(mesh_fake.infer_ear_mar(noisy, (0, 0, 256, 256)).valid)

        real_task = Path(__file__).resolve().parent.parent.parent / "models" / "face_landmarker.task"
        if real_task.is_file():
            mesh_real = FaceMesh478TFLite(str(real_task))
            if mesh_real.available:
                self.assertFalse(mesh_real.infer_ear_mar(black, (0, 0, 256, 256)).valid)

        # (b) pitch=30 deg MUST NOT trigger Hybrid 478-Mesh EAR even with glare & zero-eye streak
        class _FakeBox:
            def __init__(self, cid, conf, xyxy):
                self.cls = [cid]
                self.conf = [conf]
                self.xyxy = [np.array(xyxy, dtype=np.float32)]

        class _FakeRes:
            def __init__(self, boxes):
                self.names = {0: "Normal", 1: "Eye_Closed", 2: "Eye_Open"}
                self.boxes = boxes

        class _Pitch30Geo:
            available = True

            def detect_pose(self, _frame):
                return HeadPoseEstimate(valid=True, yaw_deg=0.0, pitch_deg=30.0, roll_deg=0.0, face_box=(100, 60, 240, 200), score=0.95)

        class _NormalOnlyYolo:
            def predict(self, *args, **kwargs):
                return [_FakeRes([_FakeBox(0, 0.70, [100, 60, 240, 200])])]

        class _SpyMesh:
            available = True

            def __init__(self):
                self.calls = 0

            def infer_ear_mar(self, _frame, _fbox):
                self.calls += 1
                return MeshSample(valid=True, presence=0.95, ear=0.10, mar=0.05)

        with tempfile.TemporaryDirectory() as td:
            cal_dir = Path(td) / "cal"
            cfg = EdgeConfig.from_env(env={"RUN_MODE": "local", "ENV_PROFILE": "lab", "CALIBRATION_DIR": str(cal_dir)})
            plan = ThermalPolicy().plan(55.0, 0)
            glare_img = np.full((280, 360, 3), 90, dtype=np.uint8)
            glare_img[80:115, 120:210] = 250

            geo_p30 = _Pitch30Geo()
            cal_p30 = DriverCalibrator10s(cal_dir, geo_p30, auto_boot=False)  # type: ignore[arg-type]
            spy_mesh = _SpyMesh()
            pipe_p30 = EdgeAiPipeline(cfg, yolo_model=_NormalOnlyYolo(), face_geo=geo_p30, calibrator=cal_p30)  # type: ignore[arg-type]
            pipe_p30._mesh = spy_mesh  # type: ignore[assignment]
            for _ in range(3):
                out_p30 = pipe_p30._run_inference(glare_img, plan)
                self.assertNotIn("MeshEAR", out_p30.behavior_detail)
                self.assertIsNot(out_p30.eye_closed, True)
            self.assertEqual(spy_mesh.calls, 0, "pitch=30 deg MUST NEVER activate MeshEAR!")

            # (c) Calibration rejects off-axis samples (yaw=35, pitch=28) & load_driver_profile clamps neutral pose
            def _off_axis_extractor(_frame):
                return (
                    HeadPoseEstimate(valid=True, yaw_deg=35.0, pitch_deg=28.0, roll_deg=0.0, face_box=(50, 50, 150, 150), score=0.9),
                    MeshSample(valid=True, presence=0.95, ear=0.28, mar=0.05),
                    False,
                )

            geo_dummy = BlazeFaceGeometry("nonexistent.task")
            cal_rej = DriverCalibrator10s(cal_dir, geo_dummy, duration_s=10.0, auto_boot=False, sample_extractor=_off_axis_extractor)
            cal_rej.request_calibration(source="CONTROL_CMD", lock=True, now_mono=10.0)
            for k in range(1, 12):
                cal_rej.step_frame(glare_img, now_mono=10.0 + k * 1.0)
            self.assertEqual(cal_rej.get_status_dict(now_mono=22.0)["state"], "FAILED")
            self.assertEqual(cal_rej.pop_event()["reason"], "NO_FACE")

            bad_prof_path = cal_dir / "clamped_profile.json"
            save_driver_profile_atomic(
                bad_prof_path,
                {
                    "locked": True,
                    "created_ts": 1700000999.0,
                    "source": "APP_BLE",
                    "ear_open_median": 0.28,
                    "ear_closed_threshold": 0.19,
                    "mar_rest_median": 0.05,
                    "neutral_yaw_deg": 42.0,
                    "neutral_pitch_deg": -33.0,
                    "neutral_roll_deg": 27.0,
                    "glare_mode": True,
                    "samples": 20,
                },
            )
            clamped = load_driver_profile(bad_prof_path)
            self.assertIsNotNone(clamped)
            self.assertEqual(clamped["neutral_yaw_deg"], 18.0)
            self.assertEqual(clamped["neutral_pitch_deg"], -15.0)
            self.assertEqual(clamped["neutral_roll_deg"], 15.0)

            # (d) glare_mode=True + Pass 1 sees 2 eyes conf>=0.52 -> NO 2nd YOLO pass; when ROI runs -> imgsz=256
            class _UprightGeo:
                available = True

                def detect_pose(self, _frame):
                    return HeadPoseEstimate(valid=True, yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0, face_box=(100, 60, 240, 200), score=0.95)

            class _TwoStrongEyesYolo:
                def __init__(self):
                    self.imgsz_calls = []

                def predict(self, _img, *args, **kwargs):
                    self.imgsz_calls.append(kwargs.get("imgsz"))
                    return [
                        _FakeRes(
                            [
                                _FakeBox(2, 0.72, [120, 100, 155, 118]),
                                _FakeBox(2, 0.68, [180, 100, 215, 118]),
                            ]
                        )
                    ]

            cal_glare = DriverCalibrator10s(cal_dir, _UprightGeo(), auto_boot=False)  # type: ignore[arg-type]
            cal_glare._profile = dict(clamped)  # glare_mode=True
            y_strong = _TwoStrongEyesYolo()
            pipe_g = EdgeAiPipeline(cfg, yolo_model=y_strong, face_geo=_UprightGeo(), calibrator=cal_glare)  # type: ignore[arg-type]
            pipe_g._run_inference(glare_img, plan)
            self.assertEqual(len(y_strong.imgsz_calls), 1, "Pass 1 with 2 eyes conf>=0.52 must NOT invoke 2nd YOLO pass even under glare_mode!")

            class _OneEyeYolo:
                def __init__(self):
                    self.imgsz_calls = []

                def predict(self, _img, *args, **kwargs):
                    self.imgsz_calls.append(kwargs.get("imgsz"))
                    return [_FakeRes([_FakeBox(2, 0.72, [120, 100, 155, 118])])]

            y_one = _OneEyeYolo()
            pipe_r = EdgeAiPipeline(cfg, yolo_model=y_one, face_geo=_UprightGeo(), calibrator=cal_glare)  # type: ignore[arg-type]
            pipe_r._run_inference(glare_img, plan)
            self.assertEqual(len(y_one.imgsz_calls), 2)
            self.assertEqual(y_one.imgsz_calls[1], 256, "Smart ROI pass must always use imgsz=256!")

            # (e) _exec_cmd("CALIBRATE") sends "STOP" strictly before "ALERT:1:120"
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                free_port = s.getsockname()[1]

            esp_cmds = []

            class _SpyEsp:
                connected = True
                device_role = "AUDIO_MQ135_NODE"
                port = "MOCK"
                baudrate = 115200

                def __init__(self, *a, **kw):
                    pass

                def start(self):
                    return True

                def close(self):
                    pass

                def send_command(self, cmd, wait_ack=True, timeout_s=0.8):
                    esp_cmds.append(str(cmd))
                    return mock.Mock(ok=True, ack_line="OK", error="")

                def get_sensor_snapshot(self):
                    return {"co2_ppm": 400.0, "raw_adc": 0, "gps": {"lat": None, "lon": None, "speed_kmh": 0.0, "satellites": 0, "is_valid": False}}

                def get_esp_status_block(self):
                    return {"connected": True, "hw_events": []}

                def pop_mute_state_block(self):
                    return {"newly_triggered": False}

            cam_ready_evt = threading.Event()

            class _OpenedCam:
                error = ""

                def __init__(self, *a, **kw):
                    pass

                def start(self):
                    cam_ready_evt.set()

                def close(self):
                    pass

                def is_opened(self):
                    return True

                def read_latest(self, last_seen_id=0, timeout_s=0.05):
                    time.sleep(0.05)
                    return None

            env_patch = {
                "RUN_MODE": "local",
                "ENV_PROFILE": "lab",
                "WEB_PORT": str(free_port),
                "DISABLE_CAMERA": "0",
                "DISABLE_AI": "1",
                "ESP_MODE": "MOCK",
                "ESP_BLE_MODE": "MOCK",
                "VIOLATION_STORAGE_DIR": str(Path(td) / "v2"),
                "LOGS_DIR": str(Path(td) / "l2"),
                "AUDIT_DIR": str(Path(td) / "a2"),
                "CALIBRATION_DIR": str(Path(td) / "c2"),
                "CAMERA_FPS": "4",
            }
            old_env = {k: os.environ.get(k) for k in env_patch}
            resp_cal = {"json": None}

            def _post_cal():
                cam_ready_evt.wait(timeout=3.0)
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    try:
                        req = urllib.request.Request(
                            f"http://127.0.0.1:{free_port}/api/control",
                            data=b'{"cmd":"CALIBRATE"}',
                            headers={"Content-Type": "application/json", "X-Auth-Token": "lab-pi-admin-token"},
                            method="POST",
                        )
                        with urllib.request.urlopen(req, timeout=1.5) as r:
                            resp_cal["json"] = json.loads(r.read().decode("utf-8"))
                            return
                    except Exception:
                        time.sleep(0.05)

            try:
                os.environ.update(env_patch)
                with (
                    mock.patch("pi_edge.comms.esp_client.EspClient", _SpyEsp),
                    mock.patch("pi_edge.camera.frame_source.CameraFrameSource", _OpenedCam),
                    mock.patch("pi_edge.camera.camera_config.inspect_eth_camera_route_readonly", return_value={"ok": True}),
                ):
                    t = threading.Thread(target=_post_cal, daemon=True)
                    t.start()
                    rc = run_edge(cli_mode="local", max_ticks=8)
                    t.join(timeout=3.0)
                self.assertEqual(rc, 0)
                self.assertIsNotNone(resp_cal["json"])
                self.assertTrue(resp_cal["json"]["ok"], f"Unexpected CALIBRATE response: {resp_cal['json']}")
                self.assertIn("STOP", esp_cmds)
                self.assertIn("ALERT:1:120", esp_cmds)
                self.assertLess(esp_cmds.index("STOP"), esp_cmds.index("ALERT:1:120"))
            finally:
                for k, v in old_env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_32_standalone_app_iot_pi_local_routes_cors_and_rbac(self):
        import http.client

        with tempfile.TemporaryDirectory() as td:
            app_dir = Path(td) / "APP_IOT"
            app_dir.mkdir()
            (app_dir / "driver.html").write_text("<!doctype html><title>Driver App</title>", encoding="utf-8")
            (app_dir / "secret.py").write_text("SECRET = 1\n", encoding="utf-8")
            vdir = Path(td) / "violations"
            vdir.mkdir()
            audit = AuditLogger(Path(td) / "audit")
            ac = EdgeAccessController(
                {
                    "vwr-standalone-123": "viewer",
                    "opr-standalone-123": "operator",
                }
            )

            # 1. minimal_server + enable_live_stream=False -> /driver, /app_iot/*, /api/app_status, POST /api/control MUST be 404
            srv_off = MiniWebServer(
                "127.0.0.1",
                0,
                "minimal_server",
                ac,
                audit,
                vdir,
                enable_live_stream=False,
                app_iot_dir=app_dir,
            )
            port_off = srv_off.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port_off, timeout=2)
                conn.request("GET", "/api/v1/health")
                r_h = conn.getresponse()
                h_data = json.loads(r_h.read().decode("utf-8"))
                self.assertEqual(r_h.status, 200)
                self.assertEqual(h_data["target"], "pi_local")
                self.assertTrue(h_data["app_iot_available"])
                conn.close()

                for method, path, body, hdrs in [
                    ("GET", "/driver", None, {}),
                    ("GET", "/app_iot/driver.html", None, {}),
                    ("GET", "/api/app_status", None, {"X-Auth-Token": "vwr-standalone-123"}),
                    (
                        "POST",
                        "/api/control",
                        b'{"cmd":"CALIBRATE"}',
                        {"Content-Type": "application/json", "X-Auth-Token": "opr-standalone-123"},
                    ),
                ]:
                    c = http.client.HTTPConnection("127.0.0.1", port_off, timeout=2)
                    c.request(method, path, body=body, headers=hdrs)
                    resp = c.getresponse()
                    resp.read()
                    self.assertEqual(resp.status, 404, f"{method} {path} must be 404 when server mode & stream off")
                    c.close()
            finally:
                srv_off.close()

            # 2. enable_live_stream=True -> /driver (307), /app_iot/driver.html (200), path traversal (403),
            #    /api/app_status (401 -> 200), POST /api/control (403 viewer -> 200 operator), CORS LAN allow / evil deny
            cmds_seen = []
            srv_on = MiniWebServer(
                "127.0.0.1",
                0,
                "minimal_server",
                ac,
                audit,
                vdir,
                on_control_cmd=lambda cmd: (cmds_seen.append(cmd) or True, "CALIBRATION_STARTED"),
                enable_live_stream=True,
                app_iot_dir=app_dir,
            )
            port_on = srv_on.start()
            try:
                srv_on.update_frame(
                    1,
                    np.zeros((120, 160, 3), dtype=np.uint8),
                    {"vehicle_id": "VEH_PI_01", "driver_state": {"alert_type": "NORMAL"}},
                    is_stale=False,
                )

                # GET /driver -> 307 redirect to /app_iot/driver.html
                c = http.client.HTTPConnection("127.0.0.1", port_on, timeout=2)
                c.request("GET", "/driver")
                r_drv = c.getresponse()
                r_drv.read()
                self.assertEqual(r_drv.status, 307)
                self.assertEqual(r_drv.getheader("Location"), "/app_iot/driver.html")
                c.close()

                # GET /app_iot/driver.html -> 200 OK
                c = http.client.HTTPConnection("127.0.0.1", port_on, timeout=2)
                c.request("GET", "/app_iot/driver.html")
                r_html = c.getresponse()
                html_body = r_html.read().decode("utf-8")
                self.assertEqual(r_html.status, 200)
                self.assertIn("text/html", r_html.getheader("Content-Type") or "")
                self.assertIn("Driver App", html_body)
                c.close()

                # Path traversal & disallowed extension -> 403
                for bad_path in ("/app_iot/../pi_edge/core/config.py", "/app_iot/%2e%2e/pi_edge/core/config.py", "/app_iot/secret.py"):
                    c = http.client.HTTPConnection("127.0.0.1", port_on, timeout=2)
                    c.request("GET", bad_path)
                    r_bad = c.getresponse()
                    r_bad.read()
                    self.assertEqual(r_bad.status, 403, f"Expected 403 for {bad_path}")
                    c.close()

                # GET /api/app_status -> 401 without token, 200 with viewer token
                c = http.client.HTTPConnection("127.0.0.1", port_on, timeout=2)
                c.request("GET", "/api/app_status")
                r_unauth = c.getresponse()
                r_unauth.read()
                self.assertEqual(r_unauth.status, 401)
                c.close()

                c = http.client.HTTPConnection("127.0.0.1", port_on, timeout=2)
                c.request(
                    "GET",
                    "/api/app_status",
                    headers={"X-Auth-Token": "vwr-standalone-123", "Origin": "http://192.168.1.3:5000"},
                )
                r_st = c.getresponse()
                st_data = json.loads(r_st.read().decode("utf-8"))
                self.assertEqual(r_st.status, 200)
                self.assertEqual(r_st.getheader("Access-Control-Allow-Origin"), "http://192.168.1.3:5000")
                self.assertEqual(r_st.getheader("Vary"), "Origin")
                self.assertTrue(st_data["ok"])
                self.assertEqual(st_data["source"], "pi_local")
                self.assertEqual(st_data["role"], "viewer")
                self.assertEqual(st_data["state"]["vehicle_id"], "VEH_PI_01")
                c.close()

                # POST /api/control CALIBRATE -> 403 viewer -> 200 operator
                c = http.client.HTTPConnection("127.0.0.1", port_on, timeout=2)
                c.request(
                    "POST",
                    "/api/control",
                    body=b'{"cmd":"CALIBRATE"}',
                    headers={"Content-Type": "application/json", "X-Auth-Token": "vwr-standalone-123"},
                )
                r_vwr = c.getresponse()
                r_vwr.read()
                self.assertEqual(r_vwr.status, 403)
                c.close()

                c = http.client.HTTPConnection("127.0.0.1", port_on, timeout=2)
                c.request(
                    "POST",
                    "/api/control",
                    body=b'{"cmd":"CALIBRATE"}',
                    headers={"Content-Type": "application/json", "X-Auth-Token": "opr-standalone-123"},
                )
                r_opr = c.getresponse()
                opr_data = json.loads(r_opr.read().decode("utf-8"))
                self.assertEqual(r_opr.status, 200)
                self.assertTrue(opr_data["ok"])
                self.assertEqual(opr_data["cmd"], "CALIBRATE")
                self.assertEqual(cmds_seen, ["CALIBRATE"])
                c.close()

                # CORS preflight OPTIONS: allows LAN origin, rejects evil origin (never '*')
                c = http.client.HTTPConnection("127.0.0.1", port_on, timeout=2)
                c.request("OPTIONS", "/api/control", headers={"Origin": "http://192.168.1.3:5000"})
                r_opt_ok = c.getresponse()
                r_opt_ok.read()
                self.assertEqual(r_opt_ok.status, 204)
                self.assertEqual(r_opt_ok.getheader("Access-Control-Allow-Origin"), "http://192.168.1.3:5000")
                self.assertIn("OPTIONS", r_opt_ok.getheader("Access-Control-Allow-Methods") or "")
                self.assertIn("X-Auth-Token", r_opt_ok.getheader("Access-Control-Allow-Headers") or "")
                self.assertEqual(r_opt_ok.getheader("Vary"), "Origin")
                c.close()

                c = http.client.HTTPConnection("127.0.0.1", port_on, timeout=2)
                c.request("OPTIONS", "/api/control", headers={"Origin": "https://evil.example.com"})
                r_opt_bad = c.getresponse()
                r_opt_bad.read()
                self.assertEqual(r_opt_bad.status, 204)
                self.assertIsNone(r_opt_bad.getheader("Access-Control-Allow-Origin"))
                c.close()
            finally:
                srv_on.close()

            # 3. R3-02: POST /api/control {"cmd":"REST_BREAK_REQUEST"} in main.run -> ok=True, REST_BREAK_ACK,
            #    sends MUTE (wait_ack=False) and NEVER forwards "REST_BREAK_REQUEST" to ESP32 #1!
            import os
            import socket
            from unittest import mock
            from pi_edge.app.main import run as run_edge

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                rb_port = s.getsockname()[1]

            esp_rb_cmds = []

            class _SpyEspRb:
                connected = True
                device_role = "AUDIO_MQ135_NODE"
                port = "MOCK"
                baudrate = 115200

                def __init__(self, *a, **kw):
                    pass

                def start(self):
                    return True

                def close(self):
                    pass

                def send_command(self, cmd, wait_ack=True, timeout_s=0.8):
                    esp_rb_cmds.append(str(cmd))
                    return mock.Mock(ok=True, ack_line="OK", error="")

                def get_sensor_snapshot(self):
                    return {"co2_ppm": 400.0, "raw_adc": 0, "gps": {"lat": None, "lon": None, "speed_kmh": 0.0, "satellites": 0, "is_valid": False}}

                def get_esp_status_block(self):
                    return {"connected": True, "hw_events": []}

                def pop_mute_state_block(self):
                    return {"newly_triggered": False}

            env_patch = {
                "RUN_MODE": "local",
                "ENV_PROFILE": "lab",
                "WEB_PORT": str(rb_port),
                "DISABLE_CAMERA": "1",
                "DISABLE_AI": "1",
                "ESP_MODE": "MOCK",
                "ESP_BLE_MODE": "MOCK",
                "VIOLATION_STORAGE_DIR": str(Path(td) / "v_rb"),
                "LOGS_DIR": str(Path(td) / "l_rb"),
                "AUDIT_DIR": str(Path(td) / "a_rb"),
                "CALIBRATION_DIR": str(Path(td) / "c_rb"),
                "CAMERA_FPS": "4",
            }
            old_env = {k: os.environ.get(k) for k in env_patch}
            rb_resp = {"json": None}

            def _post_rest_break():
                deadline = time.monotonic() + 2.5
                while time.monotonic() < deadline:
                    try:
                        req = urllib.request.Request(
                            f"http://127.0.0.1:{rb_port}/api/control",
                            data=b'{"cmd":"REST_BREAK_REQUEST"}',
                            headers={"Content-Type": "application/json", "X-Auth-Token": "lab-pi-admin-token"},
                            method="POST",
                        )
                        with urllib.request.urlopen(req, timeout=1.5) as r:
                            rb_resp["json"] = json.loads(r.read().decode("utf-8"))
                            return
                    except Exception:
                        time.sleep(0.05)

            try:
                os.environ.update(env_patch)
                with mock.patch("pi_edge.comms.esp_client.EspClient", _SpyEspRb):
                    t = threading.Thread(target=_post_rest_break, daemon=True)
                    t.start()
                    rc = run_edge(cli_mode="local", max_ticks=6)
                    t.join(timeout=2.5)
                self.assertEqual(rc, 0)
                self.assertIsNotNone(rb_resp["json"])
                self.assertTrue(rb_resp["json"]["ok"])
                self.assertEqual(rb_resp["json"]["detail"], "REST_BREAK_ACK")
                self.assertIn("MUTE", esp_rb_cmds)
                self.assertNotIn("REST_BREAK_REQUEST", esp_rb_cmds)
            finally:
                for k, v in old_env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_40_deduplicate_eliminates_two_layer_labels_and_hud_suppresses_dup_face(self):
        from pi_edge.ai.pipeline import _deduplicate_detections
        from pi_edge.app.main import _render_live_hud_overlay

        # Same eye predicted as BOTH Eye_Open (0.46) and Eye_Closed (0.43) -> only 1 box kept (Eye_Closed wins 0.05 safety margin)
        raw = [
            ("Eye_Open", 0.46, (100, 100, 140, 125)),
            ("Eye_Closed", 0.43, (102, 101, 139, 124)),
            ("Eye_Open", 0.70, (180, 100, 220, 125)),
            ("Normal", 0.80, (70, 50, 250, 240)),
            ("Distracted Head", 0.78, (72, 52, 248, 238)),
        ]
        cleaned = _deduplicate_detections(raw)
        eye_boxes = [d for d in cleaned if d[0] in ("Eye_Closed", "Eye_Open")]
        face_boxes = [d for d in cleaned if d[0] in ("Normal", "Distracted Head")]
        self.assertEqual(len(eye_boxes), 2)
        self.assertEqual(len(face_boxes), 1)
        self.assertEqual(face_boxes[0][0], "Distracted Head")

        # HUD renders cleanly with single-layer overlay (no crash, non-overlapping labels)
        img = np.zeros((360, 640, 3), dtype=np.uint8)
        rendered = _render_live_hud_overlay(
            img,
            {
                "driver_state": {
                    "alert_type": "DROWSY",
                    "drowsy_escalation": {"count": 2, "limit": 3, "state": "COUNTING", "alarm_active": False},
                    "monitoring": {"mode": "AUTO", "presence": "DRIVER_PRESENT"},
                },
                "pi": {"camera_fps": 20.0, "ai_fps": 6.5, "cpu_temp_c": 61.0},
                "gps": {"is_valid": True, "lat": 16.06, "lon": 108.22, "speed_kmh": 35.0},
            },
            {
                "valid": True,
                "detections": cleaned,
                "pose": {"valid": True, "yaw_deg": 4.0, "pitch_deg": -2.0, "roll_deg": 1.0, "face_box": (70, 50, 250, 240)},
            },
        )
        self.assertEqual(rendered.shape, (360, 640, 3))

    def test_41_smoking_fusion_window_and_mq135_smoke_delta(self):
        from pi_edge.ai.sensor_fusion import SmokingFusionChannel

        # A. Visual sliding window: 2 intermittent hits within 2.5s triggers SMOKING
        ch = SmokingFusionChannel(window_s=2.5, min_hits=2, mq135_delta_ppm=100.0)
        d1 = ch.update(True, 10.0, "YOLO_BLAZEFACE_SEQ")
        self.assertEqual(d1.alert_state, "NORMAL")
        # Intervening miss frame does NOT erase first hit inside 2.5s window
        d_miss = ch.update(False, 10.6, "YOLO_BLAZEFACE_SEQ")
        self.assertEqual(d_miss.alert_state, "NORMAL")
        d2 = ch.update(True, 11.4, "YOLO_BLAZEFACE_SEQ")
        self.assertEqual(d2.alert_state, "EDGE_TRIGGERED")
        self.assertEqual(d2.alert_type, "SMOKING")

        # B. Visual soft hit + rapid MQ-135 smoke delta (+130 ppm above baseline) confirms immediately
        ch2 = SmokingFusionChannel(window_s=2.5, min_hits=2, mq135_delta_ppm=100.0)
        for i in range(6):
            ch2.update(False, 20.0 + i * 1.0, "YOLO_BLAZEFACE_SEQ", co2_ppm=450.0, mq135_warmup=False)
        d_spike = ch2.update(
            False,
            27.0,
            "YOLO_BLAZEFACE_SEQ",
            co2_ppm=590.0,
            mq135_warmup=False,
            visual_soft_hit=True,
        )
        self.assertEqual(d_spike.alert_state, "EDGE_TRIGGERED")
        self.assertIn("MQ135", d_spike.reason)

    def test_42_drowsy_3_strike_escalation_and_probation_relapse(self):
        from pi_edge.ai.alert_policy import DrowsyEscalationController

        ctrl = DrowsyEscalationController(window_s=180.0, escalation_limit=3, episode_gap_s=6.0)
        # Strike 1 -> VOICE:1
        a1 = ctrl.update(True, 100.0)
        self.assertEqual(a1.esp_cmd, "VOICE:1")
        self.assertEqual(a1.count, 1)
        self.assertEqual(a1.state, "COUNTING")
        # Continuous drowsy in same episode must NOT double-count!
        a1_cont = ctrl.update(True, 102.0)
        self.assertIsNone(a1_cont.esp_cmd)
        self.assertEqual(a1_cont.count, 1)

        # Driver wakes up for >= 6s
        ctrl.update(False, 104.0)
        ctrl.update(False, 111.0)
        # Strike 2 (within 3 min) -> VOICE:1
        a2 = ctrl.update(True, 120.0)
        self.assertEqual(a2.esp_cmd, "VOICE:1")
        self.assertEqual(a2.count, 2)

        # Wake up >= 6s, then Strike 3 (within 3 min) -> ALARM:ON (Level 3 latched)
        ctrl.update(False, 122.0)
        ctrl.update(False, 130.0)
        a3 = ctrl.update(True, 150.0)
        self.assertEqual(a3.esp_cmd, "ALARM:ON")
        self.assertEqual(a3.state, "ALARM_LATCHED")
        self.assertEqual(a3.server_incident, "DROWSY_LEVEL3_ALARM")
        self.assertTrue(ctrl.get_status(151.0)["alarm_active"])

        # Driver taps "Tắt loa cảnh báo" -> ALARM:OFF + enters 180s PROBATION
        ok_d, _, cmd_off = ctrl.dismiss_alarm(155.0, source="PHONE_BLE")
        self.assertTrue(ok_d)
        self.assertEqual(cmd_off, "ALARM:OFF")
        st_prob = ctrl.get_status(160.0)
        self.assertEqual(st_prob["state"], "PROBATION")
        self.assertFalse(st_prob["alarm_active"])
        self.assertGreater(st_prob["probation_remaining_s"], 170.0)

        # Driver opens eyes briefly (>= 6s) then dozes off AGAIN within 2-4 min probation -> CONTINUOUS ALARM!
        ctrl.update(False, 160.0)
        ctrl.update(False, 168.0)
        a_rel = ctrl.update(True, 200.0)
        self.assertEqual(a_rel.esp_cmd, "ALARM:CONT")
        self.assertEqual(a_rel.state, "CONTINUOUS_RELAPSE")
        self.assertEqual(a_rel.server_incident, "DROWSY_RELAPSE_CONTINUOUS")

        # After dismissing again and staying awake past 180s probation -> resets cleanly to IDLE (0)
        ctrl.dismiss_alarm(210.0, source="PHONE_BLE")
        ctrl.update(False, 212.0)
        ctrl.update(False, 400.0)
        st_done = ctrl.get_status(400.0)
        self.assertEqual(st_done["state"], "IDLE")
        self.assertEqual(st_done["count"], 0)

    def test_43_three_mode_presence_monitoring_and_speed_auto_resume(self):
        from pi_edge.ai.alert_policy import MonitoringModeController

        mon = MonitoringModeController(
            absence_alert_s=20.0,
            pause_duration_s=1800.0,
            auto_resume_speed_kmh=15.0,
            speed_sustain_s=3.0,
        )
        # Always boots into AUTO
        self.assertEqual(mon.get_status(0.0)["mode"], "AUTO")

        # Mode 1 (AUTO): missing person for >= 20s -> emits DRIVER_ABSENT and auto-transitions to TEMP_PAUSE_30M
        r0 = mon.update(face_detected=False, camera_online=True, gps_valid=False, speed_kmh=0.0, now_mono=10.0)
        self.assertIsNone(r0.event)
        r1 = mon.update(face_detected=False, camera_online=True, gps_valid=False, speed_kmh=0.0, now_mono=31.0)
        self.assertEqual(r1.event, "DRIVER_ABSENT")
        self.assertEqual(r1.mode, "TEMP_PAUSE_30M")
        self.assertTrue(r1.absence_suppressed)
        self.assertFalse(r1.alerts_suppressed)  # If driver IS visible during TEMP_PAUSE_30M, violations still alert!

        # Mode 2 (TEMP_PAUSE_30M): expires back to AUTO after 30 min
        r_exp = mon.update(face_detected=True, camera_online=True, gps_valid=False, speed_kmh=0.0, now_mono=1840.0)
        self.assertEqual(r_exp.mode, "AUTO")
        self.assertEqual(r_exp.event, "PAUSE_EXPIRED_AUTO")

        # Mode 3 (REST_SLEEP_OFF): silences cabin alerts while camera keeps reporting presence
        ok_m, _ = mon.set_mode("REST_SLEEP_OFF", now_mono=2000.0, source="DRIVER_APP")
        self.assertTrue(ok_m)
        r_slp = mon.update(face_detected=True, camera_online=True, gps_valid=True, speed_kmh=0.0, now_mono=2005.0)
        self.assertEqual(r_slp.mode, "REST_SLEEP_OFF")
        self.assertEqual(r_slp.presence, "DRIVER_PRESENT")
        self.assertTrue(r_slp.alerts_suppressed)

        # Single transient GPS speed spike (< 3s) must NOT falsely wake AUTO
        r_g1 = mon.update(face_detected=True, camera_online=True, gps_valid=True, speed_kmh=18.0, now_mono=2010.0)
        self.assertEqual(r_g1.mode, "REST_SLEEP_OFF")
        r_g2 = mon.update(face_detected=True, camera_online=True, gps_valid=True, speed_kmh=0.0, now_mono=2011.0)
        self.assertEqual(r_g2.mode, "REST_SLEEP_OFF")

        # Sustained GPS >= 15 km/h for >= 3s -> auto-resumes AUTO & emits report event!
        mon.update(face_detected=True, camera_online=True, gps_valid=True, speed_kmh=16.5, now_mono=2020.0)
        r_wake = mon.update(face_detected=True, camera_online=True, gps_valid=True, speed_kmh=22.0, now_mono=2023.5)
        self.assertEqual(r_wake.mode, "AUTO")
        self.assertFalse(r_wake.alerts_suppressed)
        self.assertEqual(r_wake.event, "MONITORING_AUTO_RESUMED_BY_SPEED")


if __name__ == "__main__":
    unittest.main()
