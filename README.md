# 酒店数据采集服务

面向酒店运营的多平台数据采集与运营自动化服务，统一采集美团、携程和别样红 PMS 数据，完成结构转换、MySQL 入库、房型/商品映射，并提供本地管理面板、周期调度、登录辅助、评价回复和审核后调价能力。

主要链路：

```text
别样红 PMS / 美团 OTA / 携程 OTA
        ↓
登录态与页面/API采集
        ↓
数据转换、清洗与快照化
        ↓
MySQL 统一存储
        ↓
统一房型 / OTA 商品映射
        ↓
本地管理面板 + 定时调度
        ↓
运营分析 / 评价回复 / 调价任务
```

## 主要能力

### 美团 OTA

当前已覆盖：

- 经营指标、流量转化、曝光来源、用户来源
- 已加入权益、促销开通状态、活动
- 近 30 天推广效果、视频上传状态、周边事件
- 订单流失、扫码订单
- 评价及评价明细
- 商品价格、房型/商品映射
- 调价任务相关能力

部分美团数据依赖登录后的页面动态请求和 Edge profile，不是长期固定签名 URL 的纯接口调用。

### 携程 OTA

当前已覆盖：

- 经营指标、30 天竞争指标、流量转化
- 订单明细、订单流失
- 已加入权益、促销开通状态、活动
- 用户画像、PSI 分数、推广效果
- 评价及评价明细
- 商品价格、房型/商品映射
- 调价任务相关能力

### 别样红 PMS

统一入口支持以下 11 类报表/快照：

- `RS01` 房费收入日报
- `JD01` 预订明细
- `JD04` 在住/续住相关数据
- `JY01` 酒店经营统计日报
- `JY03` 酒店经营统计月报
- `JL01` 房型经营日报
- `JL02` 酒店经营日报
- `JL11` 房型分类
- `KF11` 房态快照
- `FORECAST` 房类预测
- `ROOM_STATUS` 每小时房态

PMS 主流程同时包含登录态复用、本轮新数据校验、统一 ETL、报表间访问间隔和连续网络失败保护。

### 调度与运维

- 本地 Web 管理面板
- 手动运行单个任务或完整采集流程
- 周期调度与任务状态记录
- 日志查看与停止当前采集
- 美团、携程登录辅助
- 公共 Edge profile 并发锁
- 房型映射与 OTA 商品映射
- 评价回复任务
- 美团、携程调价任务创建、审核、预览、执行与状态追踪

## 目录结构

```text
hotel_data/
├─ AGENTS.md                    Codex / 开发长期规则与业务约束
├─ README.md                    项目入口与运行说明
├─ docs/
│  └─ PROJECT_STATUS.md         当前实现进度、近期修复和待观察事项
├─ OTA采集服务/                 管理面板、调度、配置、数据库 Schema、房型映射
├─ 美团OTA数据采集代码/         美团采集和相关调价代码
├─ 携程OTA数据采集代码/         携程采集和相关调价代码
├─ 正式数据抓取-PMS（别样红）/  PMS 登录、报表采集和 ETL
├─ tests/                       项目测试
├─ runtime/                     便携 Python 与浏览器运行环境（不提交）
├─ OTA数据/                     本地采集输出（不提交）
├─ ota_mysql_writer.py          OTA 公共 MySQL 写入模块
└─ mysql_connection.py          公共数据库连接辅助
```

## 快速开始

交付包包含 `runtime` 时，无需另外安装 Python 或 Playwright。

1. 复制示例配置：

   ```powershell
   Copy-Item "OTA采集服务/config/settings.example.json" "OTA采集服务/config/settings.json"
   ```

2. 在配置文件或管理面板中填写酒店、MySQL、PMS 和 OTA 参数。
3. 启动服务：

   ```text
   OTA采集服务\启动酒店数据采集.bat
   ```

4. 打开：

   ```text
   http://127.0.0.1:8765
   ```

5. 完成美团、携程登录并检查房型映射。
6. 首次手动运行一轮任务，确认采集、ETL 和数据库写入正常。

## 命令行

```powershell
runtime\python.exe "OTA采集服务/runner.py" status
runtime\python.exe "OTA采集服务/runner.py" run-once
runtime\python.exe "OTA采集服务/runner.py" run-task meituan_scan_order
runtime\python.exe "OTA采集服务/runner.py" run-task pms_fetch
```

PMS 需要单独调试或补采报表时，可查看：

```powershell
runtime\python.exe "正式数据抓取-PMS（别样红）/PMS登录/fetch_main.py" --help
```

## 数据库

当前数据库 Schema 的主要事实来源是：

```text
OTA采集服务/database/create_all_tables.sql
```

该文件用于当前完整 Schema 的初始化参考；旧数据库升级脚本应根据实际版本按需执行，不应在日常采集过程中重复运行。

项目当前采用“一库一酒店”的部署边界。详细表规模、近期字段变化和 View 信息见 `docs/PROJECT_STATUS.md`。

## 数据安全与运行保护

项目包含多类外部页面/API 自动化，因此当前实现保留了若干运行保护：

- PMS 本轮未生成新 JSON 时禁止旧文件继续 ETL。
- PMS 全量报表之间加入访问间隔，并在连续连接失败时停止本轮剩余访问。
- 美团部分页面型任务主动降低访问频率，避免无意义的高频重复访问。
- 页面采集共用 Edge profile 时使用浏览器锁，避免人工和定时任务同时占用。
- 对平台风险控制只做安全失败、降低访问频率和诊断，不实现绕过机制。

具体业务规则和不可随意修改的行为见 `AGENTS.md`。

## 开发与项目状态

开始开发、Code Review 或新的 Codex 会话前，建议先阅读：

- [`AGENTS.md`](AGENTS.md)：长期业务规则、开发约束、事实来源优先级和 Git 工作方式。
- [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md)：当前功能范围、近期修复、重要提交脉络和待观察事项。

文档与实现不一致时，应重新检查当前 `master` 源码。数据库结构优先查看 `OTA采集服务/database/create_all_tables.sql`，统一调度任务优先查看 `OTA采集服务/runner.py`。

## 配置与安全

以下内容只保存在本地，不应提交到 GitHub：

- `OTA采集服务/config/settings.json`
- Cookie、密码、Token、签名 URL 和 PMS 会话
- 浏览器用户目录、日志、状态文件和采集输出
- `%LOCALAPPDATA%/HotelAgent/` 下的浏览器 profile 和任务 state
- 真实客户数据
- `runtime` 便携运行环境

仓库仅提交 `settings.example.json` 等脱敏示例。交付真实配置或登录状态时，请通过受控渠道传输，并在更换客户或酒店前清除旧凭证。

## 运行环境

- Windows 10/11
- Microsoft Edge
- MySQL 8.x 或兼容版本
- 可访问美团、携程、PMS 和目标数据库的网络
