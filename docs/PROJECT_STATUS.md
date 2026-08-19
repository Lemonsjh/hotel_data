# 项目当前进度（PROJECT_STATUS）

> 最后整理：2026-08-14
>
> 本文档记录阶段性进度和近期关键变化。开始新的 Codex 会话时，请先读根目录 `AGENTS.md`，再读本文件，并重新确认当前 `master` HEAD。

## 1. 当前实现快照

本次文档整理时核对的运行代码基线：

```text
master: 7cde2642c0921a103ad2e35aa400fb924369255d
```

该提交为 PR #9：`fix: JD01 ETL 兼容空数据结果`。

后续纯文档提交可能使 `master` HEAD 变化，因此这个提交号表示“本文档核对到的运行实现版本”，不是要求长期固定在该 SHA。

## 2. 项目目前做到什么程度

项目已经从单独的数据抓取脚本发展为一套可以持续运行的酒店多平台数据采集与运营自动化服务。

当前主流程：

```text
别样红 PMS / 美团 OTA / 携程 OTA
        ↓
登录态与页面/API采集
        ↓
结构转换、清洗、快照化
        ↓
MySQL 统一存储
        ↓
统一房型/商品映射
        ↓
本地管理面板 + 周期调度
        ↓
评价回复 / 调价任务 / 后续经营分析
```

当前 README 已明确项目提供本地管理面板、定时调度、OTA 登录助手、房型映射和审核后调价功能。

## 3. 当前任务规模

`OTA采集服务/runner.py` 当前注册 30 个统一调度任务：

- 美团：15 个任务。
- 携程：14 个任务。
- PMS：1 个统一 `pms_fetch` 任务。

### 美团当前统一调度任务

- 经营指标 `meituan_business`
- 流量转化 `meituan_flow_conversion`
- 已加入权益 `meituan_joined_rights`
- 促销开通状态 `meituan_promotion_status`
- 视频上传状态 `meituan_video_upload_status`
- 近 30 天推广效果 `meituan_promotion_performance`
- 曝光来源 `meituan_exposure_source`
- 订单流失 `meituan_order_loss`
- 扫码订单 `meituan_scan_order`
- 用户来源 `meituan_user_source`
- 评价 `meituan_review`
- 评价明细 `meituan_review_detail`
- 活动 `meituan_promotion`
- 商品价格 `meituan_goods_price`
- 周边事件 `meituan_nearby_event`

### 携程当前统一调度任务

- 经营指标 `ctrip_business`
- 30 天竞争指标 `ctrip_competition_metrics_30d`
- 流量转化 `ctrip_flow_conversion`
- 订单明细 `ctrip_order_detail`
- 订单流失 `ctrip_order_loss`
- 已加入权益 `ctrip_joined_rights`
- 促销开通状态 `ctrip_promotion_status`
- 用户画像 `ctrip_user_profile`
- PSI 分数 `ctrip_psi_score`
- 推广效果 `ctrip_promotion_performance`
- 评价 `ctrip_review`
- 评价明细 `ctrip_review_detail`
- 活动 `ctrip_promotion`
- 商品价格 `ctrip_goods_price`

## 4. PMS 当前覆盖范围

PMS 统一采集入口：

```text
正式数据抓取-PMS（别样红）/PMS登录/fetch_main.py
```

当前支持 11 类报表/快照：

1. `RS01` 房费收入日报
2. `JD01` 预订明细
3. `JD04` 在住/续住相关数据
4. `JY01` 酒店经营统计日报
5. `JY03` 酒店经营统计月报
6. `JL01` 房型经营日报
7. `JL02` 酒店经营日报
8. `JL11` 房型分类
9. `KF11` 房态快照
10. `FORECAST` 房类预测
11. `ROOM_STATUS` 每小时房态

PMS 采集已经具备：

- 登录态复用和账号变化检测。
- API/页面采集组合。
- 本轮 JSON 更新时间检查，失败时禁止旧文件进入 ETL。
- 统一 MySQL ETL。
- 报表间随机 2–5 秒等待。
- 连续 3 个任务失败后的网络探测与全量任务熔断。
- 手工 `--reports` 调试/补采入口。

## 5. 数据库现状

当前数据库 Schema 主要以：

```text
OTA采集服务/database/create_all_tables.sql
```

为事实来源。

最近核对的 Schema 规模：

| 分类 | 实体表 | 字段 |
|---|---:|---:|
| 携程 | 18 | 332 |
| 美团 | 20 | 299 |
| PMS | 11 | 200 |
| 公共 | 2 | 39 |
| **合计** | **51** | **870** |

另外有 2 个 View：

- `v_hotel_ota_operating_snapshot`
- `v_hotel_room_type_mapping_result`

公共实体表主要包括：

- `hotel_room_type_mapping`
- `ota_review_reply_task`

美团新增 `meituan_ota_business_metrics_hourly`：每酒店每小时为“今日实时”的引流价、曝光量、浏览人数、支付转化率、支付订单数、销售间夜、销售均价、销售额、入住间夜和满房率各保存一行，指标编码与日表一致，并保留同行排名、同行均值；同一酒店同一小时同一指标更新为最新采集结果，保留近 60 天。美团经营表中单位为 `%` 的数值按页面百分数保存，例如 1.54% 存为 1.54。

数据库部署边界仍是“一库一酒店”。

## 6. 房型和商品映射进度

### 美团钟点房

当前已经形成两个独立判断：

- 美团商品采集表自身写入 `is_hour_room`。
- `hotel_room_type_mapping` 在商品同步时独立根据 OTA 商品名称重新判断 `is_hour_room`。

两者当前都使用形如：

```text
-[0-9]+([.][0-9]+)?小时-
```

的规则，但业务上仍保持两个独立判断来源。

### PMS FORECAST 的统一房型 ID

近期已经补充 FORECAST ETL 的 `room_type_id` 映射：

- 从 `hotel_room_type_mapping` 中读取当前酒店的有效 PMS 房型映射。
- 只有一个 PMS 房型名称能唯一对应一个 `room_type_id` 时才写入。
- 映射读取同时兼容 dict cursor 和普通 tuple cursor。

相关近期提交：

```text
3c3223ec18b21deee2428955555d352e55785b41  fix:room_forecast
a71600c926b83ce8eab0434e426f83bfe01168f1  fix:房型预测映射修复
```

## 7. PMS 近期关键稳定性修复

### 7.1 凌晨 FORECAST / ROOM_STATUS 查询窗口

已经确认 PMS 预测服务在凌晨使用当前小时可能返回业务错误，而查询当日 12:00 可以正常工作。

当前规则：

```text
00:00–05:59 -> beginHour = 12:00
06:00–23:59 -> beginHour = 当前整点
```

FORECAST 和 ROOM_STATUS 共用该窗口。

### 7.2 PMS 网络抖动保护

曾实际出现同一 PMS 主机多个端口的连接层异常：

- Web 报表页面：`ERR_CONNECTION_CLOSED`
- API：`SSL UNEXPECTED_EOF_WHILE_READING`

这类故障没有明确 403/429，因此不要直接认定为平台限流或登录失效。

当前 `fetch_main.py` 已加入：

- 各报表之间随机 2–5 秒等待。
- 连续 3 个报表失败后进行轻量网络探测。
- 探测失败或 5xx 时停止本轮剩余访问。
- 剩余任务标记为未采集，后续 ETL 自动跳过。

### 7.3 JD01 空结果

JD01 返回 0 条预订是合法结果。

此前 ETL 会在：

```python
print(data_list[0])
```

处触发 `IndexError`，导致整个 PMS ETL 中断。

PR #9 已修复：

```text
7cde2642c0921a103ad2e35aa400fb924369255d
```

现在空结果只输出提示，正常执行 0 条写入并继续处理后续 PMS ETL。

## 8. 美团页面型任务当前策略

美团部分数据并非稳定的纯 API 调用，而是依赖登录后的页面加载、iframe、动态请求和 Edge profile。

当前三个高风险/页面型任务主动降低访问频率：

| 任务 | 随机冷却 |
|---|---:|
| 促销开通状态 | 10–14 小时 |
| 视频上传状态 | 22–26 小时 |
| 近 30 天推广效果 | 22–26 小时 |

当前冷却已经改为“实际结果后进入冷却”：

```text
冷却期调用 -> 跳过，不刷新时间
真正开始采集 -> 只记录 last_attempt_at
成功写库 -> success 冷却
实际失败 -> failure 冷却
```

对应重要合并提交：

```text
2e6a6eea57bb1d32a3737c1f5f274b77a72c9879
fix: 美团页面任务仅在实际结果后进入冷却
```

### 推广效果页面注意

推广效果脚本仍使用页面产生的 `/paginateQueryPlanAndLaunch` 动态请求，页面等待时间当前是 12 秒。

该页面曾在人工点击“推广通”时也进入美团风险控制。因此当前方向是降低触发频率，而不是增加刷新/重试或尝试绕过平台控制。

### 预约发票状态

预约发票当前页面会直接显示“当前门店暂未开通，快去开通预约发票吧！”。状态采集优先识别该页面空态并写入 `CLOSED`，旧的“酒店发票信息维护”链接仅保留为兼容后备。

### 公益流量与酒店亮点导航

工作台会先从 `eb.meituan.com` 跳转到 `me.meituan.com` 包装页，再异步加载菜单。公益流量必须先展开“促销推广”，酒店亮点必须先展开“信息管理”；采集器等待包装页完成跳转，从同名节点中选择可见菜单，再等待父菜单和子菜单出现后正常点击，避免导航中断、隐藏副本或固定短等待导致定位超时。

## 9. 浏览器与动态签名现状

当前美团部分采集依赖页面自己产生的动态签名请求，而不是保存一条长期不变的签名 URL。

已经存在公共浏览器 profile 锁，用于减少：

- 定时任务之间争抢 Edge profile。
- 人工登录/操作与自动采集同时占用 profile。

修改页面型采集代码前，应先确认是否依赖这套动态请求捕获和浏览器锁。

## 10. 最近重要提交脉络

下面只保留对理解当前实现仍有帮助的节点：

```text
f302f53f471f2051ec0de9a22c9f23cae983b808
美团商品采集增加独立钟点房标识

a609e40966f7068a050209a5c9af69cb32fd23fd
PMS FORECAST / ROOM_STATUS 凌晨查询窗口修复

ec84f01e1abf051ca9fdf74953b36c7e50d15237
美团推广效果增加每日随机冷却

000c8200cacc0e7589b20f820ec4650dab925162
降低美团页面型状态任务采集频率

0f63dadf4d84e5b81d9b8f03c6e13e76e89b5019
PMS 全量采集增加随机间隔和网络熔断

2e6a6eea57bb1d32a3737c1f5f274b77a72c9879
美团页面型任务改为实际结果后进入冷却

3c3223ec18b21deee2428955555d352e55785b41
a71600c926b83ce8eab0434e426f83bfe01168f1
FORECAST 增加并修复统一 room_type_id 映射

7cde2642c0921a103ad2e35aa400fb924369255d
JD01 ETL 兼容空数据结果
```

## 11. 已经确认、不需要重复争论的设计

为了避免新 Codex 会话重复排查已经确认的问题：

- 每个数据库只对应一家酒店，不需要优先考虑跨酒店同库隔离。
- `ctrip_order_loss_monthly` 当前快照/排名语义是有意设计。
- 评价回复调度读取 `price_scheduler.interval_minutes` 是当前有意设计。
- 登录/账号切换自动识别酒店参数失败时保留旧参数并告警是有意设计。
- 美团商品 `is_hour_room` 和统一房型映射 `is_hour_room` 保持独立判断。
- PMS 00:00–05:59 查询当日 12:00 是经过实际运行确认的规则。
- ROOM_STATUS 的查询 `beginHour` 与真实 `snapshot_hour` 不一致并不代表数据错误。
- 美团页面型任务主动降低频率是为了降低访问压力和风险控制触发概率。

## 12. 当前应优先验证的事情

以下是下一次运行时值得优先观察的项目，不代表必须立即开发新功能：

1. **跑一轮完整 PMS**：确认 JD01 0 条时能够继续执行 JD04、JY01、FORECAST 等后续 ETL。
2. **观察 PMS 网络熔断实际表现**：如果上游连接恢复，确认正常成功任务不会被误熔断。
3. **观察三个美团页面任务 state**：确认只有实际 success/failure 才生成新的 `next_allowed_at`，冷却期跳过不会延长冷却。
4. **观察 FORECAST 的 `room_type_id`**：确认可唯一映射的 PMS 房型正确写入统一 ID，歧义房型保持为空。

## 登录助手稳定性

登录助手现在提供面板中的“关闭登录助手”操作，并在退出时清空状态文件中的 PID。关闭前会校验 PID 对应的命令确实是登录助手，避免旧状态文件遇到 Windows PID 复用时误判或误结束无关进程。

## 13. 后续产品方向（非当前强制任务）

现有采集和数据库已经可以支持下一阶段从“数据采集工具”向“经营决策辅助”扩展，例如：

- 酒店每日经营看板。
- PMS 与美团/携程跨平台对比。
- OTA 流量和转化异常提醒。
- 房态、价格、活动、订单的关联分析。
- 调价建议和收益管理辅助。

开发这些能力前，优先保证现有采集任务稳定、数据库口径一致、房型映射可靠。

## 14. 新 Codex 会话推荐开场指令

可以直接给新会话发送：

```text
请先阅读仓库根目录 AGENTS.md 和 docs/PROJECT_STATUS.md。
然后重新检查当前 master HEAD，并根据当前源代码确认文档中的实现状态是否仍然有效。
本次任务开始前不要改代码，先用简短内容告诉我：
1. 你理解这个项目主要做什么；
2. 与本次任务最相关的目录/文件；
3. 你准备遵守的关键业务规则。
确认后再开始修改。
```

这样可以让新会话先建立项目上下文，再进入具体编码任务。
