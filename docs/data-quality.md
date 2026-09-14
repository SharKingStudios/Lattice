# UGA feed data-quality notes

The implementation deliberately discovers, rather than hard-codes, UGA feed shape IDs, routes, stop counts, trip IDs, and optional GTFS files. The live smoke script captures the current file list and row counts at execution time.

Known operational considerations:

- Static GTFS is treated as mutable. Every new SHA-256 is validated and stored beside older versions; realtime IDs are always interpreted against the current active version.
- `calendar.txt`, `calendar_dates.txt`, `frequencies.txt`, `feed_info.txt`, and `shapes.txt` are optional in GTFS. Missing optional data is recorded as validation information, not treated as a parser crash.
- Transit service time is `America/New_York`; GTFS values after `24:00:00` are parsed as next-day service, not invalid wall-clock times.
- Live vehicle and trip-update entity fields can be absent or inconsistent. Every malformed entity is isolated and logged, and cached last-known data stays available during an outage.
- The first data-collection period has no historical stop events. Passio fallback at this stage is intentional and visible in prediction source metadata.

Run `npm run feed:smoke` and `python -m uga_bus.cli inspect-gtfs` when deploying a new feed version; attach their output here as operational observations rather than assuming this document remains current.
