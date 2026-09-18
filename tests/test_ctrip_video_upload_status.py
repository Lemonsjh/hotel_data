from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
CTRIP_DIR = ROOT / "携程OTA数据采集代码"


def load_module():
    sys.path.insert(0, str(CTRIP_DIR))
    path = CTRIP_DIR / "ctrip_video_upload_status_data.py"
    spec = importlib.util.spec_from_file_location("ctrip_video_upload_test_target", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CtripVideoUploadStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_builds_detail_room_and_listing_video_rows(self):
        payloads = {
            self.module.DETAIL_VIDEO_PATH: {"code": 200, "data": {"mainVideo": {"id": 1}}},
            self.module.LIST_VIDEO_PATH: {"code": 200, "data": {"current": None, "others": [{"id": 2}]}},
            self.module.ROOM_TYPES_PATH: {"code": 200, "data": [{}, {}, {}]},
            self.module.ROOM_VIDEOS_PATH: {
                "code": 200,
                "data": [{"current": {"roomVideoId": 1}}, {"current": None}, {"current": {"roomVideoId": 2}}],
            },
        }
        self.assertEqual(self.module.video_rows_from_payloads(payloads), [
            ("hotel_detail_video", 1, 1),
            ("room_type_video", 2, 3),
            ("hotel_listing_video", 0, 1),
        ])

    def test_rejects_missing_or_invalid_required_response(self):
        with self.assertRaises(RuntimeError):
            self.module.video_rows_from_payloads({})

    def test_upsert_uses_one_snapshot_time_for_all_video_types(self):
        cursor = MagicMock()
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        rows = [("hotel_detail_video", 1, 1), ("room_type_video", 2, 3), ("hotel_listing_video", 0, 1)]
        with patch("pymysql.connect", return_value=connection):
            self.module.save_video_counts("hotel-1", rows)

        inserted = cursor.executemany.call_args.args[1]
        self.assertEqual([row[4] for row in inserted], ["COMPLETE", "INCOMPLETE", "INCOMPLETE"])
        self.assertEqual(len({row[5] for row in inserted}), 1)
        connection.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
