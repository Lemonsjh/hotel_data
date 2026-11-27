import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "美团OTA数据采集代码"))
import meituan_pending_reviews as pending
import meituan_review_reply_task as reply


def page(index, ids, count=12):
    return {"data": {"offset": index, "limit": 10, "total": 2000, "mtUnRepliedNum": count,
                     "dpUnRepliedNum": 0, "commentList": [{"id": i} for i in ids]}}


class MeituanPendingTests(unittest.TestCase):
    def test_paginate_by_pending_count_and_clear_empty_dianping(self):
        client = Mock()
        client.fetch_page.side_effect = [page(1, range(1, 11)), page(2, [11, 12]), page(1, [])]
        with patch.object(pending.time, "sleep"):
            rows = pending.collect_channel(client, "美团", "meituan")
        self.assertEqual([r[0] for r in rows], [str(i) for i in range(1, 13)])
        client.fetch_page.assert_called_with(2, 10, reply_type=3)
        self.assertEqual(pending.collect_channel(client, "大众点评", "dianping"), [])

    def test_reject_partial_duplicate_or_changed_count(self):
        for second in [page(2, [11]), page(2, [10, 11]), page(2, [11, 12], 13)]:
            client = Mock()
            client.fetch_page.side_effect = [page(1, range(1, 11)), second]
            with patch.object(pending.time, "sleep"), self.assertRaises(ValueError):
                pending.collect_channel(client, "美团", "meituan")

    def test_confirmed_reply_removes_only_matching_snapshot(self):
        db = MagicMock()
        db.__enter__.return_value = db
        cursor = db.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (1,)
        task = dict(channel_source="dianping", id=1, review_id="2", reply_content="谢谢")
        with patch.object(reply, "connect_mysql", return_value=db), patch.object(reply, "POI_ID", "3"):
            reply.complete_task("hotel", task, newly_replied=True)
        deletes = [args[1] for args, _ in cursor.execute.call_args_list if args[0].startswith("DELETE")]
        self.assertEqual(deletes, [("hotel", "dianping", "2", "3")])
        db.commit.assert_called_once()
