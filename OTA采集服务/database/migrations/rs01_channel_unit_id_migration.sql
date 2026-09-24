ALTER TABLE `rs01_room_revenue_daily`
  ADD COLUMN `channel_unit_id` varchar(100) DEFAULT NULL COMMENT 'OTA渠道商品/售卖单元ID，PMS未提供时为空' AFTER `order_id`,
  ADD KEY `idx_channel_unit_id` (`hotel_id`, `channel_unit_id`);
