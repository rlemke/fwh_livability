# Composite Livability Score

**Namespace:** `livability.maps` (the score is the payload of `BuildLivabilityMap`) ·
**Internals:** `src/livability/_lib.py` — `INDICATORS`, `MIN_INDICATORS`,
`percentiles()`, `compute_scores()`, `build_livability_map()` ·
**Tests:** `tests/test_livability.py` — `test_percentiles_*`, `test_compute_scores_*`

## Overview

This is the **flagship** feature: turning nine open, county-keyed indicators into a
single "best county to live in" number. The request it answers — *"rank ~3,100 US
counties by how good a place they are to live"* — has no ground truth, so the method
is deliberately transparent: each indicator is **percentile-ranked across all
counties**, direction-signed so 100 = good on every axis, and the livability score
is the **plain mean of the available percentiles** (equal weights). The equal
weighting is a disclosed value judgment, stated in the map's About popup, the FFL
docstring, and the repo README — not a claim of truth.

It sits at the analysis center of the pipeline: `fetch_* (sources) → percentiles →
compute_scores → GeoJSON features + rankings → HTML` (`build_livability_map`).

## How it works

1. **Assemble the indicator table.** `build_livability_map` builds
   `indicator_values: {indicator_key: {geoid: raw_value}}` from the four source
   fetchers (see [data-sources](data-sources.md)). ACS supplies six of the nine
   keys (`income`, `poverty`, `unemployment`, `education`, `rent_burden`,
   `broadband`); `health` = CDC PLACES, `hazard_risk` = FEMA NRI, `air_quality` =
   EPA AQI joined onto GEOIDs.
2. **Percentile-rank each indicator independently** (`percentiles(values,
   direction)`): values are sorted, ties share the mid-rank percentile, and the raw
   percentile `100·i/(n−1)` is **flipped to `100−pct` when `direction < 0`** so that
   for a "lower is better" indicator (poverty, unemployment, rent burden, health,
   hazard risk, AQI) a low raw value maps to a high (good) percentile. Empty input →
   `{}`; a single county → `0.0` or `100.0`.
3. **Average the available percentiles** (`compute_scores`): for each GEOID present
   on *any* indicator, collect its available signed percentiles, and set
   `score = round(mean, 1)` **only if at least `MIN_INDICATORS` (6) are present**;
   otherwise `score = None` (rendered grey, "no score"). Each county also carries
   its per-indicator `pcts` and `raw` values so the popup can show the full,
   auditable breakdown.
4. **Rank.** `build_livability_map` sorts scored counties descending into `ranked`,
   assigns a dense `rank` (1 = best) back onto every feature, and takes the top 50
   for the rankings page. `top_county`/`top_score` become part of the facet return.

Data shape at each stage: `source JSON → {geoid: raw} per indicator → {geoid: pct}
per indicator → {geoid: {score, pcts, raw}} → GeoJSON feature props`.

## Fan-out

**Single-task — no fan-out.** Percentile ranking is intrinsically **global**: a
county's percentile is defined relative to *all other counties*, so the score cannot
be computed per-shard and merged. The whole national table is scored in one process
inside `BuildLivabilityMap`. (Contrast fan-out domains like fwh_osm, which shard by
region because each region is independent.) See [facets-and-handlers](facets-and-handlers.md)
for the single event facet.

## Data & fields

The `INDICATORS` list is the source of truth — a list of `(key, label, direction,
format)` tuples:

| key | label | direction | format |
|-----|-------|-----------|--------|
| `income` | Median household income | +1 (higher better) | money |
| `poverty` | Poverty rate | −1 | pct |
| `unemployment` | Unemployment rate | −1 | pct |
| `education` | Bachelor's degree or higher | +1 | pct |
| `rent_burden` | Median rent as % of income | −1 | pct |
| `broadband` | Broadband subscription | +1 | pct |
| `health` | Fair/poor self-rated health (CDC PLACES) | −1 | pct |
| `hazard_risk` | Natural-hazard risk (FEMA NRI) | −1 | score |
| `air_quality` | Median AQI (EPA) | −1 | score |

`direction` drives the percentile flip; `format` drives popup rendering only
(`$`/`%`/plain). `MIN_INDICATORS = 6` is the availability floor. The score is a pure
function of these — no external weighting file, no learned model.

## External libraries / binaries

- **None beyond stdlib for the scoring math itself** — `percentiles` and
  `compute_scores` use only builtins (`sorted`, arithmetic). The surrounding
  `build_livability_map` uses `shapely` (geometry simplify) and `requests` (fetch),
  covered in [map-and-output](map-and-output.md) and [data-sources](data-sources.md).

## Facets & workflows

The score is not its own facet — it is the computation behind
`livability.maps.BuildLivabilityMap` (event; `Effect(kind="external")`,
`Cost(tier="expensive")`, `Timeout(minutes=30)`), which returns `county_count`,
`scored_count`, `top_county`, `top_score` alongside `html_path`. Full signature and
the `BuildBestCountyMap` workflow wrapper are in
[facets-and-handlers](facets-and-handlers.md).

## Cache / output

The score itself is transient (recomputed each build); it is materialized into
`counties.geojson` (per-feature `score`/`rank`/`pcts`/`raw`), `index.html`, and
`rankings.html` under `cache/livability/output/`. See
[map-and-output](map-and-output.md).

## Gotchas & notes

- **Equal weighting is a value judgment, not truth.** This is stated in the FFL
  docstring, `_DESC`, and the README. Any change to weights changes the ranking and
  must stay disclosed — do not present the composite as objective.
- **Missing-aware mean, not zero-fill.** A county missing an indicator has that
  indicator *dropped from its mean*, it is **not** scored 0. This is why AQI's
  partial coverage doesn't tank monitored-only counties — but it also means two
  counties can be compared on different indicator subsets (both must clear
  `MIN_INDICATORS=6`). `test_compute_scores_equal_weights_and_min_indicators` pins
  the `< 6 → None` behaviour and guards against a mid-value tie lifting a "bad"
  county.
- **Ties share a percentile.** `percentiles` gives tied raw values the mid-rank
  percentile (verified by `test_percentiles_direction_and_ties`), so clusters of
  identical values don't get arbitrarily ordered.
- **Percentile is global** — adding/removing counties (or a source's coverage
  changing) shifts everyone's percentiles. Scores are not comparable across builds
  with different county sets.
- **Grey ≠ bad.** An unscored county (`score=None`) means insufficient data, not a
  low rank; the legend and popup say so.

## Related specs

- [data-sources](data-sources.md) — the nine raw indicators feeding the score.
- [county-geometry](county-geometry.md) — the GEOID universe the percentiles rank
  over, and the name-join that lands AQI onto it.
- [map-and-output](map-and-output.md) — how `score`/`rank`/`pcts`/`raw` become the
  choropleth and the auditable popup.
- [facets-and-handlers](facets-and-handlers.md) — the facet/workflow that runs this.
