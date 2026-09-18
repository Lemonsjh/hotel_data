ALTER TABLE `meituan_ota_video_upload_status`
    ADD COLUMN `snapshot_time` DATETIME NULL COMMENT '采集时间' AFTER `status`,
    ADD KEY `idx_meituan_video_upload_snapshot` (`hotel_id`, `snapshot_time`);
