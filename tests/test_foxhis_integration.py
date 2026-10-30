"""西软平台接入回归；不读取真实配置、不探测网络、不访问业务数据库。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "OTA采集服务"
sys.path[:0] = [str(ROOT / "updater"), str(SERVICE), str(SERVICE / "foxhis_pms")]

import foxhis_common
import foxhis_probe
import foxhis_room_status_data
import local_reset
import package_format
import panel
import pms_providers
import room_mapping_store
import runner
import settings_validation
import update_guard
from process_runner import ProcessResult


class FoxhisIntegrationTests(unittest.TestCase):
    def settings(self):
        settings = local_reset.default_settings()
        pms_providers.apply_provider_selection(settings, "foxhis")
        settings["hotel"]["hotel_id"] = "TEST"
        settings["foxhis"].update(hotel_name="测试酒店", username="test_reader")
        return settings

    def test_task_registry_and_defaults_still_match_confirmed_rules(self):
        settings = local_reset.default_settings()
        self.assertEqual(len(runner.TASKS), 31)
        self.assertNotIn("meituan_exposure_source", runner.TASKS)
        self.assertEqual(settings["pms"]["provider"], "byh")
        self.assertFalse(settings["foxhis"]["enabled"])
        self.assertTrue(all(settings["tasks"].values()))
        self.assertFalse(settings["service"]["scheduler_enabled"])

    def test_all_seven_scripts_resolve_in_existing_updater_namespace(self):
        settings = self.settings()
        specs = runner.task_specs("pms_fetch", settings)
        self.assertEqual(len(specs), 7)
        for platform, filename, _ in specs:
            path = runner.script_path(settings, platform, filename)
            self.assertTrue(path.is_file(), path)
            package_format.allowed_path(path.relative_to(ROOT).as_posix())
        self.assertEqual(runner.task_timeout(settings, "foxhis"), 600)
        self.assertIn("pms_fetch", runner.enabled_tasks(settings))

    def test_foxhis_does_not_write_kf11(self):
        import foxhis_sql
        self.assertFalse(hasattr(foxhis_sql, "ROOM_STATUS_TABLE"))
        self.assertFalse(hasattr(foxhis_room_status_data, "sync_mysql"))
        self.assertNotIn("kf11_room_status_snapshot", Path(foxhis_sql.__file__).read_text(encoding="utf-8"))

    def test_legacy_config_gains_disabled_foxhis_without_enabling_tasks(self):
        settings = local_reset.default_settings()
        settings.pop("foxhis")
        settings["tasks"]["meituan_business"] = False
        settings["tasks"]["meituan_exposure_source"] = True
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            config = Path(directory) / "settings.json"
            runner.save_json(config, settings)
            with patch.object(runner, "CONFIG_PATH", config):
                loaded = runner.load_settings()
        self.assertFalse(loaded["foxhis"]["enabled"])
        self.assertFalse(loaded["tasks"]["meituan_business"])
        self.assertNotIn("meituan_exposure_source", loaded["tasks"])

    def test_provider_roundtrip_and_form_render_keep_all_three_cards(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            config = Path(directory) / "settings.json"
            runner.save_json(config, local_reset.default_settings())
            with patch.object(runner, "CONFIG_PATH", config), \
                 patch.object(panel.platform_login, "read_status", return_value={}):
                client = panel.app.test_client()
                data = {f"task.{name}": "on" for name in runner.TASKS}
                data["pms_provider"] = "foxhis"
                self.assertEqual(client.post("/config", data=data).status_code, 302)
                loaded = runner.load_settings()
                html = client.get("/config").get_data(as_text=True)
        self.assertEqual(loaded["pms"]["provider"], "foxhis")
        self.assertTrue(loaded["foxhis"]["enabled"])
        self.assertFalse(loaded["pms"]["enabled"])
        self.assertFalse(loaded["bypms"]["enabled"])
        for card in ("pms", "bypms", "foxhis"):
            self.assertIn(f'data-pms-card="{card}"', html)
        self.assertNotIn("task.meituan_exposure_source", html)

    def test_foxhis_reports_selection_and_validation(self):
        settings = self.settings()
        settings["foxhis"]["reports"] = "room_status,jd01"
        self.assertEqual([item[1] for item in runner.task_specs("pms_fetch", settings)],
                         ["foxhis_room_status_data.py", "foxhis_jd01_data.py"])
        self.assertFalse(settings_validation.validate_settings(settings))
        for key, value in (("reports", "kf11"), ("fetch_after", "99:99"),
                           ("business_date_offset_days", -1)):
            invalid = self.settings()
            invalid["foxhis"][key] = value
            self.assertTrue(settings_validation.validate_settings(invalid))

    def test_seven_collectors_run_under_one_task_with_no_real_processes(self):
        settings, calls = self.settings(), []
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            with patch.object(runner, "LOG_DIR", Path(directory)), \
                 patch.object(runner, "save_json"), patch.object(runner, "enrich_room_type_ids"), \
                 patch.object(runner, "run_streamed", side_effect=lambda command, **kwargs:
                              (calls.append(command) or ProcessResult(0, "ok"))):
                result = runner.run_task("pms_fetch", settings, {})
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(calls), 7)
        self.assertTrue(all("foxhis_pms" in command[1] for command in calls))

    def test_update_guard_remains_before_loading_configuration(self):
        with patch.object(update_guard, "ensure_not_updating", side_effect=RuntimeError("updating")), \
             patch.object(runner, "load_settings") as load:
            with self.assertRaisesRegex(RuntimeError, "updating"):
                runner.run_once()
        load.assert_not_called()

    def test_foxhis_credentials_are_masked_and_do_not_inherit_other_pms_name(self):
        settings = self.settings()
        settings["foxhis"]["password"] = "fake_only_for_test"
        env = runner.build_env(settings, "foxhis")
        self.assertEqual(env["FOXHIS_PASSWORD"], "fake_only_for_test")
        self.assertNotIn("fake_only_for_test", runner.sanitize("fake_only_for_test", settings))
        with patch.dict(os.environ, {"FOXHIS_HOTEL_NAME": "", "PMS_HOTEL_NAME": "另一平台酒店"}):
            self.assertEqual(foxhis_common.Config.from_env().hotel_name, "")

    def test_mapping_source_is_independent_and_keeps_exact_matching(self):
        settings = self.settings()
        self.assertEqual(room_mapping_store.active_pms_platform(settings), "pms_foxhis")
        self.assertEqual(room_mapping_store.defaults(settings)["pms_hotel_name"], "测试酒店")

    def test_readonly_guard_rejects_mixed_batches_and_select_into(self):
        for sql in ("delete from demo", "select 1; delete from demo", "select 1 into demo"):
            with self.assertRaises(foxhis_common.ReadonlyViolation):
                foxhis_common.assert_readonly(sql)
        foxhis_common.assert_readonly("select 'delete; update' as label union all select 'ok';")

    def test_full_foxhis_sources_validate_as_code_only_update(self):
        contents = {p.relative_to(ROOT).as_posix(): p.read_bytes()
                    for p in (SERVICE / "foxhis_pms").glob("*.py")}
        contents.update({name: b"# offline fixture\n" for name in (
            "mysql_connection.py", "ota_mysql_writer.py", "OTA采集服务/runner.py", "OTA采集服务/panel.py",
        )})
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            (root / "requirements-portable.lock.txt").write_bytes(b"fake==1\n")
            manifest = {"format": 1, "repository": package_format.REPOSITORY, "version": "v1.0.0",
                        "python": "3.12", "dependency_sha256": package_format.dependency_digest(b"fake==1\n"),
                        "files": {name: package_format.digest(data) for name, data in contents.items()}, "removed": []}
            path = root / "test.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                for name, data in contents.items():
                    archive.writestr(name, data)
            self.assertEqual(package_format.validate_package(path, root, "v1.0.0")[1], contents)

    def test_probe_will_not_select_another_hotel_database(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            (root / "BIN").mkdir()
            (root / "ini").mkdir()
            (root / "BIN/isql.exe").write_bytes(b"offline fixture")
            (root / "FOXHIS.INI").write_text("[Database]\nServername=Missing\nDatabase=testdb\n", encoding="ascii")
            (root / "ini/SQL.INI").write_text("[OtherHotel]\nquery=TCP,127.0.0.1,5000\n", encoding="ascii")
            with patch.object(foxhis_probe, "find_foxhis_home", return_value=(root, "test")), \
                 patch.object(foxhis_probe, "find_sybase_home", return_value=(root, "test")), \
                 patch.object(foxhis_probe, "tcp_reachable", return_value=True):
                result = foxhis_probe.probe()
        self.assertFalse(result["ok"])
        self.assertEqual(result["fields"]["server"], "")


if __name__ == "__main__":
    unittest.main()
