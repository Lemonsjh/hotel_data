from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "宝寓PMS数据采集代码" / "bypms_channel_mapping_data.py"


def load_module():
    os.environ.setdefault("HOTEL_ID", "test-hotel")
    spec = importlib.util.spec_from_file_location("bypms_channel_mapping_test_target", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load ByPMS channel mapping module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BypmsChannelMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.target = load_module()

    def test_build_rows_keeps_channel_relation_snapshot_without_auto_mapping(self):
        rows = self.target.build_rows(
            "Ctrip",
            [{
                "channel": "Ctrip", "channelName": "携程", "channelUnitId": "456", "channelUnitName": "渠道商品",
                "state": "S", "relationType": "T", "relationList": [{"id": 12, "name": "宝寓大床房", "baseRelation": 1}],
            }],
            datetime(2026, 8, 20, 10, 26, 30),
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["channel_unit_id"], "456")
        self.assertEqual(rows[0]["bypms_room_type_name"], "宝寓大床房")
        self.assertEqual(rows[0]["is_base_relation"], 1)
        self.assertEqual(rows[0]["relation_count"], 1)
        self.assertNotIn("room_type_id", rows[0])

    def test_build_rows_retains_unrelated_channel_units_for_snapshot_completeness(self):
        rows = self.target.build_rows("Dylife", [{"channelUnitId": "789", "channelUnitName": "抖音商品"}], datetime(2026, 8, 20, 10))

        self.assertEqual(rows[0]["relation_id"], "")
        self.assertEqual(rows[0]["relation_count"], 0)
        self.assertEqual(rows[0]["channel"], "Dylife")

    def test_fetch_channel_units_uses_get_parameters(self):
        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"state": 0, "data": {"channelUnits": []}}

        class Session:
            def __init__(self):
                self.args = None

            def get(self, *args, **kwargs):
                self.args = (args, kwargs)
                return Response()

        session = Session()
        old_cookie = self.target.COOKIE
        self.target.COOKIE = "test-cookie"
        try:
            self.assertEqual(self.target.fetch_channel_units("Meituan", session), [])
        finally:
            self.target.COOKIE = old_cookie
        self.assertEqual(session.args[0], (self.target.MAPPING_URL,))
        self.assertEqual(session.args[1]["params"], {"channel": "Meituan", "countLimit": 0})


if __name__ == "__main__":
    unittest.main()
