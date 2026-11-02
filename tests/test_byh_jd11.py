"""JD11 缺订单号、同订单多人及别样红来源写入。"""
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / '别样红PMS数据采集代码/PMS登录/scripts'), str(ROOT / 'OTA采集服务')]
import fetch_jd11 as report
import collection_schema
from tests.test_meituan_pms_guest_report import Database


class ByhJd11Tests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 10, 29)
        self.snapshot = datetime(2026, 10, 30, 16)
        self.item = {'roomNumber': '101', 'roomType': '测试房型', 'customerId': 123,
                     'customerName': '测试主客', 'arrivalTime': '2026-10-28 12:00:00',
                     'departureTime': '2026-10-29 14:00:00', 'price': 100,
                     'mainCheckinId': 77, 'mainCheckinPerson': 1}
        self.payload = {'status': 0, 'data': {'variables': {'startDate': str(self.day), 'endDate': str(self.day)},
                                            'dataList': [self.item, dict(self.item, customerId=456,
                                                                        customerName='测试同住人', mainCheckinPerson=0)]}}

    def rows(self):
        return report.transform(self.payload, self.day, self.snapshot, 'hotel', '测试酒店')

    def test_missing_order_and_shared_main_id_preserve_both_guests(self):
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual([row['order_id'] for row in rows], [None, None])
        self.assertEqual([row['main_checkin_id'] for row in rows], ['77', '77'])
        self.assertEqual([row['main_checkin_person'] for row in rows], [1, 0])
        self.assertEqual(report.request_body('123', self.day)['dateType'], '2')

    def test_empty_and_wrong_date(self):
        self.payload['data']['dataList'] = []
        self.assertEqual(self.rows(), [])
        self.payload['data']['variables']['startDate'] = '2026-10-28'
        with self.assertRaises(ValueError):
            self.rows()

    def test_refresh_and_retention_keep_other_pms(self):
        rows = self.rows()
        db = Database(rows[0])
        self.addCleanup(db.db.close)
        db.seed(dict(rows[0], business_date=self.day - timedelta(days=60)))
        db.seed(dict(rows[0], business_date=self.day - timedelta(days=60), source_platform='PMS（宝寓）'))
        db.db.execute("INSERT INTO hotel_room_type_mapping VALUES ('hotel','pms_byh',1,'','测试房型','BYH-1')")
        db.commit()
        from OTA采集服务.pms_guest_storage import sync_guest_details
        for _ in range(2):
            sync_guest_details(rows, 'hotel', self.snapshot, report.SOURCE, 'pms_byh', db)
        self.assertEqual(len(db.contents()), 3)
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM jd02_guest_stay_detail "
                                       "WHERE source_platform=? AND order_id IS NULL AND room_type_id='BYH-1'",
                                       (report.SOURCE,)).fetchone()[0], 2)

    def test_byh_automatic_table_selection_and_old_column_migration(self):
        specs = [('pms', Path('fetch_main.py'), ['--reports', 'JD11'])]
        self.assertEqual(collection_schema.required_tables('pms_fetch', specs),
                         {'hotel_room_type_mapping', 'jd02_guest_stay_detail'})
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ('NO',)
        with patch.object(collection_schema, 'ensure_tables', return_value=[]), \
             patch.object(collection_schema, 'connect_mysql', return_value=connection):
            collection_schema.ensure_task_tables('pms_fetch', {'mysql': {'database': 'test'}}, specs)
        self.assertIn('ALTER TABLE', cursor.execute.call_args.args[0])
        connection.close.assert_called_once()
