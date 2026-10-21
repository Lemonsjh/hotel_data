"""JD01：自然月范围、归档去重、历史/全程价格及NULL和事务保护。"""
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "OTA采集服务/foxhis_pms"))
import foxhis_jd01_data as jd
import foxhis_common as common
from foxhis_common import Config, FoxhisError, SOURCE_PLATFORM
from .test_realtime_forecast import DatabaseAdapter


class JD01Tests(unittest.TestCase):
    def setUp(self):
        self.stamp = datetime(2026,10,19,16)
        self.config = Config(hotel_id="TEST",hotel_name="测试酒店")
        self.records = [self.order("OLD","D"), self.order("OLD","O","history"),
                        self.order("LIVE","I"), self.order("FUTURE","R",count="2"),
                        self.order("UNKNOWN","S"), self.order("ZERO","O","history")]
        self.ledger = [dict(folio_no="OLD",line_no="1",amount="100.01",nights="2"),
                       dict(folio_no="ZERO",line_no="1",amount="80",nights="0")]
        self.rates = [self.rate("LIVE"),self.rate("FUTURE",quantity="2")]
        self.db = DatabaseAdapter()
        self.db.db.execute("CREATE TABLE jd01_booking_detail ("+','.join('`'+c+'` TEXT' for c in jd.COLUMNS)+")")
        self.db.db.commit()
        self.db_patch = patch.object(jd,"mysql_connection",return_value=self.db)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop();self.db.db.close()

    def order(self,key,status,origin="current",count="1"):
        return dict(folio_no=key,status_code=status,origin=origin,booking_time="20261001 10:00:00",
                    arrival_time="20261015 14:00:00",departure_time="20261020 12:00:00",type_code="AR",
                    room_count=count,share_code="F",contact="测试客人",guest_source="携程",room_type_name="测试双床房",
                    price_type="携程价",operator_name="测试操作员")

    def rate(self,key,**overrides):
        return dict(folio_no=key,type_code="AR",quantity="1",start_date="20261015",end_date="20261020",rate="110") | overrides

    def build(self,records=None,ledger=None,rates=None):
        return jd.build_rows(self.records if records is None else records,self.ledger if ledger is None else ledger,
                             self.rates if rates is None else rates,self.config,self.stamp)

    def seed(self,row):
        self.db.executemany("INSERT INTO jd01_booking_detail ("+','.join(jd.COLUMNS)+") VALUES ("+','.join(['%s']*len(jd.COLUMNS))+")",
                            [tuple(row[c] for c in jd.COLUMNS)])
        self.db.commit()

    def contents(self):
        return self.db.db.execute("SELECT hotel_id,source_platform,order_id,departure_time,room_price FROM jd01_booking_detail ORDER BY hotel_id,source_platform,order_id,departure_time").fetchall()

    def test_cutoff_is_calendar_month_with_leap_and_year_boundaries(self):
        self.assertEqual(jd.checkout_cutoff(date(2026,10,19)),date(2026,9,19))
        self.assertEqual(jd.checkout_cutoff(date(2024,3,31)),date(2024,2,29))
        self.assertEqual(jd.checkout_cutoff(date(2026,3,31)),date(2026,2,28))
        self.assertEqual(jd.checkout_cutoff(date(2026,1,31)),date(2025,12,31))

    def test_archived_order_wins_in_both_source_orders(self):
        for records in (self.records,list(reversed(self.records))):
            rows,summary=self.build(records=records)
            self.assertEqual(len(rows),5)
            self.assertEqual(next(r for r in rows if r['order_id']=='OLD')['booking_status'],'已离店')
            self.assertEqual(summary['archived_duplicates_removed'],1)

    def test_unknown_fields_and_zero_nights_stay_null(self):
        rows,summary=self.build()
        for row in rows:
            for field in summary['nullable_fields']:self.assertIsNone(row[field])
        unknown=next(r for r in rows if r['order_id']=='UNKNOWN')
        self.assertIsNone(unknown['booking_status']);self.assertIsNone(unknown['room_price'])
        self.assertIsNone(next(r for r in rows if r['order_id']=='ZERO')['room_price'])

    def test_full_posted_average_and_ledger_deduplication(self):
        rows,_=self.build(ledger=[*self.ledger,self.ledger[0]])
        self.assertEqual(next(r for r in rows if r['order_id']=='OLD')['room_price'],D('50.01'))
        ledger=[dict(folio_no='OLD',line_no='1',amount='100',nights='1'),dict(folio_no='OLD',line_no='2',amount='-20',nights='0')]
        rows,_=self.build(ledger=ledger)
        self.assertEqual(next(r for r in rows if r['order_id']=='OLD')['room_price'],D(80))

    def test_single_whole_stay_rate_supports_multiroom_reservations(self):
        rows,_=self.build();future=next(r for r in rows if r['order_id']=='FUTURE')
        self.assertEqual((future['room_count'],future['room_price']),(2,D(110)))
        self.assertEqual(next(r for r in rows if r['order_id']=='LIVE')['room_price'],D(110))

    def test_partial_overlapping_missing_or_shared_rate_is_not_guessed(self):
        for rates in ([],[self.rate('LIVE',start_date='20261016')],[self.rate('LIVE'),self.rate('LIVE')],
                      [self.rate('LIVE',quantity='2')],[self.rate('LIVE',rate='')]):
            rows,_=self.build(rates=rates)
            self.assertIsNone(next(r for r in rows if r['order_id']=='LIVE')['room_price'])
        records=[{**r,'share_code':'T'} if r['folio_no']=='LIVE' else r for r in self.records]
        rows,_=self.build(records=records)
        self.assertIsNone(next(r for r in rows if r['order_id']=='LIVE')['room_price'])

    def test_duplicate_main_or_conflicting_archive_and_ledger_fail(self):
        for records in ([*self.records,self.records[0]], [{**r,'room_count':'3'} if r['origin']=='history' and r['folio_no']=='OLD' else r for r in self.records]):
            with self.assertRaises(FoxhisError):self.build(records=records)
        with self.assertRaises(FoxhisError):self.build(ledger=[*self.ledger,{**self.ledger[0],'amount':'999'}])

    def test_canceled_out_of_range_and_invalid_core_values_fail(self):
        for field,value in [('status_code','X'),('departure_time','20260918 12:00:00'),('arrival_time','bad'),('room_count','0')]:
            with self.assertRaises(FoxhisError):self.build(records=[{**self.records[2],field:value}])
        rows,_=self.build(records=[{**self.records[2],'booking_time':'bad'}])
        self.assertIsNone(rows[0]['booking_time'])

    def test_scoped_replace_is_idempotent_and_preserves_other_sources(self):
        rows,_=self.build();row=rows[0]
        self.seed({**row,'order_id':'STALE'})
        self.seed({**row,'hotel_id':'OTHER'})
        self.seed({**row,'source_platform':'PMS（宝寓）'})
        self.seed({**row,'departure_time':datetime(2026,9,18)})
        jd.sync_rows(rows,self.config);jd.sync_rows(rows,self.config)
        self.assertEqual(len(self.contents()),len(rows)+3)
        self.assertNotIn('STALE',{r[2] for r in self.contents()})

    def test_write_failure_rolls_back_deletion(self):
        rows,_=self.build();self.seed({**rows[0],'order_id':'STALE'});before=self.contents()
        with patch.object(self.db,'executemany',side_effect=sqlite3.IntegrityError('insert failed')):
            with self.assertRaises(sqlite3.IntegrityError):jd.sync_rows(rows,self.config)
        self.assertEqual(self.contents(),before)

    def test_empty_duplicate_mixed_or_stale_batch_preserves_data(self):
        rows,_=self.build();self.seed({**rows[0],'snapshot_time':self.stamp+timedelta(seconds=1)});before=self.contents()
        for invalid in ([],[*rows,rows[0]],[{**rows[0],'hotel_id':'OTHER'}],rows):
            with self.assertRaises(FoxhisError):jd.sync_rows(invalid,self.config)
        self.assertEqual(self.contents(),before)

    def test_dry_run_never_writes(self):
        rows,summary=self.build()
        with patch.object(jd,'fetch_rows',return_value=(rows,summary)),patch.object(jd,'sync_rows',side_effect=AssertionError('不应写入')),patch.object(jd,'save_output'),patch.object(sys,'argv',['jd01','--dry-run']):
            self.assertEqual(jd.collect(self.config,date(2026,10,18),self.stamp)['written'],0)

    def test_valid_empty_query_succeeds_and_preserves_existing_orders(self):
        rows,_=self.build();self.seed(rows[0]);before=self.contents()
        output=StringIO()
        header='|folio_no|origin|status_code|arrival_time|departure_time|type_code|room_count\n|---|---|---|---|---|---|---\n'
        config=Config(server='test',database='test',username='test',hotel_id='TEST',hotel_name='测试酒店')
        with patch.object(common.Config,'from_env',return_value=config),patch.object(common,'after_gate',return_value=(True,'')),patch.object(common,'check_reachable',return_value=(True,'')),patch.object(common,'run_isql',return_value=header) as source,patch.object(jd,'mysql_connection',side_effect=AssertionError('空结果不得写库')),patch.object(jd,'save_output') as save,patch.object(sys,'argv',['jd01']),redirect_stdout(output):
            self.assertEqual(jd.main(),0)
        self.assertEqual(source.call_count,1)
        result=save.call_args.args[1]
        self.assertEqual((result['source_orders'],result['row_count'],result['written']),(0,0,0))
        self.assertIn('0 条符合条件的订单',result['message'])
        self.assertIn('OK [jd01]',output.getvalue())
        self.assertEqual(self.contents(),before)

    def test_missing_or_wrong_query_header_is_not_successful_empty(self):
        for output in ('','unrecognized output','|wrong|header\n|---|---\n'):
            with self.subTest(output=output),patch.object(common,'run_isql',return_value=output),patch.object(jd,'sync_rows') as sync:
                with self.assertRaises(FoxhisError):jd.collect(self.config,self.stamp.date(),self.stamp)
                sync.assert_not_called()

    def test_source_query_failure_does_not_become_empty_success(self):
        rows,_=self.build();self.seed(rows[0]);before=self.contents()
        with patch.object(common,'run_isql',side_effect=FoxhisError('查询失败')),patch.object(jd,'sync_rows') as sync:
            with self.assertRaises(FoxhisError):jd.collect(self.config,self.stamp.date(),self.stamp)
            sync.assert_not_called()
        self.assertEqual(self.contents(),before)

    def test_invalid_nonempty_orders_still_fail_before_writing(self):
        with patch.object(jd,'query',side_effect=[[{'folio_no':'BROKEN'}],[],[]]),patch.object(jd,'sync_rows') as sync:
            with self.assertRaises(FoxhisError):jd.collect(self.config,self.stamp.date(),self.stamp)
            sync.assert_not_called()

    def test_order_id_preserves_original_folio_without_prefix(self):
        folios = ("F26100012", "000123")
        rows, _ = self.build(records=[self.order(key, "R") for key in folios], ledger=[], rates=[])
        self.assertEqual({row["order_id"] for row in rows}, set(folios))
        self.assertTrue(all(row["source_platform"] == SOURCE_PLATFORM for row in rows))
    def test_next_snapshot_replaces_legacy_prefixed_ids_only_in_its_scope(self):
        rows, _ = self.build()
        for row in rows:
            self.seed({**row, "order_id": "foxhis:" + row["order_id"]})
        legacy = {**rows[0], "order_id": "foxhis:" + rows[0]["order_id"]}
        self.seed({**legacy, "hotel_id": "OTHER"})
        self.seed({**legacy, "source_platform": "PMS（宝寓）"})
        self.seed({**legacy, "departure_time": datetime(2026, 9, 18)})
        jd.sync_rows(rows, self.config)
        jd.sync_rows(rows, self.config)
        contents = self.contents()
        current = [r for r in contents if r[0] == self.config.hotel_id and r[1] == SOURCE_PLATFORM
                   and r[3] >= str(jd.checkout_cutoff(self.stamp.date()))]
        self.assertEqual({r[2] for r in current}, {row["order_id"] for row in rows})
        self.assertEqual(len(current), len(rows))
        self.assertEqual(sum(r[2].startswith("foxhis:") for r in contents), 3)
    def test_empty_orders_with_orphan_ledger_remain_failure(self):
        with self.assertRaises(FoxhisError):
            self.build(records=[], ledger=self.ledger, rates=[])

if __name__=='__main__':
    unittest.main(verbosity=2)
