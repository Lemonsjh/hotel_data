"""多个西软业务库汇总到现有 MySQL 写入；不连接真实数据库。"""
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "OTA采集服务/foxhis_pms"), str(ROOT / "OTA采集服务")]

import foxhis_accounts
import foxhis_common as common
import foxhis_multi as multi
import panel
import runner
import settings_validation
from process_runner import ProcessResult
from config_schema import CONFIG_SECTIONS
from foxhis_config import Config


def account(database, **extra):
    item = {"database": database, "username": "foxsyb", "password": f"password-{database}"}
    item.update(extra)
    return item


class AccountValidationTests(unittest.TestCase):
    def test_partial_duplicate_and_blank_slots(self):
        self.assertTrue(any("一部分" in item for item in foxhis_accounts.validate_accounts([
            {"database": "foxhis", "username": "foxsyb"},
        ])))
        self.assertTrue(any("重复" in item for item in foxhis_accounts.validate_accounts([
            account("foxhis"), account("foxhis"),
        ])))
        self.assertEqual(foxhis_accounts.validate_accounts([
            account("foxhis"), {}, account("foxhis1"),
        ]), [])
        self.assertTrue(foxhis_accounts.validate_accounts([account(f"db{i}") for i in range(9)]))

    def test_legacy_login_is_one_database_until_a_list_is_saved(self):
        legacy = {"database": "foxhis1", "username": "foxsyb", "password": "password-foxhis1"}
        self.assertEqual(foxhis_accounts.complete_accounts(legacy), [legacy])
        self.assertEqual(foxhis_accounts.complete_accounts({**legacy, "accounts": []}), [])

    def test_form_keeps_password_rejects_partial_and_mirrors_the_first_database(self):
        settings = {"foxhis": {"database": "foxhis1", "username": "foxsyb", "password": "password-old", "server": "FoxFront"}}
        kept = {
            "foxhis_account_0_database": "foxhis1",
            "foxhis_account_0_username": "foxsyb",
            "foxhis_account_0_password": "",
            "foxhis_account_1_database": "foxhis2",
            "foxhis_account_1_username": "foxsyb",
            "foxhis_account_1_password": "password-new",
            "foxhis_account_1_server": "OtherFront",
        }
        self.assertEqual(foxhis_accounts.apply_foxhis_accounts(settings, kept), "")
        self.assertEqual(settings["foxhis"]["accounts"][0]["password"], "password-old")
        self.assertEqual(settings["foxhis"]["accounts"][1]["server"], "OtherFront")
        self.assertEqual(settings["foxhis"]["database"], "foxhis1")
        self.assertNotIn("server", settings["foxhis"]["accounts"][0])

        rejected = dict(settings)
        rejected_foxhis = dict(settings["foxhis"])
        rejected["foxhis"] = rejected_foxhis
        before = json.loads(json.dumps(rejected))
        self.assertIn("一部分", foxhis_accounts.apply_foxhis_accounts(rejected, {
            "foxhis_account_0_database": "foxhis",
            "foxhis_account_0_username": "",
            "foxhis_account_0_password": "",
        }))
        self.assertEqual(rejected, before)
        self.assertIn("重复", foxhis_accounts.apply_foxhis_accounts(rejected, {
            "foxhis_account_0_database": "foxhis",
            "foxhis_account_0_username": "foxsyb",
            "foxhis_account_0_password": "password-a",
            "foxhis_account_1_database": "foxhis",
            "foxhis_account_1_username": "foxsyb",
            "foxhis_account_1_password": "password-b",
        }))
        self.assertEqual(rejected, before)

    def test_missing_form_fields_do_not_clear_an_existing_database(self):
        settings = {"foxhis": {"database": "foxhis1", "username": "foxsyb", "password": "password-old"}}
        self.assertEqual(foxhis_accounts.apply_foxhis_accounts(settings, {}), "")
        self.assertEqual(settings["foxhis"]["database"], "foxhis1")
        self.assertNotIn("accounts", settings["foxhis"])

    def test_saved_accounts_are_validated_with_the_rest_of_the_settings(self):
        settings = json.loads((ROOT / "OTA采集服务/config/settings.example.json").read_text(encoding="utf-8-sig"))
        self.assertFalse(settings_validation.validate_settings(settings))
        settings["foxhis"]["accounts"] = [account("foxhis"), account("foxhis")]
        errors = settings_validation.validate_settings(settings)
        self.assertTrue(any("重复" in item for item in errors))

    def test_config_page_puts_the_summary_name_above_database_slots(self):
        settings = json.loads((ROOT / "OTA采集服务/config/settings.example.json").read_text(encoding="utf-8-sig"))
        settings["foxhis"].update(database="foxhis1", username="foxsyb", password="password-secret")
        section = next(item for item in CONFIG_SECTIONS if item["key"] == "foxhis")
        html = panel.config_section(settings, section)
        self.assertLess(html.index("汇总酒店名称"), html.index("foxhis_account_0_database"))
        self.assertIn("foxhis1", html)
        self.assertIn("foxsyb", html)
        self.assertNotIn("password-secret", html)
        self.assertNotIn("name='foxhis.database'", html)
        self.assertNotIn("name='foxhis.username'", html)
        self.assertNotIn("name='foxhis.password'", html)
        self.assertIn("添加数据库", html)
        self.assertLess(html.index("添加数据库"), html.index("foxhis_account_0_database"))
        self.assertIn("高级设置（可选）", html)
        self.assertIn("aria-live='polite'>1/8", html)
        self.assertIn("name='foxhis_account_0_hotel_name'", html)
        self.assertIn("仅在此对照显示", html)
        self.assertIn("移除", html)

    def test_slot_hotel_name_is_only_stored_for_display_and_a_slot_can_be_removed(self):
        settings = {"foxhis": {"hotel_name": "汇总", "database": "foxhis1", "username": "foxsyb", "password": "password-old"}}
        self.assertEqual(foxhis_accounts.apply_foxhis_accounts(settings, {
            "foxhis_account_0_hotel_name": "黔蓬",
            "foxhis_account_0_database": "foxhis1",
            "foxhis_account_0_username": "foxsyb",
            "foxhis_account_0_password": "",
            "foxhis_account_1_hotel_name": "黔硕",
            "foxhis_account_1_database": "foxhis2",
            "foxhis_account_1_username": "foxsyb",
            "foxhis_account_1_password": "password-new",
        }), "")
        self.assertEqual(settings["foxhis"]["hotel_name"], "汇总")
        self.assertEqual(settings["foxhis"]["accounts"][0]["hotel_name"], "黔蓬")
        self.assertEqual(settings["foxhis"]["accounts"][1]["hotel_name"], "黔硕")

        self.assertEqual(foxhis_accounts.apply_foxhis_accounts(settings, {
            "foxhis_account_0_hotel_name": "",
            "foxhis_account_0_database": "",
            "foxhis_account_0_username": "",
            "foxhis_account_0_password": "",
            "foxhis_account_1_hotel_name": "黔硕",
            "foxhis_account_1_database": "foxhis2",
            "foxhis_account_1_username": "foxsyb",
            "foxhis_account_1_password": "",
        }), "")
        self.assertEqual([item["database"] for item in settings["foxhis"]["accounts"]], ["foxhis2"])
        self.assertEqual(settings["foxhis"]["accounts"][0]["hotel_name"], "黔硕")
        self.assertEqual(settings["foxhis"]["hotel_name"], "汇总")
        self.assertEqual(settings["foxhis"]["database"], "foxhis2")
        self.assertIn("一部分", foxhis_accounts.apply_foxhis_accounts({"foxhis": {"hotel_name": "汇总"}}, {
            "foxhis_account_0_hotel_name": "只有名称",
            "foxhis_account_0_database": "",
            "foxhis_account_0_username": "",
            "foxhis_account_0_password": "",
        }))


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(hotel_id="bailing", hotel_name="贵阳百灵")
        self.stamp = datetime(2026, 11, 9, 12, 0)

    def test_slot_display_name_does_not_replace_the_written_hotel_name(self):
        config = multi.config_for_account(self.config, account("foxhis1", hotel_name="黔蓬"))
        self.assertEqual(config.hotel_name, "贵阳百灵")
        self.assertEqual(config.database, "foxhis1")

    def test_daily_totals_add_amounts_and_recalculate_rates(self):
        day = date(2026, 11, 8)

        def row(count, nights, revenue, sold, remaining):
            return {"business_date": day, "room_count": count, "room_nights": Decimal(nights),
                    "room_revenue": Decimal(revenue), "sold_rooms": sold, "remaining_rooms": remaining}

        merged = multi.sum_jy01([
            [row(63, 30, 3000, 20, 40)],
            [row(61, 20, 1000, 10, 30)],
        ], self.config, self.stamp)
        self.assertEqual(merged[0]["room_count"], 124)
        self.assertEqual(merged[0]["room_nights"], Decimal(50))
        self.assertEqual(merged[0]["room_revenue"], Decimal("4000.00"))
        self.assertEqual(merged[0]["occupancy_rate"], Decimal("50.00"))
        self.assertEqual(merged[0]["adr"], Decimal("80.00"))
        self.assertEqual(merged[0]["revpar"], Decimal("32.26"))
        with self.assertRaises(common.FoxhisError):
            multi.sum_jy01([[row(63, 30, 3000, 20, 40)], []], self.config, self.stamp)

    def test_monthly_totals_use_added_capacity_not_averaged_rates(self):
        def part(nights, revenue, capacity, maintain, available):
            return {"room_nights": Decimal(nights), "net_room_revenue": Decimal(revenue),
                    "room_days": Decimal(capacity), "maintain_room_days": Decimal(maintain),
                    "available_room_days": Decimal(available)}

        rows = multi.sum_jy03([
            {"2026-10": part(100, 2000, 300, 10, 280)},
            {"2026-10": part(50, 500, 200, 5, 180)},
        ], self.config, self.stamp)
        self.assertEqual(rows[0]["room_count"], 500)
        self.assertEqual(rows[0]["room_nights"], Decimal(150))
        self.assertEqual(rows[0]["room_revenue"], Decimal("2500.00"))
        self.assertEqual(rows[0]["maintain_rooms"], 15)
        self.assertEqual(rows[0]["occupancy_rate"], Decimal("32.61"))
        self.assertEqual(rows[0]["adr"], Decimal("16.67"))
        self.assertEqual(rows[0]["revpar"], Decimal("5.00"))
        with self.assertRaises(common.FoxhisError):
            multi.sum_jy03([
                {"2026-10": part(100, 2000, 300, 10, 280)},
                {"2026-09": part(50, 500, 200, 5, 180)},
            ], self.config, self.stamp)
        partial = multi.sum_jy03([
            {"2026-10": part(100, 2000, 300, 10, 280), "2025-09": part(1, 1, 1, 0, 1)},
            {"2026-10": part(50, 500, 200, 5, 180)},
        ], self.config, self.stamp)
        self.assertEqual([row["period_month"] for row in partial], ["2026-10"])

    def test_same_room_type_name_without_rooms_is_added(self):
        period = {"snapshot_date": date(2026, 11, 10), "period_start": date(2026, 10, 11),
                  "period_end": date(2026, 11, 9), "section": "summary", "source_platform": "PMS（Foxhis）",
                  "dimension_code": "", "dimension_name": ""}
        named = {**period, "room_type_name": "云居城景投影房", "room_count": Decimal(330),
                 "room_nights": Decimal(170), "room_revenue": Decimal("22635.72"), "available_rooms": Decimal(288)}
        nameless_rooms = {**period, "room_type_name": "云居城景投影房", "room_count": Decimal(2),
                          "room_nights": Decimal(0), "room_revenue": Decimal(0), "available_rooms": Decimal(0)}
        other = {**period, "room_type_name": "悦享清新圆床房", "room_count": Decimal(600),
                 "room_nights": Decimal(100), "room_revenue": Decimal("1000.00"), "available_rooms": Decimal(400)}
        merged = multi.sum_jl11([[named, other], [nameless_rooms]], self.config, self.stamp)
        by_name = {row["room_type_name"]: row for row in merged}
        combined = by_name["云居城景投影房"]
        self.assertEqual(combined["room_count"], Decimal(332))
        self.assertEqual(combined["room_nights"], Decimal(170))
        self.assertEqual(combined["room_revenue"], Decimal("22635.72"))
        self.assertEqual(combined["occupancy_rate"], Decimal("59.0278"))
        self.assertEqual(combined["average_room_price"], Decimal("133.15"))
        self.assertEqual(combined["revpar"], Decimal("68.18"))
        self.assertEqual(combined["hotel_name"], "贵阳百灵")
        self.assertNotIn("available_rooms", combined)
        self.assertEqual(by_name["悦享清新圆床房"]["room_count"], Decimal(600))
        self.assertNotIn("available_rooms", by_name["悦享清新圆床房"])

    def test_jl01_same_name_on_one_day_is_added_and_different_codes_are_not_kept(self):
        day = date(2026, 11, 9)

        def row(name, code, nights, revenue, available, total):
            return {"business_date": day, "room_type_name": name, "pms_rate_room_type_id": code,
                    "room_nights": Decimal(nights), "room_revenue": Decimal(revenue),
                    "available_rooms": Decimal(available), "total_rooms": Decimal(total),
                    "source_platform": "PMS（Foxhis）"}

        merged = multi.sum_jl01([
            [row("云居城景投影房", "HT", 10, "1000", 20, 30), row("圆床房", "RR", 1, "100", 2, 3)],
            [row("云居城景投影房", "HT", 0, "0", 0, 2)],
        ], self.config, self.stamp)
        combined = next(item for item in merged if item["room_type_name"] == "云居城景投影房")
        self.assertEqual(combined["pms_rate_room_type_id"], "HT")
        self.assertEqual(combined["room_nights"], Decimal(10))
        self.assertEqual(combined["occupancy_rate"], Decimal("50.0000"))
        self.assertEqual(combined["revpar"], Decimal("31.25"))
        self.assertNotIn("total_rooms", combined)
        different = multi.sum_jl01([
            [row("投影房", "HT", 1, "10", 2, 2)],
            [row("投影房", "XX", 1, "10", 2, 2)],
        ], self.config, self.stamp)
        self.assertEqual(different[0]["pms_rate_room_type_id"], "")
        self.assertEqual(different[0]["room_nights"], Decimal(2))

    def test_jl01_keeps_only_days_present_in_every_database(self):
        shared_day = date(2026, 11, 9)
        only_a = date(2026, 11, 8)

        def row(name, day):
            return {"business_date": day, "room_type_name": name, "pms_rate_room_type_id": "HT",
                    "room_nights": Decimal(1), "room_revenue": Decimal(10),
                    "available_rooms": Decimal(2), "total_rooms": Decimal(2),
                    "source_platform": "PMS（Foxhis）"}

        merged = multi.sum_jl01([
            [row("大床", shared_day), row("仅A", only_a)],
            [row("套房", shared_day)],
        ], self.config, self.stamp)
        self.assertEqual({item["business_date"] for item in merged}, {shared_day})
        self.assertEqual({item["room_type_name"] for item in merged}, {"大床", "套房"})
        with self.assertRaisesRegex(common.FoxhisError, "共同营业日"):
            multi.sum_jl01([[row("大床", only_a)], [row("套房", shared_day)]], self.config, self.stamp)

        day = date(2026, 11, 9)
        merged_from_components = multi._merge("jl01", [
            {"rows": [{"business_date": day, "room_type_name": "云居城景投影房", "pms_rate_room_type_id": "HT",
                       "room_nights": Decimal(10), "room_revenue": Decimal(1000), "source_platform": "PMS（Foxhis）"}],
             "components": {"2026-11-09|云居城景投影房": {"available_rooms": Decimal(20), "total_rooms": Decimal(30)}}},
            {"rows": [{"business_date": day, "room_type_name": "云居城景投影房", "pms_rate_room_type_id": "HT",
                       "room_nights": Decimal(0), "room_revenue": Decimal(0), "source_platform": "PMS（Foxhis）"}],
             "components": {"2026-11-09|云居城景投影房": {"available_rooms": Decimal(0), "total_rooms": Decimal(2)}}},
        ], self.config, self.stamp)
        self.assertEqual(merged_from_components[0]["revpar"], Decimal("31.25"))
        self.assertNotIn("available_rooms", merged_from_components[0])

    def test_cached_dates_and_amounts_round_trip(self):
        original = {"business_date": date(2026, 11, 8), "room_revenue": Decimal("1.50"),
                    "snapshot_time": datetime(2026, 11, 9, 4, 0), "nested": [Decimal("2")]}
        self.assertEqual(multi.thaw(multi.freeze(original)), original)


class MultiRunTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.output = self.stack.enter_context(tempfile.TemporaryDirectory(prefix="foxhis_multi_", dir=ROOT))
        self.clock = self.stack.enter_context(patch.object(common, "datetime", wraps=datetime))
        self.clock.now.return_value = datetime(2026, 11, 9, 12, 0)
        self.stack.enter_context(patch.object(common, "check_reachable", return_value=(True, "ok")))
        self.stack.enter_context(patch.object(sys, "argv", ["collector"]))
        self.env = {
            "FOXHIS_SERVER": "FoxFront",
            "FOXHIS_DATABASE": "foxhis",
            "FOXHIS_USERNAME": "foxsyb",
            "FOXHIS_PASSWORD": "password-foxhis",
            "FOXHIS_HOTEL_NAME": "贵阳百灵",
            "HOTEL_ID": "bailing",
            "FOXHIS_FETCH_AFTER": "",
            "HOTEL_OTA_OUTPUT_DIR": self.output,
            "HOTEL_OTA_MYSQL_HOST": "127.0.0.1",
            "HOTEL_OTA_MYSQL_DATABASE": "hotel_bailing",
        }
        self.stack.enter_context(patch.dict(os.environ, self.env))
        self.stdout = self.stack.enter_context(redirect_stdout(StringIO()))

    def run_report(self, report_key, collect, accounts, sync=None, *, realtime=False):
        return multi.run_multi(report_key, collect, accounts, sync=sync or Mock(return_value=1), realtime=realtime)

    def test_one_failure_keeps_the_previous_rows_and_remembers_the_success(self):
        def collect(config, _day, _stamp, *, write=True):
            self.assertFalse(write)
            if config.database == "foxhis1":
                raise common.FoxhisError("查询失败")
            return {"rows": [{"room_type_name": "大床", "business_date": date(2026, 11, 8)}]}

        sync = Mock(return_value=1)
        with self.assertRaisesRegex(common.FoxhisError, "不写入"):
            self.run_report("jl01", collect, [account("foxhis"), account("foxhis1")], sync)
        sync.assert_not_called()
        state = json.loads((Path(self.output) / common.DAILY_STATE_FILE).read_text(encoding="utf-8"))
        self.assertIn("foxhis", state["multi"]["jl01"])
        self.assertNotIn("foxhis1", state["multi"]["jl01"])
        self.assertNotIn("jl01", state)

    def test_both_databases_are_queried_separately_and_written_once(self):
        seen = []

        def collect(config, _day, _stamp, *, write=True):
            self.assertFalse(write)
            self.assertEqual(config.password, f"password-{config.database}")
            seen.append((config.database, config.server))
            code = "A" if config.database == "foxhis" else "B"
            return {"rows": [{"pms_room_type_id": code}]}

        second = account("foxhis1", server="OtherFront")
        sync = Mock(return_value=2)
        self.assertEqual(self.run_report("room_status", collect, [account("foxhis"), second], sync, realtime=True), 0)
        self.assertEqual(seen, [("foxhis", "FoxFront"), ("foxhis1", "OtherFront")])
        rows = sync.call_args.args[0]
        self.assertEqual([row["pms_room_type_id"] for row in rows], ["A", "B"])
        self.assertEqual(sync.call_args.args[1].hotel_name, "贵阳百灵")
        sync.assert_called_once()

    def test_shared_room_type_name_is_written_once(self):
        period = {"snapshot_date": date(2026, 11, 9), "period_start": date(2026, 10, 10),
                  "period_end": date(2026, 11, 8), "section": "summary", "source_platform": "PMS（Foxhis）",
                  "dimension_code": "", "dimension_name": ""}

        def collect(config, _day, _stamp, *, write=True):
            self.assertFalse(write)
            count = Decimal(10) if config.database == "foxhis" else Decimal(2)
            nights = Decimal(4) if config.database == "foxhis" else Decimal(0)
            available = count if config.database == "foxhis" else Decimal(0)
            return {"rows": [{**period, "room_type_name": "云居城景投影房", "room_count": count,
                              "room_nights": nights, "room_revenue": Decimal(count * 40)}],
                    "components": {"云居城景投影房": {"available_rooms": available}}}

        sync = Mock(return_value=1)
        self.run_report("jl11", collect, [account("foxhis"), account("foxhis1")], sync)
        rows = sync.call_args.args[0]
        self.assertEqual([row["room_type_name"] for row in rows], ["云居城景投影房"])
        self.assertEqual(rows[0]["room_count"], Decimal(12))
        self.assertEqual(rows[0]["room_nights"], Decimal(4))
        self.assertEqual(rows[0]["hotel_name"], "贵阳百灵")

    def test_overlap_and_blank_name_do_not_write(self):
        collect = Mock(return_value={"rows": [{"pms_room_type_id": "A"}]})
        sync = Mock(return_value=1)
        with self.assertRaisesRegex(common.FoxhisError, "相同"):
            self.run_report("room_status", collect, [account("foxhis"), account("foxhis1")], sync, realtime=True)
        sync.assert_not_called()

        collect.reset_mock()
        with patch.dict(os.environ, {"FOXHIS_HOTEL_NAME": ""}):
            with self.assertRaisesRegex(common.FoxhisError, "汇总酒店名称"):
                self.run_report("room_status", collect, [account("foxhis"), account("foxhis1")], sync, realtime=True)
        collect.assert_not_called()

    def test_empty_merge_and_disabled_report_do_not_replace_existing_rows(self):
        sync = Mock(return_value=1)
        empty = Mock(return_value={"rows": []})
        self.assertEqual(self.run_report("jd01", empty, [account("foxhis"), account("foxhis1")], sync), 0)
        sync.assert_not_called()

        one = Mock(return_value={"rows": [{"order_id": "A100"}]})
        limited = [account("foxhis"), account("foxhis1", reports="jl01")]
        self.assertEqual(self.run_report("jd01", one, limited, sync), 0)
        self.assertEqual([row["order_id"] for row in sync.call_args.args[0]], ["foxhis:A100"])

        def both(config, _day, _stamp, *, write=True):
            return {"rows": [{"order_id": "F26J090001", "hotel_name": "旧名称"}]}

        sync = Mock(return_value=2)
        self.run_report("jd01", both, [account("foxhis"), account("foxhis1")], sync)
        self.assertEqual(
            [row["order_id"] for row in sync.call_args.args[0]],
            ["foxhis:F26J090001", "foxhis1:F26J090001"],
        )
        self.assertTrue(all(row["hotel_name"] == "贵阳百灵" for row in sync.call_args.args[0]))
        one.reset_mock()
        sync.reset_mock()
        self.assertEqual(self.run_report("jd01", one, [account("foxhis", reports="jl01"), account("foxhis1", reports="jl01")], sync), 0)
        sync.assert_not_called()
        one.assert_not_called()

    def test_room_revenue_uses_the_existing_month_window(self):
        def collect(config, _day, stamp, *, write=True):
            return {"rows": [{
                "order_id": config.database, "business_date": date(2026, 11, 2), "room_no": config.database,
                "charge_subject": "房费", "snapshot_time": stamp, "hotel_id": "bailing", "hotel_name": "旧名称",
            }]}

        sync = Mock(return_value=2)
        self.run_report("room_revenue", collect, [account("foxhis"), account("foxhis1")], sync)
        _rows, _config, start, end = sync.call_args.args
        self.assertEqual((start, end), (date(2026, 11, 1), date(2026, 12, 1)))
        self.assertTrue(all(row["hotel_name"] == "贵阳百灵" and row["snapshot_time"] == datetime(2026, 11, 9, 12, 0) for row in _rows))

    def test_jy01_and_jy03_write_the_recalculated_total_once(self):
        day = date(2026, 11, 8)

        def daily(config, _day, _stamp, *, write=True):
            count = 63 if config.database == "foxhis" else 61
            nights = 30 if config.database == "foxhis" else 20
            revenue = 3000 if config.database == "foxhis" else 1000
            sold = 20 if config.database == "foxhis" else 10
            remaining = 40 if config.database == "foxhis" else 30
            return {"rows": [{"business_date": day, "room_count": count, "room_nights": Decimal(nights),
                              "room_revenue": Decimal(revenue), "sold_rooms": sold, "remaining_rooms": remaining}]}

        sync = Mock(return_value=1)
        self.run_report("jy01", daily, [account("foxhis"), account("foxhis1")], sync)
        self.assertEqual(sync.call_args.args[0][0]["room_count"], 124)
        self.assertEqual(sync.call_args.args[0][0]["revpar"], Decimal("32.26"))

        def monthly(config, _day, _stamp, *, write=True):
            part = {"room_nights": Decimal(100), "net_room_revenue": Decimal(2000), "room_days": Decimal(300),
                    "maintain_room_days": Decimal(10), "available_room_days": Decimal(280)}
            if config.database == "foxhis1":
                part = {"room_nights": Decimal(50), "net_room_revenue": Decimal(500), "room_days": Decimal(200),
                        "maintain_room_days": Decimal(5), "available_room_days": Decimal(180)}
            return {"rows": [{"room_count": 1}], "by_month": {"2026-10": part}}

        sync = Mock(return_value=1)
        self.run_report("jy03", monthly, [account("foxhis"), account("foxhis1")], sync)
        self.assertEqual(sync.call_args.args[0][0]["room_count"], 500)
        self.assertEqual(sync.call_args.args[0][0]["occupancy_rate"], Decimal("32.61"))

        def mismatched(config, _day, _stamp, *, write=True):
            month = "2026-10" if config.database == "foxhis" else "2026-09"
            part = {"room_nights": Decimal(1), "net_room_revenue": Decimal(1), "room_days": Decimal(1),
                    "maintain_room_days": Decimal(0), "available_room_days": Decimal(1)}
            return {"rows": [], "by_month": {month: part}}

        sync = Mock(return_value=1)
        with patch.object(sys, "argv", ["collector", "--force"]):
            with self.assertRaisesRegex(common.FoxhisError, "月份"):
                multi.run_multi("jy03", mismatched, [account("foxhis"), account("foxhis1")], sync=sync)
        sync.assert_not_called()

    def test_jl01_writer_does_not_receive_a_day_missing_from_one_database(self):
        shared_day = date(2026, 11, 9)
        only_a = date(2026, 11, 8)

        def collect(config, _day, _stamp, *, write=True):
            self.assertFalse(write)
            name = "大床" if config.database == "foxhis" else "套房"
            rows = [{"business_date": shared_day, "room_type_name": name, "pms_rate_room_type_id": "HT",
                     "room_nights": Decimal(1), "room_revenue": Decimal(10),
                     "available_rooms": Decimal(2), "total_rooms": Decimal(2)}]
            components = {f"{shared_day.isoformat()}|{name}": {"available_rooms": Decimal(2), "total_rooms": Decimal(2)}}
            if config.database == "foxhis":
                rows.append({**rows[0], "business_date": only_a, "room_type_name": "仅A"})
                components[f"{only_a.isoformat()}|仅A"] = {"available_rooms": Decimal(2), "total_rooms": Decimal(2)}
            return {"rows": rows, "components": components}

        sync = Mock(return_value=2)
        self.run_report("jl01", collect, [account("foxhis"), account("foxhis1")], sync)
        self.assertEqual({row["business_date"] for row in sync.call_args.args[0]}, {shared_day})

        def split(config, _day, _stamp, *, write=True):
            day = only_a if config.database == "foxhis" else shared_day
            return {"rows": [{"business_date": day, "room_type_name": config.database,
                              "available_rooms": Decimal(1), "total_rooms": Decimal(1)}]}

        sync = Mock(return_value=1)
        with patch.object(sys, "argv", ["collector", "--force"]):
            with self.assertRaisesRegex(common.FoxhisError, "共同营业日"):
                self.run_report("jl01", split, [account("foxhis"), account("foxhis1")], sync)
        sync.assert_not_called()


class CacheAndSchedulerTests(unittest.TestCase):
    def test_successful_database_is_not_queried_again_and_a_removed_database_leaves_the_next_write(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        output = stack.enter_context(tempfile.TemporaryDirectory(prefix="foxhis_cache_", dir=ROOT))
        clock = stack.enter_context(patch.object(common, "datetime", wraps=datetime))
        clock.now.return_value = datetime(2026, 11, 9, 12, 0)
        stack.enter_context(patch.object(common, "check_reachable", return_value=(True, "ok")))
        stack.enter_context(patch.dict(os.environ, {
            "FOXHIS_SERVER": "FoxFront", "FOXHIS_DATABASE": "foxhis", "FOXHIS_USERNAME": "foxsyb",
            "FOXHIS_PASSWORD": "password-foxhis", "FOXHIS_HOTEL_NAME": "贵阳百灵", "HOTEL_ID": "bailing",
            "FOXHIS_FETCH_AFTER": "", "HOTEL_OTA_OUTPUT_DIR": output,
        }))
        calls = []

        names = {"foxhis": "大床", "foxhis1": "双床", "foxhis2": "套房"}

        def collect(config, _day, _stamp, *, write=True):
            calls.append(config.database)
            return {"rows": [{"room_type_name": names[config.database], "business_date": date(2026, 11, 8)}]}

        sync = Mock(return_value=3)
        all_three = [account("foxhis"), account("foxhis1"), account("foxhis2")]
        remaining = [account("foxhis"), account("foxhis2")]
        with patch.object(sys, "argv", ["collector"]), redirect_stdout(StringIO()):
            multi.run_multi("jl01", collect, all_three, sync=sync)
            multi.run_multi("jl01", collect, all_three, sync=sync)
            self.assertEqual(calls, ["foxhis", "foxhis1", "foxhis2"])
            multi.run_multi("jl01", collect, remaining, sync=sync)
        self.assertEqual(calls, ["foxhis", "foxhis1", "foxhis2"])
        self.assertEqual([row["room_type_name"] for row in sync.call_args_list[-1].args[0]], ["大床", "套房"])
        with patch.object(sys, "argv", ["collector", "--force"]), redirect_stdout(StringIO()):
            multi.run_multi("jl01", collect, remaining, sync=sync)
        self.assertEqual(calls[-2:], ["foxhis", "foxhis2"])

    def test_a_database_waiting_for_its_gate_does_not_fail_or_write(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        output = stack.enter_context(tempfile.TemporaryDirectory(prefix="foxhis_gate_", dir=ROOT))
        clock = stack.enter_context(patch.object(common, "datetime", wraps=datetime))
        clock.now.return_value = datetime(2026, 11, 9, 12, 0)
        stack.enter_context(patch.object(common, "check_reachable", return_value=(True, "ok")))
        stack.enter_context(patch.dict(os.environ, {
            "FOXHIS_SERVER": "FoxFront", "FOXHIS_HOTEL_NAME": "贵阳百灵", "HOTEL_ID": "bailing",
            "FOXHIS_FETCH_AFTER": "", "HOTEL_OTA_OUTPUT_DIR": output,
        }))
        calls = []

        def collect(config, _day, _stamp, *, write=True):
            calls.append(config.database)
            return {"rows": [{"room_type_name": config.database, "business_date": date(2026, 11, 8)}]}

        sync = Mock(return_value=1)
        waiting = [account("foxhis"), account("foxhis1", fetch_after="23:59")]
        with patch.object(sys, "argv", ["collector"]), redirect_stdout(StringIO()) as output_text:
            self.assertEqual(multi.run_multi("jl01", collect, waiting, sync=sync), 0)
        sync.assert_not_called()
        self.assertEqual(calls, ["foxhis"])
        self.assertIn("SKIP", output_text.getvalue())
        ready = [account("foxhis"), account("foxhis1", fetch_after="03:30")]
        with patch.object(sys, "argv", ["collector"]), redirect_stdout(StringIO()):
            self.assertEqual(multi.run_multi("jl01", collect, ready, sync=sync), 0)
        self.assertEqual(calls, ["foxhis", "foxhis1"])
        self.assertEqual([row["room_type_name"] for row in sync.call_args.args[0]], ["foxhis", "foxhis1"])

    def test_single_database_does_not_enter_the_combined_writer(self):
        config = Config(server="FoxFront", database="foxhis1", username="reader", hotel_id="bailing",
                        hotel_name="贵阳百灵", output_dir=".")
        collect = Mock(return_value={"row_count": 1, "written": 1})
        with patch.dict(os.environ, {"FOXHIS_ACCOUNTS": json.dumps([account("foxhis1")])}), \
             patch.object(Config, "from_env", return_value=config), \
             patch.object(common, "check_reachable", return_value=(True, "ok")), \
             patch("foxhis_multi.run_multi") as combined, redirect_stdout(StringIO()):
            self.assertEqual(common.report_main("room_status", collect, realtime=True, sync=Mock()), 0)
        combined.assert_not_called()
        collect.assert_called_once()
        with patch.dict(os.environ, {"FOXHIS_ACCOUNTS": json.dumps([account("foxhis"), account("foxhis1")])}), \
             patch("foxhis_multi.run_multi", return_value=0) as combined:
            self.assertEqual(common.report_main("jl01", Mock(), sync=Mock()), 0)
        combined.assert_called_once()

    def test_runner_passes_two_databases_and_refuses_a_blank_summary_name(self):
        settings = json.loads((ROOT / "OTA采集服务/config/settings.example.json").read_text(encoding="utf-8-sig"))
        settings["pms"]["provider"] = "foxhis"
        settings["hotel"]["hotel_id"] = "bailing"
        settings["foxhis"].update(enabled=True, hotel_name="贵阳百灵", server="FoxFront",
                                  accounts=[account("foxhis"), account("foxhis1")])
        env = runner.build_env(settings, "foxhis")
        self.assertEqual([item["database"] for item in json.loads(env["FOXHIS_ACCOUNTS"])], ["foxhis", "foxhis1"])
        self.assertNotIn("password-foxhis", runner.sanitize("password-foxhis password-foxhis1", settings))
        single = json.loads((ROOT / "OTA采集服务/config/settings.example.json").read_text(encoding="utf-8-sig"))
        single["foxhis"].update(database="foxhis1", username="foxsyb", password="password-foxhis1")
        with patch.dict(os.environ, {"FOXHIS_ACCOUNTS": "[{\"database\":\"leftover\"}]"}):
            single_env = runner.build_env(single, "foxhis")
        self.assertNotIn("FOXHIS_ACCOUNTS", single_env)
        self.assertEqual(single_env["FOXHIS_DATABASE"], "foxhis1")

        one = json.loads((ROOT / "OTA采集服务/config/settings.example.json").read_text(encoding="utf-8-sig"))
        one["pms"]["provider"] = "foxhis"
        one["foxhis"].update(server="GlobalFront", reports="jy01", accounts=[
            account("foxhis1", server="RoomFront", reports="jl01"),
        ])
        one_env = runner.build_env(one, "foxhis")
        self.assertEqual(one_env["FOXHIS_SERVER"], "RoomFront")
        self.assertEqual(one_env["FOXHIS_REPORTS"], "jl01")
        self.assertEqual(one_env["FOXHIS_DATABASE"], "foxhis1")
        self.assertNotIn("FOXHIS_ACCOUNTS", one_env)
        self.assertEqual([item[1] for item in runner.task_specs("pms_fetch", one)], ["foxhis_jl01_data.py"])
        self.assertEqual(runner.build_env(one, "ctrip")["FOXHIS_SERVER"], "GlobalFront")
        self.assertNotIn("FOXHIS_ACCOUNTS", runner.build_env(one, "ctrip"))

        with tempfile.TemporaryDirectory() as old_dir:
            settings["foxhis"]["code_dir"] = old_dir
            ctrip_env = runner.build_env(settings, "ctrip")
            foxhis_env = runner.build_env(settings, "foxhis")
        self.assertNotIn("FOXHIS_ACCOUNTS", ctrip_env)
        self.assertEqual([item["database"] for item in json.loads(foxhis_env["FOXHIS_ACCOUNTS"])], ["foxhis", "foxhis1"])
        self.assertEqual(foxhis_env["FOXHIS_SERVER"], "FoxFront")

        self.assertEqual(runner.task_timeout({"foxhis": {"timeout_seconds": 600, "accounts": [
            {"timeout_seconds": 900}, {"timeout_seconds": "nope"}]}}, "foxhis"), 900)

        settings["foxhis"]["hotel_name"] = ""
        directory = tempfile.TemporaryDirectory(prefix="foxhis_name_", dir=ROOT)
        self.addCleanup(directory.cleanup)
        with patch.object(runner, "CONFIG_PATH", Path(directory.name) / "settings.json"), \
             patch.object(runner, "STATUS_PATH", Path(directory.name) / "status.json"), \
             patch.object(runner, "LOG_DIR", Path(directory.name) / "logs"), \
             patch.object(common, "query") as query, patch.object(runner, "run_streamed") as run:
            result = runner.run_task("pms_fetch", settings, {})
        self.assertEqual(result["status"], "failed")
        self.assertIn("汇总酒店名称", result["error_summary"])
        query.assert_not_called()
        run.assert_not_called()

    def test_multiple_databases_refuse_an_old_code_directory_before_any_script_starts(self):
        settings = json.loads((ROOT / "OTA采集服务/config/settings.example.json").read_text(encoding="utf-8-sig"))
        settings["pms"]["provider"] = "foxhis"
        settings["hotel"]["hotel_id"] = "bailing"
        settings["foxhis"].update(enabled=True, hotel_name="贵阳百灵", reports="jl01",
                                  accounts=[account("foxhis"), account("foxhis1")])
        directory = tempfile.TemporaryDirectory(prefix="foxhis_old_", dir=ROOT)
        self.addCleanup(directory.cleanup)
        old = Path(directory.name)
        (old / "foxhis_common.py").write_text("def report_main(*args, **kwargs):\n    return 0\n", encoding="utf-8")
        (old / "foxhis_jl01_data.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
        settings["foxhis"]["code_dir"] = str(old)
        with patch.object(runner, "CONFIG_PATH", old / "settings.json"), \
             patch.object(runner, "STATUS_PATH", old / "status.json"), \
             patch.object(runner, "LOG_DIR", old / "logs"), \
             patch.object(runner, "ensure_task_tables"), \
             patch.object(runner, "run_streamed") as run:
            result = runner.run_task("pms_fetch", settings, {})
        self.assertEqual(result["status"], "failed")
        self.assertIn("不支持多个业务库", result["error_summary"])
        run.assert_not_called()

        settings["foxhis"]["accounts"] = [account("foxhis")]
        with patch.object(runner, "CONFIG_PATH", old / "settings.json"), \
             patch.object(runner, "STATUS_PATH", old / "status.json"), \
             patch.object(runner, "LOG_DIR", old / "logs"), \
             patch.object(runner, "ensure_task_tables"), \
             patch.object(runner, "enrich_room_type_ids"), \
             patch.object(runner, "run_streamed", return_value=ProcessResult(0, "ok")) as run:
            result = runner.run_task("pms_fetch", settings, {})
        self.assertEqual(result["status"], "success")
        self.assertIn("foxhis_jl01_data.py", run.call_args.args[0][1])

        settings["foxhis"]["accounts"] = [account("foxhis"), account("foxhis1")]
        settings["foxhis"]["code_dir"] = "OTA采集服务/foxhis_pms"
        with patch.object(runner, "CONFIG_PATH", old / "settings.json"), \
             patch.object(runner, "STATUS_PATH", old / "status.json"), \
             patch.object(runner, "LOG_DIR", old / "logs"), \
             patch.object(runner, "ensure_task_tables"), \
             patch.object(runner, "enrich_room_type_ids"), \
             patch.object(runner, "run_streamed", return_value=ProcessResult(0, "ok")) as run:
            result = runner.run_task("pms_fetch", settings, {})
        self.assertEqual(result["status"], "success")
        run.assert_called()

    def test_multiple_databases_launch_report_scripts_instead_of_a_custom_entry(self):
        settings = json.loads((ROOT / "OTA采集服务/config/settings.example.json").read_text(encoding="utf-8-sig"))
        settings["pms"]["provider"] = "foxhis"
        settings["hotel"]["hotel_id"] = "bailing"
        settings["foxhis"].update(enabled=True, hotel_name="贵阳百灵", reports="jl01",
                                  code_dir="OTA采集服务/foxhis_pms",
                                  accounts=[account("foxhis"), account("foxhis1")])
        directory = tempfile.TemporaryDirectory(prefix="foxhis_entry_", dir=ROOT)
        self.addCleanup(directory.cleanup)
        old_entry = Path(directory.name) / "foxhis_legacy.py"
        old_entry.write_text("raise SystemExit(0)\n", encoding="utf-8")
        settings["foxhis"]["entry_script"] = str(old_entry)
        launched = runner.script_path(settings, "foxhis", "foxhis_jl01_data.py")
        self.assertEqual(launched.name, "foxhis_jl01_data.py")
        self.assertIn("foxhis_pms", str(launched))
        with patch.object(runner, "CONFIG_PATH", Path(directory.name) / "settings.json"), \
             patch.object(runner, "STATUS_PATH", Path(directory.name) / "status.json"), \
             patch.object(runner, "LOG_DIR", Path(directory.name) / "logs"), \
             patch.object(runner, "ensure_task_tables"), \
             patch.object(runner, "enrich_room_type_ids"), \
             patch.object(runner, "run_streamed", return_value=ProcessResult(0, "ok")) as run:
            result = runner.run_task("pms_fetch", settings, {})
        self.assertEqual(result["status"], "success")
        self.assertEqual(Path(run.call_args.args[0][1]), launched)

        settings["foxhis"]["accounts"] = [account("foxhis")]
        single = runner.script_path(settings, "foxhis", "foxhis_jl01_data.py")
        self.assertEqual(single, old_entry)


if __name__ == "__main__":
    unittest.main()
