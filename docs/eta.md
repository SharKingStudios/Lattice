# ETA and evaluation

## Map matching

If GTFS-RT provides a known `trip_id`, the collector uses that active feed trip and its GTFS shape. If it only provides a route ID, it chooses a shaped route candidate but marks it lower confidence. Positions are projected to a polyline in a local meter coordinate system. The saved output includes along-shape progress and cross-track distance.

Confidence falls with distance from the shape, missing trip context, and implausible backwards progress relative to the vehicle’s previous same-shape observation. A low-confidence candidate is not silently presented as reliable. Stop sequence/trip context comes before nearest geometry, which protects looping patterns that pass through the same physical area.

## Actual stop events

A single GPS point near a stop is not ground truth. The initial inference rule requires two recent observations associated with the same next stop, both close to the route, plus stopped/unknown-low-speed evidence. It writes a confidence-scored event. This intentionally undercounts at first; its job is to avoid poisoning later model evaluation. Rebuilding events after matcher improvements is supported by the CLI.

## Baseline model

The model’s fallback order is explicit:

1. Enough historical segment samples for the remaining trip, with an uncertainty range.
2. Distance/current-speed geometric estimate.
3. GTFS scheduled timing or frequency estimate.
4. Passio trip-update prediction.
5. Unknown.

Until enough high-confidence stop events have accrued, no claim of a superior local ETA is made. The rider card therefore exposes the useful result without pretending precise certainty; developer data retains source, range, and confidence.

## Evaluation

Each prediction is retained with the time it was observed. The evaluator pairs an eligible prediction to a later high-confidence inferred arrival for the same trip and stop. It reports sample count, MAE, median absolute error, P80/P90 error, and signed bias separately for Passio and local predictions. Its next extension should group these by route, horizon (0–2, 2–5, 5–10, 10+ minutes), service/day class, and time of day before making any performance claim.
