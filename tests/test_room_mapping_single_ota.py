"""单个OTA平台也能保存房型映射。"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "OTA采集服务"))
from html.parser import HTMLParser
from unittest.mock import patch

from flask import Flask

import room_mapping_store as store
import room_mappings as routes


class FormFields(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.fields = {}
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in ("input", "select") and "name" in values:
            self.fields[values["name"]] = values


class RoomMappingTests(unittest.TestCase):
    def setUp(self):
        self.data = dict(hotel_id="TEST", pms_hotel_name="测试酒店", room_type_id="TEST01",
                         room_type_name="", pms_room_type_name="双床房", hotel_name="",
                         meituan_room_type_name="", ctrip_hotel_name="携程酒店", ctrip_room_type_name="携程双床房")

    def test_either_ota_or_both_are_allowed(self):
        self.assertIsNone(store.validate(self.data))
        both = {**self.data, "hotel_name": "美团酒店", "meituan_room_type_name": "美团双床房"}
        self.assertIsNone(store.validate(both))
        self.assertIsNone(store.validate({**both, "ctrip_hotel_name": "", "ctrip_room_type_name": ""}))

    def test_selected_platform_requires_its_hotel_name(self):
        self.assertIn("携程酒店名称", store.validate({**self.data, "ctrip_hotel_name": ""}))
        self.assertIn("美团酒店名称", store.validate({**self.data, "meituan_room_type_name": "美团双床房"}))
        self.assertIn("至少", store.validate({**self.data, "ctrip_room_type_name": ""}))

    def test_form_does_not_require_unused_platform(self):
        fields = FormFields(routes._render_form(self.data, ["双床房"], [], ["携程双床房"], False)).fields
        self.assertIn("required", fields["pms_room_type_name"])
        for name in ("hotel_name", "ctrip_hotel_name", "meituan_room_type_name", "ctrip_room_type_name"):
            self.assertNotIn("required", fields[name])

    def test_ctrip_only_submission_reaches_save(self):
        app = Flask(__name__)
        routes.register(app, lambda *args: "")
        with patch.object(routes.runner, "load_settings", return_value={}), \
             patch.object(store, "save_group", return_value={}) as save, \
             patch.object(routes.room_type_enrichment, "enrich_mapping_change", return_value={}):
            response = app.test_client().post("/room-mappings/save", data=self.data)
        self.assertEqual(response.status_code, 302)
        self.assertIn("notice=", response.location)
        saved = save.call_args.args[1]
        self.assertEqual(saved["meituan_room_type_name"], "")
        self.assertEqual(saved["ctrip_room_type_name"], "携程双床房")


if __name__ == "__main__":
    unittest.main()
