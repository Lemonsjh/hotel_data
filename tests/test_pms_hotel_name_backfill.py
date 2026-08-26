from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "OTA采集服务"


def load_runner():
    sys.path.insert(0, str(SERVICE_DIR))
    spec = importlib.util.spec_from_file_location("runner_pms_backfill_test_target", SERVICE_DIR / "runner.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load runner.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PmsHotelNameBackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def test_backfill_only_writes_a_blank_pms_hotel_name(self):
        settings = json.loads((SERVICE_DIR / "config" / "settings.example.json").read_text(encoding="utf-8-sig"))
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "settings.json"
            config_path.write_text(json.dumps(settings), encoding="utf-8")
            old_path = self.runner.CONFIG_PATH
            self.runner.CONFIG_PATH = config_path
            try:
                self.assertTrue(self.runner.backfill_pms_hotel_name("测试酒店"))
                saved = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["pms"]["hotel_name"], "测试酒店")
                self.assertFalse(self.runner.backfill_pms_hotel_name("另一家酒店"))
            finally:
                self.runner.CONFIG_PATH = old_path


if __name__ == "__main__":
    unittest.main()
