ALTER TABLE `meituan_ota_goods_price_mapping`
    ADD COLUMN `is_hour_room` TINYINT(1) NOT NULL DEFAULT 0
    COMMENT '是否钟点房：1钟点房，0非钟点房'
    AFTER `is_super_deal`;
