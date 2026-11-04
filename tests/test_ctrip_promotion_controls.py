from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'OTA采集服务'))
import ctrip_promotion_controls as control

SETTINGS = {'ctrip': {'enabled': True, 'cookie': 'fake', 'hotel_id': '123'}}


def response(status=7, identifier=10):
    return {'data': [{'campaignId': identifier, 'campaignName': '测试推广', 'accountId': 123,
                      'campaignStatus': status}], 'totalRecord': 1, 'totalPage': 1,
            'resStatus': {'rcode': 200}, 'ResponseStatus': {'Ack': 'Success'}}


class CtripPromotionTests(unittest.TestCase):
    def setUp(self):
        patch.object(control, 'save_snapshot').start()
        self.addCleanup(patch.stopall)

    def client(self):
        factory = patch.object(control.requests, 'Session', return_value=MagicMock())
        factory.start()
        self.addCleanup(factory.stop)
        return control.Client(SETTINGS)

    def test_three_is_closed_other_valid_numbers_are_enabled_and_raw_is_retained(self):
        client = self.client()
        for code in (1, 2, 3, 4, 6, 7):
            with patch.object(client, 'request', return_value=response(code)):
                plan = client.promotions()[0]
            self.assertEqual(plan['campaign_status'], code)
            self.assertEqual(plan['promotion_status'], 'PAUSED' if code == 3 else 'RUNNING')

    def test_missing_invalid_status_or_wrong_hotel_is_rejected(self):
        client = self.client()
        for code in (None, '', True, 'invalid'):
            with patch.object(client, 'request', return_value=response(code)), self.assertRaises(RuntimeError):
                client.promotions()
        payload = response()
        payload['data'][0]['accountId'] = 999
        with patch.object(client, 'request', return_value=payload), self.assertRaisesRegex(RuntimeError, '酒店'):
            client.promotions()

    def test_all_pages_are_read_and_repeated_or_truncated_pages_fail(self):
        client = self.client()
        first, second = response(identifier=10), response(identifier=11)
        for item in (first, second):
            item.update(totalRecord=2, totalPage=2)
        with patch.object(client, 'request', side_effect=[first, second]) as request, patch.object(control.time, 'sleep'):
            self.assertEqual(len(client.promotions()), 2)
            self.assertEqual([c.kwargs['pageIndex'] for c in request.call_args_list], [0, 1])
        for last in (first, {**second, 'data': []}):
            with patch.object(client, 'request', side_effect=[first, last]), patch.object(control.time, 'sleep'), self.assertRaises(RuntimeError):
                client.promotions()

    def test_empty_list_is_valid(self):
        client = self.client()
        with patch.object(client, 'request', return_value={**response(), 'data': [], 'totalRecord': 0, 'totalPage': 0}):
            self.assertEqual(client.promotions(), [])

    def test_request_requires_business_success_and_does_not_reuse_trace_or_tokens(self):
        client = self.client()
        for payload in ({'data': True}, {**response(), 'resStatus': {'rcode': 500}},
                        {**response(), 'ResponseStatus': {'Ack': 'Failure'}}):
            client.session.post.return_value.json.return_value = payload
            with self.assertRaises(RuntimeError):
                client.request('queryPromotionDetails', pageIndex=0)
        client.session.post.return_value.json.return_value = response()
        client.request('queryPromotionDetails', pageIndex=0)
        call = client.session.post.call_args
        self.assertEqual(call.args[0], control.BASE_URL + 'queryPromotionDetails')
        self.assertNotIn('rmsToken', str(call.kwargs['json']))

    def test_pause_and_recover_confirm_by_reading_back_including_balance_status(self):
        for action, before, after, target in [('pause', 7, 3, 3), ('recover', 3, 7, 2)]:
            with self.subTest(action=action):
                client = self.client()
                replies = [response(before), {'data': True, 'resStatus': {'rcode': 200},
                                              'ResponseStatus': {'Ack': 'Success'}}, response(after)]
                client.session.post.return_value.json.side_effect = replies
                with patch.object(control, 'Client', return_value=client):
                    message = control.control_promotion(SETTINGS, {'launch_id': '10', 'account_id': '123'}, action)
                calls = client.session.post.call_args_list
                self.assertEqual([c.args[0].rsplit('/', 1)[-1] for c in calls],
                                 ['queryPromotionDetails', 'modifyPromotionStatus', 'queryPromotionDetails'])
                self.assertEqual(calls[1].kwargs['json']['status'], target)
                self.assertIn(str(after), message)

    def test_already_enabled_does_not_issue_another_recovery(self):
        client = self.client()
        client.session.post.return_value.json.return_value = response(7)
        with patch.object(control, 'Client', return_value=client):
            control.control_promotion(SETTINGS, {'launch_id': '10', 'account_id': '123'}, 'recover')
        self.assertEqual(client.session.post.call_count, 1)

    def test_failed_write_or_unconfirmed_readback_is_not_reported_as_success_or_retried(self):
        for confirmed in (False, True):
            client = self.client()
            replies = [response(7), {'data': confirmed, 'resStatus': {'rcode': 200},
                                    'ResponseStatus': {'Ack': 'Success'}}, response(7)]
            client.session.post.return_value.json.side_effect = replies
            with patch.object(control, 'Client', return_value=client), self.assertRaises(RuntimeError):
                control.control_promotion(SETTINGS, {'launch_id': '10', 'account_id': '123'}, 'pause')
            modifications = [c for c in client.session.post.call_args_list if c.args[0].endswith('modifyPromotionStatus')]
            self.assertEqual(len(modifications), 1)

    def test_campaign_missing_or_account_changed_prevents_modification(self):
        client = self.client()
        client.session.post.return_value.json.return_value = response()
        for plan in ({'launch_id': '99', 'account_id': '123'}, {'launch_id': '10', 'account_id': '456'}):
            with patch.object(control, 'Client', return_value=client), self.assertRaises(RuntimeError):
                control.control_promotion(SETTINGS, plan, 'pause')
        self.assertTrue(all(c.args[0].endswith('queryPromotionDetails') for c in client.session.post.call_args_list))


if __name__ == '__main__':
    unittest.main()
