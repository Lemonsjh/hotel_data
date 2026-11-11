import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "OTA采集服务"), str(ROOT)]

import local_reset
import panel
import runner
from rezen_pms.connection import read_status, run_check


class ConnectionTests(unittest.TestCase):
    def test_check_records_counts_without_the_session(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "status.json"

            def collect(session_token, hotel_name, **_kwargs):
                self.assertEqual(session_token, "secret-session-value")
                self.assertEqual(hotel_name, "测试酒店")
                return [{
                    "total_rooms": 5, "available_rooms": 1, "occupied_rooms": 3,
                    "overbooking_rooms": 0,
                }]

            result = run_check(
                {"rezen": {"session_token": "secret-session-value", "hotel_name": "测试酒店"}},
                collect=collect, path=path)
            stored = path.read_text(encoding="utf-8")
            self.assertNotIn("secret-session-value", stored)
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["message"], "已连接，1 个房型，可售 1，已售 3")
            self.assertEqual(read_status(path)["room_types"], 1)

    def test_failed_check_keeps_the_error_without_a_session(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "status.json"

            def collect(*_args, **_kwargs):
                raise RuntimeError("请先打开并登录 Rezen One")

            result = run_check({"rezen": {"session_token": "secret-session-value"}}, collect=collect, path=path)
            self.assertEqual(result["status"], "failed")
            self.assertNotIn("secret-session-value", path.read_text(encoding="utf-8"))
            self.assertIn("打开并登录", read_status(path)["message"])

    def test_config_page_shows_the_connection_result(self):
        settings = local_reset.default_settings()
        settings["pms"]["provider"] = "meituan_rezen"
        settings["rezen"]["session_token"] = ""
        client = panel.app.test_client()
        with patch.object(runner, "load_settings", return_value=settings), \
                patch("rezen_pms.connection.read_status", return_value={"status": "never", "message": "尚未检查"}):
            page = client.get("/config").get_data(as_text=True)
        self.assertEqual(page.count("formaction='/rezen-connection'>检查连接"), 1)
        self.assertIn("尚未检查", page)
        self.assertIn("留空，从已打开的 Rezen One 读取", page)
        self.assertIn("data-rezen-connection='1'", page)

    def test_check_action_uses_the_form_and_does_not_echo_the_session(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "settings.json"
            runner.save_json(config, local_reset.default_settings())
            seen = {}

            def fake_check(settings, **_kwargs):
                seen["token"] = settings["rezen"]["session_token"]
                return {"status": "success", "message": "已连接，5 个房型，可售 30，已售 34"}

            client = panel.app.test_client()
            with patch.object(runner, "CONFIG_PATH", config), \
                    patch("rezen_pms.connection.run_check", side_effect=fake_check):
                response = client.post("/rezen-connection", data={
                    "pms_provider": "meituan_rezen",
                    "rezen.session_token": "secret-session-value",
                    "rezen.hotel_name": "测试酒店",
                })
            self.assertEqual(seen["token"], "secret-session-value")
            location = response.headers["Location"]
            self.assertNotIn("secret-session-value", location)
            self.assertIn("5", location)
            saved = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(saved["rezen"]["hotel_name"], "测试酒店")
            self.assertEqual(saved["pms"]["provider"], "meituan_rezen")
