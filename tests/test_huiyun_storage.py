"""慧云房态写入范围、事务、预览隔离与自动建表。"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'OTA采集服务')]
from huiyun_pms import storage, collector
from huiyun_pms.room_status import transform
from test_huiyun_room_status import fixture
import collection_schema


class HuiyunStorageTests(unittest.TestCase):
    def setUp(self):
        self.rows = transform(fixture())['room_types']
        self.rows[0]['hotel_id'] = 'test-hotel'
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchall.side_effect = [[], [], [('标准房', 'unified-room')]]

    def test_both_tables_share_transaction_and_exact_huiyun_mapping(self):
        with patch.dict(os.environ, {'PMS_RETENTION': '{}'}):
            storage.sync_mysql(self.rows, self.connection)
        self.connection.commit.assert_called_once()
        self.connection.rollback.assert_not_called()
        inserts = self.cursor.executemany.call_args_list
        self.assertEqual(len(inserts), 2)
        self.assertIn('pms_room_type_forecast', inserts[0].args[0])
        self.assertIn('room_revenue', inserts[0].args[0])
        self.assertIn('pms_room_type_hourly_status', inserts[1].args[0])
        self.assertEqual(inserts[0].args[1][0]['room_type_id'], 'unified-room')
        self.assertEqual(inserts[0].args[1][0]['room_revenue'], '120')
        for call in self.cursor.execute.call_args_list:
            if call.args[0].startswith('DELETE'):
                self.assertIn('source_platform=%s', call.args[0])
                self.assertIn('PMS（慧云）', call.args[1])

    def test_other_pms_collision_rolls_back_before_deletion(self):
        self.cursor.fetchall.side_effect = [[('VT', 'PMS（美团）')]]
        with self.assertRaisesRegex(ValueError, '其他PMS'):
            storage.sync_mysql(self.rows, self.connection)
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()
        self.cursor.executemany.assert_not_called()

    def test_second_table_failure_rolls_back_both_tables(self):
        self.cursor.executemany.side_effect = [None, RuntimeError('insert failed')]
        with self.assertRaisesRegex(RuntimeError, 'insert failed'):
            storage.sync_mysql(self.rows, self.connection)
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()

    def test_missing_money_never_starts_write(self):
        self.rows[0]['adr'] = None
        with self.assertRaises(ValueError):
            storage.sync_mysql(self.rows, self.connection)
        self.connection.cursor.assert_not_called()

    def test_collector_writes_only_when_not_dry_run(self):
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run), tempfile.TemporaryDirectory(dir=ROOT) as folder:
                args = ['collector.py', '--output-dir', folder] + (['--dry-run'] if dry_run else [])
                with patch.dict(os.environ, {'HOTEL_ID': 'test-hotel'}), patch.object(sys, 'argv', args), \
                     patch.object(collector, 'request', return_value=fixture()), patch.object(storage, 'sync_mysql') as sync:
                    collector.main()
                self.assertEqual(sync.call_count, int(not dry_run))
                if not dry_run:
                    self.assertEqual(sync.call_args.args[0][0]['hotel_id'], 'test-hotel')

    def test_only_live_mode_requires_three_standard_tables(self):
        spec = ('huiyun', ROOT / 'huiyun_pms/collector.py', [])
        self.assertEqual(collection_schema.required_tables('pms_fetch', [spec]),
                         {'hotel_room_type_mapping', 'pms_room_type_forecast', 'pms_room_type_hourly_status'})
        self.assertEqual(collection_schema.required_tables('pms_fetch', [(spec[0], spec[1], ['--dry-run'])]), set())


if __name__ == '__main__':
    unittest.main()
