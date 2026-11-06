from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from flask import Flask


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "OTA采集服务"))

import runner
import status_routes
from process_runner import ProcessResult


class PmsProviderSelectionTests(TestCase):
    def test_huiyun_client_discovery_never_saves_or_starts_the_client(self):
        import panel
        import huiyun_routes
        client = panel.app.test_client()
        for found in ('D:/慧云/Main/MainShellApp.exe', None):
            with self.subTest(found=found), patch.object(huiyun_routes, 'find_client', return_value=found) as find, \
                    patch.object(runner, 'save_json') as save, patch.object(panel.huiyun_bridge, 'start') as start:
                response = client.post('/api/huiyun/client-path', json={'client_path': '旧路径'})
                self.assertEqual(response.status_code, 200 if found else 404)
                self.assertEqual(response.json['ok'], bool(found))
                if found:
                    self.assertEqual(response.json['path'], found)
                find.assert_called_once_with('旧路径')
                save.assert_not_called()
                start.assert_not_called()

    def test_huiyun_can_be_selected_and_saved_without_starting_another_collector(self):
        import local_reset
        import panel
        import pms_providers
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            config = Path(directory) / 'settings.json'
            runner.save_json(config, local_reset.default_settings())
            with patch.object(runner, 'CONFIG_PATH', config), \
                 patch.object(panel.platform_login, 'read_status', return_value={}):
                client = panel.app.test_client()
                self.assertEqual(client.post('/config', data={
                    'pms_provider': 'huiyun', 'huiyun.hotel_name': '测试慧云酒店',
                    'task.pms_fetch': 'on',
                }).status_code, 302)
                loaded = runner.load_settings()
                html = client.get('/config').get_data(as_text=True)
        self.assertEqual(loaded['pms']['provider'], 'huiyun')
        self.assertEqual(loaded['huiyun']['hotel_name'], '测试慧云酒店')
        for key, spec in pms_providers.PMS_PROVIDERS.items():
            self.assertEqual(loaded[spec['config_key']]['enabled'], key == 'huiyun')
        self.assertIn('data-pms-card="huiyun"', html)
        self.assertIn("value='huiyun' selected", html)
        self.assertIn('pms_fetch', runner.enabled_tasks(loaded))
        self.assertIn('打开采集客户端', html)
        self.assertIn('data-find-huiyun-client', html)
        self.assertIn('/static/huiyun_client_path.js', html)
        loaded['mysql']['database'] = 'fake_huiyun'
        with tempfile.TemporaryDirectory(dir=ROOT) as directory, \
             patch.object(runner, 'LOG_DIR', Path(directory)), patch.object(runner, 'save_json'), \
             patch.object(runner, 'ensure_task_tables') as ensure, \
             patch.object(runner, 'run_streamed', return_value=ProcessResult(0, 'collected')) as run:
            result = runner.run_task('pms_fetch', loaded, {})
        self.assertEqual(result['status'], 'success')
        command = run.call_args_list[0].args[0]
        self.assertTrue(command[1].replace('\\', '/').endswith('huiyun_pms/collector.py'))
        self.assertNotIn('--dry-run', command)
        self.assertTrue(run.call_args_list[1].args[0][1].replace('\\', '/').endswith('huiyun_pms/booking_report.py'))
        self.assertTrue(run.call_args_list[2].args[0][1].replace('\\', '/').endswith('huiyun_pms/guest_report.py'))
        self.assertTrue(run.call_args_list[3].args[0][1].replace('\\', '/').endswith('huiyun_pms/daily_report.py'))
        self.assertTrue(run.call_args_list[4].args[0][1].replace('\\', '/').endswith('huiyun_pms/monthly_report.py'))
        self.assertTrue(run.call_args_list[5].args[0][1].replace('\\', '/').endswith('huiyun_pms/revenue_report.py'))
        self.assertTrue(run.call_args_list[6].args[0][1].replace('\\', '/').endswith('huiyun_pms/room_performance_report.py'))
        self.assertTrue(run.call_args_list[7].args[0][1].replace('\\', '/').endswith('huiyun_pms/classification_report.py'))
        ensure.assert_called_once()

    def test_old_configuration_gains_disabled_huiyun_without_switching_platform(self):
        import local_reset
        settings = local_reset.default_settings()
        settings.pop('huiyun')
        settings['tasks']['pms_fetch'] = False
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            config = Path(directory) / 'settings.json'
            runner.save_json(config, settings)
            with patch.object(runner, 'CONFIG_PATH', config):
                loaded = runner.load_settings()
        self.assertEqual(loaded['pms']['provider'], 'byh')
        self.assertFalse(loaded['huiyun']['enabled'])
        self.assertFalse(loaded['tasks']['pms_fetch'])
        self.assertTrue(loaded['huiyun']['client_path'].endswith('MainShellApp.exe'))

    def test_byh_default_and_old_local_paths_resolve_to_renamed_directory(self):
        expected = ROOT / "别样红PMS数据采集代码" / "PMS登录" / "fetch_main.py"
        for directory in (None, "正式数据抓取-PMS（别样红）/PMS登录",
                          "正式数据抓取-PMS（别样红）\\\\PMS登录",
                          str(ROOT / "正式数据抓取-PMS（别样红）" / "PMS登录")):
            settings = {"pms": {"provider": "byh", "code_dir": directory}}
            with self.subTest(directory=directory):
                self.assertEqual(runner.script_path(settings, "pms", "fetch_main.py"), expected)
                self.assertEqual(settings["pms"]["code_dir"], directory)
        self.assertTrue(expected.is_file())

    def test_byh_custom_external_directory_is_not_redirected(self):
        custom = ROOT.parent / "custom-test-root" / "正式数据抓取-PMS（别样红）" / "PMS登录"
        settings = {"pms": {"code_dir": str(custom)}}
        self.assertEqual(runner.script_path(settings, "pms", "fetch_main.py"), custom / "fetch_main.py")

    def test_byh_legacy_path_is_kept_before_directory_is_renamed(self):
        import pms_providers
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            legacy = base / "正式数据抓取-PMS（别样红）" / "PMS登录"
            legacy.mkdir(parents=True)
            with patch.object(pms_providers, "PROJECT_ROOT", base):
                self.assertEqual(pms_providers.byh_path("正式数据抓取-PMS（别样红）/PMS登录"), legacy)

    def test_byh_price_and_session_entries_follow_renamed_directory(self):
        import local_reset
        import price_tasks
        self.assertEqual(local_reset.PMS_SESSION_PATH.parent, ROOT / "别样红PMS数据采集代码" / "PMS登录")
        self.assertEqual(price_tasks.PRICE_DIR, ROOT / "别样红PMS数据采集代码" / "ota调价")
        for platform in price_tasks.PLATFORMS.values():
            self.assertTrue((price_tasks.PRICE_DIR / platform["script"]).is_file())

    def test_retired_roomnight_task_is_not_registered_or_scheduled(self):
        self.assertNotIn("meituan_exposure_source", runner.TASKS)
        settings = {"meituan": {"enabled": True}, "tasks": {"meituan_exposure_source": True}}
        self.assertNotIn("meituan_exposure_source", runner.enabled_tasks(settings))

    def test_loading_settings_removes_retired_roomnight_switch(self):
        settings = {"pms": {"provider": "byh"}, "tasks": {"meituan_exposure_source": True}}
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            config_path = Path(directory) / "settings.json"
            config_path.write_text("{}", encoding="utf-8")
            with patch.object(runner, "CONFIG_PATH", config_path), \
                 patch.object(runner, "require_valid_settings", return_value=settings), \
                 patch.object(runner, "load_json", return_value=settings), \
                 patch.object(runner, "save_json") as save:
                loaded = runner.load_settings()
        self.assertNotIn("meituan_exposure_source", loaded["tasks"])
        save.assert_called_once()

    def test_disabled_meituan_reports_its_own_reason(self):
        settings = {"pms": {"provider": "bypms"}, "meituan": {"enabled": False}, "paths": {}}
        self.assertEqual(
            runner.task_unavailable_reason("meituan_business", "meituan", settings),
            "美团采集已禁用，请先在配置页启用",
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner, "LOG_DIR", Path(directory)), \
                 patch.object(runner, "save_json"), \
                 patch.object(runner, "run_streamed") as run_streamed:
                result = runner.run_task("meituan_business", settings, {})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_summary"], "美团采集已禁用，请先在配置页启用")
        run_streamed.assert_not_called()

    def test_manual_route_respects_platform_enabled_not_schedule_flag(self):
        app = Flask(__name__)
        status_routes.register(app)
        settings = {"meituan": {"enabled": False}, "tasks": {"meituan_business": False}}
        with patch.object(runner, "load_settings", return_value=settings), \
             patch.object(status_routes, "run_background") as run_background:
            self.assertEqual(app.test_client().post("/run/meituan_business").status_code, 302)
            run_background.assert_not_called()
            settings["meituan"]["enabled"] = True
            self.assertEqual(app.test_client().post("/run/meituan_business").status_code, 302)
            run_background.assert_called_once_with(["run-task", "meituan_business"])

    def test_bypms_hotel_name_is_backfilled_only_when_blank(self):
        settings = {"pms": {"provider": "bypms"}, "bypms": {"hotel_name": ""}, "tasks": {}}
        with patch.object(runner, "load_settings", return_value=settings), patch.object(runner, "save_json") as save:
            self.assertTrue(runner.backfill_bypms_hotel_name("测试宝寓酒店"))
        self.assertEqual(settings["bypms"]["hotel_name"], "测试宝寓酒店")
        self.assertTrue(save.called)

        with patch.object(runner, "load_settings", return_value=settings), patch.object(runner, "save_json") as save:
            self.assertFalse(runner.backfill_bypms_hotel_name("另一家酒店"))
        self.assertFalse(save.called)

    def test_only_active_pms_provider_tasks_are_enabled(self):
        byh = {"pms": {"provider": "byh", "enabled": True}, "tasks": {"pms_fetch": True}}
        bypms = {"pms": {"provider": "bypms", "enabled": False}, "bypms": {"enabled": True}, "tasks": {"pms_fetch": True}}

        self.assertIn("pms_fetch", runner.enabled_tasks(byh))
        self.assertIn("pms_fetch", runner.enabled_tasks(bypms))
        self.assertEqual(runner.task_specs("pms_fetch", byh), [("pms", "fetch_main.py", [])])
        self.assertEqual(runner.task_specs("pms_fetch", bypms), [
            ("bypms", "bypms_room_status_data.py", []),
            ("bypms", "bypms_channel_mapping_data.py", []),
            ("bypms", "bypms_rs01_data.py", []),
            ("bypms", "bypms_daily_report_data.py", []),
            ("bypms", "bypms_jl11_data.py", []),
            ("bypms", "bypms_jy03_data.py", []),
            ("bypms", "bypms_jd01_data.py", []),
            ("bypms", "bypms_jd02_data.py", []),
        ])

    def test_bypms_runs_both_collectors_under_pms_fetch(self):
        settings = {
            "pms": {"provider": "bypms", "enabled": False},
            "bypms": {"enabled": True},
            "tasks": {"pms_fetch": True},
            "hotel": {}, "mysql": {}, "paths": {}, "service": {},
        }
        calls: list[list[str]] = []
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner, "LOG_DIR", Path(directory)), \
                 patch.object(runner, "save_json"), \
                 patch.object(runner, "enrich_room_type_ids"), \
                 patch.object(runner, "backfill_bypms_hotel_name_from_summary"), \
                 patch.object(runner, "run_streamed", side_effect=lambda command, **_: (calls.append(command) or ProcessResult(0, "ok"))):
                result = runner.run_task("pms_fetch", settings, {})

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(calls), 8)
        self.assertTrue(calls[0][1].endswith("bypms_room_status_data.py"))
        self.assertTrue(calls[1][1].endswith("bypms_channel_mapping_data.py"))
        self.assertTrue(calls[2][1].endswith("bypms_rs01_data.py"))
        self.assertTrue(calls[3][1].endswith("bypms_daily_report_data.py"))
        self.assertTrue(calls[4][1].endswith("bypms_jl11_data.py"))
        self.assertTrue(calls[5][1].endswith("bypms_jy03_data.py"))
        self.assertTrue(calls[6][1].endswith("bypms_jd01_data.py"))
        self.assertTrue(calls[7][1].endswith("bypms_jd02_data.py"))
