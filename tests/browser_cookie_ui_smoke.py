"""Offline UI smoke test: isolated Flask, fake credentials, no real service/Meituan."""
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from flask import Flask
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "OTA采集服务")]
import runner
import browser_cookie_routes
from browser_cookie_payload import HOSTS


def check():
    with tempfile.TemporaryDirectory(dir=ROOT) as directory, \
         patch.object(runner, "CONFIG_PATH", Path(directory) / "config/settings.json"):
        runner.save_json(runner.CONFIG_PATH, {"meituan": {"poi_id": "123"}, "tasks": {"pms_fetch": False}})
        app = Flask(__name__, template_folder=str(ROOT / "OTA采集服务/templates"),
                    static_folder=str(ROOT / "OTA采集服务/static"))
        browser_cookie_routes.register(app)
        auth, imports = app.extensions["browser_cookie"]
        imports.idle = lambda _: None

        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as p:
                extension = ROOT / "extensions/meituan-login-sync"
                context = p.chromium.launch_persistent_context(
                    str(Path(directory) / "qa-profile"), channel="chromium", headless=True,
                    viewport={"width": 1080, "height": 1000},
                    args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}"],
                )
                requests_seen = []
                review_code = 10000
                review_page = "https://me.meituan.com/ebooking/merchant/comment-manage-react"
                review_api = "https://me.meituan.com/api/gw/v1/base/comments/queryGeneralCommentInfo"
                def route_fake(route):
                    url = route.request.url
                    if url.startswith(origin + "/") or url.startswith("chrome-extension://"):
                        return route.continue_()
                    if url.startswith(review_api + "?"):
                        requests_seen.append(url)
                        query = parse_qs(urlsplit(url).query)
                        valid = query.get("offset") == ["1"] and query.get("limit") == ["20"]
                        return route.fulfill(json={"code":review_code if valid else 1000,"data":{"poiName":"测试酒店"}})
                    if url == review_page:
                        return route.fulfill(content_type="text/html", body='''<body>测试酒店<script>
                          const original = window.fetch.bind(window);
                          window.fetch = (url, options) => { const u = new URL(url, location.href);
                            u.searchParams.set('mtgsig','fake-page-signed'); return original(u.href, options); };
                          fetch('/api/gw/v1/base/comments/queryGeneralCommentInfo?poiId=123&partnerId=456&bizAccountId=789&offset=1&limit=20')
                            .then(r => r.json()).then(() => document.body.dataset.ready = 'yes');
                          </script></body>''')
                    return route.abort()
                context.route("**/*", route_fake)
                ordinary = context.new_page()
                ordinary.goto(review_page)
                ordinary.wait_for_function("document.body.dataset.ready === 'yes'")
                page = context.new_page()
                page.goto(origin + "/browser-cookie-import")
                page.get_by_role("button", name="生成一次性配对码").click()
                page.locator("#code").wait_for(state="visible")
                code = page.locator("#code").input_value()
                assert len(code) == 32
                target = Path(directory) / "screenshots"
                target.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(target / "pairing.png"), full_page=True)
                # The unpacked extension, Cookie API and service worker are real; only
                # cookies, hotel verification and the isolated collector config are fixtures.
                worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
                extension_id = worker.url.split("/")[2]
                behavior = worker.evaluate("async () => await chrome.sidePanel.getPanelBehavior()")
                options = worker.evaluate("async () => await chrome.sidePanel.getOptions({})")
                assert behavior["openPanelOnActionClick"] is True
                assert options["path"] == "popup.html"
                for key, host in HOSTS.items():
                    context.add_cookies([{"name": {"me_cookie":"mebsid", "eb_cookie":"ebbsid"}.get(key, "session"),
                                          "value": key + "-fake", "domain": ".meituan.com" if key == "me_cookie" else host, "path":"/",
                                          "httpOnly": True, "secure": key not in {"me_cookie", "eb_cookie"}}])
                context.add_cookies([{"name": "parent-session", "value": "fake-parent", "domain": ".meituan.com",
                                      "path": "/", "httpOnly": True, "secure": True}])
                popup = context.new_page()
                popup.goto(f"chrome-extension://{extension_id}/popup.html")
                popup.locator("#port").fill(str(server.server_port))
                popup.locator("#pair-code").fill(code)
                popup.get_by_role("button", name="连接本机服务").click()
                popup.locator("#connected").wait_for(state="visible", timeout=15000)
                popup.get_by_role("button", name="读取 Cookie 并核对酒店").click()
                popup.locator("#hotel").wait_for(state="visible", timeout=10000)
                assert len(requests_seen) == 2, requests_seen
                assert popup.locator("#hotel-name").inner_text() == "测试酒店"
                assert not popup.locator("#commit").is_enabled()
                popup.locator("#confirmed").check()
                for width in (300, 360, 520):
                    popup.set_viewport_size({"width": width, "height": 760})
                    dimensions = popup.evaluate("""() => ({viewport:innerWidth,
                      body:document.body.getBoundingClientRect().width,
                      scroll:document.documentElement.scrollWidth})""")
                    assert abs(dimensions["body"] - width) < 1, dimensions
                    assert dimensions["scroll"] <= width, dimensions
                popup.set_viewport_size({"width": 400, "height": 1000})
                popup.screenshot(path=str(target / "popup-ready.png"), full_page=True)
                popup.get_by_role("button", name="确认保存到采集服务").click()
                popup.locator("#message").filter(has_text="已保存").wait_for(state="visible")
                saved = runner.load_json(runner.CONFIG_PATH, {})["meituan"]["me_cookie"]
                assert "mebsid=me_cookie-fake" in saved and "parent-session=fake-parent" in saved
                assert runner.load_json(runner.CONFIG_PATH, {})["tasks"] == {"pms_fetch": False}
                # A failed hotel check must not undo the independently completed Cookie read.
                review_code = 303
                before_failure = runner.CONFIG_PATH.read_bytes()
                popup.get_by_role("button", name="读取 Cookie 并核对酒店").click()
                popup.locator("#diagnostic").wait_for(state="visible")
                assert popup.locator("#groups .cookie-row").count() == 5
                assert popup.locator("#groups").inner_text().count("已读取 · 未保存") == 5
                assert popup.locator(".badge").inner_text() == "已配对"
                assert "业务码：303" in popup.locator("#diagnostic-details").inner_text()
                assert popup.get_by_role("button", name="重新读取并核对酒店").is_enabled()
                assert not popup.locator("#commit").is_visible()
                popup.screenshot(path=str(target / "sidebar-failed.png"), full_page=True)
                assert runner.CONFIG_PATH.read_bytes() == before_failure
                # Retry only via a user click and allow a new successful check.
                review_code = 10000
                popup.get_by_role("button", name="重新读取并核对酒店").click()
                popup.locator("#hotel").wait_for(state="visible")
                popup.get_by_role("button", name="取消导入").click()
                popup.locator("#message").filter(has_text="已取消").wait_for(state="visible")
                # Confirm actual Chromium partition queries reject CHIPS, not just a mock.
                worker.evaluate("""async () => {
                  await chrome.cookies.set({url:'https://me.meituan.com/',name:'partition-test',
                    value:'fake',secure:true,partitionKey:{topLevelSite:'https://meituan.com'}});
                }""")
                popup.get_by_role("button", name="读取 Cookie 并核对酒店").click()
                popup.locator("#message").filter(has_text="分区 Cookie").wait_for(state="visible")
                assert imports.job is None
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(target / "pairing-mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                context.close()
                if "--inspect" in sys.argv:
                    print("Temporary UI screenshots: " + str(target), flush=True)
                    input("Press Enter to finish and remove temporary screenshots: ")
            print("Offline project extension flow passed; temporary screenshots are removed with the test fixture.")
        finally:
            imports.cancel()
            server.shutdown()
            thread.join(timeout=3)


if __name__ == "__main__":
    check()
