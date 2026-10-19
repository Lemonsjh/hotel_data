"""JL11：宝寓字段契约、滚动窗口、历史房型/手工收入和来源隔离事务。"""
import importlib.util
import sqlite3
import sys
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "OTA采集服务/foxhis_pms"))
import foxhis_jl11_data as jl
from foxhis_common import Config, FoxhisError, SOURCE_PLATFORM
from .test_realtime_forecast import DatabaseAdapter


class JL11Tests(unittest.TestCase):
    def setUp(self):
        self.stamp = datetime(2026, 10, 19, 15)
        self.start, self.end = jl.report_window(self.stamp.date())
        self.config = Config(hotel_id="TEST", hotel_name="测试酒店")
        self.records, self.rooms = [], []
        for n in range(jl.WINDOW_DAYS):
            day = (self.start + timedelta(days=n)).strftime("%Y%m%d")
            capacity, maintain, paid, free, gross, rebate = (D(10), D(1), D("4.5"), D("0.5"), D(100), D(-10)) if n == 0 else (D(20), D(2), D(10), D(0), D(300), D(0))
            available = capacity-maintain
            metrics = {"010100": capacity, "010190": available, "011000": paid, "013000": gross+rebate}
            for code, value in metrics.items():
                self.records.append({"business_date": day, "metric_code": code, "metric_value": str(value)})
            values = dict(room_count=str(capacity), available_rooms=str(available), room_nights=str(paid), room_revenue=str(gross+rebate))
            for code, hall, name in [("AR", "A", "历史双床房"), ("{{{", "{", "总计")]:
                self.rooms.append({"business_date": day, "gkey": "t", "hall": hall, "room_type_code": code, "room_type_name": name, **values})
        self.db = DatabaseAdapter()
        columns = ",".join(f"`{c}` TEXT" for c in jl.COLUMNS)
        self.db.db.execute(f"CREATE TABLE jl11_room_type_classification ({columns}, PRIMARY KEY(hotel_id,snapshot_date,section,room_type_name,dimension_code))")
        self.db.db.commit()
        self.db_patch = patch.object(jl, "mysql_connection", return_value=self.db)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop(); self.db.db.close()

    def build(self, records=None, rooms=None):
        return jl.build_rows(self.records if records is None else records, self.rooms if rooms is None else rooms, self.config, self.stamp)

    def seed(self, row):
        sql = "INSERT INTO jl11_room_type_classification (" + ",".join(jl.COLUMNS) + ") VALUES (" + ",".join(["%s"]*len(jl.COLUMNS)) + ")"
        self.db.executemany(sql, [tuple(row[c] for c in jl.COLUMNS)]); self.db.commit()

    def contents(self):
        return self.db.db.execute("SELECT hotel_id,source_platform,snapshot_date,section,room_type_name,room_revenue FROM jl11_room_type_classification ORDER BY hotel_id,source_platform,snapshot_date,room_type_name").fetchall()

    def test_calendar_window_has_thirty_closed_days(self):
        self.assertEqual((self.start, self.end), (date(2026,9,19), date(2026,10,18)))
        self.assertEqual(jl.report_window(date(2024,3,1)), (date(2024,1,31), date(2024,2,29)))
        self.assertEqual(jl.report_window(date(2026,1,1))[1], date(2025,12,31))

    def test_business_columns_and_summary_conventions_match_bypms(self):
        path = Path(__file__).resolve().parents[2] / "宝寓PMS数据采集代码/bypms_jl11_data.py"
        spec = importlib.util.spec_from_file_location("bypms_jl11_contract", path)
        bypms = importlib.util.module_from_spec(spec); spec.loader.exec_module(bypms)
        with patch.object(bypms, "HOTEL_ID", "TEST"), patch.object(bypms, "HOTEL_NAME", "测试酒店"):
            expected = bypms.build_rows([{"title":"历史双床房", "roomCount":590, "roomCountSold":294.5, "roomSoldPercent":0.554614,
                                         "price":8790, "priceAvg":29.85, "revPar":14.90}], self.start, self.end, self.stamp)[0]
        rows, _ = self.build(); row = rows[0]
        self.assertEqual(set(row), set(expected))
        self.assertEqual(set(jl.COLUMNS), set(expected))
        for field in ("section", "room_type_id", "dimension_code", "dimension_name", "period_start", "period_end", "snapshot_date"):
            self.assertEqual(row[field], expected[field])
        self.assertEqual(row["overnight_room_count"], row["room_nights"])
        self.assertEqual(row["overnight_occupancy_rate"], row["occupancy_rate"])

    def test_fractional_paid_nights_net_rebate_and_weighted_period_rates(self):
        rows, summary = self.build(); row = rows[0]
        self.assertEqual((row["room_count"], row["room_nights"], row["room_revenue"]), (D(590), D("294.5"), D(8790)))
        self.assertEqual((row["average_room_price"], row["revpar"], row["occupancy_rate"]), (D("29.85"), D("14.90"), D("55.4614")))
        self.assertEqual(len(rows), 1)

    def test_manual_income_is_audited_but_not_written_as_room_type(self):
        rooms = [{**r,"room_revenue":"80"} if r["business_date"]=="20260919" and r["room_type_code"]=="AR" else r for r in self.rooms]
        rooms.append({"business_date":"20260919", "gkey":"t", "hall":"{", "room_type_code":"ZZZ", "room_type_name":"手工输入",
                      **{f:"0" for f in jl.ROOM_FIELDS}, "room_revenue":"10"})
        rows, summary = self.build(rooms=rooms)
        self.assertNotIn("手工输入", {r["room_type_name"] for r in rows})
        self.assertEqual(summary["room_revenue_total"],D(8780))
        self.assertEqual(summary["excluded_manual_room_revenue"],D(10))
        self.assertEqual(summary["room_revenue_total"]+summary["excluded_manual_room_revenue"],D(8790))

    def test_zero_manual_placeholder_is_skipped(self):
        rooms = [*self.rooms, {"business_date":"20260919", "gkey":"t", "hall":"{", "room_type_code":"ZZZ", "room_type_name":"手工输入", **{f:"0" for f in jl.ROOM_FIELDS}}]
        rows,_ = self.build(rooms=rooms)
        self.assertEqual(len(rows),1)

    def test_historical_renaming_and_multiple_halls_are_not_overwritten(self):
        rows,_ = self.build(rooms=[{**r,"room_type_name":"旧房型名"} if r["business_date"]=="20260919" and r["room_type_code"]=="AR" else r for r in self.rooms])
        self.assertEqual({r["room_type_name"] for r in rows},{"旧房型名","历史双床房"})
        # 使用第2天的偶数容量记录验证分楼栋汇总。
        index=2; original=self.rooms[index]
        a={**original, **{f:str(D(original[f])/2) for f in jl.ROOM_FIELDS}}
        rows,_=self.build(rooms=[*self.rooms[:index],a,{**a,"hall":"B"},*self.rooms[index+1:]])
        self.assertEqual(rows[0]["room_revenue"],D(8790))

    def test_no_available_rooms_has_undefined_occupancy(self):
        records=[{**r,"metric_value":"0","daily_rebate":"0"} for r in self.records]
        rooms=[{**r,**{f:"0" for f in jl.ROOM_FIELDS}} for r in self.rooms]
        rows,_=self.build(records,rooms)
        self.assertIsNone(rows[0]["occupancy_rate"])
        self.assertIsNone(rows[0]["average_room_price"])
        self.assertIsNone(rows[0]["revpar"])

    def test_missing_duplicate_or_out_of_range_day_metrics_fail(self):
        for records in (self.records[:-1],self.records[4:],[*self.records,self.records[0]],[{**self.records[0],"business_date":"20261019"},*self.records[1:]]):
            with self.assertRaises(FoxhisError):self.build(records=records)

    def test_source_totals_and_detail_mismatches_fail(self):
        for code in jl.CONTROL_FIELDS:
            with self.assertRaises(FoxhisError):self.build(records=[{**r,"metric_value":"999"} if r["metric_code"]==code else r for r in self.records])
        for rooms in (self.rooms[:-1],[*self.rooms,self.rooms[0]],[{**self.rooms[0],"room_revenue":"999"},*self.rooms[1:]]):
            with self.assertRaises(FoxhisError):self.build(rooms=rooms)

    def test_missing_names_nonfinite_or_negative_counts_fail(self):
        for field,value in (("room_type_name",""),("room_nights","-1"),("room_revenue","NaN"),("room_count","999"),("gkey","f")):
            with self.assertRaises(FoxhisError):self.build(rooms=[{**self.rooms[0],field:value},*self.rooms[1:]])

    def test_source_night_audit_must_have_closed_yesterday(self):
        with patch.object(jl,"query",return_value=[{"current_business_date":"20261018"}]):
            with self.assertRaises(FoxhisError):jl.fetch_rows(self.config,self.stamp)

    def test_latest_snapshot_replacement_preserves_other_hotel_and_source(self):
        rows,_=self.build();row=rows[0]
        self.seed({**row,"snapshot_date":self.stamp.date()-timedelta(days=1),"snapshot_time":self.stamp-timedelta(days=1)})
        self.seed({**row,"section":"channel","dimension_code":"OLD","room_type_name":"旧明细"})
        self.seed({**row,"hotel_id":"OTHER"})
        self.seed({**row,"source_platform":"PMS（宝寓）","room_type_name":"OTHER_SOURCE"})
        jl.sync_rows(rows,self.config);jl.sync_rows(rows,self.config)
        result=self.contents()
        self.assertEqual(len(result),3)
        self.assertEqual(sum(r[1]==SOURCE_PLATFORM and r[0]=="TEST" for r in result),1)

    def test_foreign_source_primary_key_collision_rolls_back(self):
        rows,_=self.build();row=rows[0]
        self.seed({**row,"snapshot_date":self.stamp.date()-timedelta(days=1)})
        self.seed({**row,"source_platform":"PMS（宝寓）"})
        before=self.contents()
        with self.assertRaises(sqlite3.IntegrityError):jl.sync_rows(rows,self.config)
        self.assertEqual(self.contents(),before)

    def test_invalid_batch_and_old_snapshot_cannot_remove_data(self):
        rows,_=self.build();self.seed({**rows[0],"snapshot_time":self.stamp+timedelta(seconds=1)})
        before=self.contents()
        for invalid in ([],[*rows,rows[0]],[{**rows[0],"period_start":self.start+timedelta(days=1)}],[{**rows[0],"section":"channel"}],[{**rows[0],"overnight_room_count":D(1)}],rows):
            with self.assertRaises(FoxhisError):jl.sync_rows(invalid,self.config)
        self.assertEqual(self.contents(),before)

    def test_dry_run_never_writes(self):
        rows,summary=self.build()
        with patch.object(jl,"fetch_rows",return_value=(rows,summary)),patch.object(jl,"sync_rows",side_effect=AssertionError("不应写入")),patch.object(jl,"save_output"),patch.object(sys,"argv",["jl11","--dry-run"]):
            result=jl.collect(self.config,self.end,self.stamp)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["written"],0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
