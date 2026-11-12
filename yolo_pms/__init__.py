"""余乐通 E8HotelMis 采集模块。

基础层
  yolo_config.py  连接配置、命令行客户端定位、实例地址解析
  yolo_common.py  只读护栏、查询与解析、业务日闸门、整段替换落库
  yolo_sql.py     源库 SQL 与口径常量
  yolo_probe.py   配置页探测：读 数据库设置.ini、端口与只读账号连通性校验

按 PMS 注册表的 `scripts` 声明分发：
  yolo_daily_report_data.py  → jl01 房型经营日报 + jy01 酒店统计日报
  yolo_room_status_data.py   → pms_room_type_forecast 房型实时经营快照
                               + pms_room_type_hourly_status 小时留档
  yolo_jd01_data.py          → jd01 预订 / 入住明细
"""
