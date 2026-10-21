import copy
import json
import sys
import tempfile
import typing
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from fd_config.config_loader import ERROR_CODE, ConfigLoader, SystemConfig

DELETE = object()


def good():
    return json.loads((ROOT / "src" / "fd_config" / "config.json").read_text(encoding="utf-8"))


def mutated(path, value):
    cfg = good()
    node = cfg
    for key in path[:-1]:
        node = node[key]
    if value is DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return cfg


class ConfigLoaderTest(unittest.TestCase):
    def test_load_default_config_succeeds(self):
        loader = ConfigLoader()
        res = loader.load(trace_id="test-cfg-01")
        self.assertIsInstance(res, SystemConfig)
        self.assertEqual(loader.health(), "OK")
        self.assertEqual(res.mode, "sim")
        self.assertEqual(res.camera.source, "rtsp")
        self.assertIn("192.168.1.210:554", res.camera.uri or "")
        self.assertEqual(res.camera.width, 640)
        self.assertEqual(res.camera.height, 480)
        self.assertEqual(res.camera.fps, 25)
        self.assertEqual(res.esp32.uri, "COM3")
        self.assertEqual(res.sampler.target_fps, 5)
        self.assertEqual(res.ai.adapter, "stub")
        ok, detail = loader.self_test()
        self.assertTrue(ok)
        self.assertIn("mode=sim", detail)

    def test_bad_values_are_reported_with_the_key(self):
        loader = ConfigLoader()
        cases = [
            (["mode"], "pc", "mode"),
            (["camera", "fps"], 0, "camera.fps"),
            (["camera", "fps"], True, "camera.fps"),
            (["camera", "width"], "640", "camera.width"),
            (["ai", "adapter"], "gpt", "ai.adapter"),
            (["ai", "timeout_ms"], -1, "ai.timeout_ms"),
            (["esp32", "enabled"], "yes", "esp32.enabled"),
            (["response", "buffer_frames"], 0, "response.buffer_frames"),
        ]
        for path, value, needle in cases:
            with self.subTest(path=path, value=value):
                res = loader.validate_dict(mutated(path, value), trace_id="test-cfg-sub")
                self.assertIsInstance(res, L.ErrorEvent)
                self.assertEqual(res.code, "E_CFG_INVALID")
                self.assertIn(needle, res.message)

    def test_missing_key_and_missing_section(self):
        loader = ConfigLoader()
        res_key = loader.validate_dict(mutated(["camera", "fps"], DELETE), trace_id="test-cfg-del-key")
        self.assertIsInstance(res_key, L.ErrorEvent)
        self.assertIn("camera.fps", res_key.message)

        res_sec = loader.validate_dict(mutated(["ai"], DELETE), trace_id="test-cfg-del-sec")
        self.assertIsInstance(res_sec, L.ErrorEvent)
        self.assertIn("'ai'", res_sec.message)

    def test_cross_key_rules(self):
        loader = ConfigLoader()

        res_fps = loader.validate_dict(mutated(["sampler", "target_fps"], 99), trace_id="test-cfg-cross-fps")
        self.assertIsInstance(res_fps, L.ErrorEvent)
        self.assertIn("target_fps", res_fps.message)

        res_base = loader.validate_dict(mutated(["baseline", "seconds_max"], 1), trace_id="test-cfg-cross-base")
        self.assertIsInstance(res_base, L.ErrorEvent)
        self.assertIn("seconds_max", res_base.message)

        res_buf = loader.validate_dict(mutated(["response", "buffer_frames"], 1), trace_id="test-cfg-cross-buf")
        self.assertIsInstance(res_buf, L.ErrorEvent)
        self.assertIn("buffer_frames", res_buf.message)

        cfg_esp = mutated(["esp32", "enabled"], True)
        cfg_esp["esp32"]["uri"] = None
        res_esp = loader.validate_dict(cfg_esp, trace_id="test-cfg-cross-esp")
        self.assertIsInstance(res_esp, L.ErrorEvent)
        self.assertIn("esp32.uri", res_esp.message)

        cfg_cam = mutated(["camera", "source"], "rtsp")
        cfg_cam["camera"]["uri"] = None
        res_cam = loader.validate_dict(cfg_cam, trace_id="test-cfg-cross-cam")
        self.assertIsInstance(res_cam, L.ErrorEvent)
        self.assertIn("camera.uri", res_cam.message)

    def test_root_must_be_object(self):
        loader = ConfigLoader()
        res = loader.validate_dict([], trace_id="test-cfg-root")
        self.assertIsInstance(res, L.ErrorEvent)
        self.assertEqual(res.code, "E_CFG_INVALID")

    def test_missing_file_returns_error_event(self):
        missing = Path(tempfile.gettempdir()) / "nonexistent_fd_config_9999.json"
        loader = ConfigLoader(config_path=missing)
        res = loader.load(trace_id="test-cfg-02")
        self.assertIsInstance(res, L.ErrorEvent)
        self.assertEqual(res.code, "E_CFG_INVALID")
        self.assertEqual(res.node_path, "config")
        self.assertEqual(res.trace_id, "test-cfg-02")
        self.assertEqual(loader.health(), "DOWN")

    def test_malformed_json_file_returns_error_event(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("{broken json")
            tmp_path = Path(f.name)
        try:
            loader = ConfigLoader(config_path=tmp_path)
            res = loader.load(trace_id="test-cfg-06")
            self.assertIsInstance(res, L.ErrorEvent)
            self.assertEqual(res.code, "E_CFG_INVALID")
            self.assertEqual(loader.health(), "DOWN")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_error_code_is_a_declared_contract_code(self):
        self.assertIn(ERROR_CODE, typing.get_args(L.ErrorCode))
        self.assertIn("config", typing.get_args(L.NodeId))

    def test_validate_does_not_mutate_input(self):
        loader = ConfigLoader()
        cfg = good()
        snapshot = copy.deepcopy(cfg)
        loader.validate_dict(cfg, trace_id="test-cfg-immutable")
        self.assertEqual(cfg, snapshot)


if __name__ == "__main__":
    unittest.main()
