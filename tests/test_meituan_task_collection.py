from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
MEITUAN_DIR = ROOT / "美团OTA数据采集代码"


def load_module(filename: str, module_name: str):
    sys.path.insert(0, str(MEITUAN_DIR))
    spec = importlib.util.spec_from_file_location(module_name, MEITUAN_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class MeituanTaskCollectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scan = load_module("meituan_scan_order_data.py", "meituan_scan_order_test_target")
        cls.video = load_module("meituan_video_upload_status_data.py", "meituan_video_upload_test_target")

    def test_scan_order_accepts_successful_empty_data(self):
        response = _Response({"status": 0, "data": None})
        with patch.object(self.scan, "MEITUAN_EB_COOKIE", "test-cookie"), patch.object(
            self.scan.requests, "get", return_value=response
        ):
            data = self.scan.request_page(date(2026, 8, 1), date(2026, 8, 2), 1)
        self.assertEqual(data, {})

    def test_scan_order_failure_includes_platform_message(self):
        response = _Response({"status": 303, "msg": "登录状态失效", "data": None})
        with patch.object(self.scan, "MEITUAN_EB_COOKIE", "test-cookie"), patch.object(
            self.scan.requests, "get", return_value=response
        ):
            with self.assertRaisesRegex(RuntimeError, "status=303; message=登录状态失效"):
                self.scan.request_page(date(2026, 8, 1), date(2026, 8, 2), 1)

    def test_video_rows_are_read_from_task_api_payload(self):
        payload = {
            "status": 0,
            "data": {
                "hasHotelOfficial": 1,
                "hasHotelOfficialPreview": 0,
                "hasRoomVideoOnlineRealRoomNum": 7,
                "onlineRealRoomNum": 10,
            },
        }
        self.assertEqual(
            self.video.video_rows_from_payload(payload),
            [
                ("hotel_official_video", 1, 1),
                ("hotel_official_preview_video", 0, 1),
                ("room_type_video", 7, 10),
            ],
        )

    def test_video_task_response_matches_only_get_endpoint(self):
        response = SimpleNamespace(
            url="https://tdc.meituan.com/gw/tdc/hubble/eb/hotel/video/poi/video/task?poiId=1879794992&mtgsig=signed",
            request=SimpleNamespace(method="GET"),
        )
        self.assertTrue(self.video.is_video_task_response(response))
        response.request.method = "POST"
        self.assertFalse(self.video.is_video_task_response(response))

    def test_video_task_payload_rejects_failed_or_missing_data(self):
        with self.assertRaises(RuntimeError):
            self.video.video_rows_from_payload({"status": 1, "data": {}})
        with self.assertRaisesRegex(RuntimeError, "hasHotelOfficialPreview"):
            self.video.video_rows_from_payload(
                {
                    "status": 0,
                    "data": {
                        "hasHotelOfficial": 1,
                        "hasRoomVideoOnlineRealRoomNum": 7,
                        "onlineRealRoomNum": 10,
                    },
                }
            )

    def test_video_save_updates_all_rows_with_one_snapshot_time(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        rows = [("hotel_official_video", 1, 1), ("room_type_video", 7, 10)]
        with patch.dict(sys.modules, {"pymysql": SimpleNamespace(connect=lambda **_kwargs: connection)}):
            self.video.save_video_counts("hotel-1", rows)
        _query, values = cursor.executemany.call_args.args
        self.assertEqual([value[-1] for value in values][0], [value[-1] for value in values][1])
        self.assertIn("snapshot_time", cursor.executemany.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
