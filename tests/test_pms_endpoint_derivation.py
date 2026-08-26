import importlib.util
import os
import unittest
from pathlib import Path


CONFIG_PATH = Path(__file__).resolve().parents[1] / "正式数据抓取-PMS（别样红）" / "PMS登录" / "scripts" / "pms_config.py"
URL_KEYS = (
    "PMS_LOGIN_BASE_URL",
    "PMS_REPORT_BASE_URL",
    "PMS_SERVICE_API_BASE_URL",
    "PMS_FORECAST_API_BASE_URL",
)


def load_config(values: dict[str, str]):
    previous = {key: os.environ.get(key) for key in URL_KEYS}
    try:
        for key in URL_KEYS:
            os.environ.pop(key, None)
        os.environ.update(values)
        spec = importlib.util.spec_from_file_location("pms_config_test", CONFIG_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class PmsEndpointDerivationTests(unittest.TestCase):
    def test_blank_service_urls_follow_login_host(self):
        config = load_config({"PMS_LOGIN_BASE_URL": "https://yinmo.beyondh.com:8101"})
        self.assertEqual(config.REPORT_BASE_URL, "https://yinmo.beyondh.com:8081")
        self.assertEqual(config.SERVICE_API_BASE_URL, "https://yinmo.beyondh.com:8077")
        self.assertEqual(config.FORECAST_API_BASE_URL, "https://yinmo.beyondh.com:8111")

    def test_explicit_service_url_still_takes_priority(self):
        config = load_config({
            "PMS_LOGIN_BASE_URL": "https://yinmo.beyondh.com:8101",
            "PMS_REPORT_BASE_URL": "https://report.example.test:9000",
        })
        self.assertEqual(config.REPORT_BASE_URL, "https://report.example.test:9000")


if __name__ == "__main__":
    unittest.main()
