import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "OTA采集服务"))
from bypms_auto_mapping import build_plan, migrate_legacy_ids


def snapshot(channel="Ctrip", unit="100", relation="217401", pms="PMS大床", name="渠道房型"):
    return {"channel": channel, "channel_unit_id": unit, "channel_unit_name": name,
            "relation_id": relation, "bypms_room_type_name": pms, "relation_count": 1}


def mapping(source, name, room_id="217401", product="", status="AUTO", active=1, remark=""):
    return {"source_platform": source, "source_room_type_name": name,
            "source_product_id": product, "room_type_id": room_id,
            "pms_room_type_name": "PMS大床", "mapping_status": status,
            "is_active": active, "remark": remark}


class AutoMappingTests(unittest.TestCase):
    def setUp(self):
        self.goods = {"ctrip": [{"ota_room_type_id": "100", "room_type_name": "携程房型",
                                 "ota_product_id": "888", "ota_product_name": "标准价"}], "meituan": []}

    def test_first_bootstrap_uses_room_id_then_goods_id(self):
        plan = build_plan([snapshot()], [], self.goods, {"217401": "PMS大床"})
        self.assertEqual(len(plan["aliases"]), 1)
        self.assertEqual(plan["aliases"][0][0], "217401")
        self.assertEqual(plan["bases"][0][3], "携程房型")
        self.assertEqual(plan["products"][0][4], "888")
        self.assertFalse(plan["conflicts"])

    def test_same_snapshot_is_noop(self):
        existing = [mapping("pms_bypms", "PMS大床", remark="BYPMS_AUTO_ROOM:217401"),
                    mapping("ctrip", "携程房型", remark="BYPMS_AUTO:Ctrip:100"),
                    mapping("携程", "携程房型", product="888")]
        plan = build_plan([snapshot()], existing, self.goods, {"217401": "PMS大床"})
        self.assertEqual({k: len(v) for k, v in plan.items()},
                         {"aliases": 0, "bases": 0, "renames": 0, "products": 0, "conflicts": 0})

    def test_disabled_mapping_is_not_overwritten(self):
        existing = [mapping("ctrip", "携程房型", active=0)]
        plan = build_plan([snapshot()], existing, self.goods, {"217401": "PMS大床"})
        self.assertEqual(len(plan["aliases"]), 0)
        self.assertEqual(len(plan["bases"]), 0)
        self.assertTrue(plan["conflicts"])

    def test_ambiguous_or_unverified_relation_is_skipped(self):
        item = snapshot()
        item["relation_count"] = 2
        self.assertEqual(len(build_plan([item], [], self.goods, {"217401": "PMS大床"})["bases"]), 0)
        self.assertEqual(len(build_plan([snapshot()], [], self.goods, {})["bases"]), 0)

    def test_existing_product_conflict_blocks_group(self):
        existing = [mapping("携程", "别的房型", room_id="OTHER", product="888")]
        plan = build_plan([snapshot()], existing, self.goods, {"217401": "PMS大床"})
        self.assertEqual(len(plan["aliases"]), 0)
        self.assertEqual(len(plan["products"]), 0)
        self.assertTrue(plan["conflicts"])

    def test_duplicate_master_name_is_not_guessed(self):
        plan = build_plan([snapshot()], [], self.goods,
                          {"217401": "PMS大床", "217499": "PMS大床"})
        self.assertEqual(len(plan["aliases"]), 0)
        self.assertEqual(plan["conflicts"][0][2], "duplicate_pms_name")

    def test_legacy_migration_updates_every_room_id_table(self):
        class Cursor:
            rowcount = 0
            rows = []

            def execute(self, sql, params=()):
                if "SELECT DISTINCT room_type_id,remark" in sql:
                    self.rows = [{"room_type_id": "BYPMS-217401", "remark": "BYPMS_AUTO_ROOM:217401"}]
                elif "SELECT DISTINCT room_type_id FROM" in sql:
                    self.rows = []
                elif "SELECT c.TABLE_NAME" in sql:
                    self.rows = [{"TABLE_NAME": "hotel_room_type_mapping"},
                                 {"TABLE_NAME": "jd01_booking_detail"}]
                else:
                    self.rowcount = 3
                    self.rows = []
                    self.last_params = params

            def fetchall(self):
                return self.rows

        cursor = Cursor()
        self.assertEqual(migrate_legacy_ids(cursor, "test-hotel"), (1, 6))
        self.assertEqual(cursor.last_params, ("test-hotel", "BYPMS-217401"))


if __name__ == "__main__":
    unittest.main()
