from __future__ import annotations

import json
import subprocess
import sys
import threading
from typing import Any

from flask import Flask, jsonify, redirect, request, url_for

from config_schema import (
    CONFIG_SECTIONS,
    NUMBER_FIELDS,
    SHORT_SECRET_FIELDS,
)
import foxhis_probe
import browser_cookie_routes
import log_routes
import local_reset
import platform_login
import pms_providers
import price_routes
import price_tasks
import promotion_routes
import room_mappings
import runner
import status_routes
from panel_common import esc, get_path, page, set_path, task_label


app = Flask(__name__)


def mask(value: Any) -> str:
    text = "" if value is None else str(value)
    if not text:
        return "\u672a\u914d\u7f6e"
    if len(text) <= 16:
        return "*" * len(text)
    return f"{text[:8]}...{text[-6:]}"


def apply_form_to_settings(settings: dict[str, Any]) -> dict[str, Any]:
    for section in CONFIG_SECTIONS:
        for key, _label, secret, _is_advanced in section["fields"]:
            value = request.form.get(key)
            if value is None:
                continue
            value = value.strip()
            if secret:
                if value:
                    set_path(settings, key, value)
                continue
            if not value and key in {"meituan.hotel_name", "meituan.poi_id", "meituan.partner_id", "meituan.biz_account_id"}:
                continue
            old = get_path(settings, key)
            if isinstance(old, int):
                try:
                    value = int(value)
                except ValueError:
                    value = old
            set_path(settings, key, value)
    provider = request.form.get("pms_provider")
    if provider:
        pms_providers.apply_provider_selection(settings, provider)
    settings.setdefault("tasks", {})
    for name in runner.TASKS:
        settings["tasks"][name] = request.form.get(f"task.{name}") == "on"
    return settings


def probe_hotel_name(platform: str, cookie: str, secondary_cookie: str = "") -> dict[str, Any]:
    command = [sys.executable, str(runner.ROOT / "hotel_name_probe.py"), platform, "--cookie", cookie]
    if secondary_cookie:
        command.extend(["--secondary-cookie", secondary_cookie])
    completed = subprocess.run(
        command,
        cwd=str(runner.ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = completed.stdout.strip() or completed.stderr.strip()
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        data = {"ok": False, "error": output[-800:], "hotel_name": "", "candidates": []}
    if completed.returncode != 0 and not data.get("error"):
        data["error"] = f"return_code={completed.returncode}"
    return data


room_mappings.register(app, page)
status_routes.register(app)
log_routes.register(app)
price_routes.register(app)
promotion_routes.register(app)
browser_cookie_routes.register(app)


def config_control(settings: dict[str, Any], key: str, label: str, secret: bool) -> str:
    if key == "pms.login_base_url":
        from pms_manual_login import login_control
        return login_control(settings)
    current = get_path(settings, key)
    pair_class = " mysql-pair-field" if key in {"mysql.password", "mysql.database"} else ""
    if secret:
        if key in SHORT_SECRET_FIELDS:
            control = (
                f"<input type='password' name='{esc(key)}' value='' "
                "autocomplete='new-password' placeholder='留空则保留原值'>"
            )
        else:
            control = (
                f"<textarea name='{esc(key)}' rows='2' "
                "placeholder='粘贴新值；留空则保留原值'></textarea>"
            )
        return (
            f"<div class='field secret-field{pair_class}'><label>{esc(label)}</label>"
            f"<div class='secret-current'>已配置 · {esc(mask(current))}</div>{control}</div>"
        )

    input_type = "number" if key in NUMBER_FIELDS else "text"
    autocomplete = " autocomplete='username'" if key == "pms.username" else ""
    return (
        f"<div class='field{pair_class}'><label>{esc(label)}</label>"
        f"<input type='{input_type}' name='{esc(key)}' value='{esc(current)}'{autocomplete}></div>"
    )


def config_section(settings: dict[str, Any], section: dict[str, Any]) -> str:
    regular = []
    advanced = []
    for key, label, secret, is_advanced in section["fields"]:
        (advanced if is_advanced else regular).append(config_control(settings, key, label, secret))

    section_key = section["key"]
    badge_map = {"system": "CORE", "meituan": "MEI", "ctrip": "CTRIP"}
    badge_map.update(
        {spec["card_key"]: spec["badge"] for spec in pms_providers.PMS_PROVIDERS.values()}
    )
    badge = badge_map.get(section_key, section_key.upper())
    advanced_html = ""
    if advanced:
        login_help = (
            "<p>登录助手无法完成登录时，可在日常 Edge 登录后手动填写 Cookie；推广效果另需推广后台 Cookie。</p>"
            "<div class='advanced-login-actions'>"
            "<button type='submit' class='secondary compact' name='login_mode' value='manual' formaction='/platform-login/meituan'>日常Edge登录（手动填写Cookie）</button>"
            "<a class='button secondary compact' href='/browser-cookie-import' target='_blank' rel='noopener'>浏览器扩展导入</a>"
            "</div>"
        ) if section_key == "meituan" else ""
        advanced_html = (
            "<details class='inner-details'>"
            f"<summary>接口与高级参数 <span>{len(advanced)} 项</span></summary>"
            f"{login_help}"
            f"<div class='config-grid advanced-grid'>{''.join(advanced)}</div></details>"
        )

    actions = ""
    login_html = ""
    if section_key == "meituan":
        actions = (
            "<div class='config-actions'>"
            "<button type='submit' class='compact' formaction='/platform-login/meituan'>打开Edge登录</button>"
            "<button type='submit' class='secondary compact' name='login_mode' value='switch' formaction='/platform-login/meituan'>&#20999;&#25442;&#36134;&#21495;</button>"
            "<button type='submit' class='secondary compact' formaction='/platform-login/meituan/cancel'>关闭登录助手</button>"
            "<button type='submit' class='secondary compact' formaction='/detect-hotel/meituan'>识别酒店参数</button>"
            "</div>"
        )
    elif section_key == "ctrip":
        actions = (
            "<div class='config-actions'>"
            "<button type='submit' class='compact' formaction='/platform-login/ctrip'>打开Edge登录</button>"
            "<button type='submit' class='secondary compact' name='login_mode' value='switch' formaction='/platform-login/ctrip'>&#20999;&#25442;&#36134;&#21495;</button>"
            "<button type='submit' class='secondary compact' formaction='/platform-login/ctrip/cancel'>关闭登录助手</button>"
            "<button type='submit' class='secondary compact' formaction='/detect-hotel/ctrip'>识别酒店</button>"
            "</div>"
        )
    elif section_key == "bypms":
        actions = (
            "<div class='config-actions'>"
            "<button type='submit' class='compact' formaction='/platform-login/bypms'>打开Edge登录</button>"
            "<button type='submit' class='secondary compact' name='login_mode' value='switch' formaction='/platform-login/bypms'>切换账号</button>"
            "<button type='submit' class='secondary compact' formaction='/platform-login/bypms/cancel'>关闭登录助手</button>"
            "</div>"
        )
    elif section_key == "foxhis":
        # Foxhis 不需要浏览器登录，改为「从本机客户端读取连接参数」
        actions = (
            "<div class='config-actions'>"
            "<button type='submit' class='compact' formaction='/detect-foxhis'>从本机客户端自动读取</button>"
            "<button type='submit' class='secondary compact' name='deep' value='1' formaction='/detect-foxhis'>校验数据库连通性</button>"
            "</div>"
        )
    if section_key in {"meituan", "ctrip", "bypms"}:
        login = platform_login.read_status(section_key)
        state = str(login.get("status") or "never")
        state_class = {"success": "good", "failed": "danger", "waiting": "warn", "syncing": "warn", "starting": "warn"}.get(state, "idle")
        login_hint = ("使用专用 Edge 会话手动登录，Cookie 自动保存；遇到登录验证问题，可在高级参数中使用日常 Edge 手动填写 Cookie。"
                      if section_key == "meituan" else "使用可见Edge手动登录，Cookie自动保存；高级参数中仍可手动填写。")
        login_html = (
            f"<div class='login-assistant' data-login-platform='{section_key}'>"
            "<div><strong>浏览器登录助手</strong>"
            f"<small>{login_hint}</small></div>"
            f"<span class='pill {state_class} login-state' data-login-state>{esc(login.get('message') or '尚未使用')}</span>"
            "</div>"
        )

    heading_actions = actions
    platform_actions = ""
    if section_key in pms_providers.card_keys():
        heading_actions = pms_source_selector(settings)
        if actions:
            platform_actions = f"<div class='pms-platform-actions'>{actions}</div>"
    elif section_key in {"meituan", "ctrip"}:
        heading_actions = ""
        platform_actions = f"<div class='ota-platform-actions'>{actions}</div>"

    return f"""
    <section class="panel config-card config-{esc(section_key)}" data-pms-card="{esc(section_key)}">
      <div class="config-heading">
        <div>
          <div class="config-kicker">{badge}</div>
          <h2>{esc(section['title'])}</h2>
        </div>
        {heading_actions}
      </div>
      <p class="config-hint">{esc(section['hint'])}</p>
      {platform_actions}
      {login_html}
      <div class="config-grid">{''.join(regular)}</div>
      {advanced_html}
    </section>
    """


def active_pms_provider(settings: dict[str, Any]) -> str:
    return runner.active_pms_provider(settings)


def pms_source_selector(settings: dict[str, Any]) -> str:
    selected = active_pms_provider(settings)
    options = "".join(
        f"<option value='{esc(item['value'])}'"
        f"{' selected' if selected == item['value'] else ''}>{esc(item['label'])}</option>"
        for item in pms_providers.provider_options()
    )
    return (
        "<label class='pms-provider-select'><span>当前 PMS 平台</span>"
        "<select name='pms_provider' aria-label='PMS 平台选择'>"
        f"{options}"
        "</select><small>切换后保留其他平台配置</small></label>"
    )


@app.get("/config")
def config_page() -> str:
    settings = runner.load_settings()
    notice = request.args.get("notice", "")
    error = request.args.get("error", "")
    message_html = ""
    if notice:
        message_html += f"<div class='success'>{esc(notice)}</div>"
    if error:
        message_html += f"<div class='warning'>{esc(error)}</div>"
    task_checks = []
    for name in runner.TASKS:
        checked = "checked" if (settings.get("tasks") or {}).get(name, False) else ""
        task_checks.append(
            f"<label class='switch-item'><input type='checkbox' name='task.{esc(name)}' {checked}>"
            f"<span><strong>{esc(task_label(name))}</strong><small>{esc(name)}</small></span></label>"
        )
    sections = {section["key"]: config_section(settings, section) for section in CONFIG_SECTIONS}
    pms_cards = "".join(
        sections[spec["card_key"]] for spec in pms_providers.PMS_PROVIDERS.values()
    )
    card_map_js = json.dumps(
        {key: spec["card_key"] for key, spec in pms_providers.PMS_PROVIDERS.items()},
        ensure_ascii=False,
    )
    default_provider = pms_providers.DEFAULT_PROVIDER
    base_group = (
        "<section class='config-group'><div class='config-group-heading'>基础配置</div>"
        "<div class='config-layout'>"
        f"{sections['system']}"
        "<div class='pms-config-stack'>"
        f"{pms_cards}"
        "</div></div></section>"
    )
    ota_group = "".join(
        "<section class='config-group'>"
        f"<div class='config-group-heading'>{title}</div>"
        f"<div class='config-layout'>{''.join(sections[key] for key in keys)}</div>"
        "</section>"
        for title, keys in (("OTA 平台", ("meituan", "ctrip")),)
    )
    grouped_sections = base_group + ota_group
    body = f"""
<form method="post" action="/config" class="config-form">
  {message_html}
  <div class="config-groups">{grouped_sections}</div>
  <section class="panel config-card task-switches">
    <div class="config-heading">
      <div>
        <div class="config-kicker">JOBS</div>
        <h2>采集任务</h2>
        <p>关闭暂时不需要运行的采集模块。</p>
      </div>
    </div>
    <div class="switch-grid">{''.join(task_checks)}</div>
  </section>
  <div class="save-bar">
    <div><strong>敏感信息不会回显</strong><span>密码、Cookie 和签名 URL 留空即保留原值</span></div>
    <div class="save-actions">
      <button type="submit" class="secondary reset-config" formmethod="post" formaction="/config/reset"
        onclick="return confirm('将清除本地配置、登录 Cookie 与专用浏览器 Profile；数据库中的已采集数据不会删除。确定继续吗？')">重置本地配置</button>
      <button type="submit">保存全部配置</button>
    </div>
  </div>
</form>
<script>
async function refreshLoginStates() {{
  for (const box of document.querySelectorAll('[data-login-platform]')) {{
    const platform = box.dataset.loginPlatform;
    try {{
      const response = await fetch('/api/platform-login/' + platform, {{cache: 'no-store'}});
      const data = await response.json();
      const state = box.querySelector('[data-login-state]');
      state.textContent = data.message || data.status;
      state.className = 'pill login-state ' + ({{
        success: 'good', failed: 'danger', waiting: 'warn',
        syncing: 'warn', starting: 'warn'
      }}[data.status] || 'idle');
      const completed = data.completed_at || data.updated_at;
      if (platform === 'pms' && data.status === 'success' && data.hotel_name && completed && box.dataset.completedAt !== completed) {{
        const input = document.querySelector('[name="pms.hotel_name"]');
        if (input) input.value = input.defaultValue = data.hotel_name;
        box.dataset.completedAt = completed;
      }}
    }} catch (_) {{}}
  }}
}}
setInterval(refreshLoginStates, 2000);

const PMS_CARDS = {card_map_js};
const PMS_DEFAULT_PROVIDER = '{default_provider}';

function selectPmsProvider(provider) {{
  for (const select of document.querySelectorAll('select[name="pms_provider"]')) select.value = provider;
  const target = PMS_CARDS[provider] || PMS_CARDS[PMS_DEFAULT_PROVIDER];
  for (const card of document.querySelectorAll('[data-pms-card]')) {{
    if (!Object.values(PMS_CARDS).includes(card.dataset.pmsCard)) continue;
    card.hidden = card.dataset.pmsCard !== target;
  }}
}}
for (const select of document.querySelectorAll('select[name="pms_provider"]')) {{
  select.addEventListener('change', () => selectPmsProvider(select.value));
}}
selectPmsProvider(document.querySelector('select[name="pms_provider"]')?.value || PMS_DEFAULT_PROVIDER);
</script>"""
    return page("\u914d\u7f6e\u4e2d\u5fc3", body, "config")


@app.post("/config")
def save_config():
    settings = runner.load_settings()
    settings = apply_form_to_settings(settings)
    runner.save_json(runner.CONFIG_PATH, settings)
    return redirect(url_for("config_page"))


@app.post("/config/reset")
def reset_config():
    try:
        local_reset.reset_local_configuration()
        return redirect(
            url_for(
                "config_page",
                notice="本地配置与登录状态已重置；数据库数据未删除，定时任务已暂停",
            )
        )
    except Exception as exc:
        return redirect(url_for("config_page", error=str(exc)))


@app.post("/platform-login/<platform>")
def start_platform_login(platform: str):
    if platform not in platform_login.PLATFORMS:
        return redirect(url_for("config_page", error="不支持的平台"))
    try:
        settings = apply_form_to_settings(runner.load_settings())
        if platform == "pms":
            from pms_manual_login import ensure_idle
            ensure_idle()
            if runner.active_pms_provider(settings) != "byh":
                raise ValueError("请先选择别样红 PMS 平台")
            password_mode = request.form.get("login_mode") == "password"
            if password_mode:
                if not platform_login.cancel("pms"):
                    raise RuntimeError("请先关闭别样红登录助手")
            settings.setdefault("pms", {})["login_mode"] = "password" if password_mode else "manual"
        runner.save_json(runner.CONFIG_PATH, settings)
        if platform == "pms" and password_mode:
            return redirect(url_for("config_page", notice="已切回别样红账号密码自动登录；下一次采集按原有方式执行"))
        if platform == "meituan" and request.form.get("login_mode") == "manual":
            platform_login.open_local_edge()
            return redirect(url_for("config_page", notice="已请求打开本机 Edge；请手动完成美团登录，再在高级参数填写 ME/EB Cookie 并保存配置。"))
        switch_account = request.form.get("login_mode") == "switch"
        platform_login.start(platform, settings, switch_account=switch_account)
        label = platform_login.PLATFORMS[platform]["label"]
        return redirect(url_for("config_page", notice=f"正在启动{label}登录窗口，请在Edge中手动登录"))
    except Exception as exc:
        return redirect(url_for("config_page", error=str(exc)))


@app.post("/platform-login/<platform>/cancel")
def cancel_platform_login(platform: str):
    if platform not in platform_login.PLATFORMS:
        return redirect(url_for("config_page", error="不支持的平台"))
    if platform_login.cancel(platform):
        return redirect(url_for("config_page", notice="登录助手已关闭"))
    return redirect(url_for("config_page", error="无法关闭登录助手，请稍后重试"))


@app.get("/api/platform-login/<platform>")
def platform_login_status(platform: str):
    if platform not in platform_login.PLATFORMS:
        return jsonify({"status": "failed", "message": "不支持的平台"}), 404
    data = platform_login.read_status(platform)
    return jsonify(
        {
            "status": data.get("status", "never"),
            "message": data.get("message", ""),
            "updated_at": data.get("updated_at", ""),
            "completed_at": data.get("completed_at", ""),
            "cookie_count": data.get("cookie_count", 0),
            "hotel_name": (get_path(runner.load_settings(), "pms.hotel_name") or "")
            if platform == "pms" and data.get("status") == "success" else "",
        }
    )


@app.post("/config/ota-scope")
def save_ota_scope():
    scope = request.form.get("scope", "")
    if scope not in {"all", "meituan", "ctrip"}:
        return redirect(url_for("config_page", error="无效的 OTA 采集范围"))
    settings = runner.load_settings()
    settings.setdefault("meituan", {})["enabled"] = scope in {"all", "meituan"}
    settings.setdefault("ctrip", {})["enabled"] = scope in {"all", "ctrip"}
    runner.save_json(runner.CONFIG_PATH, settings)
    target = request.form.get("return_to", "/config")
    return redirect(target if target in {"/", "/config"} else "/config")


@app.post("/detect-hotel/<platform>")
def detect_hotel(platform: str):
    if platform not in {"meituan", "ctrip"}:
        return redirect(url_for("config_page", error="\u4e0d\u652f\u6301\u7684\u5e73\u53f0"))

    settings = apply_form_to_settings(runner.load_settings())
    if platform == "meituan":
        cookie = (get_path(settings, "meituan.me_cookie") or "").strip()
        secondary_cookie = (get_path(settings, "meituan.eb_cookie") or "").strip()
        if not cookie:
            cookie = secondary_cookie
            secondary_cookie = ""
        target_key = "meituan.hotel_name"
        label = "\u7f8e\u56e2"
    else:
        cookie = (get_path(settings, "ctrip.cookie") or "").strip()
        secondary_cookie = ""
        target_key = "ctrip.hotel_name"
        label = "\u643a\u7a0b"

    if not cookie:
        return redirect(url_for("config_page", error=f"{label} Cookie \u4e3a\u7a7a\uff0c\u8bf7\u5148\u7c98\u8d34 Cookie"))

    result = probe_hotel_name(platform, cookie, secondary_cookie)
    hotel_name = str(result.get("hotel_name") or "").strip()
    if platform == "meituan":
        config_fields = {
            "meituan.poi_id": "poi_id",
            "meituan.partner_id": "partner_id",
            "meituan.biz_account_id": "biz_account_id",
            "meituan.review_detail_url": "review_detail_url",
        }
        missing = [source for source in config_fields.values() if not str(result.get(source) or "").strip()]
        if not result.get("ok") or missing:
            reason = result.get("error") or (
                "\u8df3\u5230\u767b\u5f55\u9875\uff0cCookie \u53ef\u80fd\u5df2\u8fc7\u671f"
                if result.get("login_like")
                else "\u672a\u6355\u83b7\u5230\u5b8c\u6574\u7684 queryGeneralCommentInfo \u7b7e\u540d\u8bf7\u6c42"
            )
            return redirect(url_for("config_page", error=f"\u7f8e\u56e2\u8bc6\u522b\u5931\u8d25\uff1a{reason}"))
        for target, source in config_fields.items():
            set_path(settings, target, str(result[source]).strip())
        if hotel_name:
            set_path(settings, "meituan.hotel_name", hotel_name)
            notice = f"\u7f8e\u56e2\u9152\u5e97\u53c2\u6570\u8bc6\u522b\u6210\u529f\uff1a{hotel_name}"
        else:
            notice = "\u7f8e\u56e2\u9152\u5e97\u53c2\u6570\u5df2\u66f4\u65b0\uff1b\u672a\u8bc6\u522b\u5230\u9152\u5e97\u540d\uff0c\u5df2\u4fdd\u7559\u539f\u503c"
        runner.save_json(runner.CONFIG_PATH, settings)
        return redirect(url_for("config_page", notice=notice))

    if not result.get("ok") or not hotel_name:
        reason = result.get("error") or ("\u8df3\u5230\u767b\u5f55\u9875\uff0cCookie \u53ef\u80fd\u5df2\u8fc7\u671f" if result.get("login_like") else "\u672a\u8bc6\u522b\u5230\u9152\u5e97\u540d")
        return redirect(url_for("config_page", error=f"{label}\u8bc6\u522b\u5931\u8d25\uff1a{reason}"))

    set_path(settings, target_key, hotel_name)
    runner.save_json(runner.CONFIG_PATH, settings)
    return redirect(url_for("config_page", notice=f"{label}\u8bc6\u522b\u6210\u529f\uff1a{hotel_name}"))


@app.post("/detect-foxhis")
def detect_foxhis():
    """从本机 Foxhis 客户端读取连接参数并回填到配置（只读探测）。"""
    settings = apply_form_to_settings(runner.load_settings())
    deep = request.form.get("deep") == "1"
    result = foxhis_probe.probe(deep=deep)
    if not result.get("ok"):
        reason = "；".join(result.get("warnings") or ["未探测到完整的连接参数"])
        return redirect(url_for("config_page", error=f"Foxhis 自动探测失败：{reason}"))

    fields = result["fields"]
    mapping = {
        "foxhis.isql_path": "isql_path",
        "foxhis.interfaces_file": "interfaces_file",
        "foxhis.server": "server",
        "foxhis.database": "database",
        "foxhis.charset": "charset",
    }
    filled: list[str] = []
    for target, source in mapping.items():
        value = str(fields.get(source) or "").strip()
        if value:
            set_path(settings, target, value)
            filled.append(target.split(".", 1)[1])
    if user := (request.form.get("foxhis.username") or "").strip():
        set_path(settings, "foxhis.username", user)
    runner.save_json(runner.CONFIG_PATH, settings)

    notice = f"已从本机 Foxhis 客户端读取 {len(filled)} 项连接参数（{'、'.join(filled)}）"
    deep_check = (result.get("evidence") or {}).get("deep_check") or {}
    if deep_check.get("verdict"):
        notice += f"；{deep_check['verdict']}"
    if result.get("warnings"):
        notice += "；提示：" + "；".join(result["warnings"])
    notice += "；数据库账号与密码需手工填写"
    return redirect(url_for("config_page", notice=notice))


if __name__ == "__main__":
    cfg = runner.load_settings()
    svc = cfg.get("service") or {}
    threading.Thread(target=price_tasks.page_data, args=(cfg,), daemon=True).start()
    app.run(host=svc.get("panel_host", "127.0.0.1"), port=int(svc.get("panel_port", 8765)), debug=False)
