from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


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

    def test_combined_provider_reads_room_types_from_the_merged_forecast(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"name": "溪眠舒适双床房"}]
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        settings = {"pms": {"provider": "meituan_rezen"}, "hotel": {"hotel_id": "h1"}, "ctrip": {}}
        with patch.object(self.store.price_tasks, "connection", return_value=connection), \
             patch.object(self.store.runner, "internal_hotel_id", return_value="h1"):
            _hotels, rooms, _meituan, _ctrip = self.store.room_options(settings)
        self.assertEqual(rooms, ["溪眠舒适双床房"])
        _sql, params = cursor.execute.call_args_list[1].args
        self.assertEqual(params, ("PMS（美团+Rezen）", "h1"))
        self.assertEqual(self.store.active_pms_platform(settings), self.store.PMS_MEITUAN_PLATFORM)
        single = {"pms": {"provider": "meituan_pms"}}
        self.assertEqual(self.store.forecast_source_label(single), "PMS（美团）")

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

    def test_combined_provider_drops_joined_forecast_names(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            {"name": "标准大床房 / 雅致大床房"},
            {"name": "标准大床房"},
        ]
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        settings = {"pms": {"provider": "meituan_rezen"}, "hotel": {"hotel_id": "h1"}, "ctrip": {}}
        with patch.object(self.store.price_tasks, "connection", return_value=connection), \
             patch.object(self.store.runner, "internal_hotel_id", return_value="h1"):
            _hotels, rooms, _meituan, _ctrip = self.store.room_options(settings)
        self.assertEqual(rooms, ["标准大床房"])

    def test_side_options_keep_original_names_and_skip_the_joined_label(self):
        cursor = MagicMock()
        cursor.fetchall.side_effect = [
            [
                {"source_platform": "pms_meituan", "name": "标准大床房"},
                {"source_platform": "pms_rezen", "name": "雅致大床房"},
            ],
            [
                {"name": "标准大床房 / 雅致大床房", "raw_id": "U01"},
                {"name": "未映射大床房", "raw_id": "mt:9"},
                {"name": "观岚行政套房", "raw_id": "rz:GL"},
                {"name": "同名大床房", "raw_id": "同名大床房"},
            ],
        ]
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        settings = {"pms": {"provider": "meituan_rezen"}, "hotel": {"hotel_id": "h1"}}
        with patch.object(self.store, "_observed_room_types", return_value=(["山语亲子房"], ["雅致大床房"])), \
             patch.object(self.store.price_tasks, "connection", return_value=connection):
            meituan, rezen = self.store.side_room_options(settings)
        self.assertEqual(meituan, ["同名大床房", "山语亲子房", "未映射大床房", "标准大床房"])
        self.assertEqual(rezen, ["同名大床房", "观岚行政套房", "雅致大床房"])
        self.assertIsNone(self.store.side_room_options({"pms": {"provider": "meituan_pms"}}))

    def test_dual_save_writes_each_side_to_its_own_platform(self):
        settings = {"pms": {"provider": "meituan_rezen"}}
        data = {
            "hotel_id": "h1", "pms_hotel_name": "测试酒店", "hotel_name": "测试酒店",
            "ctrip_hotel_name": "", "room_type_id": "U01", "room_type_name": "大床房",
            "pms_room_type_name": "", "meituan_room_type_name": "大床房", "ctrip_room_type_name": "",
            "dual_pms_aliases": True,
            "meituan_pms_room_type_names": ["标准大床房"],
            "rezen_pms_room_type_names": ["雅致大床房"],
        }
        aliases = {platform: set() for platform in self.store.BASE_PLATFORMS}
        cursor = self._cursor()
        with patch.object(self.store.price_tasks, "connection", return_value=self._connection(cursor)), \
             patch.object(self.store, "_collect_aliases", return_value=aliases), \
             patch.object(self.store, "_assert_no_conflicts"), \
             patch.object(self.store, "_replace_ota_base_rows"), \
             patch.object(self.store, "_sync_product_rows"), \
             patch.object(self.store, "_sync_pms_alias_rows") as sync:
            change = self.store.save_group(settings, data, "h1", "U01")
        self.assertEqual(
            [call.args[4:] for call in sync.call_args_list],
            [(["标准大床房"], self.store.PMS_MEITUAN_PLATFORM), (["雅致大床房"], self.store.PMS_REZEN_PLATFORM)],
        )
        self.assertEqual(change["aliases"][self.store.PMS_MEITUAN_PLATFORM], {"标准大床房"})
        self.assertEqual(change["aliases"][self.store.PMS_REZEN_PLATFORM], {"雅致大床房"})

    def test_joined_label_is_not_saved_as_one_alias(self):
        data = {
            "hotel_id": "h1", "pms_hotel_name": "测试酒店", "hotel_name": "测试酒店",
            "room_type_id": "U01", "room_type_name": "大床房", "meituan_room_type_name": "大床房",
            "ctrip_room_type_name": "", "dual_pms_aliases": True,
            "meituan_pms_room_type_names": ["标准大床房 / 雅致大床房"],
            "rezen_pms_room_type_names": [],
        }
        with self.assertRaisesRegex(ValueError, "合并后的名称"):
            self.store.save_group({"pms": {"provider": "meituan_rezen"}}, data, "h1", "U01")

    def test_clearing_one_side_does_not_build_an_empty_in_list(self):
        cursor = self._cursor()
        data = {"hotel_id": "h1", "room_type_id": "U01", "room_type_name": "大床房", "pms_hotel_name": "测试酒店"}
        self.store._sync_pms_alias_rows(cursor, data, "h1", "U01", [], self.store.PMS_REZEN_PLATFORM)
        self.assertTrue(any("REJECTED" in sql for sql, _params in cursor.calls))
        self.assertFalse(any("NOT IN ()" in sql for sql, _params in cursor.calls))

    def test_dual_form_edits_the_two_sources_separately(self):
        import room_mappings

        html = room_mappings._render_form(
            {
                "hotel_id": "h1", "room_type_id": "U01",
                "meituan_pms_room_type_names": ["标准大床房"],
                "rezen_pms_room_type_names": ["雅致大床房"],
            },
            ["标准大床房 / 雅致大床房"], [], [], True,
            sides=(["标准大床房"], ["雅致大床房"]),
        )
        self.assertIn("name='meituan_pms_room_type_names'", html)
        self.assertIn("name='rezen_pms_room_type_names'", html)
        self.assertIn("value='标准大床房' selected", html)
        self.assertIn("value='雅致大床房' selected", html)
        self.assertNotIn("标准大床房 / 雅致大床房", html)
        self.assertNotIn("name='pms_room_type_name'", html)
        self.assertIn("美团 PMS：标准大床房", room_mappings._pms_alias_html(
            {"pms_room_type_name": "标准大床房", "pms_side_label": "美团 PMS"}
        ))

    def test_combined_history_matches_either_pms_alias(self):
        import room_type_enrichment

        class SqlCursor:
            rowcount = 0

            def execute(self, sql, params=None):
                self.sql = sql

        cursor = SqlCursor()
        room_type_enrichment._update_active_pms_alias(cursor, "rs01_room_revenue_daily", "")
        self.assertIn("m.source_platform IN ('pms_meituan','pms_rezen')", cursor.sql)
        self.assertIn("COUNT(DISTINCT d.room_type_id)", cursor.sql)
        self.assertIn("t.`room_type_name`", cursor.sql)

    def _cursor(self):
        class Cursor:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params=None):
                self.calls.append((sql, params))

            def fetchall(self):
                return []

            def fetchone(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        return Cursor()

    def _connection(self, cursor):
        class Connection:
            def cursor(self):
                return cursor

            def commit(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        return Connection()


if __name__ == "__main__":
    unittest.main()
