ALTER TABLE `rs01_room_revenue_daily`
  ADD COLUMN `source_payment_id` varchar(100) NOT NULL DEFAULT '' COMMENT '来源流水 ID' AFTER `channel_unit_id`,
  DROP INDEX `uk_room_fee_daily`,
  ADD UNIQUE KEY `uk_room_fee_daily` (`hotel_id`, `order_id`, `business_date`, `room_no`, `charge_subject`, `source_payment_id`);
