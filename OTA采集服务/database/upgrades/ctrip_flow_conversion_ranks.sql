ALTER TABLE ctrip_ota_flow_conversion_30d
  ADD COLUMN list_exposure_peer_rank INT NULL AFTER peer_order_to_submit_rate_pct,
  ADD COLUMN detail_exposure_peer_rank INT NULL AFTER list_exposure_peer_rank,
  ADD COLUMN order_filling_peer_rank INT NULL AFTER detail_exposure_peer_rank,
  ADD COLUMN exposure_to_detail_rate_peer_rank INT NULL AFTER order_filling_peer_rank,
  ADD COLUMN detail_to_order_rate_peer_rank INT NULL AFTER exposure_to_detail_rate_peer_rank;
