"""美团 PMS + Rezen 经营日报。列与 jy01_hotel_statistics_daily 的单系统报表一致。"""
from pms_dual.jobs import main_daily

if __name__ == "__main__":
    raise SystemExit(main_daily())
