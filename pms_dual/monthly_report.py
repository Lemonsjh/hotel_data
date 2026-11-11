"""美团 PMS + Rezen 经营月报。列与 jy03_hotel_statistics_month 的单系统报表一致。"""
from pms_dual.jobs import main_monthly

if __name__ == "__main__":
    raise SystemExit(main_monthly())
