import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "携程OTA数据采集代码"))
import ctrip_review_reply_task as reply


class CtripReviewReplyTests(unittest.TestCase):
    def test_first_reply_for_each_channel_uses_submission_confirmation(self):
        for channel, source in (("ctrip", 1), ("zhixing", 7), ("qunar", 9), ("tongcheng", 8)):
            with self.subTest(channel=channel):
                client = Mock()
                target = {"commentId": 11, "sourceId": source, "enableReply": True}
                client.fetch_page.return_value = {"masterHotelId": 3, "commentlist": [target]}
                client.session.post.return_value.status_code = 200
                client.session.post.return_value.json.return_value = {
                    "ResponseStatus": {"Ack": "Success", "Errors": []}, "resStatus": {"rcode": 200},
                    **({} if channel == "qunar" else {"replystatus": 1, "commentid": 22}),
                }
                self.assertIsNotNone(reply.execute_reply(client, channel, "11", "3", "测试回复"))
                body = client.session.post.call_args.kwargs["json"]
                self.assertEqual((body["commentid"], body["sourceid"], body["fbcommentid"]), ("11", source, -1))
                client.fetch_page.assert_called_once()

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

    def test_qunar_rejects_explicit_failure_even_with_success_ack(self):
        client = Mock()
        client.fetch_page.return_value = {"masterHotelId": 3, "commentlist": [
            {"commentId": 11, "sourceId": 9, "enableReply": True}
        ]}
        client.session.post.return_value.status_code = 200
        client.session.post.return_value.json.return_value = {
            "ResponseStatus": {"Ack": "Success", "Errors": []}, "resStatus": {"rcode": 200}, "replystatus": 0,
        }
        with self.assertRaisesRegex(ValueError, "接口未确认回复成功"):
            reply.execute_reply(client, "qunar", "11", "3", "测试回复")
        client.session.post.assert_called_once()
