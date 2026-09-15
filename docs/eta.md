# ETA and evaluation

## Map matching

Known GTFS-RT trips are matched to their exact GTFS shape. Route-only updates use a shaped candidate at lower confidence. Positions are projected onto that shape, and saved with along-route progress, cross-track distance, and the next stop. Low-confidence matches are retained for diagnostics but do not become trusted learning data.

## Actual stop events

A single nearby GPS point is not an arrival. The collector needs two recent, low-speed observations for the same next stop and route before writing a confidence-scored stop event. When the next stop changes, the preceding event receives a departure time. Those events remain available for replay and later model rebuilding.

## UGA estimation

UGA estimation is the local, rider-facing ETA model. It learns travel time for each route segment from high-confidence observed departures and arrivals. Weekdays are grouped into local Athens three-hour operating windows; weekends use their own bucket.

Every five minutes, the collector rebuilds segment statistics from retained replayable stop events. A segment is used only after at least three observations, and an ETA uses local timing only when every remaining segment to that stop has sufficient history. This is deliberately conservative: a partial local model must not make a rider-facing prediction worse.

Until that threshold is met, the app uses the live official prediction when it is available. Geometric speed and GTFS schedule remain data-quality fallbacks, not a claim that the local model has learned traffic.

## Evaluation

Each prediction is retained with the time it was observed. The evaluator compares the latest available forecast at fixed 2-, 5-, 10-, and 20-minute lead times with a later high-confidence inferred arrival for the same trip and stop. It reports sample count, MAE, median absolute error, P80/P90 error, and signed bias separately for the official feed and UGA estimation. The diagnostics output is evidence, not a claim of superiority, until enough matching arrivals exist in each time bucket.
