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

    def test_status_write_retries_windows_sharing_errors(self):
        path = self.root / "status.json"
        error = PermissionError("temporarily locked")
        error.winerror = 5
        replace = os.replace
        failures = iter([error, error])

        def replace_after_unlock(source, target):
            failure = next(failures, None)
            if failure:
                raise failure
            replace(source, target)

        with patch.object(runner.os, "replace", side_effect=replace_after_unlock) as mocked, \
             patch.object(runner.time, "sleep") as sleep:
            runner.save_json(path, {"status": "running"})
        self.assertEqual(runner.load_json(path, {}), {"status": "running"})
        self.assertEqual(mocked.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.05, 0.1])

    def test_status_write_stops_after_retries_and_keeps_previous_file(self):
        path = self.root / "status.json"
        runner.save_json(path, {"status": "previous"})
        error = PermissionError("still locked")
        error.winerror = 32
        with patch.object(runner.os, "replace", side_effect=error) as replace, \
             patch.object(runner.time, "sleep"), self.assertRaises(PermissionError):
            runner.save_json(path, {"status": "running"})
        self.assertEqual(replace.call_count, 4)
        self.assertEqual(runner.load_json(path, {}), {"status": "previous"})

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

    def test_bypms_daily_success_persists_and_failed_report_retries(self):
        from datetime import datetime
        import pms_subtasks

        self.settings["pms"]["provider"] = "bypms"
        self.settings["foxhis"]["enabled"] = False
        self.settings["bypms"] = {"enabled": True}
        scripts = ["bypms_room_status_data.py", "bypms_channel_mapping_data.py",
                   "bypms_jl11_data.py", "bypms_jd01_data.py", "bypms_rs01_data.py"]
        self.specs[:] = [("bypms", name, []) for name in scripts]
        for name in scripts:
            (self.root / name).touch()
        env = {"MYSQL_HOST": "localhost", "MYSQL_DATABASE": "hotel_a", "HOTEL_ID": "a"}
        failure = ProcessResult(1, "Error: report unavailable")
        success = ProcessResult(0, "OK")
        self.execute.side_effect = [success, success, failure, success, success]
        with patch.object(runner, "build_env", return_value=env), patch.object(pms_subtasks, "datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 16, 12)
            self.assertEqual(self.run_task()["status"], "partial_failed")
            self.execute.reset_mock(side_effect=True)
            result = self.run_task()
            self.assertEqual([entry["status"] for entry in result["subtasks"]],
                             ["success", "skipped", "success", "success", "success"])
            self.assertEqual([Path(call.args[0][1]).name for call in self.execute.call_args_list],
                             [scripts[i] for i in (0, 2, 3, 4)])

            env["MYSQL_DATABASE"] = "hotel_b"
            self.assertTrue(all(item["status"] == "success" for item in self.run_task()["subtasks"]))
            env["MYSQL_DATABASE"] = "hotel_a"
            clock.now.return_value = datetime(2026, 11, 17, 12)
            self.assertTrue(all(item["status"] == "success" for item in self.run_task()["subtasks"]))

    def test_meituan_refreshes_live_reports_and_scopes_daily_marks_by_org(self):
        from datetime import datetime
        import pms_subtasks

        self.settings["pms"]["provider"] = "meituan_pms"
        self.settings["foxhis"]["enabled"] = False
        self.settings["meituan_pms"] = {"enabled": True}
        live = ("collector.py", "booking_report.py")
        daily = ("daily_report.py", "monthly_report.py", "performance_report.py", "classification_report.py",
                 "room_performance_report.py", "revenue_report.py", "guest_report.py")
        self.specs[:] = [("meituan_pms", name, []) for name in (*live, *daily)]
        for _, name, _ in self.specs:
            (self.root / name).touch()
        env = {"MYSQL_DATABASE": "hotel_a", "HOTEL_ID": "a", "MEITUAN_PMS_ORG_ID": "1"}
        with patch.object(runner, "build_env", return_value=env), patch.object(pms_subtasks, "datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 16, 12)
            self.assertEqual(self.run_task()["status"], "success")
            self.execute.reset_mock()
            result = self.run_task()
            self.assertEqual(result["status"], "success")
            self.assertEqual([Path(call.args[0][1]).name for call in self.execute.call_args_list], list(live))
            self.assertEqual(sum(item["status"] == "skipped" for item in result["subtasks"]), len(daily))
            env["MEITUAN_PMS_ORG_ID"] = "2"
            self.assertTrue(all(item["status"] == "success" for item in self.run_task()["subtasks"]))

    def test_yolo_and_foxhis_night_audit_skip_does_not_mark_month_report(self):
        for platform in ("yolo", "foxhis"):
            with self.subTest(platform=platform):
                self.settings["pms"]["provider"] = platform
                self.settings[platform] = {"enabled": True}
                name = f"{platform}_jy03_data.py"
                (self.root / name).touch()
                self.specs[:] = [(platform, name, [])]
                self.execute.side_effect = [ProcessResult(0, "SKIP [jy03] before night audit"),
                                            ProcessResult(0, "OK [jy03]")]
                for expected in ("skipped", "success", "skipped"):
                    self.assertEqual(self.run_task()["subtasks"][0]["status"], expected)

    def test_dual_monthly_retries_failed_merge_then_skips_after_success(self):
        self.settings["pms"]["provider"] = "meituan_rezen"
        self.settings["foxhis"]["enabled"] = False
        self.settings["meituan_rezen"] = {"enabled": True}
        self.settings["meituan_pms"] = {"enabled": True}
        self.settings["rezen"] = {"enabled": True}
        self.specs[:] = [("meituan_rezen", "monthly_report.py", [])]
        (self.root / "monthly_report.py").touch()
        self.execute.side_effect = [ProcessResult(1, "Error: one side failed"), ProcessResult(0, "OK")]
        for expected in ("failed", "success", "skipped"):
            self.assertEqual(self.run_task()["subtasks"][0]["status"], expected)

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
