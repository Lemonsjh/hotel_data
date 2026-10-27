# 酒店数据采集服务

面向酒店运营的多平台数据采集与运营自动化服务，统一采集美团、携程及别样红、宝寓、西软 PMS 数据，完成结构转换、MySQL 入库、房型/商品映射，并提供本地管理面板、周期调度、登录辅助、评价回复和审核后调价能力。

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

## 一键代码更新（GitHub Releases）

部署目录包含 `一键更新.bat`、`update_guard.py` 和 `updater/` 时，可双击入口检查并确认安装公开仓库的最新正式 Release，不需要 Git 或 Token。此前发出的旧部署包需先补装更新器。

- 只替换白名单内的本地程序文件，不覆盖酒店配置、Cookie、浏览器资料、日志、采集数据和运行环境；不连接或迁移数据库。
- 有采集、调价、回复等业务正在运行时拒绝更新。校验通过、业务空闲后才停服，备份、安装并检查，失败回退代码，恢复此前运行的组件。
- Python/依赖不匹配时停止，另行安装运行环境。更新器不从 master 源码包直接覆盖程序。
- 发布者通过 GitHub Actions 的 **Build release update** 工作流构建；默认只上传测试包，明确勾选 publish 且在 master 上执行才创建正式 Release。
- 中断恢复：确认没有更新进程后移除本项目 `updater/update.lock`，执行 `runtime\python.exe updater\update.py --recover`；不要删除备份目录或酒店数据。

## 主要能力

### 美团 OTA

当前已覆盖：

- 经营指标、流量转化、用户来源
- 已加入权益、促销开通状态、活动
- 近 30 天推广效果、视频上传状态、周边事件
- 订单流失、扫码订单
- 评价及评价明细
- 商品价格、房型/商品映射
- 调价任务相关能力

部分美团数据依赖登录后的页面动态请求和 Edge profile，不是长期固定签名 URL 的纯接口调用。

登录助手无法完成验证时，可在美团卡片“接口与高级参数”点击“日常Edge登录（手动填写Cookie）”，在普通 Edge 中人工完成登录，然后把同一账号的完整 Cookie 填入对应配置并保存：

- ME Cookie：美团主站；EB Cookie：EB 工作台。
- 推广后台 Cookie：`ebmidas.dianping.com`，供近 30 天推广效果使用。
- 酒店亮点 Cookie：`tdc.meituan.com`；公益流量 Cookie：`gongyi.meituan.com`，对应内页未登录时填写。

普通 Edge 入口只打开网页，不自动读取或同步 Cookie，也不隐藏自动化标记或处理验证码。账号切换时请暂停采集，逐项核对 Cookie 和酒店参数后再运行；不同后台的 Cookie 不要混用。识别酒店只使用当前填写的 ME/EB Cookie，不回退到旧账号的浏览器资料。助手成功登录时另存一份本地会话，只有与当前 ME/EB Cookie 完全匹配时才供促销状态页复用；敏感会话不进入源码或更新包。现有冷却规则和动态签名请求方式不变。

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

统一入口支持以下 11 类报表/快照，其中 KF11 和 JD04 已暂停默认采集，可手工指定补采：

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

### 西软 Foxhis PMS

- 配置页下拉选择“西软 Foxhis”，仍共用一个 `pms_fetch` 任务；默认平台仍为别样红，任务默认勾选、自动调度默认关闭。
- 通过本机 Sybase 客户端的 `isql` 只读查询，不操作桌面界面。需安装客户端，确认服务名、数据库名、网络连接及只读账号权限；账号密码需人工填写。自动探测不会切换到另一个可达数据库。
- 实时房态写入 `pms_room_type_forecast` 和 `pms_room_type_hourly_status`；不采集西软 KF11，也不删除数据库已有表或历史数据。
- 同时支持 RS01、JL01、JY01、JY03、JL11、JD01。日结类夜审后每天成功写入一次；实时房态、订单和房费流水按轮刷新。默认夜审闸门为 03:30，应按酒店实际夜审完成时间调整。
- 酒店名称为空时，从源库 `sysoption` 的酒店名称配置读取并回填；不覆盖人工名称，读取失败或连接配置变化时停止本轮。
- 实时执行价支持经校验的同日入住离店价格段；当天应离但仍在住且没有当天价格段时，使用与主单离店时间一致的最后一晚价格，缺价和重叠仍拒绝覆盖快照。房费、ADR、RevPAR 保持两位小数。
- JL01 的手工调整项参与酒店总额核对，不生成虚构房型或分摊到真实房型。JD01 保留原始订单 ID，合法空结果正常完成并保留旧数据；无效响应表头不能当作空结果。
- 子报表仍串行执行；单张失败后继续其余独立子报表，任务显示“部分失败”并列出子项结果。人工中断立即停止后续子项，已成功写入的数据不回滚。
- 西软房型映射来源为 `pms_foxhis`，可先选择美团或携程任一平台建立映射，后续再补充另一平台。业务表按 PMS 原始房型名称精确补全统一 ID。
- 代码在 `OTA采集服务/foxhis_pms/`，沿用现有一键更新白名单，不增加 pip 依赖、不修改数据库结构。

手工只读取数、不写入 MySQL（仍会生成本地采集输出，且需配置对应环境变量）：

```powershell
runtime\python.exe "OTA采集服务/foxhis_pms/foxhis_main.py" --dry-run
```

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

### 美团登录脱敏诊断

从配置页打开美团登录助手或切换账号时，自动为本次人工登录生成一个 `*meituan_login_diagnostic*.log`，在“日志”页查看，原文件位于 `OTA采集服务/logs/`。重新打开助手后生效，不会自动提交登录、刷新重试或修改浏览器启动参数。

日志记录 UTC 时间、登录阶段、Edge/Playwright 版本、实测 `navigator.webdriver` 布尔值、固定请求分类、HTTP 状态、有限的 JSON 业务错误码及网络错误类型。不记录账号、密码、Cookie、Token、完整网址、请求头、请求正文、响应正文或页面截图。URL 中的路径、查询参数和子域名均不写入；未知错误文本丢弃。每次最多约 300 个事件、128 KiB，JSON 业务码读取最多 30 次；诊断异常不改变登录结果，成功保存 Cookie 后停止观察。手动关闭时的 `cancelled` 只表示窗口关闭，不代表服务端风控判定。

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
├─ 别样红PMS数据采集代码/     PMS 登录、报表采集和 ETL
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
runtime\python.exe "别样红PMS数据采集代码/PMS登录/fetch_main.py" --help
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
