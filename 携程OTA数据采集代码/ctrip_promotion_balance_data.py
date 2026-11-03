import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
                str(Path(__file__).resolve().parents[1] / 'OTA采集服务')]
from promotion_balance import main

if __name__ == '__main__':
    raise SystemExit(main('ctrip'))
