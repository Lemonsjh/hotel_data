import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "携程OTA数据采集代码"))
import ctrip_review_reply_task as reply


class CtripReviewReplyTests(unittest.TestCase):
    def test_first_reply_for_each_channel_requires_readback(self):
        for channel, source in (("ctrip", 1), ("zhixing", 7), ("qunar", 9), ("tongcheng", 8)):
            with self.subTest(channel=channel):
                client = Mock()
                target = {"commentId": 11, "sourceId": source, "enableReply": True}
                submitted = {"replyId": 22, "replyContent": "测试回复", "isOwner": 1}
                updated = {**target, **({"qReplyList": [submitted]} if channel == "qunar" else {"replyDetail": submitted})}
                client.fetch_page.side_effect = [
                    {"masterHotelId": 3, "commentlist": [row]} for row in (target, updated)
                ]
                client.session.post.return_value.status_code = 200
                client.session.post.return_value.json.return_value = {
                    "ResponseStatus": {"Ack": "Success"}, "resStatus": {"rcode": 200},
                    "replystatus": 1, "commentid": 22,
                }
                reply.execute_reply(client, channel, "11", "3", "测试回复")
                body = client.session.post.call_args.kwargs["json"]
                self.assertEqual((body["commentid"], body["sourceid"], body["fbcommentid"]), ("11", source, -1))
                self.assertEqual(client.fetch_page.call_count, 2)

    def test_existing_qunar_reply_does_not_post_again(self):
        client = Mock()
        client.fetch_page.return_value = {"masterHotelId": 3, "commentlist": [
            {"commentId": 11, "sourceId": 9, "enableReply": True, "qReplyList": [
                {"replyId": 22, "replyContent": "已回复", "isOwner": 1}
            ]}
        ]}
        reply.execute_reply(client, "qunar", "11", "3", "已回复")
        with self.assertRaisesRegex(ValueError, "已有其他回复"):
            reply.execute_reply(client, "qunar", "11", "3", "其他内容")
        with self.assertRaisesRegex(ValueError, "未确认当前酒店"):
            reply.execute_reply(client, "qunar", "11", "4", "其他内容")
        client.session.post.assert_not_called()

    def test_success_response_without_matching_reply_is_unconfirmed(self):
        client = Mock()
        client.fetch_page.return_value = {"masterHotelId": 3, "commentlist": [
            {"commentId": 11, "sourceId": 1, "enableReply": True}
        ]}
        client.session.post.return_value.status_code = 200
        client.session.post.return_value.json.return_value = {
            "ResponseStatus": {"Ack": "Success"}, "resStatus": {"rcode": 200}, "replystatus": 1,
        }
        with self.assertRaisesRegex(ValueError, "提交后未查到"):
            reply.execute_reply(client, "ctrip", "11", "3", "测试回复")
        client.session.post.assert_called_once()
