"""状态页、分块保存和更新文案的离线测试。不连接酒店数据库，不跑线上采集。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "OTA采集服务"
sys.path.insert(0, str(SERVICE))

import local_reset
import panel
import panel_update
import runner
import status_routes


def _blank_settings() -> dict:
    return {
        "service": {"scheduler_enabled": False},
        "hotel": {"hotel_id": ""},
        "mysql": {"database": "", "password": "secret"},
        "meituan": {"enabled": True, "hotel_name": "", "me_cookie": ""},
        "ctrip": {"enabled": True, "hotel_name": "", "cookie": ""},
        "pms": {"enabled": False},
        "bypms": {"enabled": False},
        "foxhis": {"enabled": False},
        "meituan_pms": {"enabled": False},
        "tasks": {"meituan_business": True},
    }


def _ready_settings(**changes) -> dict:
    settings = {
        "service": {"scheduler_enabled": True},
        "hotel": {"hotel_id": "puyue"},
        "mysql": {"database": "hotel_puyue", "password": "secret"},
        "meituan": {"enabled": True, "hotel_name": "栖筑", "me_cookie": "me=1"},
        "ctrip": {"enabled": True, "hotel_name": "栖筑携程", "cookie": "ct=1"},
        "pms": {"enabled": False},
        "tasks": {"meituan_business": True, "pms_fetch": False},
    }
    settings.update(changes)
    return settings


class StaffExperienceTests(unittest.TestCase):
    def render_status(self, settings, status, query="/", **patches):
        app = Flask(__name__)
        status_routes.register(app)
        with ExitStack() as stack:
            stack.enter_context(patch.object(runner, "load_settings", return_value=settings))
            stack.enter_context(patch.object(runner, "reconcile_stale_run", return_value=status))
            stack.enter_context(patch.object(status_routes, "manual_scheduler_status", return_value={"alive": False, "scheduler_status": "stopped"}))
            for name in ("price_scheduler_status", "reply_scheduler_status", "promotion_scheduler_status"):
                stack.enter_context(patch.object(status_routes, name, return_value={}))
            stack.enter_context(patch.object(status_routes, "legacy_collector_enabled", return_value=patches.get("legacy", False)))
            stack.enter_context(patch.object(panel_update, "current_notice", return_value=patches.get("notice", {})))
            stack.enter_context(patch.object(status_routes, "page", side_effect=lambda title, body, *args, **kwargs: body))
            response = app.test_client().get(query)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_three_conclusion_bars(self):
        blank = self.render_status(_blank_settings(), {"last_run_status": "never_run"})
        self.assertIn("尚未填写酒店", blank)
        self.assertIn("未开启自动采集", blank)
        self.assertIn("请完成酒店和数据库配置", blank)
        self.assertIn("/config?view=installer", blank)
        self.assertIn("查看全部任务", blank)
        self.assertNotIn("task-card", blank)
        self.assertNotIn("<h2 style=\"margin:0\">采集任务</h2>", blank)
        self.assertIn("服务在后台运行，关闭浏览器不会停止", blank)
        self.assertNotIn('action="/config/save/hotel"', blank)

        ready = self.render_status(
            _ready_settings(),
            {"last_run_status": "success", "last_run_tasks": ["meituan_business"], "tasks": {"meituan_business": {"status": "success"}}},
        )
        self.assertIn("puyue（美团：栖筑，携程：栖筑携程）", ready)
        self.assertIn("正常", ready)
        self.assertIn("没有待办", ready)
        self.assertNotIn("请完成酒店和数据库配置", ready)
        self.assertNotIn("请重新登录", ready)
        self.assertNotIn('id="install-setup"', ready)

        missing_meituan = _ready_settings()
        missing_meituan["meituan"] = {"enabled": True, "hotel_name": "栖筑", "me_cookie": ""}
        meituan_page = self.render_status(missing_meituan, {"last_run_status": "success", "last_run_tasks": [], "tasks": {}})
        self.assertIn("请重新登录美团", meituan_page)
        self.assertNotIn("请重新登录携程", meituan_page)
        self.assertIn("/config?view=staff", meituan_page)
        self.assertNotIn("platform-login/meituan", meituan_page)
        tasks = self.render_status(_ready_settings(), {"last_run_status": "never_run"}, query="/?tasks=1")
        self.assertIn("task-card", tasks)
        self.assertIn("采集任务", tasks)

        missing_ctrip = _ready_settings()
        missing_ctrip["ctrip"] = {"enabled": True, "hotel_name": "栖筑携程", "cookie": ""}
        ctrip_page = self.render_status(missing_ctrip, {"last_run_status": "success", "last_run_tasks": [], "tasks": {}})
        self.assertIn("请重新登录携程", ctrip_page)
        self.assertNotIn("请重新登录美团", ctrip_page)

        running = self.render_status(
            _ready_settings(),
            {
                "last_run_status": "running",
                "last_run_tasks": ["meituan_business"],
                "tasks": {"meituan_business": {"status": "running"}},
            },
        )
        self.assertIn("<div class=\"label\">采集</div><strong>美团经营</strong>", running)

    def test_empty_booking_is_not_called_a_failure(self):
        summary = status_routes.build_conclusion(
            _ready_settings(),
            {
                "last_run_status": "failed",
                "last_run_tasks": ["pms_fetch"],
                "tasks": {"pms_fetch": {"status": "failed", "error_summary": "查询成功，0 条符合条件的订单；保留已有数据。"}},
            },
        )
        self.assertEqual(summary["collection"], "正常")
        failed = status_routes.build_conclusion(
            _ready_settings(),
            {
                "last_run_status": "failed",
                "last_run_tasks": ["pms_fetch"],
                "tasks": {"pms_fetch": {"status": "failed", "error_summary": "接口失败"}},
            },
        )
        self.assertEqual(failed["collection"], "最近一轮没有全部成功")

    def test_new_tasks_stay_off_and_are_listed(self):
        settings = local_reset.default_settings()
        settings["tasks"].pop("meituan_order_detail")
        settings["tasks"].pop("meituan_promotion_balance")
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            config = Path(directory) / "settings.json"
            runner.save_json(config, settings)
            with patch.object(runner, "CONFIG_PATH", config):
                loaded = runner.load_settings()
        self.assertFalse(loaded["tasks"]["meituan_order_detail"])
        self.assertTrue(loaded["tasks"]["meituan_promotion_balance"])
        page = self.render_status(_ready_settings(), {"last_run_status": "success"})
        self.assertNotIn("本版新增、尚未启用", page)

    def test_saving_database_does_not_change_task_switches(self):
        settings = local_reset.default_settings()
        settings["tasks"]["meituan_business"] = True
        settings["tasks"]["pms_fetch"] = False
        settings["mysql"]["database"] = "old_db"
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            config = Path(directory) / "settings.json"
            runner.save_json(config, settings)
            with patch.object(runner, "CONFIG_PATH", config):
                client = panel.app.test_client()
                response = client.post("/config/save/hotel", data={
                    "return_to": "/config",
                    "hotel.hotel_id": "demo-hotel",
                    "service.interval_minutes": "30",
                    "price_scheduler.interval_minutes": "5",
                    "mysql.host": "127.0.0.1",
                    "mysql.port": "3306",
                    "mysql.user": "reader",
                    "mysql.database": "new_db",
                })
                loaded = runner.load_settings()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(loaded["mysql"]["database"], "new_db")
        self.assertEqual(loaded["hotel"]["hotel_id"], "demo-hotel")
        self.assertTrue(loaded["tasks"]["meituan_business"])
        self.assertFalse(loaded["tasks"]["pms_fetch"])
        self.assertTrue(loaded["tasks"]["ctrip_business"])

    def test_staff_login_form_does_not_change_task_switches(self):
        settings = _ready_settings()
        settings["tasks"] = {"meituan_business": True, "pms_fetch": False, "ctrip_business": True}
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            config = Path(directory) / "settings.json"
            runner.save_json(config, settings)
            with patch.object(runner, "CONFIG_PATH", config), \
                    patch.object(panel.platform_login, "start") as start:
                response = panel.app.test_client().post("/platform-login/meituan", data={"return_to": "/config"})
                loaded = runner.load_settings()
        self.assertEqual(response.status_code, 302)
        start.assert_called_once()
        self.assertTrue(loaded["tasks"]["meituan_business"])
        self.assertFalse(loaded["tasks"]["pms_fetch"])
        self.assertTrue(loaded["tasks"]["ctrip_business"])

    def test_config_sections_save_separately_and_reset_stays_in_advanced(self):
        keys = {field[0] for field in panel.all_config_fields()}
        self.assertFalse(panel.HOTEL_FIELDS & panel.ADVANCED_FIELDS)
        self.assertFalse(panel.HOTEL_FIELDS & panel.platform_keys())
        self.assertFalse(panel.ADVANCED_FIELDS & panel.platform_keys())
        self.assertEqual(panel.HOTEL_FIELDS | panel.ADVANCED_FIELDS | panel.platform_keys(), keys)
        settings = local_reset.default_settings()
        with patch.object(runner, "load_settings", return_value=settings), \
                patch.object(panel.platform_login, "read_status", return_value={}):
            client = panel.app.test_client()
            staff = client.get("/config").get_data(as_text=True)
            html = client.get("/config?view=installer").get_data(as_text=True)
        self.assertIn("美团 PMS 和美团 OTA 是两套登录", staff)
        self.assertNotIn("保存酒店和数据库", staff)
        self.assertNotIn("name='task.", staff)
        self.assertNotIn("重置本地配置", staff)
        self.assertNotIn("空闲时安装", html)
        self.assertNotIn("试点版", html)
        self.assertNotIn("确认建立数据表", html)
        self.assertIn("保存酒店和数据库", html)
        self.assertIn("保存当前 PMS 和平台登录", html)
        self.assertIn("保存采集任务开关", html)
        self.assertIn("保存高级项", html)
        self.assertNotIn("保存全部配置", html)
        self.assertIn("没有 isql", html)
        self.assertIn("两套登录", html)
        self.assertIn("不会自动安装", html)
        self.assertIn("将清除本地配置、登录 Cookie 与专用浏览器 Profile；数据库中的已采集数据不会删除。确定继续吗？", html)
        self.assertLess(html.index('action="/config/save/advanced"'), html.index("重置本地配置"))
        self.assertNotIn("确认建立数据表", html)
        for name in runner.TASKS:
            self.assertIn(f"name='task.{name}' checked", html)

    def test_update_messages_and_confirmation_gate(self):
        samples = {
            "Already up to date; no downgrade will be performed.": "已是最新。",
            "Update installed: v1.2.0; backup: updater/backups/20261105-120000.": "已安装，备份在 updater/backups/20261105-120000。",
            "Locally modified program file; refusing overwrite: OTA采集服务/runner.py": "程序文件被改过，更新已停止。",
            "Python runtime differs; install a new portable runtime first": "需要另换便携运行环境，现有配置不会被覆盖。",
            "Dependencies differ; install a new portable runtime first": "需要另换便携运行环境，现有配置不会被覆盖。",
            "An interrupted update exists; use --recover first": "上次更新中断，要先恢复。",
            "Browser extension files updated. Reload the extension in Edge/Chrome.": "扩展文件有变化，请到 Edge 扩展页手动重新加载。",
            "A collector, login helper or other project process is running. Try again when idle.": "现在正在采集或有任务占用，更新已停止。请等空闲后再试。",
        }
        for english, chinese in samples.items():
            with self.subTest(english=english):
                self.assertIn(chinese, panel_update.classify_update_text(english))
        with patch.object(panel_update, "update_pending", return_value=False), \
                patch.object(panel_update, "installed_version", return_value="v1.0.0"), \
                patch.object(panel_update, "_updater") as updater:
            updater.return_value.latest_release.return_value = {"tag_name": "v1.0.0"}
            updater.return_value.version.side_effect = lambda value: tuple(int(part) for part in value.lstrip("v").split("."))
            self.assertEqual(panel_update.check_release()["message"], "已是最新。")
            updater.return_value.latest_release.return_value = {"tag_name": "v1.1.0"}
            found = panel_update.check_release()
            self.assertEqual(found["kind"], "available")
            self.assertIn("v1.1.0", found["message"])
        with patch.object(panel_update, "check_release", return_value={"kind": "current", "message": "已是最新。"}), \
                patch.object(panel_update, "apply_confirmed", return_value={"kind": "result", "message": "已安装。"}) as apply, \
                patch.object(panel_update, "store_notice"):
            client = panel.app.test_client()
            self.assertEqual(client.post("/update/check").status_code, 302)
            apply.assert_not_called()
            self.assertEqual(client.post("/update/apply").status_code, 302)
            apply.assert_not_called()
            self.assertEqual(client.post("/update/apply", data={"confirm": "yes"}).status_code, 302)
            apply.assert_called_once()
        completed = SimpleNamespace(stdout="Update installed: v9; backup: updater/backups/stamp-1.", stderr="", returncode=0)
        with patch.object(panel_update, "update_pending", return_value=False), \
                patch.object(panel_update.subprocess, "run", return_value=completed) as run:
            message = panel_update.apply_confirmed()["message"]
        self.assertIn("已安装，备份在 updater/backups/stamp-1。", message)
        command = run.call_args.args[0]
        self.assertIn("--apply", command)
        self.assertNotIn("--allow-downgrade", command)
        self.assertEqual(run.call_args.kwargs["input"], "y\n")

    def test_warnings_and_startup_text_are_chinese(self):
        settings = {
            "pms": {"enabled": True, "username": "", "password": ""},
            "meituan": {"enabled": False},
            "ctrip": {"enabled": False},
            "mysql": {"password": ""},
        }
        with patch.object(runner, "python_path", return_value=Path(sys.executable)), \
                patch.object(runner, "script_path", return_value=Path("missing-pms-entry.py")):
            warnings = runner.config_warnings(settings)
        self.assertTrue(warnings)
        self.assertTrue(all("is empty" not in warning.lower() and "not found" not in warning.lower() for warning in warnings))
        self.assertFalse(any("0 条" in warning and "失败" in warning for warning in warnings))
        self.assertIn("还没有填写 PMS 登录账号。", warnings)
        script = (SERVICE / "service_control.ps1").read_text(encoding="utf-8-sig")
        for sentence in (
            "找不到配置文件 settings.json",
            "已被其他程序占用",
            "8765",
            "HotelOTACollector 仍在启用，和手动启动冲突",
            "定时采集调度没有启动",
            "调价调度没有启动",
            "评论回复调度没有启动",
            "推广控制调度没有启动",
            "酒店数据采集已启动。请在浏览器打开：$url",
            "关闭浏览器不会停止",
        ):
            self.assertIn(sentence, script)
        self.assertIn('Start-Process $url', script)
        self.assertNotIn('"$url/config"', script)
        self.assertNotIn("Port $port is occupied", script)
        common = (SERVICE / "task_scheduler_common.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("Get-Credential", common)
        self.assertIn("-AtStartup", common)
        logon = (SERVICE / "install_logon_task.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("-AtLogOn", logon)
        self.assertNotIn("Get-Credential", logon)
        self.assertNotIn("HotelOTACollector", logon)
        self.assertNotIn("idle_update", logon)
        installer = (SERVICE / "install_task.bat").read_text(encoding="utf-8")
        self.assertIn("install_logon_task.ps1", installer)
        self.assertNotIn("Get-Credential", installer)

    def test_legacy_task_link_does_not_delete_it(self):
        page = self.render_status(_ready_settings(), {"last_run_status": "success"}, legacy=True)
        self.assertIn("HotelOTACollector 仍在启用", page)
        self.assertIn("不会自动删除", page)
        self.assertIn("href='/legacy-task'", page)
        with patch.object(status_routes, "page", side_effect=lambda title, body, *args, **kwargs: body):
            html = panel.app.test_client().get("/legacy-task").get_data(as_text=True)
        self.assertIn("uninstall_task.bat", html)
        self.assertIn("不会自动删除", html)


if __name__ == "__main__":
    unittest.main()
