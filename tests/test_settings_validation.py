from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "OTA采集服务"


def load_validation_module():
    path = SERVICE_DIR / "settings_validation.py"
    spec = importlib.util.spec_from_file_location("settings_validation_test_target", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SettingsValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = load_validation_module()

    def test_example_settings_is_valid_json_and_schema(self):
        path = SERVICE_DIR / "config" / "settings.example.json"
        settings = json.loads(path.read_text(encoding="utf-8-sig"))
        self.assertEqual(self.validation.validate_settings(settings), [])

    def test_invalid_values_are_reported_together(self):
        settings = {
            "service": {"interval_minutes": 0},
            "mysql": {"port": "3306"},
            "pms": {"report_base_url": "not-a-url"},
            "tasks": {"sample": "true"},
        }
        errors = self.validation.validate_settings(settings)
        self.assertEqual(len(errors), 4)

    def test_partial_first_run_settings_are_allowed(self):
        self.assertEqual(self.validation.validate_settings({"tasks": {}}), [])


if __name__ == "__main__":
    unittest.main()
