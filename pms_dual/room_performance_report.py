"""美团 PMS + Rezen 房型业绩。列与 jl01_room_type_performance_daily 的单系统报表一致。"""
from pms_dual.jobs import main_room_performance

if __name__ == "__main__":
    raise SystemExit(main_room_performance())
