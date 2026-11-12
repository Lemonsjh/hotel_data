"""余乐通 E8HotelMis 查询定义。

集中所有源库 SQL，采集脚本不再内联语句；改动字段映射只动这一个文件。

数据来源说明
  · jl01 / jy01 ← T_Web_ReportMultiple  余乐通自算的多维经营报表，
    一次查询同时覆盖「房型分类」「客人分类」「策略分类」等口径，
    各口径合计天然对齐，可作为交叉校验依据。
  · jd01 ← T_RoomOrder  预订与入住合并记录，F_Status 区分阶段
    （T_BkOrder 预订单自 2024-08 后已停用，故不纳入）。
  · forecast / hourly ← T_Show_RoomStatus（房号与房型）＋ T_RoomOrder（在住）
    ＋ T_PriceList（营业日房费）＋ T_RoomInApps（房型标识）。
    余乐通业务表只存房型名称，整数房型标识只在上架表里存在。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 经营报表（jl01 / jy01）
# ---------------------------------------------------------------------------

DAILY_REPORT_QUERY = """
select
    convert(varchar(10), F_ReportDayfrom, 120) as business_date,
    F_Caption      as dimension_group,
    F_TotalType    as dimension_value,
    F_TotalRooms   as total_rooms,
    F_DNums        as room_nights,
    F_DRoomSalesum as room_revenue,
    F_DRoomAvg     as adr,
    F_DRentalRate  as occupancy_rate
from T_Web_ReportMultiple
where F_ReportDayfrom >= '{start}' and F_ReportDayfrom < '{end}'
order by F_ReportDayfrom, F_Caption, F_TotalType
"""

# 报表口径：jl01 取房型分类统计；jy01 只存日度总营业指标，由房型口径合计得出。
# 源表另有客人/策略/消费/外卖四个分类口径（F_Caption 原值），属明细视角，不落库，
# 其中策略分类统计的房晚仍用于与房型口径交叉校验，发现源库口径漂移。
ROOM_TYPE_GROUP = "房型分类统计"
STRATEGY_GROUP = "策略分类统计"

# ---------------------------------------------------------------------------
# 房型 30 天经营汇总（jl11）
# ---------------------------------------------------------------------------

# 与 jl01 同源，只是把窗口内的逐日行累加成房型级汇总。
# 余乐通报表的 F_TotalRooms 恒为酒店总房量（不是房型房量），房型房量另取房号清单，
# 这里只带回总房量用于校验房号口径是否漂移。
JL11_SUMMARY_QUERY = """
select
    F_TotalType    as room_type_name,
    sum(F_DNums)   as room_nights,
    sum(F_DRoomSalesum) as room_revenue,
    count(*)       as active_days,
    max(F_TotalRooms) as hotel_rooms
from T_Web_ReportMultiple
where F_Caption = '{caption}'
  and F_ReportDayfrom >= '{start}' and F_ReportDayfrom < '{end}'
group by F_TotalType
order by F_TotalType
"""

# ---------------------------------------------------------------------------
# 酒店经营月报（jy03）
# ---------------------------------------------------------------------------

# 源表每行自带月累计：F_MNums / F_MRoomSalesum 是余乐通在月末重算后的结果，
# 比逐日累加更准——夜审漏记的房费会在月累计里被补回来
# （实测 2026-10：日报累加 522 房晚 / 65283.32，月累计 526 / 65682.84，
#   与账务流水口径分厘不差）。因此 jy03 直接取月累计，不做窗口累加。
#
# 不用 F_MonthDays：实测值等于「日号 + 1」（2026-10-31 是 32），
# 且存在漏递增（2026-11-09 仍是 9），据此算房日会把出租率算错。
# 房日改由 Python 按逐日房量累加，以兼容 2024-10-10 的房量变更（61 → 30）。
MONTHLY_REPORT_QUERY = """
select
    convert(varchar(10), F_ReportDayfrom, 120) as business_date,
    max(F_TotalRooms)   as total_rooms,
    sum(F_DNums)        as day_nights,
    sum(F_DRoomSalesum) as day_revenue,
    sum(F_MNums)        as month_nights,
    sum(F_MRoomSalesum) as month_revenue
from T_Web_ReportMultiple
where F_Caption = '{caption}'
  and ({ranges})
group by F_ReportDayfrom
order by F_ReportDayfrom
"""

# ---------------------------------------------------------------------------
# 客房收入日表（rs01）
# ---------------------------------------------------------------------------

# 一行 = 一条房费流水；只取 F_RoomPriceOrOther='房价'（消费/营销/洗涤/赔偿等非房费已排除）。
# 余乐通的 OTA 字段（F_Ota_Orderno / f_ota_order_id / F_XyzOut_Orderid）与四张 OTA 表
# 全库无一有效值，渠道单号无处可取，因此订单号只能回落到 PMS 单号 o.F_ID。
# 渠道信息仅以文字存在：F_GuestType（价格方案名，含「美团/赫程/飞猪」）
# 与 F_Memo（挂账渠道，如「挂账:美团（OTA平台）」），由采集端归一成客户来源。
# 备注与科目名都是自由文本，换行会破坏按行解析，落库前压成单行。
RS01_QUERY = """
select
    convert(varchar(20), l.F_ID) as payment_id,
    convert(varchar(20), l.F_RoomOrderID) as order_id,
    isnull(l.F_KeyOrRoomId, '') as room_no,
    left(replace(replace(isnull(l.F_Caption, ''), char(13), ' '), char(10), ' '), 100) as charge_subject,
    l.F_TotalPrice  as room_fee,
    l.F_Price       as daily_price,
    convert(varchar(19), l.F_TradeDatetime, 120) as trade_time,
    isnull(nullif(l.F_Cashier, ''), isnull(l.F_Seller, '')) as operator_name,
    isnull(o.F_RoomType, '') as room_type_name,
    replace(replace(isnull(o.F_GuestLead, ''), char(13), ' '), char(10), ' ') as guest_name,
    isnull(o.F_GuestType, '') as guest_type,
    isnull(o.F_BookingType, '') as booking_type,
    convert(varchar(19), o.F_DateTime, 120) as checkin_time,
    convert(varchar(19), o.F_EndDateTime, 120) as checkout_time,
    replace(replace(isnull(o.F_Memo, ''), char(13), ' '), char(10), ' ') as remarks
from T_PriceList l
inner join T_RoomOrder o on o.F_ID = l.F_RoomOrderID
where l.F_RoomPriceOrOther = '房价'
  and l.F_TradeDatetime >= '{start}' and l.F_TradeDatetime < '{end}'
order by l.F_TradeDatetime, l.F_ID
"""

# 流水合计与经营报表的逐日对账基准（房型分类统计口径）。
# 两者同源不同路：报表由余乐通夜审生成，流水是账务明细，
# 差额用于暴露源库数据质量问题，不参与落库字段计算。
RS01_RECONCILE_QUERY = """
select
    business_date,
    sum(room_nights)  as room_nights,
    sum(room_revenue) as room_revenue
from (
    select convert(varchar(10), F_ReportDayfrom, 120) as business_date,
           F_DNums as room_nights,
           F_DRoomSalesum as room_revenue
    from T_Web_ReportMultiple
    where F_Caption = '{caption}'
      and F_ReportDayfrom >= '{start}' and F_ReportDayfrom < '{end}'
) t
group by business_date
order by business_date
"""

# ---------------------------------------------------------------------------
# 预订 / 入住明细（jd01）
# ---------------------------------------------------------------------------

# 窗口只设下界，不设上界：抵店在窗口内、或离店仍不早于窗口起点的单全部返回。
# 覆盖三类：近 N 天到店（含已结帐/取消）、长期住宿尚未离店、以及全部未来预订。
# 与西软 JD01 的 departure_time >= cutoff 口径一致。
BOOKING_QUERY = """
select
    convert(varchar(40), o.F_ID) as order_id,
    isnull(o.F_KeyId, '') as room_no,
    isnull(o.F_RoomType, '') as room_type_name,
    isnull(o.F_GuestType, '') as guest_source,
    isnull(o.F_BookingType, '') as price_type,
    isnull(o.F_GuestLead, '') as contact,
    convert(varchar(19), o.F_BeginDateTime, 120) as booking_time,
    convert(varchar(19), o.F_DateTime, 120) as arrival_time,
    convert(varchar(19), o.F_EndDateTime, 120) as departure_time,
    o.F_Price as room_price,
    o.F_GetMoney as prepayment,
    isnull(o.F_Status, '') as booking_status,
    isnull(o.F_PersonNumber, 0) as room_count,
    isnull(o.F_Writer, '') as operator_name,
    -- 备注是多行自由文本，换行会破坏按行解析，落库前压成单行
    replace(replace(isnull(o.F_Memo, ''), char(13), ' '), char(10), ' ') as remarks,
    isnull(o.F_Ota_Orderno, '') as ota_order_no
from T_RoomOrder o
where o.F_DateTime >= '{start}' or o.F_EndDateTime >= '{start}'
order by o.F_DateTime, o.F_ID
"""

# ---------------------------------------------------------------------------
# 客史 / 离店客人明细（jd02）
# ---------------------------------------------------------------------------

# 一行 = 一条「订单 × 客人」登记记录。T_OrderClient 是办理入住时逐位客人登记的表，
# 语义与 jd02 的「每条客人记录单独保存」一致；T_Guest_info 全库只有 3 行，是空表不可用。
#
# 只取「已结帐 / 已换房」：取消预订（10495 条）与取消入住（641 条）从未入住，
# 计入客史会让入住行为统计失真。已换房保留，因为换房前后的客人确实住过。
#
# 注意 1：证件号与手机号在本库里是**明文**（余乐通是本地 SQL Server 直连，
#   不像宝寓等 SaaS 接口返回已脱敏值）。建表注释「接口返回的证件号原值，
#   通常已脱敏」对余乐通不成立，按业务要求原样落库。
# 注意 2：total_checkin_count 必须区分「证件号为空」的情况——空字符串相等会让
#   全库空值互相匹配，实测会得出 4634 这种荒谬值；证件号缺失时退到手机号，
#   两者都没有则返回 NULL。
JD02_QUERY = """
select
    convert(varchar(20), c.F_ID) as guest_id,
    convert(varchar(40), o.F_ID) as order_id,
    isnull(c.F_InRoom, '') as room_no,
    isnull(o.F_RoomType, '') as room_type_name,
    replace(replace(isnull(c.F_LinkMan, ''), char(13), ' '), char(10), ' ') as guest_name,
    isnull(c.F_CardNumber, '') as id_card_number,
    isnull(c.F_Phone, '') as mobile,
    isnull(c.F_ClientType, '') as client_type,
    isnull(o.F_GuestType, '') as guest_type,
    isnull(o.F_BookingType, '') as booking_type,
    isnull(o.F_Status, '') as booking_status,
    o.F_Price as room_price,
    convert(varchar(19), o.F_DateTime, 120) as arrival_time,
    convert(varchar(19), o.F_EndDateTime, 120) as departure_time,
    convert(varchar(10), dateadd(hour, -5, o.F_EndDateTime), 120) as business_date,
    isnull(c.F_BlLeadMan, 0) as is_main_guest,
    (select top 1 convert(varchar(20), c0.F_ID)
       from T_OrderClient c0
      where c0.F_OrderId = c.F_OrderId and c0.F_BlLeadMan = 1
      order by c0.F_ID) as main_guest_id,
    case
        when isnull(c.F_CardNumber, '') <> '' then
            (select count(*)
               from T_OrderClient c2
               inner join T_RoomOrder o2 on o2.F_ID = c2.F_OrderId
              where c2.F_CardNumber = c.F_CardNumber and o2.F_Status = '已结帐')
        when isnull(c.F_Phone, '') <> '' then
            (select count(*)
               from T_OrderClient c3
               inner join T_RoomOrder o3 on o3.F_ID = c3.F_OrderId
              where c3.F_Phone = c.F_Phone and o3.F_Status = '已结帐')
    end as total_checkin_count,
    replace(replace(isnull(o.F_Memo, ''), char(13), ' '), char(10), ' ') as order_memo,
    isnull(o.F_Writer, '') as operator_name
from T_OrderClient c
inner join T_RoomOrder o on o.F_ID = c.F_OrderId
where o.F_Status in ('已结帐', '已换房')
  and o.F_EndDateTime >= '{start}' and o.F_EndDateTime < '{end}'
order by o.F_EndDateTime, o.F_ID, c.F_ID
"""

# ---------------------------------------------------------------------------
# 房型实时经营快照（pms_room_type_forecast / pms_room_type_hourly_status）
# ---------------------------------------------------------------------------

# 房型标识：业务表只存房型名，系统里唯一稳定的整数标识在「上架房型」表。
ROOM_TYPE_ID_QUERY = """
select
    convert(varchar(64), f_id) as pms_room_type_id,
    isnull(F_RoomType, '') as room_type_name,
    isnull(F_Status_Online, '') as online_status
from T_RoomInApps
where isnull(F_RoomType, '') <> ''
order by f_id
"""

# 房号级实时房态：每房取最新一条清洁记录，房型与在住状态从入住单倒推。
# 有效范围 = 近 N 天有过记录的房间 ∪ 当前在住的房间，
# 后者兜住刚入住、尚未产生清洁记录，或长期未改状态的在住房间。
ROOM_STATUS_QUERY = """
select
    s.F_RoomNo as room_no,
    isnull((select top 1 o.F_RoomType from T_RoomOrder o
             where o.F_KeyId = s.F_RoomNo and isnull(o.F_RoomType, '') <> ''
             order by o.F_DateTime desc), '') as room_type_name,
    isnull((select count(*) from T_RoomOrder o2
             where o2.F_KeyId = s.F_RoomNo and o2.F_Status = '入住'), 0) as inhouse_count,
    isnull(s.F_Caption, '') as clean_status
from T_Show_RoomStatus s
where s.F_ID in (
        select max(x.F_ID) from T_Show_RoomStatus x
        where x.F_CreateDt >= '{since}'
           or exists (select 1 from T_RoomOrder o3
                       where o3.F_KeyId = x.F_RoomNo and o3.F_Status = '入住')
        group by x.F_RoomNo
      )
order by s.F_RoomNo
"""

# 营业日房费：只取 F_RoomPriceOrOther='房价' 的房费流水。
# 钟点房（F_Caption 含「钟点房」）按 0.5 房晚折算，与余乐通经营报表口径一致；
# 消费、营销、洗涤、赔偿等非房费流水已被过滤。
ROOM_REVENUE_QUERY = """
select
    isnull(o.F_RoomType, '') as room_type_name,
    sum(l.F_TotalPrice) as room_revenue,
    sum(case when l.F_Caption like '%钟点房%' then 0.5 else 1 end) as room_nights,
    count(*) as charge_count
from T_PriceList l
inner join T_RoomOrder o on l.F_RoomOrderID = o.F_ID
where l.F_RoomPriceOrOther = '房价'
  and l.F_TradeDatetime >= '{start}' and l.F_TradeDatetime < '{end}'
group by o.F_RoomType
"""

# 关联不到入住单的房费流水。正常应为 0；非 0 说明账务与订单脱节，
# 这部分金额不会进入房型口径，需人工核查。
ROOM_ORPHAN_REVENUE_QUERY = """
select
    count(*) as orphan_count,
    isnull(sum(l.F_TotalPrice), 0) as orphan_revenue
from T_PriceList l
where l.F_RoomPriceOrOther = '房价'
  and l.F_TradeDatetime >= '{start}' and l.F_TradeDatetime < '{end}'
  and not exists (select 1 from T_RoomOrder o where o.F_ID = l.F_RoomOrderID)
"""
