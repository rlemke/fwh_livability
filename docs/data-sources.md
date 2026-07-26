# Indicator Data Sources

**Internals:** `src/livability/_lib.py` — `fetch_acs()`, `fetch_places()`,
`fetch_nri()`, `fetch_aqi()`, `_fetch_cached()`, `_num()` ·
**Cache:** `src/livability/storage.py` — `cache_root()`

## Overview

Four open, keyless-friendly, county-keyed public sources supply the nine raw
indicators the composite scores. Each has its own fetcher that downloads once,
caches to the storage backend, and parses to a `{geoid: value}` (or, for AQI,
name-keyed) dict. All fetchers accept `force=` to bypass the cache and re-download.

| # | Indicator(s) | Source | Fetcher | Join key |
|---|--------------|--------|---------|----------|
| 1–6 | income, poverty, unemployment, education, rent burden, broadband | Census ACS 5-yr (2023) | `fetch_acs` | GEOID (`state`+`county`) |
| 7 | fair/poor self-rated health | CDC PLACES (`GHLTH`, crude prevalence) | `fetch_places` | GEOID (`locationid`) |
| 8 | natural-hazard risk | FEMA National Risk Index (ArcGIS FeatureServer) | `fetch_nri` | GEOID (`STCOFIPS`) |
| 9 | median AQI (2024) | EPA AirData annual-by-county zip | `fetch_aqi` | **state+county name** (no FIPS) |

## How it works

Each fetcher follows a cache-first pattern (`_fetch_cached(url, name, force)`:
return the cached path if it exists and `force` is false, else `requests.get` and
write to `cache_root()`), then parses:

- **`fetch_acs`** — one ACS `for=county:*` call returning all counties in a single
  JSON array. It reads the header row, indexes columns by name, computes `geoid =
  state + county`, and derives six indicators from the raw table cells (see fields
  below). `CENSUS_API_KEY` is appended to the URL when set (keyless ACS is capped at
  ~500 req/day; this needs one call). Raises if it parses to zero rows.
- **`fetch_places`** — CDC Socrata JSON, filtered server-side to `measureid=GHLTH` +
  `datavaluetypeid=CrdPrv`, `$limit=4000`. Keyed by `locationid` zero-padded to a
  5-digit GEOID.
- **`fetch_nri`** — pages the FEMA ArcGIS FeatureServer `query` endpoint
  (`where=1=1`, `outFields=STCOFIPS,RISK_SCORE`, `returnGeometry=false`,
  `resultRecordCount=2000`, advancing `resultOffset` until a short page). The paged
  attributes are cached as a flat JSON list (`nri_counties.json`); the FeatureServer
  is used **because** the static `hazards.fema.gov` NRI zip sits behind bot
  protection — same data, keyless + paged.
- **`fetch_aqi`** — downloads the EPA AirData `annual_aqi_by_county_<year>.zip`,
  finds the `.csv` inside, and reads `Median AQI` per row via `csv.DictReader`
  (utf-8-sig). Because AirData carries **no FIPS**, the key is
  `(STATE_UPPER, norm_county(County))`.

`_num(x)` is the shared numeric guard: non-numeric → `None`, and **negatives →
`None`** (ACS uses sentinel negatives for suppressed/unavailable estimates).

## Fan-out

**Single-task — no fan-out.** All four sources are national single-shot downloads
(one ACS call, one CDC query, one paged FeatureServer sweep, one EPA zip), so there
is nothing to shard. They run sequentially inside the one `BuildLivabilityMap` task.

## Data & fields

**ACS table cells** (raw), and how `fetch_acs` derives indicators:

- `B19013_001E` → `income` (median household income, used raw).
- `B17001_002E / B17001_001E` → `poverty` = `100·below/total`.
- `B23025_005E / B23025_003E` → `unemployment` = `100·unemployed/labor-force`.
- `B15003_022E..025E / B15003_001E` → `education` = `100·(bachelor+master+prof+
  doctorate)/pop 25+`.
- `B25071_001E` → `rent_burden` (median gross rent as % of household income, raw).
- `B28002_004E / B28002_001E` → `broadband` = `100·broadband/total households`.
- `B01003_001E` → `population` (carried for popup display + the top-county metadata,
  not scored).

**CDC PLACES:** `data_value` for `GHLTH` (crude prevalence, % fair/poor health).
**FEMA NRI:** `RISK_SCORE` (composite hazard risk), keyed by `STCOFIPS`.
**EPA AirData:** `Median AQI` column, keyed by state+county name.

Join keys: ACS/PLACES/NRI join by 5-digit GEOID directly; AQI joins by normalized
name (`norm_county`, `State.upper()`) — see [county-geometry](county-geometry.md)
for how the name join and GEOID normalization land these onto county geometry.

## External libraries / binaries

- **`requests`** (pip) — every fetch (`_fetch_cached`, the NRI paging loop). 600 s
  timeout on cached bulk downloads; 120 s on NRI pages.
- **stdlib** — `json`, `csv`, `io`, `zipfile` (AQI zip), `os` (env for
  `CENSUS_API_KEY`).
- No binary dependencies; no `osmium`/`shapely` in the fetchers (shapely enters at
  render time).

## Facets & workflows

The fetchers are internal to `build_livability_map`; there is no per-source facet.
The single facet is `livability.maps.BuildLivabilityMap` — see
[facets-and-handlers](facets-and-handlers.md). Its `force=true` parameter is
threaded to **all four** fetchers, re-downloading every source.

## Cache / output

Source downloads cache under `cache_root()` (`cache/livability/cache/` on S3, or
`<data_root>/livability-cache/` locally), with fixed filenames:

- `acs_2023_counties.json`
- `places_ghlth.json`
- `nri_counties.json` (the parsed FeatureServer attributes, not the raw pages)
- `aqi_2024.zip`

These are inputs, not published artifacts. `FW_LIVABILITY_CACHE_DIR` overrides the
location. Rendered outputs are separate — see [map-and-output](map-and-output.md).

## Gotchas & notes

- **AQI has no FIPS and partial coverage.** Only monitored counties appear, and the
  join is by name — a name mismatch or an unmonitored county simply drops the
  `air_quality` indicator for that county (missing-aware mean absorbs it). This is
  the biggest coverage caveat; it is disclosed in `_DESC`.
- **ACS negatives are suppression sentinels**, not real values — `_num` maps them to
  `None`, so a suppressed cell doesn't poison a derived ratio.
- **No crime indicator.** There is no complete open national county-level crime
  source, so it is **omitted rather than faked** (stated in `_DESC`/README).
- **FEMA static zip is bot-blocked** — the FeatureServer path is deliberate; if it
  changes, `fetch_nri` raises `"FEMA NRI query produced zero counties"`.
- **`CENSUS_API_KEY` recommended** — one ACS call is under the keyless cap, but set
  the key on hosts that also run other census-hitting domains to avoid contention.
- Each fetcher raises a specific `RuntimeError` on zero rows so a silently empty
  source fails loud rather than producing a blank map.

## Related specs

- [composite-scoring](composite-scoring.md) — how these nine raw values become
  signed percentiles and a mean.
- [county-geometry](county-geometry.md) — the GEOID universe and the AQI name-join.
- [map-and-output](map-and-output.md) — where the joined data is rendered.
