"""本店稀疏日期正常汇总，同业缺失及空本店不覆盖已有结果。"""
import io
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "携程OTA数据采集代码"))
import ctrip_flow_conversion_data as flow


class FlowDateTests(unittest.TestCase):
    def setUp(self):
        self.start, self.end = date(2026, 9, 30), date(2026, 10, 29)
        self.hotel = [{"hotelId": 9, "date": str(self.start + timedelta(days=i)),
                       "listExposure": 10, "detailExposure": 2, "orderFillingNum": 1,
                       "orderSubmitNum": 1} for i in range(30)]
        self.peer = [dict(row, hotelId=-1) for row in self.hotel]

    def build(self, rows):
        with patch.dict("os.environ", {"HOTEL_ID": "test"}):
            result = flow.build_row(rows, "qunar", self.start, self.end,
                                    datetime(2026, 10, 30), 20, 30, {i: 1 for i in range(6, 11)})
        return dict(zip(flow.HEADERS, result))

    def test_complete_period_still_has_same_totals(self):
        output = io.StringIO()
        with redirect_stdout(output):
            row = self.build(self.hotel + self.peer)
        self.assertEqual(row["list_exposure"], 300)
        self.assertEqual(row["peer_list_exposure"], 300)
        self.assertEqual(output.getvalue(), "")

    def test_reported_missing_october_7_is_aggregated_and_logged(self):
        hotel = [row for row in self.hotel if row["date"] != "2026-10-07"]
        output = io.StringIO()
        with redirect_stdout(output):
            row = self.build(hotel + self.peer)
        self.assertEqual(row["list_exposure"], 290)
        self.assertEqual(row["peer_list_exposure"], 300)
        self.assertEqual(row["period_start_date"], self.start)
        self.assertEqual(row["period_end_date"], self.end)
        self.assertIn("2026-10-07", output.getvalue())
        self.assertIn("29 天汇总", output.getvalue())

    def test_empty_hotel_or_missing_peer_still_fails(self):
        for rows in (self.peer, self.hotel + self.peer[1:]):
            with self.subTest(rows=len(rows)), self.assertRaisesRegex(RuntimeError, "流量日期不完整"):
                self.build(rows)

    def test_out_of_period_hotel_data_still_fails(self):
        extra = dict(self.hotel[0], date="2026-09-29")
        with self.assertRaisesRegex(RuntimeError, "流量日期不完整"):
            self.build(self.hotel + self.peer + [extra])


if __name__ == "__main__":
    unittest.main()
