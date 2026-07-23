TEXT_GROUPS = [
    (
        "基础配置",
        [
            ("service.interval_minutes", "定时间隔（分钟）"),
            ("price_scheduler.interval_minutes", "调价检查间隔（分钟）"),
        ],
    ),
    (
        "数据库",
        [
            ("mysql.host", "MySQL host"),
            ("mysql.port", "MySQL port"),
            ("mysql.user", "MySQL user"),
            ("mysql.database", "MySQL database"),
        ],
    ),
    (
        "酒店与平台账号",
        [
            ("hotel.hotel_id", "酒店 ID"),
            ("meituan.hotel_name", "美团酒店名"),
            ("ctrip.hotel_name", "携程酒店名"),
            ("pms.hotel_name", "PMS 酒店名"),
            ("pms.username", "PMS 账号"),
        ],
    ),
]

ADVANCED_TEXT_GROUPS = [
    (
        "基础运行",
        [
            ("python_path", "Python 路径"),
            ("paths.base_dir", "项目根目录"),
            ("paths.output_dir", "数据输出目录"),
            ("paths.meituan_code_dir", "美团采集代码目录"),
            ("paths.ctrip_code_dir", "携程采集代码目录"),
            ("service.timeout_seconds", "单任务超时（秒）"),
            ("price_scheduler.startup_delay_seconds", "调价启动延迟（秒）"),
            ("price_scheduler.max_tasks_per_run", "单次最多调价任务"),
            ("data_retention.pms_hourly_days", "PMS 小时房态保留天数"),
            ("data_retention.pms_daily_days", "PMS 日数据保留天数"),
            ("data_retention.jl02_daily_days", "JL02 同比数据保留天数"),
            ("data_retention.pms_monthly_months", "PMS 月数据保留月数"),
            ("data_retention.price_task_days", "已完成调价任务保留天数"),
        ],
    ),
    (
        "美团高级参数",
        [
            ("meituan.poi_id", "美团 poiId"),
            ("meituan.partner_id", "美团 partnerId"),
            ("meituan.biz_account_id", "美团 bizAccountId"),
            ("meituan.price_status_payload_file", "美团调价 payload 文件"),
        ],
    ),
    (
        "携程高级参数",
        [
            ("ctrip.hotel_id", "携程酒店 ID"),
        ],
    ),
    (
        "PMS",
        [
            ("pms.code_dir", "PMS 代码目录"),
            ("pms.entry_script", "PMS 入口脚本"),
            ("pms.timeout_seconds", "PMS 超时（秒）"),
            ("pms.login_base_url", "PMS 登录地址"),
            ("pms.report_base_url", "PMS 报表地址"),
            ("pms.service_api_base_url", "PMS 业务接口地址"),
            ("pms.forecast_api_base_url", "PMS 房态接口地址"),
            ("pms.navigation_timeout_ms", "页面超时（毫秒）"),
            ("pms.action_timeout_ms", "操作超时（毫秒）"),
            ("pms.api_timeout_seconds", "接口超时（秒）"),
        ],
    ),
]

SECRET_GROUPS = [
    (
        "平台 Cookie",
        [
            ("meituan.eb_cookie", "美团 EB Cookie（经营/热词/部分后台接口）"),
            ("meituan.me_cookie", "美团 ME Cookie（评价评分/产品/调价页）"),
            ("ctrip.cookie", "携程 Cookie"),
        ],
    ),
    (
        "数据库",
        [
            ("mysql.password", "MySQL password"),
        ],
    ),
    (
        "PMS 登录",
        [
            ("pms.password", "PMS 密码"),
        ],
    ),
]

ADVANCED_SECRET_GROUPS = [
    (
        "美团",
        [
            ("meituan.review_contrast_url", "美团评价 contrast URL"),
            ("meituan.dianping_review_contrast_url", "大众点评评分 contrast URL"),
            ("meituan.review_ranking_url", "美团评价 ranking URL"),
            ("meituan.review_detail_url", "美团评价明细完整签名 URL"),
            ("meituan.dianping_review_detail_url", "大众点评评价明细完整签名 URL"),
            ("meituan.goods_query_url", "美团商品列表完整签名 URL"),
            ("meituan.calc_price_url", "美团计价完整签名 URL"),
            ("meituan.price_status_url", "美团调价 URL"),
            ("meituan.promotion_performance_url", "美团近30天推广效果 URL"),
        ],
    ),
    (
        "携程",
        [
            ("ctrip.rating_url", "携程评价 URL"),
            ("ctrip.comment_list_url", "携程评价明细 URL"),
            ("ctrip.promotion_url", "携程活动 URL"),
            ("ctrip.goods_query_url", "携程商品 URL"),
        ],
    ),
]

CONFIG_SECTIONS = [
    {
        "key": "system",
        "title": "酒店与数据库",
        "hint": "全局标识、采集频率和数据落库位置。",
        "fields": [
            ("hotel.hotel_id", "酒店 ID", False, False),
            ("service.interval_minutes", "采集间隔（分钟）", False, False),
            ("price_scheduler.interval_minutes", "调价检查间隔（分钟）", False, False),
            ("mysql.host", "MySQL 地址", False, False),
            ("mysql.port", "MySQL 端口", False, False),
            ("mysql.user", "MySQL 账号", False, False),
            ("mysql.password", "MySQL 密码", True, False),
            ("mysql.database", "MySQL 数据库", False, False),
            ("python_path", "Python 路径", False, True),
            ("paths.base_dir", "项目根目录", False, True),
            ("paths.output_dir", "数据输出目录", False, True),
            ("paths.meituan_code_dir", "美团代码目录", False, True),
            ("paths.ctrip_code_dir", "携程代码目录", False, True),
            ("service.timeout_seconds", "单任务超时（秒）", False, True),
            ("price_scheduler.startup_delay_seconds", "调价启动延迟（秒）", False, True),
            ("price_scheduler.max_tasks_per_run", "单次最多调价任务", False, True),
            ("data_retention.pms_hourly_days", "PMS 小时房态保留天数", False, True),
            ("data_retention.pms_daily_days", "PMS 日数据保留天数", False, True),
            ("data_retention.jl02_daily_days", "JL02 同比数据保留天数", False, True),
            ("data_retention.pms_monthly_months", "PMS 月数据保留月数", False, True),
            ("data_retention.price_task_days", "已完成调价任务保留天数", False, True),
        ],
    },
    {
        "key": "meituan",
        "title": "美团",
        "hint": "酒店身份、Cookie 和签名接口集中维护。",
        "fields": [
            ("meituan.hotel_name", "酒店名称", False, False),
            ("meituan.eb_cookie", "EB Cookie（经营数据、周边事件、评价排行）", True, True),
            ("meituan.me_cookie", "ME Cookie（评价明细、活动、调价商品与执行）", True, True),
            ("meituan.poi_id", "poiId", False, True),
            ("meituan.partner_id", "partnerId", False, True),
            ("meituan.biz_account_id", "bizAccountId", False, True),
            ("meituan.review_contrast_url", "评价概览签名 URL", True, True),
            ("meituan.dianping_review_contrast_url", "大众点评评分签名 URL", True, True),
            ("meituan.review_ranking_url", "评价排行 URL", True, True),
            ("meituan.review_detail_url", "评价明细签名 URL", True, True),
            ("meituan.dianping_review_detail_url", "大众点评评价明细签名 URL", True, True),
            ("meituan.goods_query_url", "商品列表签名 URL", True, True),
            ("meituan.calc_price_url", "计价签名 URL", True, True),
            ("meituan.price_status_url", "货价状态签名 URL", True, True),
            ("meituan.promotion_performance_url", "近30天推广效果签名 URL", True, True),
            ("meituan.price_status_payload_file", "货价 payload 文件", False, True),
        ],
    },
    {
        "key": "ctrip",
        "title": "携程",
        "hint": "携程酒店独立标识、登录 Cookie 和采集接口集中维护。",
        "fields": [
            ("ctrip.hotel_name", "酒店名称", False, False),
            ("ctrip.internal_hotel_id", "内部酒店 ID（仅携程）", False, False),
            ("ctrip.cookie", "登录 Cookie（经营数据、评价、活动、调价商品与执行）", True, True),
            ("ctrip.hotel_id", "携程酒店 ID", False, True),
            ("ctrip.rating_url", "评价概览 URL", True, True),
            ("ctrip.comment_list_url", "评价明细 URL", True, True),
            ("ctrip.promotion_url", "活动 URL", True, True),
            ("ctrip.goods_query_url", "商品 URL", True, True),
        ],
    },
    {
        "key": "pms",
        "title": "PMS（别样红）",
        "hint": "登录账号、密码和采集入口放在同一处。",
        "fields": [
            ("pms.hotel_name", "酒店名称", False, False),
            ("pms.username", "登录账号", False, False),
            ("pms.password", "登录密码", True, False),
            ("pms.code_dir", "代码目录", False, True),
            ("pms.entry_script", "入口脚本", False, True),
            ("pms.timeout_seconds", "超时（秒）", False, True),
            ("pms.login_base_url", "登录地址", False, True),
            ("pms.report_base_url", "报表地址", False, True),
            ("pms.service_api_base_url", "业务接口地址", False, True),
            ("pms.forecast_api_base_url", "房态接口地址", False, True),
            ("pms.navigation_timeout_ms", "页面超时（毫秒）", False, True),
            ("pms.action_timeout_ms", "操作超时（毫秒）", False, True),
            ("pms.api_timeout_seconds", "接口超时（秒）", False, True),
        ],
    },
]

NUMBER_FIELDS = {
    "service.interval_minutes",
    "service.timeout_seconds",
    "price_scheduler.interval_minutes",
    "price_scheduler.startup_delay_seconds",
    "price_scheduler.max_tasks_per_run",
    "data_retention.pms_hourly_days",
    "data_retention.pms_daily_days",
    "data_retention.jl02_daily_days",
    "data_retention.pms_monthly_months",
    "data_retention.price_task_days",
    "mysql.port",
    "pms.timeout_seconds",
    "pms.navigation_timeout_ms",
    "pms.action_timeout_ms",
    "pms.api_timeout_seconds",
}

SHORT_SECRET_FIELDS = {"mysql.password", "pms.password"}
