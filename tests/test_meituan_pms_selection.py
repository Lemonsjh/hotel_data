"""美团 PMS 选择入口回归；不连接酒店后台或业务数据库。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "OTA采集服务"))

import local_reset
import panel
import pms_providers
import room_mapping_store
import runner


class MeituanPmsSelectionTests(unittest.TestCase):
    def test_selection_roundtrip_and_unavailable_task(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            config = Path(directory) / "settings.json"
            runner.save_json(config, local_reset.default_settings())
            with patch.object(runner, "CONFIG_PATH", config), \
                 patch.object(panel.platform_login, "read_status", return_value={}):
                client = panel.app.test_client()
                page = client.get("/config").get_data(as_text=True)
                self.assertIn("美团 PMS", page)
                self.assertIn("美团 PMS 采集尚未接入", page)
                client.post("/config", data={"pms_provider": "meituan_pms", "task.pms_fetch": "on"})
                settings = runner.load_settings()
                self.assertEqual(settings["pms"]["provider"], "meituan_pms")
                self.assertTrue(settings["meituan_pms"]["enabled"])
                self.assertFalse(settings["pms"]["enabled"])
                self.assertFalse(settings["bypms"]["enabled"])
                self.assertFalse(settings["foxhis"]["enabled"])
                self.assertIn("value='meituan_pms' selected", client.get("/config").get_data(as_text=True))
                self.assertNotIn("pms_fetch", runner.enabled_tasks(settings))
                self.assertEqual(runner.task_unavailable_reason("pms_fetch", "pms", settings), "美团 PMS采集尚未接入")
                with self.assertRaisesRegex(ValueError, "尚未接入"):
                    pms_providers.task_specs("pms_fetch", settings)
                self.assertEqual(room_mapping_store.active_pms_platform(settings), "pms_meituan")
                self.assertEqual(room_mapping_store.defaults(settings)["pms_hotel_name"], "")


if __name__ == "__main__":
    unittest.main()
