ALTER TABLE `meituan_ota_promotion_status`
    ADD COLUMN `snapshot_time` DATETIME NULL AFTER `status`;

ALTER TABLE `meituan_ota_promotion_status`
    ADD KEY `idx_meituan_promotion_status_snapshot` (`hotel_id`, `snapshot_time`);
