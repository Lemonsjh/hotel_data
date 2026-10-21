import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "OTA采集服务"))
"""PMS subtask failure isolation, cancellation, and dashboard aggregation."""
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import runner
from process_runner import ProcessCancelledError, ProcessResult, ProcessTimeoutError, run_streamed


class PMSRunnerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])))
        self.settings = {"pms": {"provider": "foxhis"}, "foxhis": {"enabled": True}, "paths": {"base_dir": str(self.root)}}
        self.specs = [("foxhis", name + ".py", []) for name in ("room_status", "jl01", "jd01")]
        for _, name, _ in self.specs:
            (self.root / name).write_text("print('OK')\n", encoding="utf-8")
        for name, value in {
            "STATUS_PATH": self.root / "status.json", "LOG_DIR": self.root / "logs",
            "RUN_STOP_PATH": self.root / "stop",
        }.items():
            self.stack.enter_context(patch.object(runner, name, value))
        self.stack.enter_context(patch.object(runner, "load_settings", return_value=self.settings))
        self.stack.enter_context(patch.object(runner, "task_specs", return_value=self.specs))
        self.stack.enter_context(patch.object(runner, "script_path", side_effect=lambda settings, platform, name: self.root / name))
        self.stack.enter_context(patch.object(runner, "python_path", return_value=Path(sys.executable)))
        self.stack.enter_context(patch.object(runner, "build_env", return_value=dict(os.environ)))
        self.stack.enter_context(patch.object(runner, "ensure_foxhis_hotel_name"))
        self.enrich = self.stack.enter_context(patch.object(runner, "enrich_room_type_ids"))
        self.execute = self.stack.enter_context(patch.object(runner, "run_streamed", return_value=ProcessResult(0, "OK")))

    def run_task(self):
        return runner.run_task("pms_fetch", self.settings, {"tasks": {}})

    def test_failure_at_any_position_preserves_remaining_work_and_nonzero_code(self):
        for position in range(3):
            with self.subTest(position=position):
                self.execute.reset_mock()
                self.execute.side_effect = [ProcessResult(7, "Error: failed") if i == position else ProcessResult(0, "OK") for i in range(3)]
                result = self.run_task()
                self.assertEqual(self.execute.call_count, 3)
                self.assertEqual(result["status"], "partial_failed")
                self.assertEqual(result["return_code"], 7)
                self.assertEqual(sum(child["status"] == "success" for child in result["subtasks"]), 2)
                self.assertIn(self.specs[position][1], result["error_summary"])

    def test_timeout_and_launch_error_allow_later_report_to_run(self):
        self.execute.side_effect = [ProcessTimeoutError(60, "timeout"), OSError("cannot launch"), ProcessResult(0, "OK")]
        result = self.run_task()
        self.assertEqual(self.execute.call_count, 3)
        self.assertEqual(result["status"], "partial_failed")
        self.assertIn("timeout after 60s", result["error_summary"])
        self.assertIn("cannot launch", result["error_summary"])

    def test_missing_script_does_not_block_other_scripts(self):
        (self.root / "jl01.py").unlink()
        result = self.run_task()
        self.assertEqual(self.execute.call_count, 2)
        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(result["subtasks"][1]["status"], "failed")

    def test_all_failures_remain_failed_and_do_not_enrich(self):
        self.execute.return_value = ProcessResult(1, "Error: failed")
        result = self.run_task()
        self.assertEqual(self.execute.call_count, 3)
        self.assertEqual(result["status"], "failed")
        self.enrich.assert_not_called()

    def test_all_successes_remain_successful(self):
        result = self.run_task()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["return_code"], 0)
        self.assertEqual(result["error_summary"], "")
        self.enrich.assert_called_once()

    def test_single_ota_task_retains_its_original_result_semantics(self):
        self.execute.return_value = ProcessResult(3, "Error: single task failed")
        with patch.object(runner, "task_specs", return_value=self.specs[:1]):
            result = runner.run_task("meituan_business", self.settings, {"tasks": {}})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["return_code"], 3)
        self.assertNotIn("subtasks", result)
        self.assertEqual(self.execute.call_count, 1)

    def test_subtask_errors_do_not_expose_configured_secrets(self):
        self.settings["meituan"] = {"me_cookie": "private-session-value"}
        self.execute.side_effect = OSError("failed with private-session-value")
        result = self.run_task()
        self.assertNotIn("private-session-value", result["error_summary"])
        self.assertNotIn("private-session-value", Path(result["log_path"]).read_text(encoding="utf-8"))

    def test_cancellation_stops_later_scripts(self):
        self.execute.side_effect = [ProcessResult(0, "OK"), ProcessCancelledError("stopped")]
        result = self.run_task()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.execute.call_count, 2)
        self.assertEqual([child["status"] for child in result["subtasks"]], ["success", "cancelled", "pending"])

    def test_stop_between_scripts_is_respected(self):
        with patch.object(runner, "run_stop_requested", side_effect=[False, True]):
            result = self.run_task()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.execute.call_count, 1)

    def test_actual_process_failure_preserves_first_write_and_runs_final_script(self):
        marker = self.root / "written.txt"
        (self.root / "room_status.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('first\\n')\n", encoding="utf-8")
        (self.root / "jl01.py").write_text("raise RuntimeError('simulated report failure')\n", encoding="utf-8")
        (self.root / "jd01.py").write_text(f"from pathlib import Path\nwith Path({str(marker)!r}).open('a') as f: f.write('last\\n')\n", encoding="utf-8")
        self.execute.side_effect = run_streamed
        state = runner.run_once(["pms_fetch"])
        self.assertEqual(state["last_run_status"], "partial_failed")
        self.assertEqual(state["tasks"]["pms_fetch"]["status"], "partial_failed")
        self.assertEqual(marker.read_text(), "first\nlast\n")

    def test_dashboard_counts_partial_failure_and_shows_failed_script(self):
        import panel
        import status_routes
        state = {"last_run_status": "partial_failed", "last_run_tasks": ["pms_fetch"], "tasks": {"pms_fetch": {
            "status": "partial_failed", "error_summary": "jl01.py: failed",
            "subtasks": [{"script": name, "status": status} for name, status in (("room_status.py", "success"), ("jl01.py", "failed"), ("jd01.py", "success"))],
        }}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(runner, "reconcile_stale_run", return_value=state))
            stack.enter_context(patch.object(runner, "config_warnings", return_value=[]))
            for name in ("manual_scheduler_status", "price_scheduler_status", "reply_scheduler_status", "promotion_scheduler_status"):
                stack.enter_context(patch.object(status_routes, name, return_value={}))
            response = panel.app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("部分失败", html)
        self.assertIn("2 成功 / 1 失败", html)
        self.assertIn("jl01.py", html)
        self.assertIn('class="value">0 / 1</div>', html)
        self.assertIn('class="value">1/1</div>', html)


if __name__ == "__main__":
    unittest.main()
