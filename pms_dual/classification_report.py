"""美团 PMS + Rezen 房型分类。列与 jl11_room_type_classification 的单系统报表一致。"""
from pms_dual.jobs import main_classification

if __name__ == "__main__":
    raise SystemExit(main_classification())
