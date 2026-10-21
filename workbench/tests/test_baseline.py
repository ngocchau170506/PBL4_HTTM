import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from contracts.state import BaselineProfile
from fd_process.baseline.baseline_collector import BaselineCollector
from fd_process.baseline.baseline_storage import BaselineStorage


class BaselineTest(unittest.TestCase):
    def test_collect_5s_and_lock_immutably(self):
        collector = BaselineCollector(seconds_min=5, seconds_max=10, min_frames=5)
        for i in range(25):
            pkt = L.SignalPacket(
                trace_id=f"b-1-{i + 1}",
                ts_ms=1000 + i * 200,
                source="process.sampler",
                frame_id=i + 1,
                width=640,
                height=480,
                driver_id="drv-01",
            )
            prof = collector.process_frame(pkt)
            self.assertIsInstance(prof, BaselineProfile)
            self.assertEqual(prof.state, "COLLECTING")

        lock_pkt = L.SignalPacket(
            trace_id="b-1-lock",
            ts_ms=6000,
            source="process.sampler",
            frame_id=26,
            width=640,
            height=480,
            driver_id="drv-01",
        )
        locked = collector.process_frame(lock_pkt)
        self.assertIsInstance(locked, BaselineProfile)
        self.assertEqual(locked.state, "LOCKED")
        self.assertEqual(locked.duration_ms, 5000)
        self.assertEqual(locked.frame_count, 26)

        later_pkt = L.SignalPacket(
            trace_id="b-1-later",
            ts_ms=9000,
            source="process.sampler",
            frame_id=40,
            width=640,
            height=480,
            driver_id="drv-01",
        )
        after = collector.process_frame(later_pkt)
        self.assertEqual(after, locked)

    def test_multi_driver_isolation_and_switching(self):
        collector = BaselineCollector(seconds_min=5, seconds_max=10, min_frames=3)
        for i in range(6):
            collector.process_frame(
                L.SignalPacket(f"a-{i}", 1000 + i * 1000, "process.sampler", i + 1, 640, 480, "drv-A")
            )
        prof_a = collector.current_profile("drv-A")
        self.assertIsNotNone(prof_a)
        assert prof_a is not None
        self.assertEqual(prof_a.state, "LOCKED")

        res_b1 = collector.process_frame(
            L.SignalPacket("b-0", 20000, "process.sampler", 10, 640, 480, "drv-B")
        )
        self.assertIsInstance(res_b1, BaselineProfile)
        self.assertEqual(res_b1.driver_id, "drv-B")
        self.assertEqual(res_b1.state, "COLLECTING")
        self.assertEqual(res_b1.frame_count, 1)

        self.assertEqual(collector.current_profile("drv-A"), prof_a)
        self.assertEqual(collector.storage.driver_ids(), ("drv-A", "drv-B"))

    def test_storage_prevents_overwriting_locked_profile_until_expired(self):
        store = BaselineStorage()
        locked = BaselineProfile("drv-X", "LOCKED", 1000, 5000, 25)
        self.assertEqual(store.save(locked, trace_id="s-1"), locked)

        overwrite_attempt = BaselineProfile("drv-X", "COLLECTING", 8000, 200, 2)
        err = store.save(overwrite_attempt, trace_id="s-2")
        self.assertIsInstance(err, L.ErrorEvent)
        self.assertEqual(err.code, "E_PROC_BAD_OUTPUT")
        self.assertEqual(err.node_path, "process.baseline")
        self.assertEqual(store.get("drv-X"), locked)

        exp = store.expire_driver("drv-X", trace_id="s-3")
        self.assertIsInstance(exp, BaselineProfile)
        self.assertEqual(exp.state, "EXPIRED")

        recollect = store.save(overwrite_attempt, trace_id="s-4")
        self.assertEqual(recollect, overwrite_attempt)

    def test_storage_json_export_and_import_validation(self):
        store1 = BaselineStorage()
        p1 = BaselineProfile("drv-01", "LOCKED", 1000, 5200, 27)
        p2 = BaselineProfile("drv-02", "COLLECTING", 3000, 1200, 7)
        store1.save(p1, "j-1")
        store1.save(p2, "j-2")

        exported = store1.export_json()
        store2 = BaselineStorage()
        self.assertIsNone(store2.import_json(exported, trace_id="j-import"))
        self.assertEqual(store2.get("drv-01"), p1)
        self.assertEqual(store2.get("drv-02"), p2)

        bad_err = store2.import_json("{not valid json", trace_id="j-bad")
        self.assertIsInstance(bad_err, L.ErrorEvent)
        self.assertEqual(bad_err.code, "E_PROC_BAD_OUTPUT")

    def test_timeout_and_missing_driver_id_errors(self):
        collector = BaselineCollector(seconds_min=5, seconds_max=10, min_frames=5)
        no_drv = L.SignalPacket("err-nodrv", 1000, "process.sampler", 1, 640, 480, None)
        err1 = collector.process_frame(no_drv)
        self.assertIsInstance(err1, L.ErrorEvent)
        self.assertEqual(err1.code, "E_PROC_BAD_OUTPUT")
        self.assertEqual(err1.node_path, "process.baseline")

        p_start = L.SignalPacket("t-start", 1000, "process.sampler", 1, 640, 480, "drv-slow")
        p_late = L.SignalPacket("t-late", 15000, "process.sampler", 2, 640, 480, "drv-slow")
        collector.process_frame(p_start)
        err2 = collector.process_frame(p_late)
        self.assertIsInstance(err2, L.ErrorEvent)
        self.assertEqual(err2.code, "E_PROC_TIMEOUT")
        self.assertEqual(err2.node_path, "process.baseline")

    def test_self_test(self):
        collector = BaselineCollector()
        ok, msg = collector.self_test()
        self.assertTrue(ok)
        self.assertIn("BaselineCollector OK", msg)


if __name__ == "__main__":
    unittest.main()
