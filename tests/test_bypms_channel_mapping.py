from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "宝寓PMS数据采集代码" / "bypms_channel_mapping_data.py"


def load_target():
    os.environ.setdefault("HOTEL_ID", "test-hotel")
    spec = importlib.util.spec_from_file_location("test_bypms_channel_mapping_target", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_channel_relation_snapshot_does_not_guess_unified_room_type():
    target = load_target()
    rows = target.build_rows(
        "Ctrip",
        [{
            "channel": "Ctrip", "channelName": "携程", "channelUnitId": "456", "channelUnitName": "渠道商品",
            "state": "S", "relationType": "T", "relationList": [{"id": 12, "name": "宝寓大床房", "baseRelation": 1}],
        }],
        datetime(2026, 8, 20, 10),
    )

    assert rows[0]["channel_unit_id"] == "456"
    assert rows[0]["bypms_room_type_name"] == "宝寓大床房"
    assert rows[0]["is_base_relation"] == 1
    assert "room_type_id" not in rows[0]


class ChannelSnapshotTests(unittest.TestCase):
    def test_new_auto_mapping_enriches_existing_room_data(self):
        target = load_target()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(target, "HOTEL_ID", "test-hotel"), \
             patch.object(target, "COOKIE", "test-cookie"), \
             patch.object(target, "OUTPUT_DIR", Path(directory)), \
             patch.object(target.requests, "Session") as session, \
             patch.object(target, "fetch_channel_units", return_value=[]), \
             patch.object(target, "fetch_room_master", return_value={"typeVos": [{"id": 1, "name": "房型"}]}), \
             patch.object(target, "sync_mysql"), \
             patch.object(target.pymysql, "connect", return_value=MagicMock()), \
             patch.object(target.bypms_auto_mapping, "synchronize", return_value={
                 "aliases": 1, "bases": 0, "renames": 0, "products": 0, "conflicts": 0,
             }), \
             patch.object(target.room_type_enrichment, "enrich_tables", return_value={}) as enrich:
            self.assertEqual(target.main(), 0)
            enrich.assert_called_once()
            self.assertEqual(enrich.call_args.kwargs["hotel_id"], "test-hotel")

    def test_unchanged_auto_mapping_does_not_repeat_enrichment(self):
        target = load_target()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(target, "HOTEL_ID", "test-hotel"), \
             patch.object(target, "COOKIE", "test-cookie"), \
             patch.object(target, "OUTPUT_DIR", Path(directory)), \
             patch.object(target.requests, "Session"), \
             patch.object(target, "fetch_channel_units", return_value=[]), \
             patch.object(target, "fetch_room_master", return_value={"typeVos": [{"id": 1, "name": "房型"}]}), \
             patch.object(target, "sync_mysql"), \
             patch.object(target.pymysql, "connect", return_value=MagicMock()), \
             patch.object(target.bypms_auto_mapping, "synchronize", return_value={
                 "aliases": 0, "bases": 0, "renames": 0, "products": 0, "conflicts": 0,
             }), \
             patch.object(target.room_type_enrichment, "enrich_tables") as enrich:
            self.assertEqual(target.main(), 0)
            enrich.assert_not_called()

    def test_sync_replaces_all_previous_hotel_snapshots(self):
        target = load_target()

        class Cursor:
            def __init__(self):
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, params=()):
                self.calls.append((sql, params))

            def executemany(self, sql, _rows):
                self.calls.append((sql, ()))

        class Connection:
            def __init__(self):
                self.cursor_instance = Cursor()

            def cursor(self):
                return self.cursor_instance

            def commit(self):
                pass

            def rollback(self):
                pass

        connection = Connection()
        target.sync_mysql([], datetime(2026, 8, 20, 10), connection=connection)

        delete_sql, delete_params = connection.cursor_instance.calls[0]
        self.assertIn("DELETE FROM `bypms_channel_unit_mapping_snapshot` WHERE hotel_id=%s", delete_sql)
        self.assertEqual(delete_params, ("test-hotel",))
