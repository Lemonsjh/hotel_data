"""计划快照使用真实事务验证替换、空列表和失败回滚。"""
from datetime import datetime
from pathlib import Path
import os
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'OTA采集服务'))
import ctrip_promotion_controls as control

SETTINGS = {'hotel': {'hotel_id': 'local'}, 'ctrip': {'hotel_name': '测试酒店'}}
PLAN = {'launch_id': '10', 'promotion_name': '推广一', 'account_id': '123', 'campaign_status': 7,
        'promotion_status': 'RUNNING', 'snapshot_time': datetime(2026, 11, 3, 12)}


class Cursor:
    def __init__(self, connection):
        self.cursor = connection.cursor()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cursor.close()

    def execute(self, sql, params):
        self.cursor.execute(sql.replace('%s', '?'), params)

    def executemany(self, sql, params):
        self.cursor.executemany(sql.replace('%s', '?'), [
            tuple(value.isoformat(' ') if isinstance(value, datetime) else value for value in row)
            for row in params])

    @property
    def description(self):
        return self.cursor.description

    def fetchall(self):
        return self.cursor.fetchall()


class Connection:
    def __init__(self, database):
        self.database = database

    def cursor(self):
        return Cursor(self.database)

    def commit(self):
        self.database.commit()

    def rollback(self):
        self.database.rollback()

    def close(self):
        pass


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute(f'CREATE TABLE {control.TABLE} (hotel_id TEXT, hotel_name TEXT, '
                        'campaign_id TEXT NOT NULL, campaign_name TEXT, account_id TEXT, '
                        'campaign_status INTEGER, promotion_status TEXT, snapshot_time TEXT, '
                        'PRIMARY KEY (hotel_id, campaign_id))')
        patch.object(control, 'connect_database', return_value=Connection(self.db)).start()
        patch.object(control, 'ensure_tables').start()
        self.addCleanup(patch.stopall)
        control.save_snapshot(SETTINGS, [PLAN])
        control.save_snapshot({**SETTINGS, 'hotel': {'hotel_id': 'other'}}, [PLAN])

    def test_management_reads_saved_name_and_status_without_network(self):
        with patch.object(control, 'Client') as client:
            plans = control.current_promotions(SETTINGS)
        client.assert_not_called()
        self.assertEqual(len(plans), 1)
        self.assertEqual((plans[0]['promotion_name'], plans[0]['campaign_status']), ('推广一', 7))

    def test_replace_and_valid_empty_result_only_affect_current_hotel(self):
        control.save_snapshot(SETTINGS, [{**PLAN, 'launch_id': '11', 'promotion_name': '新推广'}])
        self.assertEqual([p['launch_id'] for p in control.current_promotions(SETTINGS)], ['11'])
        control.save_snapshot(SETTINGS, [])
        self.assertEqual(control.current_promotions(SETTINGS), [])
        self.assertEqual(self.db.execute(f'SELECT hotel_id FROM {control.TABLE}').fetchall(), [('other',)])

    def test_failed_insert_rolls_back_deleted_snapshot(self):
        with self.assertRaises(sqlite3.IntegrityError):
            control.save_snapshot(SETTINGS, [{**PLAN, 'launch_id': None}])
        self.assertEqual(control.current_promotions(SETTINGS)[0]['promotion_name'], '推广一')

    def test_failed_api_leaves_snapshot_unchanged(self):
        with patch.object(control, 'Client') as client:
            client.return_value.__enter__.return_value.promotions.side_effect = RuntimeError('请求失败')
            with self.assertRaises(RuntimeError):
                control.collect_snapshot(SETTINGS)
        self.assertEqual(control.current_promotions(SETTINGS)[0]['campaign_status'], 7)

    def test_successful_collection_persists_plan_and_returns_count(self):
        with patch.object(control, 'Client') as client:
            client.return_value.__enter__.return_value.promotions.return_value = [{**PLAN, 'campaign_status': 3}]
            self.assertEqual(control.collect_snapshot(SETTINGS), 1)
        self.assertEqual(control.current_promotions(SETTINGS)[0]['campaign_status'], 3)


class CollectionEntryTests(unittest.TestCase):
    def test_existing_performance_task_collects_plans_with_same_hotel_and_database(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '携程OTA数据采集代码'))
        import ctrip_promotion_performance_data as entry
        with patch.dict(os.environ, {'HOTEL_ID': 'local', 'CTRIP_HOTEL_ID': '123', 'CTRIP_COOKIE': 'fake'}), \
             patch.object(entry, 'FlowBrowserClient'), patch.object(entry, 'build_row', return_value=['metrics']), \
             patch.object(entry, 'sync_table') as sync, patch.object(entry, 'collect_snapshot', return_value=1) as collect:
            self.assertEqual(entry.main(), 0)
        sync.assert_called_once_with(entry.TABLE_NAME, entry.HEADERS, [['metrics']])
        settings = collect.call_args.args[0]
        self.assertEqual(settings['hotel']['hotel_id'], 'local')
        self.assertEqual(settings['ctrip']['hotel_id'], '123')
        self.assertIs(settings['mysql'], entry.DB_CONFIG)


if __name__ == '__main__':
    unittest.main()
