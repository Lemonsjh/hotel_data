"""美团 PMS 当日房态响应校验与转换，不访问网络或数据库。"""
from decimal import Decimal, InvalidOperation


def parse_room_types(response):
    """提取实时房型列表；保留上游 ID 与名称，拒绝歧义。"""
    if not isinstance(response, dict) or response.get("code") != 10000:
        raise ValueError("房型列表接口响应无效")
    items = response.get("data")
    if not isinstance(items, list) or not items:
        raise ValueError("房型列表为空或无效")
    result, names, ids = [], set(), set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("房型数据无效")
        name = str(item.get("roomTypeName") or "").strip()
        room_id = str(item.get("roomTypeId") or "").strip()
        if not name or not room_id or name in names or room_id in ids:
            raise ValueError("房型名称或 ID 缺失、重复")
        names.add(name)
        ids.add(room_id)
        result.append({"id": room_id, "roomTypeName": name})
    return result


def room_count(value, field):
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field} 缺失或无效")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{field} 无效") from exc
    if not number.is_finite() or number != number.to_integral_value() or number < 0:
        raise ValueError(f"{field} 无效")
    return int(number)


def metric(value, field):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} 无效")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{field} 无效") from exc
    if not number.is_finite():
        raise ValueError(f"{field} 无效")
    return number


def transform(room_types, response, snapshot_time, hotel_id, hotel_name):
    """严格按唯一房型名关联请求和响应；任一缺失或歧义均拒绝整批。"""
    if not hotel_id or not isinstance(room_types, list) or not room_types:
        raise ValueError("酒店 ID 或请求房型缺失")
    if not isinstance(response, dict) or response.get("code") != 10000 or not isinstance(response.get("data"), list):
        raise ValueError("房态接口响应无效")
    types = {}
    ids = set()
    for item in room_types:
        if not isinstance(item, dict):
            raise ValueError("请求房型无效")
        name = str(item.get("roomTypeName") or "").strip()
        room_id = str(item.get("id") or "").strip()
        if not name or not room_id or name in types or room_id in ids:
            raise ValueError("请求房型名称或 ID 缺失、重复")
        types[name] = room_id
        ids.add(room_id)
    rows = []
    seen = set()
    stay_date = snapshot_time.date()
    for item in response["data"]:
        if not isinstance(item, dict):
            raise ValueError("响应房型无效")
        if item.get("isAggregation") is True:
            continue
        name = str(item.get("roomTypeName") or "").strip()
        if name not in types or name in seen:
            raise ValueError("响应房型无法唯一匹配")
        seen.add(name)
        details = item.get("details")
        if not isinstance(details, list):
            raise ValueError("房态明细缺失")
        today = [detail for detail in details if isinstance(detail, dict)
                 and str(detail.get("date") or "").split(" ")[0] == stay_date.isoformat()]
        if len(today) != 1:
            raise ValueError("当日房态明细缺失或重复")
        detail = today[0]
        rows.append({
            "hotel_id": hotel_id, "hotel_name": hotel_name,
            "source_platform": "PMS（美团）", "snapshot_time": snapshot_time,
            "snapshot_hour": snapshot_time.replace(minute=0, second=0, microsecond=0),
            "stay_date": stay_date, "room_type_name": name, "pms_room_type_id": types[name],
            "total_rooms": room_count(item.get("totalCount"), "totalCount"),
            "available_rooms": room_count(detail.get("availableCount"), "availableCount"),
            "occupied_rooms": room_count(detail.get("occupationCount"), "occupationCount"),
            "overbooking_rooms": room_count(detail.get("overbookingCount"), "overbookingCount"),
            "room_revenue": metric(detail.get("roomRent"), "roomRent"),
            "adr": metric(detail.get("adr"), "adr"),
            "revpar": metric(detail.get("revPar"), "revPar"),
        })
    if seen != set(types):
        raise ValueError("响应房型不完整")
    return rows
