#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Foxhis X5 报表 SQL —— 集中定义，便于核对与调整

【SQL 可信度说明 —— 请先读这段】

1. RS01 房费流水 ROOM_REVENUE_QUERY（2026-10-19 已实测并落库）
   master/hmaster 按当月实际退房时间、sta=O 选主单；account/haccount 读取对应房费。
   pccode.jierep=010 是现场房费分类，金额取 charge，非 credit；按 accnt+number 去重。
   字典与关联字段均已实测，不能再使用早期草稿中的 ccode/cclass 或 deptno=C/D。
   保留跨月营业日期及正负冲账，不把押金、代收款计入房费。

2. 房态 ROOM_STATUS_QUERY
   表名与字段名均已在 pcode 里找到**真实 SQL 语句**为证（`maint_front.pbd`）：
       select roomno, oroomno, type, ocsta, sta, flr, hall, bedno, feature, people from rmsta
   配套证据：
       · `dyn_fo_rpt1.pbd`: select roomno, type, sta, bksta, ... from rmstarep
       · `house0.pbd`:     select type, descript, descript1 from typim where type in
                           (select distinct type from rmsta ...)
       · `dyn_fo_other.pbd`: select a.folio,...,b.descript from hrm_ooo a, rmstalist b
                             where a.sta = b.sta          → rmstalist 是房态字典
       · `dyn_fo_other.pbd`: select sta from rmsta where roomno=' ' and ocsta='V'
   → `rmsta` 字段：roomno / oroomno / type / ocsta / sta / flr / hall / bedno / feature / people
   → `typim` 字段：type / descript / descript1   （房型代码 → 名称）
   → `rmstalist` 字段：sta / descript            （房态代码 → 中文描述）
   结论：**表名与字段名均有实证**。房态中文含义优先取 rmstalist.descript（系统自带字典），
   取不到才回落到脚本内的 ROOM_STATUS_LABELS，再不行用 ocsta 判断，最后原样保留代码。
   仍需实测确认的只剩：字典表是否在你们这套库里写满、以及 `sta` 与 `ocsta` 的语义分工。

【拿到只读账号后的第一步】

    python foxhis_introspect.py --list-tables            # 看全部表
    python foxhis_introspect.py --columns rmsta typim rmstalist
    # 然后按实际字段微调本文件

采集器在运行前会校验所需字段是否存在；不存在会直接报错并打印**实际字段清单**，
不会静默返回空数据。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 目标表（HotelCollector 标准表，已带 source_platform 列，无需改建表脚本）
# ---------------------------------------------------------------------------

ROOM_TYPE_FORECAST_TABLE = "pms_room_type_forecast"
ROOM_TYPE_HOURLY_STATUS_TABLE = "pms_room_type_hourly_status"
JL01_TABLE = "jl01_room_type_performance_daily"
JL11_TABLE = "jl11_room_type_classification"
JL11_ROOM_QUERY = """
select convert(char(8),date,112) as business_date, gkey, hall,
       code as room_type_code, descript as room_type_name,
       convert(numeric(18,4),ttl) as room_count,
       convert(numeric(18,4),avl) as available_rooms,
       convert(numeric(18,4),soldf+soldg+soldc+soldl-ent) as room_nights,
       convert(numeric(18,4),incomef+incomeg+incomec+incomel) as room_revenue
from yrmsalerep_new
where gkey='t' and date >= '{start_date}' and date < '{end_date}'
order by date,code,hall
""".strip()

JL11_CONTROL_QUERY = """
select convert(char(8),date,112) as business_date, class as metric_code,
       convert(numeric(18,4),case when class='013000' then day+day_rebate else day end) as metric_value
from yjourrep
where date >= '{start_date}' and date < '{end_date}'
  and class in ('010100','010190','011000','013000')
order by date,class
""".strip()
JY01_TABLE = "jy01_hotel_statistics_daily"
JY03_TABLE = "jy03_hotel_statistics_month"
JD01_TABLE = "jd01_booking_detail"


def _jd01_orders(table: str, origin: str) -> str:
    name = "m.name" if table == "hmaster" else "g.name"
    states = "('O')" if table == "hmaster" else "('R','I','O','S','D')"
    return f"""
select '{origin}' as origin,m.accnt as folio_no,m.sta as status_code,
       convert(char(8),m.restime,112)+' '+convert(char(8),m.restime,108) as booking_time,
       convert(char(8),m.arr,112)+' '+convert(char(8),m.arr,108) as arrival_time,
       convert(char(8),m.dep,112)+' '+convert(char(8),m.dep,108) as departure_time,
       {name} as contact,m.type as type_code,t.descript as room_type_name,
       m.rmnum as room_count,m.share as share_code,k.descript as guest_source,
       r.descript as price_type,e.name as operator_name
from {table} m left join guest g on g.no=m.haccnt
left join typim t on t.type=m.type
left join mktcode k on k.code=m.market
left join rmratecode r on r.code=m.ratecode
left join sys_empno e on e.empno=m.resby
where m.dep >= '{{cutoff}}' and m.rmnum>0 and m.type<>'' and m.sta in {states}
""".strip()


JD01_ORDERS_QUERY = _jd01_orders("master", "current") + "\nunion all\n" + _jd01_orders("hmaster", "history")

JD01_LEDGER_QUERY = "\nunion all\n".join(f"""
select a.accnt as folio_no,a.number as line_no,
       convert(numeric(18,4),a.charge) as amount,
       convert(numeric(18,4),case when p.tail='01' then a.quantity else 0 end) as nights
from {table} a join pccode p on p.pccode=a.pccode
where p.jierep='010' and (
 exists(select 1 from master m where m.accnt=a.accnt and m.sta='O' and m.dep>='{{cutoff}}' and m.rmnum>0 and m.type<>'')
 or exists(select 1 from hmaster m where m.accnt=a.accnt and m.sta='O' and m.dep>='{{cutoff}}' and m.rmnum>0 and m.type<>''))
""".strip() for table in ("account", "haccount"))

JD01_RATE_QUERY = """
select r.accnt as folio_no,r.type as type_code,r.quantity,
       convert(char(8),r.begin_,112) as start_date,convert(char(8),r.end_,112) as end_date,
       convert(numeric(18,4),r.rate) as rate
from rsvsrc r join master m on m.accnt=r.accnt
where m.dep>='{cutoff}' and m.rmnum>0 and m.type<>'' and m.sta in ('R','I')
""".strip()

# 月报按完整日结累计；返减在日报单列，净房费须加上带符号的 rebate。
JY03_DAILY_QUERY = """
select convert(char(8),date,112) as business_date, class as metric_code,
       convert(numeric(18,4),day) as metric_value,
       convert(numeric(18,4),month) as month_value,
       convert(numeric(18,4),day_rebate) as daily_rebate,
       convert(numeric(18,4),month_rebate) as month_rebate
from yjourrep
where date >= '{start_date}' and date < '{end_date}'
  and class in ('010100','010110','010120','010130','010190',
                '011000','012000','013000','014000','018530','018560')
order by date,class
""".strip()

# JY01 使用酒店原生历史营业日报；日结房型表只作独立房量/房费控制。
JY01_DAILY_QUERY = """
select convert(char(8),date,112) as business_date, class as metric_code,
       convert(numeric(18,4),day) as metric_value,
       convert(numeric(18,4),day_rebate) as daily_rebate
from yjourrep
where date >= '{start_date}' and date < '{end_date}'
  and class in ('010100','010110','010120','010130','010190',
                '011000','012000','013000','014000','017000','018000','018530','018560')
order by date,class
""".strip()

JY01_ROOM_CONTROL_QUERY = """
select convert(char(8),date,112) as business_date, gkey, hall,
       code as room_type_code,
       convert(numeric(18,4),ttl) as total_rooms,
       convert(numeric(18,4),mnt) as maintain_rooms,
       convert(numeric(18,4),os) as locked_rooms,
       convert(numeric(18,4),htl) as own_use_rooms,
       convert(numeric(18,4),avl) as available_rooms,
       convert(numeric(18,4),vac) as remaining_rooms,
       convert(numeric(18,4),soldf+soldg+soldc+soldl) as sold_rooms,
       convert(numeric(18,4),ent) as free_rooms,
       convert(numeric(18,4),incomef+incomeg+incomec+incomel) as room_revenue
from yrmsalerep_new
where gkey='t' and date >= '{start_date}' and date < '{end_date}'
order by date,code,hall
""".strip()

# gkey=t 是原生房型维度。{{{ 为总计，ZZZ 为手工输入行，由采集器校验后排除。
JL01_REPORT_QUERY = """
select convert(char(8),date,112) as business_date, gkey, hall,
       code as room_type_code, descript as room_type_name,
       convert(numeric(18,4),ttl) as total_rooms,
       convert(numeric(18,4),avl) as available_rooms,
       convert(numeric(18,4),soldf+soldg+soldc+soldl-ent) as room_nights,
       convert(numeric(18,4),incomef+incomeg+incomec+incomel) as room_revenue
from yrmsalerep_new
where gkey='t' and (
 (date >= '{yesterday}' and date < dateadd(day,1,'{yesterday}')) or
 (date >= '{last_year}' and date < dateadd(day,1,'{last_year}'))
)
order by date,code,hall
""".strip()

JL01_CONTROL_QUERY = """
select convert(char(8),date,112) as business_date, class as metric_code,
       convert(numeric(18,4),day) as metric_value
from yjourrep
where class in ('010100','010190','011000','012000','013000','014000','018560') and (
 (date >= '{yesterday}' and date < dateadd(day,1,'{yesterday}')) or
 (date >= '{last_year}' and date < dateadd(day,1,'{last_year}'))
)
order by date,class
""".strip()

# 实时快照收入与物理在住房集合一致，只取有效在住主单和当天有效的执行价段。
# 左连接保留缺少主单/价格的在住房，由调用方拒绝不完整或重复价格，而非静默漏算。
# 同日入住离店的价格段允许 begin_=end_=当天，但实际抵离也必须在当天且离店晚于抵店。
# 当天应离但尚未退房：没有当天价格段时，取与主单离店时间一致的最后一晚价格。
REALTIME_ROOM_RATE_QUERY = """
select r.roomno as room_no, r.type as room_type_code,
       m.accnt as folio_no, m.rmnum as room_quantity, m.share as share_code,
       s.id as rate_segment_id, s.quantity as rate_quantity, s.rate as room_rate
from rmsta r
left join master m on m.roomno = r.roomno and m.sta = 'I'
left join rsvsrc s on s.accnt = m.accnt
  and ((s.begin_ <= '{business_date}' and s.end_ > '{business_date}')
    or (s.begin_ = '{business_date}' and s.end_ = '{business_date}'
      and convert(char(8),s.arr,112) = '{business_date}'
      and convert(char(8),s.dep,112) = '{business_date}' and s.dep > s.arr)
    or (s.begin_ < '{business_date}' and s.end_ = '{business_date}'
      and convert(char(8),m.dep,112) = '{business_date}' and s.dep = m.dep
      and not exists (
        select 1 from rsvsrc n where n.accnt = m.accnt
        and ((n.begin_ <= '{business_date}' and n.end_ > '{business_date}')
          or (n.begin_ = '{business_date}' and n.end_ = '{business_date}'
            and convert(char(8),n.arr,112) = '{business_date}'
            and convert(char(8),n.dep,112) = '{business_date}' and n.dep > n.arr)))))
where r.ocsta = 'O'
order by r.type, r.roomno
""".strip()

# 现场已验证：jierep=010 为房费统计分类，tail=01 的 quantity 为计费间夜。
# 当前账与历史账同时读取，调用方按 accnt+number 去重，避免退房转账后漏计。
# 保留作入账口径对账；实时快照默认使用 REALTIME_ROOM_RATE_QUERY。
POSTED_ROOM_REVENUE_QUERY = """
select 'current' as ledger_source, a.accnt as folio_no, a.number as line_no,
       a.roomno as room_no, a.pccode as charge_code, a.charge as amount,
       case when p.tail = '01' then a.quantity else 0 end as room_nights
from account a, pccode p
where a.pccode = p.pccode and p.jierep = '010'
  and a.bdate >= '{business_date}'
  and a.bdate < dateadd(day, 1, '{business_date}')
union all
select 'history' as ledger_source, a.accnt as folio_no, a.number as line_no,
       a.roomno as room_no, a.pccode as charge_code, a.charge as amount,
       case when p.tail = '01' then a.quantity else 0 end as room_nights
from haccount a, pccode p
where a.pccode = p.pccode and p.jierep = '010'
  and a.bdate >= '{business_date}'
  and a.bdate < dateadd(day, 1, '{business_date}')
""".strip()
ROOM_REVENUE_TABLE = "rs01_room_revenue_daily"


# ---------------------------------------------------------------------------
# RS01：当月已退房订单的完整房费过账流水（当前账 + 历史账）
# ---------------------------------------------------------------------------

def _rs01_order_select(table: str, origin: str) -> str:
    name = "m.name" if table == "hmaster" else "g.name"
    return f"""
select '{origin}' as order_origin, m.accnt as folio_no, m.roomno as room_no, m.class as account_class,
       m.type as room_type_code, {name} as guest_name,
       m.market as market_code, m.ratecode as price_type,
       convert(char(8),m.citime,112)+' '+convert(char(8),m.citime,108) as checkin_time,
       convert(char(8),m.cotime,112)+' '+convert(char(8),m.cotime,108) as checkout_time
from {table} m left join guest g on m.haccnt = g.no
where m.sta = 'O' and m.cotime >= '{{checkout_start}}' and m.cotime < '{{checkout_end}}'
""".strip()


ROOM_REVENUE_ORDERS_QUERY = _rs01_order_select("master", "current") + "\nunion all\n" + _rs01_order_select("hmaster", "history")


def _rs01_ledger_select(table: str, origin: str) -> str:
    return f"""
select '{origin}' as ledger_origin, a.accnt as folio_no, a.number as line_no,
       convert(char(8),a.bdate,112) as business_date, a.roomno as room_no,
       a.pccode as charge_code, p.descript as charge_subject, p.tail as charge_tail,
       convert(numeric(18,4),a.charge) as amount,
       convert(numeric(18,4),a.quantity) as quantity,
       a.empno as operator_code, e.name as operator_name
from {table} a join pccode p on a.pccode = p.pccode
left join sys_empno e on a.empno = e.empno
where p.jierep = '010' and (
 exists(select 1 from master m where m.accnt=a.accnt and m.sta='O'
        and m.cotime >= '{{checkout_start}}' and m.cotime < '{{checkout_end}}')
 or exists(select 1 from hmaster m where m.accnt=a.accnt and m.sta='O'
        and m.cotime >= '{{checkout_start}}' and m.cotime < '{{checkout_end}}')
)
""".strip()


# 按退房月筛选订单，保留其全部房费日期，不以账务日期截断跨月住宿。
# jierep=010 为现场房费科目；charge 为费用，不混入押金/代收的 credit。
ROOM_REVENUE_QUERY = _rs01_ledger_select("account", "current") + "\nunion all\n" + _rs01_ledger_select("haccount", "history")
ROOM_REVENUE_REQUIRED = ("haccount", ["accnt", "number", "bdate", "roomno", "charge", "quantity", "pccode", "empno"])


# ---------------------------------------------------------------------------
# 房态快照
#
# 口径：房间状态主表 rmsta（房号 / 房型 / 占用状态 / 房态 / 楼层 / 楼栋）。
#       字段名来自 pcode 中的真实 SQL（见文件头第 2 条），不是推测。
#       `sta` 为房态代码、`ocsta` 为占用状态（'V'/'O'），中文含义交给字典表。
# ---------------------------------------------------------------------------

ROOM_STATUS_QUERY = """
select
    r.roomno      as room_no,
    r.type        as room_type_code,
    r.sta         as room_status,
    r.ocsta       as occupy_status,
    r.flr         as floor_no,
    r.hall        as building,
    r.locked      as locked
from rmsta r
order by r.roomno
""".strip()

ROOM_STATUS_REQUIRED = ("rmsta", ["roomno", "type", "sta", "ocsta", "locked"])


# ---------------------------------------------------------------------------
# 房型名称（字典表，用于把房型代码翻译成名称）
# 证据：house0.pbd 中 `select type, descript, descript1 from typim where type in
#       (select distinct type from rmsta ...)`
# ---------------------------------------------------------------------------

ROOM_TYPE_NAME_QUERY = """
select type as room_type_code, descript as room_type_name
from typim
""".strip()


# ---------------------------------------------------------------------------
# 房态字典（房态代码 → 中文描述）
# 证据：dyn_fo_other.pbd 中 `from hrm_ooo a, rmstalist b where a.sta = b.sta`
#       且 select 取了 b.descript
# ---------------------------------------------------------------------------

ROOM_STATUS_NAME_QUERY = """
select sta as status_code, descript as status_name
from rmstalist
""".strip()


# ---------------------------------------------------------------------------
# 业务日范围内的客源/渠道（可选，用于 customer_source 字段）
# ---------------------------------------------------------------------------

GUEST_SOURCE_QUERY = """
select distinct accnt as folio_no, market as customer_source
from hmaster
where market is not null
""".strip()
