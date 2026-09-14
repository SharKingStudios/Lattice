# Data model

Every static GTFS import has a new `gtfs_feed_versions` row. It holds the source URL, HTTP metadata, SHA-256 checksum, feed version/date range when present, table row counts, validation results, fetch time, and an `is_active` pointer. Previous versions are never replaced.

The GTFS tables `agencies`, `routes`, `stops`, `trips`, `stop_times`, `shape_points`, `calendar`, `calendar_dates`, and `frequencies` retain original GTFS IDs and each belong to one feed version. The compound indexes include that feed version so IDs can safely recur after a schedule change.

`realtime_snapshots` records every poll’s timing, status, payload size/checksum, feed timestamp, parse count/error, latency, identical-payload flag, and short-lived raw payload. A malformed entity does not invalidate the snapshot.

`vehicle_observations` is append-only detailed live history. Along with GTFS-RT fields it stores map-match output: chosen trip/shape, progress, cross-track distance, surrounding stops, confidence, and method. `upstream_predictions` is also append-only: each Passio stop-time prediction is a separate observation. `service_alerts` retains normalized translated text, active windows, and informed entities.

`stop_events` contains conservatively inferred actual arrivals/departures and confidence. `segment_statistics` holds downsampled stop-to-stop travel aggregates by route and service/time bucket. `eta_predictions` is reserved for append-only local model predictions, so future evaluator runs can compare what the model said at a particular time with actual arrivals.

Foreign keys preserve the relationship to a snapshot/feed version where possible. SQLite uses WAL and foreign-key enforcement; a later PostgreSQL migration should retain these natural keys and indexes.
