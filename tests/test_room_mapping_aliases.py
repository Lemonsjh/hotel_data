from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "OTA采集服务"


def load_store_module():
    sys.path.insert(0, str(SERVICE_DIR))
    path = SERVICE_DIR / "room_mapping_store.py"
    spec = importlib.util.spec_from_file_location("room_mapping_store_test_target", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RoomMappingAliasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = load_store_module()

    def test_multiple_pms_aliases_are_preserved_and_deduplicated(self):
        data = {
            self.store.PMS_ALIASES_FIELD: ["豪华大床房A", "豪华大床房B", "豪华大床房A", ""],
        }
        self.assertEqual(
            self.store.pms_room_type_names(data),
            ["豪华大床房A", "豪华大床房B"],
        )

    def test_existing_aliases_are_kept_when_same_room_id_is_reused(self):
        self.assertEqual(
            self.store._merge_pms_names(
                {"豪华大床房A"},
                ["豪华大床房B"],
            ),
            ["豪华大床房A", "豪华大床房B"],
        )

    def test_validation_accepts_multiple_pms_aliases_without_legacy_single_value(self):
        data = {
            "hotel_id": "HT01",
            "pms_hotel_name": "测试酒店",
            "hotel_name": "测试酒店",
            "ctrip_hotel_name": "",
            "room_type_id": "RT01",
            "room_type_name": "统一大床房",
            "pms_room_type_name": "",
            self.store.PMS_ALIASES_FIELD: ["豪华大床房A", "豪华大床房B"],
            "meituan_room_type_name": "豪华大床房",
            "ctrip_room_type_name": "",
        }
        self.assertIsNone(self.store.validate(data))

    def test_legacy_single_pms_alias_is_still_supported(self):
        self.assertEqual(
            self.store.pms_room_type_names({"pms_room_type_name": "豪华双床房"}),
            ["豪华双床房"],
        )

    def test_active_pms_alias_namespace_follows_selected_provider(self):
        self.assertEqual(
            self.store.active_pms_platform({"pms": {"provider": "bypms"}}),
            self.store.PMS_BYPMS_PLATFORM,
        )
        self.assertEqual(
            self.store.active_pms_platform({"pms": {"provider": "byh"}}),
            self.store.PMS_BYH_PLATFORM,
        )

    def test_edit_replaces_old_ctrip_base_mapping_instead_of_keeping_it(self):
        calls = []

        class Cursor:
            def execute(self, sql, params):
                calls.append((sql, params))

        data = {
            "hotel_id": "HT01",
            "pms_hotel_name": "测试酒店",
            "hotel_name": "测试酒店",
            "ctrip_hotel_name": "测试酒店",
            "room_type_id": "RT01",
            "room_type_name": "统一双床房",
            "pms_room_type_name": "标准双床房",
            "meituan_room_type_name": "美团双床房",
            "ctrip_room_type_name": "携程双床房",
        }
        with patch.object(self.store, "_insert_base") as insert_base:
            self.store._replace_ota_base_rows(Cursor(), data, "HT01", "RT01")
        self.assertEqual(insert_base.call_count, 2)
        self.assertIn(
            (data, self.store.CTRIP_PLATFORM, "携程双床房"),
            [call.args[1:] for call in insert_base.call_args_list],
        )
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("mapping_status='REJECTED'" in sql for sql, _ in calls))


if __name__ == "__main__":
    unittest.main()
