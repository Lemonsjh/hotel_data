from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PMS_SCRIPTS = ROOT / "正式数据抓取-PMS（别样红）" / "PMS登录" / "scripts"
SERVICE_DIR = ROOT / "OTA采集服务"
MEITUAN_DIR = ROOT / "美团OTA数据采集代码"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class SessionSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(PMS_SCRIPTS))
        cls.pms_utils = load_module("test_pms_utils", PMS_SCRIPTS / "pms_utils.py")

    def test_session_path_is_absolute_and_stable(self):
        expected = PMS_SCRIPTS.parent / "pms_session_playwright.json"
        self.assertEqual(self.pms_utils.SESSION_PATH, expected)
        self.assertTrue(self.pms_utils.SESSION_PATH.is_absolute())

    def test_invalid_session_is_backed_up(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            path.write_text("{", encoding="utf-8")
            with patch.object(self.pms_utils, "SESSION_PATH", path):
                self.assertIsNone(self.pms_utils.read_session(quiet=True))
            self.assertFalse(path.exists())
            self.assertEqual(len(list(Path(directory).glob("session.corrupt-*.json"))), 1)

    def test_session_write_replaces_file_without_temp_residue(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            with patch.object(self.pms_utils, "SESSION_PATH", path):
                self.pms_utils.write_session({"login_time": "2026-07-17 10:00:00"})
                session = self.pms_utils.read_session(quiet=True)
            self.assertEqual(session["login_time"], "2026-07-17 10:00:00")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])


class ScanOrderSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(MEITUAN_DIR))
        cls.module = load_module("test_scan_order", MEITUAN_DIR / "meituan_scan_order_data.py")

    def test_blank_order_ids_are_filtered_and_other_ids_normalized(self):
        response = {
            "completeOrderDetailInfo": {
                "completeOrderDetailList": [
                    {"orderIdStr": None},
                    {"orderIdStr": "   "},
                    {"orderIdStr": "  A123  "},
                    {"orderIdStr": 456},
                ]
            },
            "totalNum": 2,
        }
        with patch.object(self.module, "request_page", return_value=response):
            rows = self.module.fetch_orders(date(2026, 7, 1), date(2026, 7, 17))
        self.assertEqual([row["orderIdStr"] for row in rows], ["A123", "456"])

        built = self.module.build_rows(rows, date(2026, 7, 1), date(2026, 7, 17), datetime.now())
        self.assertEqual([row[1] for row in built], ["A123", "456"])

    def test_null_scan_times_are_cleaned_by_collection_time(self):
        writer = sys.modules["ota_mysql_writer"]

        class Cursor:
            rowcount = 1

            def __init__(self):
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, params=None):
                self.calls.append((sql, params))

        class Connection:
            def __init__(self):
                self.db_cursor = Cursor()

            def cursor(self):
                return self.db_cursor

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        connection = Connection()
        columns = {"hotel_id": "varchar", "order_id": "varchar", "scan_time": "datetime", "collected_at": "datetime"}
        with patch.object(writer, "connect_mysql", return_value=connection), patch.object(
            writer, "load_column_types", return_value=columns
        ):
            writer.sync_meituan_scan_orders(list(columns), [], "H1", date(2026, 6, 17))
        delete_sql, params = connection.db_cursor.calls[-1]
        self.assertIn("scan_time IS NULL AND collected_at <", delete_sql)
        self.assertEqual(params, ("H1", date(2026, 6, 17), date(2026, 6, 17)))


class ReplacementSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT))
        cls.writer = load_module("test_replacement_writer", ROOT / "ota_mysql_writer.py")

    class Cursor:
        rowcount = 0

        def __init__(self, fail_insert=False):
            self.calls = []
            self.fail_insert = fail_insert

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=None):
            self.calls.append((sql, params))

        def executemany(self, sql, rows):
            self.calls.append((sql, rows))
            if self.fail_insert:
                raise RuntimeError("insert failed")

    class Connection:
        def __init__(self, fail_insert=False):
            self.db_cursor = ReplacementSafetyTests.Cursor(fail_insert)
            self.commits = 0
            self.rollbacks = 0

        def cursor(self):
            return self.db_cursor

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            pass

    def test_empty_replacements_are_rejected_before_connecting(self):
        with patch.object(self.writer, "connect_mysql") as connect:
            with self.assertRaises(self.writer.MysqlSyncError):
                self.writer.sync_table("sample_table", ["value"], [])
            with self.assertRaises(self.writer.MysqlSyncError):
                self.writer.sync_order_loss_snapshot(["value"], [])
            with self.assertRaises(self.writer.MysqlSyncError):
                self.writer.sync_joined_rights_snapshot(["value"], [])
        connect.assert_not_called()

    def test_explicit_empty_replace_deletes_and_commits(self):
        connection = self.Connection()
        with patch.object(self.writer, "connect_mysql", return_value=connection), patch.object(
            self.writer, "load_column_types", return_value={"value": "varchar"}
        ):
            self.writer.sync_table("sample_table", ["value"], [], allow_empty_replace=True)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertIn("DELETE FROM", connection.db_cursor.calls[-1][0])

    def test_order_loss_insert_failure_rolls_back_delete(self):
        connection = self.Connection(fail_insert=True)
        with patch.object(self.writer, "connect_mysql", return_value=connection), patch.object(
            self.writer, "load_column_types", return_value={"value": "varchar"}
        ):
            with self.assertRaisesRegex(RuntimeError, "insert failed"):
                self.writer.sync_order_loss_snapshot(["value"], [["x"]])
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertIn("DELETE FROM", connection.db_cursor.calls[0][0])
        self.assertNotIn("TRUNCATE", " ".join(call[0] for call in connection.db_cursor.calls))


class TaskDefaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SERVICE_DIR))
        cls.runner = load_module("test_runner", SERVICE_DIR / "runner.py")

    def test_missing_task_flags_are_disabled(self):
        settings = {"tasks": {}}
        self.assertEqual(self.runner.enabled_tasks(settings), [])

    def test_explicit_task_flag_is_enabled(self):
        name, (platform, _script, _args) = next(iter(self.runner.TASKS.items()))
        settings = {"tasks": {name: True}, platform: {"enabled": True}}
        self.assertEqual(self.runner.enabled_tasks(settings), [name])

    def test_old_settings_are_upgraded_with_disabled_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"tasks": {}}', encoding="utf-8")
            with patch.object(self.runner, "CONFIG_PATH", path):
                settings = self.runner.load_settings()
            self.assertEqual(set(settings["tasks"]), set(self.runner.TASKS))
            self.assertTrue(all(value is False for value in settings["tasks"].values()))
            self.assertEqual(self.runner.load_json(path, {})["tasks"], settings["tasks"])

    def test_signed_urls_and_secrets_are_redacted(self):
        cookie = "cookie-value-that-must-not-appear"
        signed_url = "https://example.test/data?mtgsig=signed-secret-value"
        settings = {"meituan": {"cookie": cookie, "flow_url": signed_url}}
        sanitized = self.runner.sanitize(f"cookie={cookie} url={signed_url}", settings)
        self.assertNotIn(cookie, sanitized)
        self.assertNotIn(signed_url, sanitized)
        self.assertEqual(sanitized.count("[REDACTED]"), 2)


class LoginLoggingTests(unittest.TestCase):
    def test_login_source_does_not_print_cookie_values(self):
        source = (PMS_SCRIPTS.parent / "login.py").read_text(encoding="utf-8")
        self.assertNotIn("cookies[cookie_name][:30]", source)
        self.assertIn("mask_username(username)", source)


class SettingsValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SERVICE_DIR))
        cls.validation = load_module("test_settings_validation", SERVICE_DIR / "settings_validation.py")

    def test_invalid_manual_values_are_reported_together(self):
        settings = {
            "mysql": {"port": "3306"},
            "service": {"timeout_seconds": -1},
            "tasks": {"new_task": "true"},
            "pms": {"report_base_url": "not-a-url"},
        }
        errors = self.validation.validate_settings(settings)
        self.assertEqual(len(errors), 4)

    def test_partial_first_run_config_is_allowed(self):
        self.assertEqual(self.validation.validate_settings({"tasks": {}}), [])


class StreamedProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SERVICE_DIR))
        cls.process_runner = load_module("test_process_runner", SERVICE_DIR / "process_runner.py")

    def test_output_is_streamed_and_transformed(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "task.log"
            result = self.process_runner.run_streamed(
                [sys.executable, "-u", "-c", "print('token=secret')"],
                cwd=directory,
                env=dict(os.environ),
                timeout=5,
                log_path=log_path,
                transform=lambda line: line.replace("secret", "[REDACTED]"),
            )
            self.assertEqual(result.return_code, 0)
            self.assertEqual(log_path.read_text(encoding="utf-8"), "token=[REDACTED]\n")
            self.assertEqual(result.output_tail, "token=[REDACTED]\n")

    def test_timeout_terminates_process_and_preserves_output(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "timeout.log"
            with self.assertRaises(self.process_runner.ProcessTimeoutError) as raised:
                self.process_runner.run_streamed(
                    [sys.executable, "-u", "-c", "import time; print('started'); time.sleep(5)"],
                    cwd=directory,
                    env=dict(os.environ),
                    timeout=1,
                    log_path=log_path,
                    transform=lambda line: line,
                )
            self.assertIn("started", raised.exception.output_tail)
            self.assertIn("started", log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
