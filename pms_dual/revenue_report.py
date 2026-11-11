"""美团 PMS + Rezen 房费明细。列与 rs01_room_revenue_daily 的单系统报表一致。"""
from pms_dual.jobs import main_revenue

if __name__ == "__main__":
    raise SystemExit(main_revenue())
