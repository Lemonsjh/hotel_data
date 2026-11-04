from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'OTA采集服务'))
import promotion_routes as routes
import promotion_queue as queue
import promotion_task_executor as executor

SETTINGS = {'hotel': {'hotel_id': 'hotel'}, 'mysql': {'database': 'fake'},
            'ctrip': {'enabled': True}, 'meituan': {'enabled': False}}
PLAN = {'launch_id': '10', 'promotion_name': '测试推广',
        'promotion_status': 'RUNNING', 'campaign_status': 7, 'account_id': '123'}


class ManagementPageTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        routes.register(app)
        self.client = app.test_client()
        patch.object(routes.runner, 'load_settings', return_value=SETTINGS).start()
        patch.object(routes, 'page', side_effect=lambda title, body, active: body).start()
        patch.object(queue, 'recent', return_value=[]).start()
        self.addCleanup(patch.stopall)

    def test_ctrip_page_shows_insufficient_balance_and_offers_pause_for_seven(self):
        with patch.object(routes.ctrip_promotion_controls, 'current_promotions', return_value=[PLAN]):
            result = self.client.get('/promotion-controls?platform=ctrip')
        text = result.get_data(as_text=True)
        self.assertIn('携程金字塔推广管理', text)
        self.assertIn('<th>推广名称</th>', text)
        self.assertNotIn('<th>计划名称</th>', text)
        self.assertIn('<strong>测试推广</strong>', text)
        self.assertIn("pill warn'>账户余额不足", text)
        self.assertNotIn('已开启', text)
        self.assertNotIn('原始状态：', text)
        self.assertIn('/promotion-controls/ctrip/10/pause', text)

    def test_confirmed_click_only_enqueues_and_does_not_modify_remote_campaign(self):
        with patch.object(routes.ctrip_promotion_controls, 'find_promotion', return_value=PLAN), \
             patch.object(queue, 'enqueue', return_value=42) as enqueue, \
             patch.object(routes.ctrip_promotion_controls, 'control_promotion') as modify:
            result = self.client.post('/promotion-controls/ctrip/10/pause', data={'confirmation': '暂停'})
        self.assertEqual(result.status_code, 302)
        self.assertIn('platform=ctrip', result.location)
        enqueue.assert_called_once_with(SETTINGS, 'ctrip', PLAN, 'pause')
        modify.assert_not_called()

    def test_missing_confirmation_and_unknown_platform_never_enqueue(self):
        with patch.object(queue, 'enqueue') as enqueue:
            self.client.post('/promotion-controls/ctrip/10/pause', data={})
            result = self.client.post('/promotion-controls/unknown/10/pause', data={'confirmation': '暂停'})
        self.assertEqual(result.status_code, 400)
        enqueue.assert_not_called()

    def test_legacy_meituan_link_still_enqueues_for_meituan(self):
        with patch.object(routes.promotion_controls, 'find_promotion', return_value=PLAN), \
             patch.object(queue, 'enqueue', return_value=1) as enqueue:
            self.client.post('/promotion-controls/10/pause', data={'confirmation': '暂停'})
        self.assertEqual(enqueue.call_args.args[1], 'meituan')


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = None
        self.cursor.lastrowid = 42
        patch.object(queue, 'connect_database', return_value=self.connection).start()
        patch.object(queue, 'ensure_tables').start()
        self.addCleanup(patch.stopall)

    def test_ctrip_task_is_scoped_to_hotel_and_platform(self):
        self.assertEqual(queue.enqueue(SETTINGS, 'ctrip', PLAN, 'pause'), 42)
        self.assertEqual(self.cursor.execute.call_args.args[1], ('hotel', 'ctrip', '10', 'pause'))
        self.connection.commit.assert_called_once()
        queue.finish(SETTINGS, 'ctrip', 42, 'success')
        sql, params = self.cursor.execute.call_args.args
        self.assertIn('hotel_id=%s AND platform=%s', sql)
        self.assertEqual(params[-3:], (42, 'hotel', 'ctrip'))

    def test_duplicate_task_is_not_enqueued(self):
        self.cursor.fetchone.return_value = (9,)
        with self.assertRaisesRegex(ValueError, '已在待执行'):
            queue.enqueue(SETTINGS, 'ctrip', PLAN, 'pause')
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.connection.rollback.assert_called_once()


class ExecutorTests(unittest.TestCase):
    def test_ctrip_executes_in_existing_queue_without_meituan_operations(self):
        task = {'id': 42, 'launch_id': '10', 'action': 'pause'}
        with patch.object(executor.runner, 'load_settings', return_value=SETTINGS), \
             patch('update_guard.ensure_not_updating'), \
             patch.object(queue, 'fail_interrupted') as interrupted, \
             patch.object(queue, 'claim', return_value=[task]) as claim, \
             patch.object(queue, 'finish') as finish, \
             patch.object(executor.ctrip_promotion_controls, 'find_promotion', return_value=PLAN), \
             patch.object(executor.ctrip_promotion_controls, 'control_promotion', return_value='已关闭') as modify, \
             patch.object(executor.promotion_controls, 'control_promotion') as meituan:
            self.assertEqual(executor.run(5), 0)
        interrupted.assert_called_once_with(SETTINGS, 'ctrip')
        claim.assert_called_once_with(SETTINGS, 'ctrip', 5)
        modify.assert_called_once_with(SETTINGS, PLAN, 'pause')
        finish.assert_called_once_with(SETTINGS, 'ctrip', 42, 'success')
        meituan.assert_not_called()


if __name__ == '__main__':
    unittest.main()
