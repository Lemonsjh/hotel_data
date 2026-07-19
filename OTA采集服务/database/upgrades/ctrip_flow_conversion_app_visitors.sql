ALTER TABLE ctrip_ota_flow_conversion_30d
  ADD COLUMN app_visitors BIGINT NULL AFTER snapshot_time,
  ADD COLUMN peer_app_visitors BIGINT NULL AFTER app_visitors;
