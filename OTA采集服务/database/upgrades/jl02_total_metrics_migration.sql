-- JL02 仅保存总营业指标，删除不再使用的房型字段。
ALTER TABLE `jl02_hotel_performance_daily`
    DROP INDEX `uk_jl02_hotel_date_metric`,
    DROP INDEX `idx_jl02_room_type_id`,
    DROP COLUMN `room_type_name`,
    DROP COLUMN `room_type_id`,
    ADD UNIQUE KEY `uk_jl02_hotel_date_metric` (
        `hotel_id`, `business_date`, `category`, `metric_name`
    );
