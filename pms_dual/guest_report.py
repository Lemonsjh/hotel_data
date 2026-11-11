"""美团 PMS + Rezen 离店客人。列与 jd02_guest_stay_detail 的单系统报表一致。"""
from pms_dual.jobs import main_guests

if __name__ == "__main__":
    raise SystemExit(main_guests())
