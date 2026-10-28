"""美团 PMS 人工登录助手：核验 Cookie 后刷新当前酒店名称和报表机构 ID。"""
import re
import time
from urllib.parse import urlsplit

from .room_status import parse_room_types
import config_transaction

PLATFORM = "meituan_pms"
ROOM_TYPES_PATH = "/hotelpms/api/v1/inventory/rooms/getRealTimeRoomCount"
HOTEL_INFO_URL = "https://pms.meituan.com/hotelpms/api/v1/queryUserCurrentLoginInfo"


def verified_cookie(response):
    """只接受已成功读取有效房型的目标站点 GET，不接受静态页或登录跳转。"""
    url = urlsplit(response.url)
    if (url.scheme != "https" or url.netloc != "pms.meituan.com" or url.path != ROOM_TYPES_PATH
            or response.status != 200 or response.request.method != "GET"):
        return ""
    parse_room_types(response.json())
    cookie = str(response.request.all_headers().get("cookie") or "").strip()
    if not cookie or "\r" in cookie or "\n" in cookie:
        return ""
    return cookie


def fetch_hotel_identity(context, cookie):
    """仅查询一次已验证会话；hotelId 才是报表请求的 orgId，不能取组织 orgId。"""
    response = context.request.get(HOTEL_INFO_URL, headers={"Cookie": cookie}, timeout=15000, max_redirects=0)
    if response.status != 200 or response.url != HOTEL_INFO_URL:
        raise ValueError("当前酒店信息接口未返回成功响应")
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or payload.get("code") != 10000 or str(data.get("orgType")) != "4":
        raise ValueError("当前登录机构不是有效酒店")
    org_id = str(data.get("hotelId") or "").strip()
    name = data.get("orgName")
    name = " ".join(name.split()) if isinstance(name, str) else ""
    if not re.fullmatch(r"[0-9]+", org_id) or not name or len(name) > 255:
        raise ValueError("当前酒店名称或酒店标识缺失")
    return {"hotel_name": name, "org_id": org_id}


def save_verified_cookie(api, cookie, hotel_id, identity=None):
    if identity is not None:
        if (not isinstance(identity.get("hotel_name"), str) or not identity["hotel_name"].strip()
                or not re.fullmatch(r"[0-9]+", str(identity.get("org_id") or ""))):
            raise ValueError("当前酒店信息无效，未保存登录结果")
    with config_transaction.transaction(api.runner.CONFIG_PATH):
        settings = api.runner.load_settings()
        if (api.runner.active_pms_provider(settings) != PLATFORM
                or api.runner.internal_hotel_id(settings) != hotel_id):
            raise RuntimeError("当前酒店或 PMS 已切换，本次登录状态未保存，请重新打开助手")
        section = settings.setdefault(PLATFORM, {})
        section["cookie"] = cookie
        if identity is not None:
            section.update(hotel_name=identity["hotel_name"].strip(), org_id=str(identity["org_id"]))
        api.runner.save_json(api.runner.CONFIG_PATH, settings)


def run(switch_account, *, api):
    api.stop_path(PLATFORM).unlink(missing_ok=True)
    context = None
    try:
        settings = api.runner.load_settings()
        hotel_id = api.runner.internal_hotel_id(settings)
        with api.sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(api.profile_path(PLATFORM)), channel="msedge", headless=False,
                chromium_sandbox=True, no_viewport=True, locale="zh-CN", timezone_id="Asia/Shanghai",
                args=["--start-maximized"],
            )
            context.add_init_script("Object.defineProperty(Navigator.prototype, 'webdriver', {get: () => false});")
            if switch_account:
                context.clear_cookies()
            observed = {"cookie": ""}

            def observe(response):
                try:
                    cookie = verified_cookie(response)
                    if cookie:
                        observed["cookie"] = cookie
                except Exception:
                    pass  # 不把响应正文、请求头或原始错误写入日志。

            context.on("response", observe)
            page = api.initial_login_page(context, PLATFORM)
            api.write_status(PLATFORM, "waiting", "请在 Edge 手动登录美团 PMS，并进入房类预测页")
            api.navigate(page, api.PLATFORMS[PLATFORM]["url"])
            deadline = time.monotonic() + 600
            while not observed["cookie"]:
                if api.stop_path(PLATFORM).exists():
                    raise api.LoginCancelled("美团 PMS 登录已取消")
                if not context.pages or time.monotonic() >= deadline:
                    raise api.LoginCancelled("登录窗口已关闭或等待超时，请重新打开助手")
                # 仅处理浏览器事件，不主动发送核验请求、不自动刷新。
                context.pages[-1].wait_for_timeout(500)
            context.remove_listener("response", observe)
            if api.stop_path(PLATFORM).exists():
                raise api.LoginCancelled("美团 PMS 登录已取消")
            identity = None
            try:
                identity = fetch_hotel_identity(context, observed["cookie"])
            except Exception:
                pass  # 保留旧酒店参数并在成功提示中告警，不记录响应或认证信息。
            if api.stop_path(PLATFORM).exists():
                raise api.LoginCancelled("美团 PMS 登录已取消")
            save_verified_cookie(api, observed["cookie"], hotel_id, identity)
            message = ("美团 PMS 登录已验证，Cookie、酒店名称和报表机构 ID 已更新" if identity else
                       "美团 PMS 登录已验证，Cookie 已保存；当前酒店识别失败，已保留原酒店名称和机构 ID，请手工核对")
            api.write_status(PLATFORM, "success", message,
                             completed_at=api.now_text(), identity_updated=bool(identity), hotel_id=hotel_id)
            try:
                api.show_success(context, PLATFORM, len(observed["cookie"].split(";")))
                page.wait_for_timeout(3000)
                context.close()
            except Exception:
                pass  # 保存已成功，用户关闭窗口不应将登录结果改成失败。
        return 0
    except api.LoginCancelled as exc:
        api.write_status(PLATFORM, "cancelled", str(exc), pid=0)
        return 1
    except Exception:
        api.write_status(PLATFORM, "failed", "美团 PMS 手动登录未完成，未更新 Cookie，请重新打开助手", pid=0)
        return 2
    finally:
        api.stop_profile_edge_processes(PLATFORM)
        api.stop_path(PLATFORM).unlink(missing_ok=True)
        status = api.read_status(PLATFORM)
        if status.get("pid"):
            api.write_status(PLATFORM, status.get("status", "cancelled"), status.get("message", "登录助手已退出"), pid=0)
