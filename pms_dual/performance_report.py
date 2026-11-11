"""美团 PMS + Rezen 经营业绩。列与 jl02_hotel_performance_daily 的单系统报表一致。"""
from pms_dual.jobs import main_performance

if __name__ == "__main__":
    raise SystemExit(main_performance())
