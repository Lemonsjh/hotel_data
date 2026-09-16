from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
CTRIP_DIR = ROOT / "携程OTA数据采集代码"


def load_module():
    sys.path.insert(0, str(CTRIP_DIR))
    path = CTRIP_DIR / "ctrip_promotion_status_data.py"
    spec = importlib.util.spec_from_file_location("ctrip_information_score_test_target", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CtripInformationScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_reads_current_score_from_api_payload(self):
        self.assertEqual(self.module.information_score_from_payload({
            "success": True, "scoreInfo": {"currentScore": 96, "fullScore": 100, "scoreText": "96%"},
        }), 96.0)

    def test_rejects_missing_or_failed_score(self):
        for payload in ({"success": False, "scoreInfo": {"currentScore": 96}},
                        {"success": True, "scoreInfo": {}},
                        {"success": True, "scoreInfo": {"currentScore": "not-a-score"}}):
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                self.module.information_score_from_payload(payload)

    def test_uses_page_request_and_expands_information_menu(self):
        page = MagicMock()
        home = MagicMock()
        home.is_visible.return_value = False
        menu = MagicMock()
        page.get_by_text.side_effect = lambda text, exact: SimpleNamespace(first=home if text == "信息首页" else menu)
        response = SimpleNamespace(url="https://ebooking.ctrip.com/restapi/soa2/23942/getHotelInfoScoreItems")
        page.expect_response.return_value.__enter__.return_value.value.json.return_value = {
            "success": True, "scoreInfo": {"currentScore": 96},
        }
        with patch.object(self.module, "ensure_logged_in"), patch.object(self.module, "dismiss_overlays"):
            self.assertEqual(self.module.information_completeness_score(page), 96.0)
        page.get_by_text.assert_any_call("信息维护", exact=True)
        page.get_by_text.assert_any_call("信息首页", exact=True)
        self.assertTrue(page.expect_response.call_args.args[0](response))
        menu.click.assert_called_once()
        home.click.assert_called_once()

    def test_homepage_video_uses_information_menu(self):
        page = MagicMock()
        video_menu = MagicMock()
        video_menu.is_visible.return_value = False
        parent_menu = MagicMock()
        page.get_by_text.side_effect = lambda text, exact: SimpleNamespace(
            first=video_menu if text == "图片视频" else parent_menu,
        )
        title = page.locator.return_value
        title.count.return_value = 1
        title.first.inner_text.return_value = "主视频"
        title.first.locator.return_value.locator.return_value.count.return_value = 1
        with patch.object(self.module, "dismiss_overlays"):
            self.assertEqual(self.module.homepage_video_status(page), 1)
        parent_menu.click.assert_called_once()
        video_menu.click.assert_called_once()
        page.mouse.click.assert_called_once_with(*self.module.VIDEO_TAB_POINT)

    def test_promotion_rows_exclude_listing_pass(self):
        results = {key: (0, None) for key in (
            "points_alliance", "preferred_club", "business_travel", "travel_photo", "homepage_video",
        )}
        results.update(hourly_room=((0, 0), None), information=(96, None))
        rows = self.module.status_rows("hotel-1", datetime(2026, 9, 16), results)
        self.assertEqual(len(rows), 7)
        self.assertNotIn("listing_pass", [row[3] for row in rows])

if __name__ == "__main__":
    unittest.main()
