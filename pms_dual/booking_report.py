"""美团 PMS + Rezen 预订明细。列与 jd01_booking_detail 的单系统报表一致。"""
from pms_dual.jobs import main_bookings

if __name__ == "__main__":
    raise SystemExit(main_bookings())
