"""Import-only health check; no task execution, database writes or login."""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(root), str(root / "OTA采集服务")]
from settings_validation import require_valid_settings

path = root / "OTA采集服务" / "config" / "settings.json"
require_valid_settings(json.loads(path.read_text(encoding="utf-8-sig")), path)
import runner
import status_routes
import config_schema

print("Program imports and configuration validated; no tasks executed.")
