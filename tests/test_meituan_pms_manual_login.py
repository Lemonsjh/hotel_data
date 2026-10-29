import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "OTA采集服务")]
import platform_login
from meituan_pms import manual_login


def response():
    value = MagicMock(url="https://pms.meituan.com" + manual_login.ROOM_TYPES_PATH, status=200)
    value.request.method = "GET"
    value.request.all_headers.return_value = {"cookie": "session=private"}
    value.json.return_value = {"code": 10000, "data": [{"roomTypeId": "A", "roomTypeName": "大床房"}]}
    return value


def identity_response():
    value = MagicMock(url=manual_login.HOTEL_INFO_URL, status=200)
    value.request.method = "GET"
    value.request.all_headers.return_value = {"cookie": "session=identity"}
    value.json.return_value = {"code": 10000, "data": {"hotelId": 987654, "orgId": 123456,
                               "orgType": 4, "orgName": " 新登录酒店 "}}
    return value


class MeituanPmsManualLoginTests(unittest.TestCase):
    def test_only_verified_login_apis_can_supply_cookie(self):
        self.assertEqual(manual_login.verified_login(response()), ("session=private", None))
        self.assertEqual(manual_login.verified_login(identity_response()),
                         ("session=identity", {"hotel_name": "新登录酒店", "org_id": "987654"}))
        for field, value in (("url", "https://pms.meituan.com/login"), ("status", 302),
                             ("url", "https://other.example" + manual_login.ROOM_TYPES_PATH)):
            reply = response()
            setattr(reply, field, value)
            self.assertEqual(manual_login.verified_login(reply), ("", None))
        reply = response()
        reply.json.return_value["code"] = 401
        with self.assertRaises(ValueError):
            manual_login.verified_login(reply)
        for factory in (response, identity_response):
            for cookie in ("", "session=bad\r\nheader=value"):
                reply = factory()
                reply.request.all_headers.return_value = {"cookie": cookie}
                self.assertEqual(manual_login.verified_login(reply), ("", None))
            reply = factory()
            reply.request.method = "POST"
            self.assertEqual(manual_login.verified_login(reply), ("", None))

    def test_save_is_scoped_to_current_hotel_and_separate_cookie(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            settings = {"meituan": {"me_cookie": "ota"}, "pms": {"password": "password"},
                        "meituan_pms": {"hotel_name": "旧酒店", "org_id": "111", "cookie": "old"}}
            runner = SimpleNamespace(CONFIG_PATH=Path(directory) / "config/settings.json",
                                     load_settings=lambda: settings, active_pms_provider=lambda _: "meituan_pms",
                                     internal_hotel_id=lambda _: "hotel", save_json=MagicMock())
            api = SimpleNamespace(runner=runner)
            identity = {"hotel_name": "新登录酒店", "org_id": "987654"}
            manual_login.save_verified_cookie(api, "session=private", "hotel", identity)
            self.assertEqual(settings["meituan_pms"]["cookie"], "session=private")
            self.assertEqual(settings["meituan_pms"]["hotel_name"], "新登录酒店")
            self.assertEqual(settings["meituan_pms"]["org_id"], "987654")
            self.assertEqual(settings["meituan"]["me_cookie"], "ota")
            with self.assertRaises(RuntimeError):
                manual_login.save_verified_cookie(api, "other", "changed-hotel")
            self.assertEqual(runner.save_json.call_count, 1)

    def test_current_hotel_query_uses_verified_cookie_once_and_maps_hotel_id(self):
        context = MagicMock()
        context.request.get.return_value = identity_response()
        identity = manual_login.fetch_hotel_identity(context, "verified-cookie")
        self.assertEqual(identity, {"hotel_name": "新登录酒店", "org_id": "987654"})
        context.request.get.assert_called_once_with(manual_login.HOTEL_INFO_URL, headers={"Cookie": "verified-cookie"},
                                                   timeout=15000, max_redirects=0)

    def test_bad_hotel_response_never_uses_organization_id_as_fallback(self):
        for change in (lambda r: setattr(r, "status", 302),
                       lambda r: setattr(r, "url", "https://other.example/info"),
                       lambda r: r.json.return_value.update(code=401),
                       lambda r: r.json.return_value["data"].pop("hotelId"),
                       lambda r: r.json.return_value["data"].update(hotelId=True),
                       lambda r: r.json.return_value["data"].update(orgType=1),
                       lambda r: r.json.return_value["data"].update(orgName=" ")):
            context = MagicMock()
            reply = identity_response()
            change(reply)
            context.request.get.return_value = reply
            with self.assertRaises(ValueError):
                manual_login.fetch_hotel_identity(context, "verified-cookie")
            context.request.get.assert_called_once()
            if reply.status == 200 and reply.url == manual_login.HOTEL_INFO_URL:
                with self.assertRaises(ValueError):
                    manual_login.verified_login(reply)
            else:
                self.assertEqual(manual_login.verified_login(reply), ("", None))

    def test_missing_identity_retains_existing_params_and_invalid_identity_does_not_save(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            settings = {"meituan_pms": {"hotel_name": "旧酒店", "org_id": "111"}}
            runner = SimpleNamespace(CONFIG_PATH=Path(directory) / "settings.json", load_settings=lambda: settings,
                                     active_pms_provider=lambda _: "meituan_pms", internal_hotel_id=lambda _: "hotel",
                                     save_json=MagicMock())
            api = SimpleNamespace(runner=runner)
            manual_login.save_verified_cookie(api, "updated-cookie", "hotel", None)
            self.assertEqual(settings["meituan_pms"], {"hotel_name": "旧酒店", "org_id": "111", "cookie": "updated-cookie"})
            for identity in ({"hotel_name": "", "org_id": "222"}, {"hotel_name": "新酒店", "org_id": "bad"}):
                with self.assertRaises(ValueError):
                    manual_login.save_verified_cookie(api, "bad-cookie", "hotel", identity)
            runner.save_json.assert_called_once()
            self.assertEqual(settings["meituan_pms"]["cookie"], "updated-cookie")
            runner.active_pms_provider = lambda _: "byh"
            with self.assertRaises(RuntimeError):
                manual_login.save_verified_cookie(api, "bad-cookie", "hotel")
            runner.save_json.assert_called_once()

    def test_manual_flow_uses_passive_observation_and_visible_separate_profile(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            page, context = MagicMock(), MagicMock()
            context.pages = [page]
            context.request.get.return_value = identity_response()
            launcher = MagicMock(return_value=context)
            playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launcher))
            api = SimpleNamespace(
                runner=SimpleNamespace(load_settings=lambda: {}, internal_hotel_id=lambda _: "hotel"),
                stop_path=lambda _: Path(directory) / "stop", profile_path=lambda _: Path(directory) / "profile",
                sync_playwright=lambda: nullcontext(playwright), initial_login_page=lambda *_: page,
                write_status=MagicMock(), read_status=lambda _: {}, now_text=lambda: "now",
                show_success=MagicMock(), stop_profile_edge_processes=MagicMock(),
                LoginCancelled=platform_login.LoginCancelled, PLATFORMS=platform_login.PLATFORMS,
            )
            def navigate(*_):
                context.add_init_script.assert_called_once_with(
                    "Object.defineProperty(Navigator.prototype, 'webdriver', {get: () => false});")
                context.on.call_args.args[1](response())

            api.navigate = navigate
            for switch_account in (False, True):
                context.request.get.reset_mock()
                with patch.object(manual_login, "save_verified_cookie") as save:
                    self.assertEqual(manual_login.run(switch_account, api=api), 0)
                save.assert_called_once_with(api, "session=private", "hotel", {"hotel_name": "新登录酒店", "org_id": "987654"})
                context.request.get.assert_called_once()
                self.assertIn("酒店名称和报表机构 ID 已更新", api.write_status.call_args_list[-1].args[2])
                context.add_init_script.reset_mock()
            self.assertFalse(launcher.call_args.kwargs["headless"])
            context.clear_cookies.assert_called_once()

            context.request.get.side_effect = RuntimeError("secret should not be logged")
            with patch.object(manual_login, "save_verified_cookie") as save:
                self.assertEqual(manual_login.run(False, api=api), 0)
            save.assert_called_once_with(api, "session=private", "hotel", None)
            message = api.write_status.call_args_list[-1].args[2]
            self.assertIn("当前酒店识别失败", message)
            self.assertNotIn("secret", message)

            def login_home(*_):
                observe = context.on.call_args.args[1]
                invalid = identity_response()
                invalid.json.return_value["code"] = 401
                observe(invalid)
                observe(identity_response())
                if switch_account:
                    observe(response())  # 后续房型响应不能拆散已验证的 Cookie 与酒店信息。

            api.navigate = login_home
            for switch_account in (False, True):
                context.request.get.reset_mock()
                with patch.object(manual_login, "save_verified_cookie") as save:
                    self.assertEqual(manual_login.run(switch_account, api=api), 0)
                save.assert_called_once_with(api, "session=identity", "hotel",
                                             {"hotel_name": "新登录酒店", "org_id": "987654"})
                context.request.get.assert_not_called()
                waiting = [call.args[2] for call in api.write_status.call_args_list if call.args[1] == "waiting"]
                self.assertTrue(all("预测页" not in message for message in waiting))

    def test_dispatch_and_profile_are_independent(self):
        with patch.object(manual_login, "run", return_value=0) as run:
            self.assertEqual(platform_login.run("meituan_pms", True), 0)
        self.assertTrue(run.call_args.args[0])
        self.assertNotEqual(platform_login.profile_path("meituan_pms"), platform_login.profile_path("meituan"))
