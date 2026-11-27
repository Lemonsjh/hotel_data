import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "携程OTA数据采集代码"))
import ctrip_pending_reviews as pending
import ctrip_review_reply_task as reply
from ctrip_review_detail_data import CtripReviewDetailError, build_payload


def page(source, ids, *, total=None, pages=1, index=1, hotel=123):
    return {
        "ResponseStatus": {"Ack": "Success", "Errors": []}, "resStatus": {"rcode": 200},
        "masterHotelId": hotel, "commentCount": len(ids) if total is None else total,
        "pageCount": pages, "currentPageIndex": index,
        "commentlist": [{"commentId": value, "sourceId": source, "enableReply": True} for value in ids],
    }


class PendingReviewsTests(unittest.TestCase):
    def test_pending_payload_and_multiple_pages(self):
        original = build_payload(1, 10, "7")
        filtered = build_payload(1, 10, "7", pending=True)
        self.assertEqual(filtered, {**original, "catalogTab": "NotFeedBack", "catalogName": "待回复"})
        client = Mock()
        client.fetch_page.side_effect = [
            page(7, list(range(1, 11)), total=11, pages=2),
            page(7, [11], total=11, pages=2, index=2),
        ]
        with patch.object(pending.time, "sleep"):
            hotel, rows = pending.collect_channel(client, "zhixing", "123")
        self.assertEqual(hotel, "123")
        self.assertEqual([row[0] for row in rows], [str(i) for i in range(1, 12)])
        self.assertEqual(client.fetch_page.call_args_list, [call(1, 10, "7", pending=True), call(2, 10, "7", pending=True)])

    def test_incomplete_or_wrong_channel_does_not_produce_a_snapshot(self):
        for second in (
            page(7, [], total=11, pages=2, index=2),
            page(7, [1], total=11, pages=2, index=2),
            page(9, [11], total=11, pages=2, index=2),
            page(7, [11], total=11, pages=2, index=2, hotel=456),
            page(7, [11], total=12, pages=2, index=2),
        ):
            with self.subTest(second=second), patch.object(pending.time, "sleep"):
                client = Mock()
                client.fetch_page.side_effect = [page(7, list(range(1, 11)), total=11, pages=2), second]
                with self.assertRaises(CtripReviewDetailError):
                    pending.collect_channel(client, "zhixing", "123")

    def test_failed_channel_keeps_snapshot_while_empty_channels_are_replaced(self):
        client = Mock()
        client.fetch_page.side_effect = [page(1, [], pages=0), page(9, [11]), page(9, [], pages=0), page(8, [], pages=0)]
        db = MagicMock()
        db.__enter__.return_value = db
        cursor = db.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (1,)
        with patch.object(pending, "connect_mysql", return_value=db):
            self.assertEqual(pending.sync_pending_reviews(client, "hotel"), 1)
        deletes = [args[1] for args, _ in cursor.execute.call_args_list if args[0].startswith("DELETE")]
        self.assertEqual(deletes, [("hotel", "ctrip"), ("hotel", "qunar"), ("hotel", "tongcheng")])
        self.assertEqual(db.commit.call_count, 3)
        cursor.executemany.assert_not_called()

    def test_replace_filters_confirmed_replies_and_rolls_back_failed_insert(self):
        db, cursor = Mock(), Mock()
        cursor.fetchone.return_value = (1,)
        cursor.fetchall.return_value = [("100",)]
        rows = [(key, None, "", None, "内容", "", True, None) for key in ("100", "200")]
        self.assertEqual(pending.replace_snapshot(db, cursor, "hotel", "zhixing", "123", rows), 1)
        inserted = cursor.executemany.call_args.args[1]
        self.assertEqual([row[4] for row in inserted], ["200"])
        db.commit.assert_called_once()
        db.reset_mock()
        cursor.executemany.side_effect = RuntimeError("insert failed")
        with self.assertRaisesRegex(RuntimeError, "insert failed"):
            pending.replace_snapshot(db, cursor, "hotel", "zhixing", "123", rows)
        db.rollback.assert_called_once()
        db.commit.assert_not_called()


class PendingReplyTests(unittest.TestCase):
    def test_success_resolves_snapshot_only_review_and_removes_it(self):
        db, cursor, client = Mock(), Mock(), Mock()
        cursor.rowcount = 1
        cursor.fetchone.side_effect = [(1,), (1,), ("123",)]
        cursor.fetchall.return_value = []
        client.fetch_page.return_value = page(7, ["11"])
        with patch.object(reply, "submit_reply") as submit:
            self.assertTrue(reply.process_task(client, db, cursor, "hotel", (2, "智行", "11", "回复")))
        client.fetch_page.assert_called_once_with(1, 10, "7", pending=True)
        submit.assert_called_once_with(client, "11", "7", "回复")
        deletes = [args[1] for args, _ in cursor.execute.call_args_list if args[0].startswith("DELETE")]
        self.assertEqual(deletes, [("hotel", "zhixing", "11", "123")])
        db.commit.assert_called_once()

    def test_failed_reply_keeps_snapshot_and_busy_channel_keeps_task_pending(self):
        db, cursor, client = Mock(), Mock(), Mock()
        cursor.rowcount = 1
        cursor.fetchone.side_effect = [(1,), (1,), ("123",)]
        cursor.fetchall.return_value = [(99, "123")]
        client.fetch_page.return_value = page(7, ["11"])
        with patch.object(reply, "submit_reply", side_effect=ValueError("failed")):
            self.assertFalse(reply.process_task(client, db, cursor, "hotel", (2, "智行", "11", "回复")))
        self.assertFalse(any(args[0].startswith("DELETE") for args, _ in cursor.execute.call_args_list))
        db.commit.assert_not_called()
        db.rollback.assert_called_once()

        @contextmanager
        def busy(*args):
            yield False

        cursor.reset_mock()
        with patch.object(reply, "channel_lock", busy):
            self.assertTrue(reply.process_task(client, db, cursor, "hotel", (2, "智行", "11", "回复")))
        cursor.execute.assert_not_called()
