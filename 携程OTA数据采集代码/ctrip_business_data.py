from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from ctrip_config import COOKIE, DEFAULT_HOTEL_NAME, EXTRA_HEADERS, USER_AGENT

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import OUTPUT_DIR, sync_table


ENDPOINTS = {
    "hotel_advice": "/datacenter/api/dataCenter/report/getHotelAdvice",
    "real_time": "/datacenter/api/dataCenter/report/getDayReportRealTimeDate",
    "flow_compete": "/datacenter/api/dataCenter/report/getDayReportFlowCompete",
    "visitor_title": "/datacenter/api/dataCenter/current/fetchVisitorTitleV2",
    "capacity": "/datacenter/api/dataCenter/current/fetchCapacityOverViewV4",
    "tensity": "/datacenter/api/dataCenter/current/fetchTensityOverViewV1",
    "hotel_min_price": "/datacenter/api/dataCenter/current/queryHotelMinPriceV1",
    "order_trend": "/datacenter/api/dataCenter/current/queryOrderTrendV1",
    "market_details": "/datacenter/api/dataCenter/sale/queryMarketDetailsV1",
    "rank": "/datacenter/api/biddingajax/fetchCurrentHotelSeqInfoV1",
    "flow_transfor": "/datacenter/api/inland/marketanalysis/flowanalysis/queryFlowTransforNewV1?hostType=Ebooking&v=0.123",
    "scan_flow": "/datacenter/api/inland/marketanalysis/flowanalysis/queryScanFlowDetailsV2?hostType=Ebooking&v=0.123",
}
DEFAULT_OUTPUT = OUTPUT_DIR / "ctrip_ota_business_metrics.xlsx"
COMPETITION_CAPTURE = OUTPUT_DIR / "ctrip_competition_profile_dom_capture.json"
COMPETITION_REALTIME_CAPTURE = OUTPUT_DIR / "ctrip_competition_profile_realtime_dom_capture.json"
SHEET_NAME = "采集明细"
HOTEL_ID = os.environ.get("HOTEL_ID", "").strip()

HEADERS = [
    "snapshot_time",
    "stats_period_type",
    "business_date",
    "period_days",
    "hotel_name",
    "metric_group",
    "metric_name",
    "metric_display_name",
    "metric_value",
    "metric_unit",
    "compare_label",
    "compare_value",
    "competitor_rank",
    "peer_average",
]

METRIC_LABELS = {
    "booking_order_count": ("\u7ecf\u8425\u5bf9\u6bd4", "\u9884\u8ba2\u8ba2\u5355\u91cf"),
    "booking_sales_amount": ("\u7ecf\u8425\u5bf9\u6bd4", "\u9884\u8ba2\u9500\u552e\u989d"),
    "booking_room_night": ("\u7ecf\u8425\u6536\u76ca", "\u9884\u8ba2\u95f4\u591c\u91cf"),
    "checkout_sales_amount": ("\u7ecf\u8425\u6536\u76ca", "\u79bb\u5e97\u9500\u552e\u989d"),
    "checkout_room_night": ("\u7ecf\u8425\u6536\u76ca", "\u79bb\u5e97\u95f4\u591c\u91cf"),
    "checkout_conversion_rate": ("\u7ecf\u8425\u6536\u76ca", "\u79bb\u5e97\u6210\u4ea4\u7387"),
    "checkout_average_sale_price": ("\u7ecf\u8425\u6536\u76ca", "\u79bb\u5e97\u5e73\u5747\u5356\u4ef7"),
    "inhouse_room_night": ("\u7ecf\u8425\u5bf9\u6bd4", "\u5728\u5e97\u95f4\u591c"),
    "occupancy_rate": ("\u7ecf\u8425\u5bf9\u6bd4", "\u51fa\u79df\u7387"),
    "room_tensity_rate": ("\u5b9e\u65f6\u7ecf\u8425", "\u7d27\u5f20\u5ea6"),
    "lowest_sale_price": ("\u5b9e\u65f6\u7ecf\u8425", "\u5f53\u524d\u6700\u4f4e\u4ef7"),
    "ctrip_app_visitor_count": ("\u7ecf\u8425\u5bf9\u6bd4", "\u643a\u7a0bAPP\u8bbf\u5ba2"),
    "ctrip_app_conversion_rate": ("\u7ecf\u8425\u5bf9\u6bd4", "\u643a\u7a0bAPP\u8f6c\u5316\u7387"),
    "realtime_booking_order_count": ("实时经营", "实时预订订单"),
    "realtime_inhouse_room_night": ("实时经营", "实时在店间夜"),
    "realtime_ctrip_order_count": ("\u5b9e\u65f6\u7ecf\u8425", "\u643a\u7a0b\u8ba2\u5355\u91cf"),
    "realtime_qunar_order_count": ("\u5b9e\u65f6\u7ecf\u8425", "\u53bb\u54ea\u513f\u8ba2\u5355\u91cf"),
    "realtime_elong_order_count": ("\u5b9e\u65f6\u7ecf\u8425", "\u827a\u9f99\u8ba2\u5355\u91cf"),
    "realtime_visitor_count": ("实时经营", "实时访客量"),
    "realtime_rank": ("实时经营", "实时排名"),
    "qunar_realtime_visitor_count": ("实时经营", "去哪儿实时访客量"),
    "list_page_exposure_count": ("流量漏斗", "列表页曝光量"),
    "detail_page_visitor_count": ("流量漏斗", "详情页访客量"),
    "order_page_visitor_count": ("流量漏斗", "订单页访客量"),
    "order_submit_count": ("流量漏斗", "订单提交数"),
    "exposure_conversion_rate": ("流量漏斗", "曝光转化率"),
    "order_conversion_rate": ("流量漏斗", "下单转化率"),
    "transaction_conversion_rate": ("流量漏斗", "成交转化率"),
    "visitor_order_conversion_rate": ("流量漏斗", "访客订单转化率"),
    "detail_page_uv_count": ("流量漏斗", "详情页UV"),
    "detail_page_order_count": ("流量漏斗", "详情页订单数"),
    "lost_room_night_count_7d": ("流失诊断", "近7天流失间夜"),
    "lost_visitor_count_7d": ("流失诊断", "近7天流失访客"),
    "lost_order_amount_7d": ("流失诊断", "近7天流失订单金额"),
}


class CtripApiError(RuntimeError):
    pass


class CtripClient:
    def __init__(self, cookie: str, base_url: str = "https://ebooking.ctrip.com"):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json;charset=UTF-8",
                "Origin": "https://ebooking.ctrip.com",
                "Referer": "https://ebooking.ctrip.com/",
            }
        )
        if cookie.strip():
            self.session.headers["Cookie"] = cookie.strip()
        if EXTRA_HEADERS:
            self.session.headers.update(EXTRA_HEADERS)

    def post_json(
        self,
        path_or_url: str,
        payload: dict[str, Any] | None = None,
        content_type: str | None = None,
    ) -> Any:
        if content_type:
            response = self.session.post(
                self._url(path_or_url),
                data=json.dumps(payload or {}),
                headers={"Content-Type": content_type},
                timeout=30,
            )
        else:
            response = self.session.post(self._url(path_or_url), json=payload or {}, timeout=30)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise CtripApiError(f"接口没有返回 JSON，HTTP={response.status_code}") from exc
        return data

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return f"{self.base_url}/{path_or_url.lstrip('/')}"


def require_config(name: str, value: str) -> str:
    if not value or "TODO" in value:
        raise CtripApiError(f"请先配置 {name}")
    return value


def unwrap_payload(data: dict[str, Any]) -> Any:
    code = data.get("code", data.get("status", data.get("resultCode")))
    success = data.get("success")
    if success is False or code not in (None, 0, "0", 200, "200", "success"):
        message = data.get("message") or data.get("msg") or data.get("errorMsg")
        raise CtripApiError(f"接口返回异常：code={code}, message={message}")
    for key in ("data", "result", "value"):
        if key in data:
            return data[key]
    return data


def parse_number(value: Any) -> Any:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().replace(",", "")
    if text in ("-", "--"):
        return "-"
    if text.endswith("%"):
        text = text[:-1]
        try:
            return float(text) / 100
        except ValueError:
            return value
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except ValueError:
        return value


def write_single_sheet(
    output_path: Path,
    sheet_name: str,
    headers: list[str],
    rows: list[list[Any]],
    widths: list[int],
    datetime_columns: set[int] | None = None,
    date_columns: set[int] | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(headers)
    ws.freeze_panes = "A2"
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center")
    for row in rows:
        ws.append(row)
    for idx, width in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = width
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if datetime_columns and cell.column in datetime_columns:
                cell.number_format = "yyyy-mm-dd hh:mm:ss"
            if date_columns and cell.column in date_columns:
                cell.number_format = "yyyy-mm-dd"
    wb.save(output_path)
    return output_path


def json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if hasattr(value, "isoformat") and not isinstance(value, (str, int, float, bool)):
        return value.isoformat()
    return value


def write_standard_json(output_path: Path, headers: list[str], rows: list[list[Any]]) -> Path:
    json_path = output_path.with_suffix(".json")
    payload = {
        "table_name": output_path.stem,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "row_count": len(rows),
        "rows": [
            {header: json_safe(row[index]) if index < len(row) else "" for index, header in enumerate(headers)}
            for row in rows
        ],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path


class CtripBusinessClient(CtripClient):
    def optional_post_json(
        self,
        path_or_url: str,
        payload: dict[str, Any] | None = None,
        content_type: str | None = None,
    ) -> Any:
        try:
            return self.post_json(path_or_url, payload, content_type)
        except Exception as exc:
            print(f"WARN optional endpoint skipped: {path_or_url} ({exc})")
            return {}

    def query_all(self) -> dict[str, Any]:
        yesterday = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
        data = {
            name: self.post_json(path, {})
            for name, path in ENDPOINTS.items()
            if name not in {"flow_transfor", "scan_flow", "market_details", "order_trend"}
        }
        data["market_details"] = {}
        for target_type in (1, 2, 0):
            payload = {
                "platform": 0,
                "startDateType": 0,
                "startDate": yesterday,
                "endDate": yesterday,
                "type": target_type,
            }
            data["market_details"][str(target_type)] = self.post_json(ENDPOINTS["market_details"], payload)
        data["order_trend"] = self.optional_post_json(ENDPOINTS["order_trend"], {"ota": 0, "includeCanceled": False})
        flow_payload = {"platform": "Ctrip", "startDate": yesterday, "endDate": yesterday}
        scan_payload = {
            "platform": "Ctrip",
            "channelType": "0",
            "startDate": yesterday,
            "endDate": yesterday,
            "dateDimension": "0",
            "dataType": 0,
        }
        scan_peer_payload = dict(scan_payload, dataType=3)
        data["flow_transfor"] = self.optional_post_json(
            ENDPOINTS["flow_transfor"], flow_payload, "application/json;"
        )
        data["scan_flow"] = self.optional_post_json(
            ENDPOINTS["scan_flow"], scan_payload, "application/json;|cas"
        )
        data["scan_flow_peer"] = self.optional_post_json(
            ENDPOINTS["scan_flow"], scan_peer_payload, "application/json;|cas"
        )
        return data


def payload_data(response: Any) -> Any:
    if isinstance(response, dict) and "data" in response:
        return response.get("data")
    return response


def pick(item: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return default


def row(
    captured_at: datetime,
    period_type: str,
    business_date: Any,
    period_days: Any,
    hotel_name: str,
    metric_name: str,
    metric_value: Any,
    metric_unit: str = "",
    compare_label: str = "",
    compare_value: Any = "",
    competitor_rank: Any = "",
    peer_average: Any = "",
) -> list[Any]:
    metric_group, metric_display_name = METRIC_LABELS.get(metric_name, ("其他", metric_name))
    return [
        captured_at,
        period_type,
        business_date,
        period_days,
        hotel_name,
        metric_group,
        metric_name,
        metric_display_name,
        parse_number(metric_value),
        metric_unit,
        compare_label,
        compare_value,
        competitor_rank,
        peer_average,
    ]


def rank_text(rank_value: Any, total_value: Any = "") -> str:
    if rank_value in (None, ""):
        return ""
    if total_value not in (None, ""):
        return f"{rank_value}/{total_value}"
    return str(rank_value)


COMPETITION_TITLE_MAP = {
    "\u9884\u8ba2\u8ba2\u5355\u91cf": ("booking_order_count", "order"),
    "\u9884\u8ba2\u9500\u552e\u989d": ("booking_sales_amount", "CNY"),
    "\u5728\u5e97\u95f4\u591c": ("inhouse_room_night", "room_night"),
    "\u51fa\u79df\u7387": ("occupancy_rate", "%"),
    "\u643a\u7a0bAPP\u8bbf\u5ba2": ("ctrip_app_visitor_count", "person"),
    "\u643a\u7a0bAPP\u8f6c\u5316\u7387": ("ctrip_app_conversion_rate", "%"),
}


def load_competition_capture(path: Path = COMPETITION_CAPTURE) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_competition_captures() -> list[dict[str, Any]]:
    captures = []
    for path in (COMPETITION_REALTIME_CAPTURE, COMPETITION_CAPTURE):
        capture = load_competition_capture(path)
        if capture:
            captures.append(capture)
    return captures


def normalize_compare_text(text: Any) -> tuple[str, Any]:
    if not text:
        return "", ""
    value = str(text).strip()
    for label in ("\u8f83\u4e0a\u5468\u540c\u671f", "\u8f83\u53bb\u5e74\u540c\u671f", "\u8f83\u6628\u65e5"):
        if value.startswith(label):
            return "same_period_last_week" if label == "\u8f83\u4e0a\u5468\u540c\u671f" else label, value[len(label) :].strip()
    return "", value


def competition_profile_rows(data: dict[str, Any], captured_at: datetime, business_date: Any, hotel_name: str) -> list[list[Any]]:
    metrics = data.get("metrics") if isinstance(data, dict) else []
    if not isinstance(metrics, list):
        return []
    period_type = data.get("statsPeriodType") or "yesterday"
    period_business_date = captured_at.date() if period_type == "realtime" else business_date
    period_days = 0 if period_type == "realtime" else 1
    rows: list[list[Any]] = []
    for item in metrics:
        if not isinstance(item, dict):
            continue
        mapped = COMPETITION_TITLE_MAP.get(str(item.get("title", "")).strip())
        if not mapped:
            continue
        metric_name, metric_unit = mapped
        compare_label, compare_value = normalize_compare_text(item.get("hotelCompare"))
        rows.append(
            row(
                captured_at,
                period_type,
                period_business_date,
                period_days,
                hotel_name,
                metric_name,
                item.get("hotelValue"),
                metric_unit,
                compare_label,
                compare_value,
                item.get("competitorRank", ""),
                parse_number(item.get("peerAverage", "")),
            )
        )
    return rows


def extract_hotel_name_from_api(data: dict[str, Any]) -> str | None:
    """尝试从各API接口响应中提取酒店名。"""
    # 1. 尝试 visitor_title 接口
    visitor_title = payload_data(data.get("visitor_title"))
    if isinstance(visitor_title, dict):
        for key in ("hotelName", "hotel_name", "name", "title", "hotelTitle"):
            value = visitor_title.get(key)
            if value:
                return str(value).strip()
    # 2. 尝试 capacity / tensity 等接口中的 hotelIdName 类字段
    for endpoint in ("capacity", "tensity", "hotel_min_price", "hotel_advice"):
        resp = payload_data(data.get(endpoint))
        if isinstance(resp, dict):
            for key in ("hotelName", "hotel_name", "name", "hotelTitle", "hotelIdName"):
                value = resp.get(key)
                if value:
                    return str(value).strip()
    return None


def extract_hotel_name(payload: dict[str, Any], override_name: str | None = None) -> str:
    """提取酒店名，优先级：外部传入 > API响应 > 竞对数据 > 默认值。"""
    if override_name:
        return override_name.strip()

    # 从顶层 payload 取
    for key in ("hotelName", "hotel_name"):
        value = payload.get(key)
        if value:
            return str(value).strip()

    # 从各API接口尝试提取
    api_name = extract_hotel_name_from_api(payload)
    if api_name:
        return api_name

    # 从竞对分析数据中取
    profiles = payload.get("competition_profiles") or []
    if payload.get("competition_profile"):
        profiles = [payload["competition_profile"], *profiles]
    for profile in profiles:
        if isinstance(profile, dict):
            for key in ("hotelName", "hotel_name"):
                value = profile.get(key)
                if value:
                    return str(value).strip()

    return DEFAULT_HOTEL_NAME


def business_diagnosis_rows(data: dict[str, Any], captured_at: datetime, business_date: Any, hotel_name: str) -> list[list[Any]]:
    flow = payload_data(data.get("flow_compete")) or {}
    return [
        row(captured_at, "last_7_days", business_date, 7, hotel_name, "lost_room_night_count_7d", flow.get("ordquantity"), "room_night"),
        row(captured_at, "last_7_days", business_date, 7, hotel_name, "lost_visitor_count_7d", flow.get("comhtluv"), "person"),
        row(captured_at, "last_7_days", business_date, 7, hotel_name, "lost_order_amount_7d", flow.get("ordamount"), "CNY"),
    ]


MARKET_DETAIL_METRICS = {
    "1": [
        ("booking_sales_amount", "CNY"),
        ("booking_room_night", "room_night"),
        ("booking_order_count", "order"),
    ],
    "2": [
        ("inhouse_room_night", "room_night"),
        ("room_tensity_rate", "%"),
        ("occupancy_rate", "%"),
    ],
    "0": [
        ("checkout_sales_amount", "CNY"),
        ("checkout_room_night", "room_night"),
        ("checkout_conversion_rate", "%"),
        ("checkout_average_sale_price", "CNY"),
    ],
}


def market_detail_rows(data: dict[str, Any], captured_at: datetime, business_date: Any, hotel_name: str) -> list[list[Any]]:
    market_details = data.get("market_details") or {}
    rows: list[list[Any]] = []
    if not isinstance(market_details, dict):
        return rows
    for target_type, metrics in MARKET_DETAIL_METRICS.items():
        response = market_details.get(target_type) or {}
        items = payload_data(response) or []
        if not isinstance(items, list):
            continue
        for index, (metric_name, metric_unit) in enumerate(metrics):
            if index >= len(items) or not isinstance(items[index], dict):
                continue
            item = items[index]
            rows.append(
                row(
                    captured_at,
                    "yesterday",
                    business_date,
                    1,
                    hotel_name,
                    metric_name,
                    item.get("tip1"),
                    metric_unit,
                    "same_period_last_week_change_rate",
                    item.get("tip2"),
                    "",
                    item.get("tip3"),
                )
            )
    return rows


def realtime_business_rows(data: dict[str, Any], captured_at: datetime, hotel_name: str) -> list[list[Any]]:
    business_date = captured_at.date()
    capacity = data.get("capacity") or {}
    tensity = data.get("tensity") or {}
    min_price = payload_data(data.get("hotel_min_price")) or {}
    now_tensity = tensity.get("nowTensityDetail") or {}
    pre_tensity = tensity.get("preTensityDetail") or {}
    currency = min_price.get("currency") or "CNY"
    if currency == "RMB":
        currency = "CNY"
    rows = [
        row(captured_at, "realtime", business_date, 0, hotel_name, "realtime_booking_order_count", capacity.get("orderQuantity"), "order", "same_period_last_week", capacity.get("synchronizationOrderQuantity"), capacity.get("rankOfOrderQuantity"), capacity.get("competitorsAverageOrderQuantity")),
        row(captured_at, "realtime", business_date, 0, hotel_name, "realtime_ctrip_order_count", capacity.get("ctripOrderQuantity"), "order", "same_period_last_week", capacity.get("ctripSynchronizationOrderQuantity"), capacity.get("ctripRankOfOrderQuantity"), ""),
        row(captured_at, "realtime", business_date, 0, hotel_name, "realtime_qunar_order_count", capacity.get("qunarOrderQuantity"), "order", "same_period_last_week", capacity.get("qunarSynchronizationOrderQuantity"), capacity.get("qunarRankOfOrderQuantity"), ""),
        row(captured_at, "realtime", business_date, 0, hotel_name, "realtime_elong_order_count", capacity.get("elongOrderQuantity"), "order", "same_period_last_week", capacity.get("elongSynchronizationOrderQuantity"), capacity.get("elongRankOfOrderQuantity"), ""),
        row(captured_at, "realtime", business_date, 0, hotel_name, "realtime_inhouse_room_night", capacity.get("occupiedRooms"), "room_night", "same_period_last_week", capacity.get("synchronizationOccupiedRooms"), capacity.get("rankOfOccupiedRooms"), capacity.get("competitorsAverageOccupiedRooms")),
        row(captured_at, "realtime", business_date, 0, hotel_name, "occupancy_rate", capacity.get("occupancyRate"), "%", "same_period_last_week", capacity.get("synchronizationOccupancyRate"), capacity.get("rankOfOccupancyRate"), ""),
        row(captured_at, "realtime", business_date, 0, hotel_name, "room_tensity_rate", now_tensity.get("tensityScore"), "%", "previous_period", pre_tensity.get("tensityScore"), tensity.get("rankOfTensity"), ""),
        row(captured_at, "realtime", business_date, 0, hotel_name, "lowest_sale_price", min_price.get("minPrice"), currency, "", "", rank_text(min_price.get("minPriceRank"), min_price.get("competitorHotelTotal")), ""),
    ]
    return [item for item in rows if item[8] not in (None, "")]


def first_flow_item(items: Any, hotel_id: int | None = None) -> dict[str, Any]:
    if not isinstance(items, list):
        return {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if hotel_id is None and item.get("hotelId", 0) > 0:
            return item
        if item.get("hotelId") == hotel_id:
            return item
    return {}


def pct(value: Any) -> Any:
    if value in (None, ""):
        return ""
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def safe_rate(numerator: Any, denominator: Any) -> Any:
    try:
        denominator = float(denominator)
        if denominator == 0:
            return ""
        return float(numerator) / denominator * 100
    except (TypeError, ValueError):
        return ""


def sum_list(values: Any) -> Any:
    if not isinstance(values, list):
        return ""
    total = 0
    has_value = False
    for value in values:
        if value is None:
            continue
        total += float(value)
        has_value = True
    if not has_value:
        return ""
    return int(total) if total.is_integer() else total


def first_list_value(data: Any, key: str) -> Any:
    if not isinstance(data, dict):
        return ""
    payload = data.get("data") or data
    values = payload.get(key) if isinstance(payload, dict) else None
    return values[0] if isinstance(values, list) and values else ""


def flow_conversion_rows(data: dict[str, Any], captured_at: datetime, business_date: Any, hotel_name: str) -> list[list[Any]]:
    flow_items = payload_data(data.get("flow_transfor")) or []
    mine = first_flow_item(flow_items)
    peer = first_flow_item(flow_items, -1)
    scan = payload_data(data.get("scan_flow")) or {}
    scan_peer = payload_data(data.get("scan_flow_peer")) or {}

    list_exposure = mine.get("listExposure")
    detail_exposure = mine.get("detailExposure")
    order_filling = mine.get("orderFillingNum")
    order_submit = mine.get("orderSubmitNum")
    peer_list_exposure = peer.get("listExposure")
    peer_detail_exposure = peer.get("detailExposure")
    peer_order_filling = peer.get("orderFillingNum")
    peer_order_submit = peer.get("orderSubmitNum")

    rows = [
        row(captured_at, "yesterday", business_date, 1, hotel_name, "list_page_exposure_count", list_exposure, "count", "", "", "", peer_list_exposure),
        row(captured_at, "yesterday", business_date, 1, hotel_name, "detail_page_visitor_count", detail_exposure, "person", "", "", "", peer_detail_exposure),
        row(captured_at, "yesterday", business_date, 1, hotel_name, "order_page_visitor_count", order_filling, "person", "", "", "", peer_order_filling),
        row(captured_at, "yesterday", business_date, 1, hotel_name, "order_submit_count", order_submit, "order", "", "", "", peer_order_submit),
        row(captured_at, "yesterday", business_date, 1, hotel_name, "exposure_conversion_rate", pct(mine.get("flowRate")), "%", "", "", "", pct(peer.get("flowRate"))),
        row(captured_at, "yesterday", business_date, 1, hotel_name, "order_conversion_rate", safe_rate(order_filling, detail_exposure), "%", "", "", "", safe_rate(peer_order_filling, peer_detail_exposure)),
        row(captured_at, "yesterday", business_date, 1, hotel_name, "transaction_conversion_rate", safe_rate(order_submit, order_filling), "%", "", "", "", safe_rate(peer_order_submit, peer_order_filling)),
        row(captured_at, "yesterday", business_date, 1, hotel_name, "visitor_order_conversion_rate", pct(first_list_value(scan, "conversionsRatesDataList")), "%", "", "", "", pct(first_list_value(scan_peer, "conversionsRatesDataList"))),
        row(captured_at, "yesterday", business_date, 1, hotel_name, "detail_page_uv_count", sum_list((scan.get("data") or scan).get("uvDataList")), "person", "same_period_last_week", sum_list((scan.get("data") or scan).get("uvLyList")), "", sum_list((scan_peer.get("data") or scan_peer).get("uvDataList"))),
        row(captured_at, "yesterday", business_date, 1, hotel_name, "detail_page_order_count", sum_list((scan.get("data") or scan).get("orderDataList")), "order", "same_period_last_week", sum_list((scan.get("data") or scan).get("orderLyList")), "", sum_list((scan_peer.get("data") or scan_peer).get("orderDataList"))),
    ]
    return rows


def normalize_rows(payload: dict[str, Any], captured_at: datetime, hotel_name: str | None = None) -> list[list[Any]]:
    business_date = captured_at.date() - timedelta(days=1)
    hotel_name = extract_hotel_name(payload, override_name=hotel_name)
    profiles = payload.get("competition_profiles") or []
    if not profiles and payload.get("competition_profile"):
        profiles = [payload["competition_profile"]]
    rows: list[list[Any]] = []
    rows.extend(market_detail_rows(payload, captured_at, business_date, hotel_name))
    rows.extend(realtime_business_rows(payload, captured_at, hotel_name))
    for profile in profiles:
        rows.extend(competition_profile_rows(profile, captured_at, business_date, hotel_name))
    rows.extend(flow_conversion_rows(payload, captured_at, business_date, hotel_name))
    rows.extend(business_diagnosis_rows(payload, captured_at, business_date, hotel_name))
    return rows


def sample_payload() -> dict[str, Any]:
    return {
        "hotelName": "示例酒店",
        "metrics": [
            {"name": "销售额", "value": "1234.00", "unit": "元", "compareLabel": "较昨日", "compareValue": "+8.2%", "rank": "3/20", "peerAvg": "980.00"},
            {"name": "订单数", "value": "12", "unit": "单", "compareLabel": "较昨日", "compareValue": "-1", "rank": "5/20", "peerAvg": "10"},
            {"name": "支付转化率", "value": "6.50%", "unit": "%", "compareLabel": "较昨日", "compareValue": "+0.8pp", "rank": "6/20", "peerAvg": "5.80%"},
        ],
    }


def save_rows(rows: list[list[Any]], output: Path = DEFAULT_OUTPUT) -> Path:
    headers = [*HEADERS, "hotel_id"]
    rows = [list(row) + [HOTEL_ID] for row in rows]
    output_path = write_single_sheet(
        output,
        SHEET_NAME,
        headers,
        rows,
        widths=[20, 16, 14, 10, 24, 14, 28, 22, 14, 10, 22, 18, 14, 14, 16],
        datetime_columns={1},
        date_columns={3},
    )
    write_standard_json(output_path, headers, rows)
    sync_table(output_path.stem, headers, rows)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="携程经营概览采集框架")
    parser.add_argument("--self-test", action="store_true", help="使用样例 JSON 测试解析和 Excel 输出")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--cookie", default=COOKIE)
    parser.add_argument("--hotel-name", default="", help="指定酒店名称，覆盖默认值和API返回值")
    args = parser.parse_args()

    captured_at = datetime.now()
    if args.self_test:
        payload = sample_payload()
    else:
        payload = CtripBusinessClient(args.cookie).query_all()
        competition_profiles = load_competition_captures()
        if competition_profiles:
            payload["competition_profiles"] = competition_profiles
    rows = normalize_rows(payload, captured_at, hotel_name=args.hotel_name or None)
    output = save_rows(rows, Path(args.output))
    print(f"OK 携程经营指标行数={len(rows)} 输出={output}")


if __name__ == "__main__":
    main()
