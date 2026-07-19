ALTER TABLE ctrip_ota_flow_conversion_30d
  ADD COLUMN peer_list_exposure BIGINT NULL AFTER order_submit_count,
  ADD COLUMN peer_detail_exposure BIGINT NULL AFTER peer_list_exposure,
  ADD COLUMN peer_exposure_to_detail_rate_pct DECIMAL(9,4) NULL AFTER peer_detail_exposure,
  ADD COLUMN peer_order_filling_count BIGINT NULL AFTER peer_exposure_to_detail_rate_pct,
  ADD COLUMN peer_order_submit_count BIGINT NULL AFTER peer_order_filling_count;

UPDATE ctrip_ota_flow_conversion_30d AS hotel
JOIN ctrip_ota_flow_conversion_30d AS peer
  ON peer.hotel_id=hotel.hotel_id
 AND peer.platform_scope=hotel.platform_scope
 AND peer.business_date=hotel.business_date
 AND peer.data_scope='peer_average'
SET hotel.peer_list_exposure=peer.list_exposure,
    hotel.peer_detail_exposure=peer.detail_exposure,
    hotel.peer_exposure_to_detail_rate_pct=peer.exposure_to_detail_rate_pct,
    hotel.peer_order_filling_count=peer.order_filling_count,
    hotel.peer_order_submit_count=peer.order_submit_count
WHERE hotel.data_scope='hotel';

DELETE FROM ctrip_ota_flow_conversion_30d WHERE data_scope='peer_average';

ALTER TABLE ctrip_ota_flow_conversion_30d
  DROP INDEX uk_ctrip_flow_conversion_daily,
  DROP COLUMN data_scope,
  ADD UNIQUE KEY uk_ctrip_flow_conversion_daily (
    hotel_id, platform_scope, business_date
  );
