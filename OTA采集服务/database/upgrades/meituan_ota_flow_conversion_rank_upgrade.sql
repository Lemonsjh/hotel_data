ALTER TABLE meituan_ota_flow_conversion_30d
    ADD COLUMN exposure_peer_rank VARCHAR(32) NULL,
    ADD COLUMN browse_peer_rank VARCHAR(32) NULL,
    ADD COLUMN pay_order_peer_rank VARCHAR(32) NULL,
    ADD COLUMN exposure_to_browse_peer_rank VARCHAR(32) NULL,
    ADD COLUMN browse_to_pay_peer_rank VARCHAR(32) NULL;
