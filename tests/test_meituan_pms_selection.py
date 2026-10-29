"""美团 PMS 选择入口回归；不连接酒店后台或业务数据库。"""
import sys
import shutil
import subprocess
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
    def test_login_status_exposes_only_current_hotel_identity_without_secrets(self):
        settings = local_reset.default_settings()
        settings["pms"]["provider"] = "meituan_pms"
        settings["hotel"]["hotel_id"] = "hotel"
        settings["meituan_pms"].update(hotel_name="新酒店", org_id="987654", cookie="private-cookie")
        status = {"status": "success", "completed_at": "new", "identity_updated": True, "hotel_id": "hotel"}
        client = panel.app.test_client()
        with patch.object(runner, "load_settings", return_value=settings), \
                patch.object(panel.platform_login, "read_status", return_value=status):
            data = client.get("/api/platform-login/meituan_pms").get_json()
            self.assertEqual(data["hotel_identity"], {"hotel_name": "新酒店", "org_id": "987654"})
            self.assertNotIn("private-cookie", str(data))
            for changed in ({"identity_updated": False}, {"hotel_id": "another-hotel"}, {"status": "waiting"}):
                with patch.object(panel.platform_login, "read_status", return_value={**status, **changed}):
                    self.assertEqual(client.get("/api/platform-login/meituan_pms").get_json()["hotel_identity"], {})
            settings["pms"]["provider"] = "byh"
            self.assertEqual(client.get("/api/platform-login/meituan_pms").get_json()["hotel_identity"], {})

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the config form polling regression")
    def test_login_polling_updates_form_once_per_completion_without_reloading(self):
        settings = local_reset.default_settings()
        with patch.object(runner, "load_settings", return_value=settings), \
                patch.object(panel.platform_login, "read_status", return_value={"completed_at": "old"}):
            page = panel.app.test_client().get("/config").get_data(as_text=True)
        self.assertIn("data-login-completed-at='old'", page)
        function = "async function refreshLoginStates()" + page.split("async function refreshLoginStates()", 1)[1].split(
            "setInterval(refreshLoginStates", 1)[0]
        setup = r'''
const assert = require('node:assert/strict');
const fields = {'[name="meituan_pms.hotel_name"]': {value:'old-name'}, '[name="meituan_pms.org_id"]': {value:'111'}};
const box = {dataset:{loginPlatform:'meituan_pms',loginCompletedAt:'old'},querySelector:()=>({})};
const document = {querySelectorAll:()=>[box],querySelector:key=>fields[key]};
let data = {status:'success',completed_at:'new',hotel_identity:{hotel_name:'new-name',org_id:'222',cookie:'private'}};
const fetch = async()=>({json:async()=>data});
'''
        checks = r'''
(async()=>{
 await refreshLoginStates();
 assert.equal(fields['[name="meituan_pms.hotel_name"]'].value,'new-name');
 assert.equal(fields['[name="meituan_pms.org_id"]'].value,'222');
 fields['[name="meituan_pms.hotel_name"]'].value='manual-edit';
 await refreshLoginStates();
 assert.equal(fields['[name="meituan_pms.hotel_name"]'].value,'manual-edit');
 data={status:'success',completed_at:'failed-identification',hotel_identity:{}};
 await refreshLoginStates();
 assert.equal(fields['[name="meituan_pms.hotel_name"]'].value,'manual-edit');
 data={status:'success',completed_at:'next-login',hotel_identity:{hotel_name:'next-name',org_id:'333'}};
 await refreshLoginStates();
 assert.equal(fields['[name="meituan_pms.hotel_name"]'].value,'next-name');
 assert.equal(fields['[name="meituan_pms.org_id"]'].value,'333');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            script = Path(directory) / "login-form-regression.js"
            script.write_text(setup + function + checks, encoding="utf-8")
            result = subprocess.run([shutil.which("node"), str(script)], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))

    def test_selection_roundtrip_and_independent_task(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            config = Path(directory) / "settings.json"
            runner.save_json(config, local_reset.default_settings())
            with patch.object(runner, "CONFIG_PATH", config), \
                 patch.object(panel.platform_login, "read_status", return_value={}):
                client = panel.app.test_client()
                page = client.get("/config").get_data(as_text=True)
                self.assertIn("美团 PMS", page)
                self.assertNotIn("经营报表仅采总营业指标", page)
                self.assertIn("formaction='/platform-login/meituan_pms'", page)
                self.assertIn("data-login-platform='meituan_pms'", page)
                client.post("/config", data={"pms_provider": "meituan_pms", "task.pms_fetch": "on"})
                settings = runner.load_settings()
                self.assertEqual(settings["pms"]["provider"], "meituan_pms")
                self.assertTrue(settings["meituan_pms"]["enabled"])
                self.assertFalse(settings["pms"]["enabled"])
                self.assertFalse(settings["bypms"]["enabled"])
                self.assertFalse(settings["foxhis"]["enabled"])
                self.assertIn("value='meituan_pms' selected", client.get("/config").get_data(as_text=True))
                self.assertIn("pms_fetch", runner.enabled_tasks(settings))
                self.assertEqual(runner.task_unavailable_reason("pms_fetch", "pms", settings), "")
                self.assertEqual(pms_providers.task_specs("pms_fetch", settings),
                                 [("meituan_pms", "collector.py", []), ("meituan_pms", "daily_report.py", []),
                                  ("meituan_pms", "monthly_report.py", []), ("meituan_pms", "performance_report.py", []),
                                  ("meituan_pms", "booking_report.py", []), ("meituan_pms", "classification_report.py", []),
                                  ("meituan_pms", "room_performance_report.py", []), ("meituan_pms", "revenue_report.py", [])])
                self.assertEqual(runner.script_path(settings, "meituan_pms", "daily_report.py").name, "daily_report.py")
                self.assertTrue(runner.script_path(settings, "meituan_pms", "collector.py").is_file())
                self.assertEqual(runner.script_path(settings, "meituan_pms", "collector.py").parent, ROOT / "meituan_pms")
                self.assertEqual(room_mapping_store.active_pms_platform(settings), "pms_meituan")
                self.assertEqual(room_mapping_store.defaults(settings)["pms_hotel_name"], "")


if __name__ == "__main__":
    unittest.main()
