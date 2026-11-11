"""把 Rezen 夜审房费明细收成房型间夜和房费。不保留客人字段。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP


def _amount(value) -> Decimal:
    if isinstance(value, bool) or value is None or value == "":
        raise ValueError("Rezen 房费金额无效，拒绝写入")
    try:
        number = Decimal(str(value))
    except Exception:
        raise ValueError("Rezen 房费金额无效，拒绝写入") from None
    if not number.is_finite() or abs(number) >= Decimal("100000000"):
        raise ValueError("Rezen 房费金额无效，拒绝写入")
    return number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _clock(value) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("T", " ")
    for pattern, size in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:size], pattern)
        except ValueError:
            continue
    return None


def normalize_lines(rows: list, day: date) -> list[dict]:
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Rezen 房费明细无效，拒绝写入")
    lines = []
    for row in rows:
        try:
            business = date.fromisoformat(str(row.get("businessDate") or "")[:10])
        except ValueError:
            raise ValueError("Rezen 房费营业日无效，拒绝写入") from None
        name = str(row.get("rmTypeDrpt") or "").strip()
        code = str(row.get("rmTypeCd") or "").strip()
        room = str(row.get("rmNum") or "").strip()
        account = str(row.get("acctNum") or "").strip()
        subject = str(row.get("trnCd") or "").strip()
        checkout = _clock(row.get("checkoutTime"))
        if business != day or not name or not code or not room or not account or not subject:
            raise ValueError("Rezen 房费明细的日期、房型、房号或科目无效，拒绝写入")
        if subject == "100" and checkout is None:
            raise ValueError("Rezen 房费离店日期无效，拒绝写入")
        lines.append({
            "room_type_name": name, "room_type_code": code, "room": room, "account": account,
            "code": subject, "amount": _amount(row.get("trnAmt")),
            "checkout": checkout.date() if checkout else None,
        })
    return lines


def nights_by_room_type(lines: list[dict], day: date) -> dict[str, Decimal]:
    """离店日晚于营业日的自动房费才算间夜。同一间房金额相同的多笔只算一间夜，金额不同的分别计算。"""
    grouped: dict[tuple[str, str], dict[str, Decimal]] = {}
    room_types: dict[str, str] = {}
    for line in lines:
        if line["code"] != "100" or line["checkout"] is None or line["checkout"] <= day:
            continue
        room = line["room"]
        name = line["room_type_name"]
        if room in room_types and room_types[room] != name:
            raise ValueError("同一房号出现在两个房型，拒绝写入")
        room_types[room] = name
        accounts = grouped.setdefault((name, room), {})
        accounts[line["account"]] = accounts.get(line["account"], Decimal(0)) + line["amount"]
    totals: dict[str, Decimal] = {}
    for (name, _room), accounts in grouped.items():
        positives = [amount for amount in accounts.values() if amount > 0]
        count = Decimal(len(set(positives)) if positives else 1)
        totals[name] = totals.get(name, Decimal(0)) + count
    return totals


def revenue_by_room_type(lines: list[dict]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for line in lines:
        totals[line["room_type_name"]] = totals.get(line["room_type_name"], Decimal(0)) + line["amount"]
    return totals


def _text(value, limit: int) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text or len(text) > limit:
        return None
    return text


def _optional_amount(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return _amount(value)
    except ValueError:
        return None


def revenue_lines(rows: list, start: date, end: date) -> list[dict]:
    """夜审房费明细。客人字段不进入结果，科目用中文名，不用交易号。"""
    if end < start or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Rezen 房费明细无效，拒绝写入")
    lines = []
    for row in rows:
        try:
            business = date.fromisoformat(str(row.get("businessDate") or "")[:10])
        except ValueError:
            raise ValueError("Rezen 房费营业日无效，拒绝写入") from None
        name = str(row.get("rmTypeDrpt") or "").strip()
        code = str(row.get("rmTypeCd") or "").strip()
        room = str(row.get("rmNum") or "").strip()
        account = str(row.get("acctNum") or "").strip()
        subject = str(row.get("trnDrpt1") or "").strip()
        trn = str(row.get("trnCd") or "").strip()
        checkout = _clock(row.get("checkoutTime"))
        if not start <= business <= end or not name or not code or not room or not account or not subject or not trn:
            raise ValueError("Rezen 房费明细的日期、房型、房号或科目无效，拒绝写入")
        if len(room) > 50 or len(subject) > 100 or len(account) > 80:
            raise ValueError("Rezen 房费明细的房号、科目或单号过长，拒绝写入")
        if trn == "100" and checkout is None:
            raise ValueError("Rezen 房费离店日期无效，拒绝写入")
        lines.append({
            "business": business, "room_type_name": name, "room_type_code": code, "room": room,
            "account": account, "subject": subject, "code": trn, "amount": _amount(row.get("trnAmt")),
            "checkout": checkout.date() if checkout else None, "checkout_at": checkout,
            "checkin": _clock(row.get("arrivalTime")),
            "rack_rate": _optional_amount(row.get("roomRate")),
            "price_type": _text(row.get("rateDrpt"), 100) or _text(row.get("rateCd"), 100),
            "operator_name": _text(row.get("operNm"), 100),
        })
    return lines


def assigned_nights(lines: list[dict], day: date) -> dict[tuple[str, str, str], Decimal]:
    """把房型间夜落到一条科目上，合计与 nights_by_room_type 相同。"""
    grouped: dict[tuple[str, str], dict[str, Decimal]] = {}
    subjects: dict[tuple[str, str], dict[str, Decimal]] = {}
    room_types: dict[str, str] = {}
    for line in lines:
        if line["code"] != "100" or line["checkout"] is None or line["checkout"] <= day:
            continue
        subject = str(line.get("subject") or "").strip()
        if not subject:
            raise ValueError("Rezen 房费科目无效，拒绝写入")
        room = line["room"]
        name = line["room_type_name"]
        if room in room_types and room_types[room] != name:
            raise ValueError("同一房号出现在两个房型，拒绝写入")
        room_types[room] = name
        account = line["account"]
        accounts = grouped.setdefault((name, room), {})
        accounts[account] = accounts.get(account, Decimal(0)) + line["amount"]
        by_subject = subjects.setdefault((room, account), {})
        by_subject[subject] = by_subject.get(subject, Decimal(0)) + line["amount"]
    assigned: dict[tuple[str, str, str], Decimal] = {}
    for (_name, room), accounts in grouped.items():
        positives: dict[Decimal, list[str]] = {}
        for account, amount in accounts.items():
            if amount > 0:
                positives.setdefault(amount, []).append(account)
        chosen = [min(accounts_for_amount) for accounts_for_amount in positives.values()] if positives else [min(accounts)]
        for account in chosen:
            by_subject = subjects[(room, account)]
            subject = min(by_subject, key=lambda item: (-abs(by_subject[item]), item))
            key = (account, room, subject)
            assigned[key] = assigned.get(key, Decimal(0)) + 1
    return assigned


def collapse_revenue(lines: list[dict]) -> list[dict]:
    """同一单号、营业日、房号和科目的多行加总，房费不丢。"""
    room_names: dict[tuple, str] = {}
    for line in lines:
        marker = (line["business"], line["room"])
        if marker in room_names and room_names[marker] != line["room_type_name"]:
            raise ValueError("同一房号出现在两个房型，拒绝写入")
        room_names[marker] = line["room_type_name"]
    grouped: dict[tuple, list[dict]] = {}
    for line in lines:
        grouped.setdefault((line["business"], line["account"], line["room"], line["subject"]), []).append(line)
    nights: dict[tuple, Decimal] = {}
    for day in {line["business"] for line in lines}:
        for (account, room, subject), count in assigned_nights(
                [line for line in lines if line["business"] == day], day).items():
            nights[(day, account, room, subject)] = count
    rows = []
    for (business, account, room, subject), bucket in grouped.items():
        names = {line["room_type_name"] for line in bucket}
        if len(names) != 1:
            raise ValueError("同一房号出现在两个房型，拒绝写入")
        rows.append({
            "business_date": business, "order_id": account, "room_no": room, "charge_subject": subject,
            "room_type_name": next(iter(names)), "guest_name": None, "customer_source": None,
            "checkin_time": _shared(line["checkin"] for line in bucket),
            "checkout_time": _shared(line["checkout_at"] for line in bucket),
            "rack_rate": _shared(line["rack_rate"] for line in bucket),
            "price_type": _shared(line["price_type"] for line in bucket),
            "room_daily_price": None, "stay_type": None,
            "room_nights": nights.get((business, account, room, subject), Decimal(0)),
            "room_fee": sum((line["amount"] for line in bucket), Decimal(0)),
            "operator_name": _shared(line["operator_name"] for line in bucket),
            "channel_unit_id": None,
        })
    return rows


def _shared(values):
    found = [value for value in values if value not in (None, "")]
    if not found or any(value != found[0] for value in found):
        return None
    return found[0]
