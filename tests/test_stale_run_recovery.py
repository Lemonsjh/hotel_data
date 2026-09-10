from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "OTA采集服务"


def load_runner():
    sys.path.insert(0, str(SERVICE_DIR))
    spec = importlib.util.spec_from_file_location("runner_stale_run_test_target", SERVICE_DIR / "runner.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load runner.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class StaleRunRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def test_reconcile_marks_missing_stopping_process_as_cancelled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            old_status, old_stop = self.runner.STATUS_PATH, self.runner.RUN_STOP_PATH
            self.runner.STATUS_PATH = state_dir / "status.json"
            self.runner.RUN_STOP_PATH = state_dir / "collection_run.stop"
            self.runner.save_json(
                self.runner.STATUS_PATH,
                {
                    "last_run_status": "stopping",
                    "last_run_tasks": ["pms_fetch"],
                    "tasks": {"pms_fetch": self.runner.pending_result("pms_fetch")},
                },
            )
            try:
                with patch.object(self.runner, "process_alive", return_value=False):
                    status = self.runner.reconcile_stale_run()
                self.assertEqual(status["last_run_status"], "cancelled")
                self.assertEqual(status["tasks"]["pms_fetch"]["status"], "cancelled")
            finally:
                self.runner.STATUS_PATH, self.runner.RUN_STOP_PATH = old_status, old_stop


if __name__ == "__main__":
    unittest.main()
