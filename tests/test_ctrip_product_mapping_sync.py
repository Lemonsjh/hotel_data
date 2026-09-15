from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "OTA采集服务"


def load_module():
    path = SERVICE_DIR / "mapping_product_sync.py"
    spec = importlib.util.spec_from_file_location("ctrip_product_sync_test_target", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load mapping_product_sync.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Cursor:
    def __init__(self):
        self.calls = []
        self.rowcount = 0

    def execute(self, sql, params):
        self.calls.append((sql, params))
        self.rowcount += 1


class CtripProductMappingSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_sync_copies_ctrip_product_fields(self):
        cursor = Cursor()

        result = self.module.sync_ctrip_products(cursor)

        self.assertEqual(result, {"updated": 1, "inserted_or_refreshed": 2, "deactivated": 3})
        self.assertEqual(len(cursor.calls), 3)
        insert_sql, insert_params = cursor.calls[1]
        self.assertIn("ctrip_ota_goods_price_mapping", insert_sql)
        self.assertIn("product_cipher", insert_sql)
        self.assertIn("price_editable_flag", insert_sql)
        self.assertIn("COALESCE(g.is_hour_room,0)", insert_sql)
        self.assertIn("'AUTO','ROOM_NAME'", insert_sql)
        self.assertEqual(insert_params, ("ctrip", "携程", "ctrip"))


if __name__ == "__main__":
    unittest.main()
