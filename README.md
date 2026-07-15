# 酒店数据采集服务

面向酒店运营的数据采集与调价服务，统一采集美团、携程和别样红 PMS 数据，并写入 MySQL。项目提供本地管理面板、定时调度、OTA 登录助手、房型映射和审核后调价功能。

## 主要功能

- 美团：经营指标、评价及明细、活动、商品价格、扫码订单、用户与曝光来源等。
- 携程：经营指标、评价及明细、活动和商品价格等。
- PMS：RS01、JD01、JD04、JY01、JY03、JL01、JL02、KF11 和房类预测。
- 调价：美团和携程调价任务创建、审核、预览、执行与状态追踪。
- 运维：本地 Web 面板、手动运行、周期调度、日志查看和桌面快捷方式。

## 目录结构

```text
酒店数据采集服务/
├─ OTA采集服务/                 管理面板、调度、配置和数据库迁移
├─ 美团OTA数据采集代码/         美团采集任务
├─ 携程OTA数据采集代码/         携程采集任务
├─ 正式数据抓取-PMS（别样红）/  PMS 采集与 OTA 调价
├─ runtime/                     便携 Python 与浏览器运行环境（不提交）
├─ OTA数据/                     本地采集输出（不提交）
└─ ota_mysql_writer.py          公共 MySQL 写入模块
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

4. 打开 `http://127.0.0.1:8765`，完成美团、携程登录并检查房型映射。
5. 首次手动运行一轮任务，确认采集和数据库写入正常。

## 命令行

```powershell
runtime\python.exe "OTA采集服务/runner.py" status
runtime\python.exe "OTA采集服务/runner.py" run-once
runtime\python.exe "OTA采集服务/runner.py" run-task meituan_scan_order
runtime\python.exe "OTA采集服务/runner.py" run-task pms_fetch
```

## 数据库

`OTA采集服务/*.sql` 用于首次建表和旧数据库升级。日常采集不会自动执行这些 SQL；部分迁移包含去重、删列或索引调整，应根据当前数据库版本按需执行，不要重复运行。

## 配置与安全

以下内容只保存在本地，不应提交到 GitHub：

- `OTA采集服务/config/settings.json`
- Cookie、密码、签名 URL 和 PMS 会话
- 浏览器用户目录、日志、状态文件和采集输出
- `runtime` 便携运行环境

仓库仅提交 `settings.example.json` 等脱敏示例。交付真实配置或登录状态时，请通过受控渠道传输，并在更换客户或酒店前清除旧凭证。

## 运行环境

- Windows 10/11
- Microsoft Edge
- MySQL 8.x 或兼容版本
- 可访问美团、携程、PMS 和目标数据库的网络
