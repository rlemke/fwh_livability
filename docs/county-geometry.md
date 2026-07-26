# County Geometry & GEOID Joins

**Internals:** `src/livability/_lib.py` — `assemble_counties()`,
`normalize_geoid()`, `norm_county()`, `_list_census_states()`,
`_census_metrics_path()`, `CENSUS_METRICS_PREFIX`, `_NAME_SUFFIXES` ·
**Tests:** `tests/test_livability.py` — `test_normalize_geoid`,
`test_norm_county_strips_suffixes`

## Overview

The livability map needs one polygon per county to colour, but this domain **does
not fetch or store county geometry of its own**. It reuses the **census-us cached
per-state metrics GeoJSON** already sitting on the shared storage backend (the same
reuse pattern as fwh_h1b), and its job is the *joins*: mapping the census GeoID
format onto bare FIPS, and mapping AQI's name-only rows onto that same universe.
Getting these joins right is the load-bearing correctness concern of the whole
domain (silent join failure → a blank or half-grey map).

## How it works

1. **Discover states.** `_list_census_states()` finds every state directory under
   `cache/census-us/output/metrics/<STATE>/metrics.geojson`. On a remote
   (`s3://…`) data root it uses `boto3` to paginate the bucket; on a local root it
   lists the directory. Either way it returns the states that actually have a
   `metrics.geojson`.
2. **Assemble counties.** `assemble_counties()` reads each state's
   `metrics.geojson`, and for every feature keeps
   `{geoid, name, state, geometry}` — the county `name` is the part of the metrics
   `NAME` ("Clay County, Alabama") before the comma; `state` is the directory name;
   `geoid` is normalized.
3. **Normalize the GEOID.** The census-us metrics carry **AFF-style**
   `0500000US01027`; `normalize_geoid` splits on `US` and keeps the trailing bare
   FIPS `01027`, which is what ACS/PLACES/NRI produce. This is the exact gotcha
   flagged in the domain memory ("bare-FIPS joins silently empty").
4. **Land AQI by name.** Because EPA AirData has no FIPS, `build_livability_map`
   builds an `aqi_by_geoid` by keying each county as
   `(state.upper(), norm_county(name))` and looking it up in the AQI dict.
   `norm_county` upper-cases, strips a suffix from `_NAME_SUFFIXES`
   (" COUNTY", " PARISH", " BOROUGH", " CENSUS AREA", " MUNICIPALITY",
   " CITY AND BOROUGH", " PLANNING REGION"), and removes `.`/`'` — so
   "St. Mary's County" → "ST MARYS" matches on both sides.

## Fan-out

**Single-task — no fan-out.** Geometry assembly is a bounded read of ~50 state
GeoJSONs in the one build process. (The states are listed and read sequentially;
there is no per-state fleet task.)

## Data & fields

- **Input:** `cache/census-us/output/metrics/<STATE>/metrics.geojson`
  (`CENSUS_METRICS_PREFIX = "cache/census-us/output/metrics"`) — a
  FeatureCollection whose feature `properties` carry `GEOID` (AFF-style) and `NAME`
  ("<County> County, <State>").
- **Kept per county:** `geoid` (bare FIPS), `name` (county part only), `state`
  (directory), `geometry`.
- **Join keys produced:** bare 5-digit `GEOID` (for ACS/PLACES/NRI) and
  `(STATE_UPPER, norm_county(name))` (for AQI).

## External libraries / binaries

- **`boto3`** (pip, imported lazily inside `_list_census_states`) — only on a remote
  data root, to list the census-us metrics prefix in the MinIO/S3 bucket using
  `FW_S3_ENDPOINT` / `FW_S3_ACCESS_KEY` / `FW_S3_SECRET_KEY`.
- **stdlib** `os`/`json` for local-path discovery and reading.
- Geometry is *read* here; `shapely` simplification happens at render
  (see [map-and-output](map-and-output.md)).

## Facets & workflows

No facet of its own — `assemble_counties()` is called first inside
`build_livability_map`, which raises a clear error ("no county geometry in the
census-us cache … run a census map first") if the cache is empty. See
[facets-and-handlers](facets-and-handlers.md).

## Cache / output

- **Reads** the census-us cache (`cache/census-us/output/metrics/<STATE>/…`) — a
  **dependency on another domain's output**, not this domain's cache.
- Produces no cache of its own; the assembled + scored geometry is written as
  `counties.geojson` at output time (see [map-and-output](map-and-output.md)).

## Gotchas & notes

- **AFF-style GEOID is the #1 trap.** census-us metrics GEOIDs are
  `0500000US<FIPS>`; every other source is bare FIPS. Skipping `normalize_geoid`
  makes *every* join silently empty and the map comes back all-grey. Pinned by
  `test_normalize_geoid`.
- **Hard dependency on census-us having run.** If no state has a `metrics.geojson`
  in the cache, there is no geometry and the build fails fast — a census map must be
  built first on the same storage backend.
- **AQI name-join is inherently lossy** — spelling/suffix variants that
  `norm_county` doesn't cover (independent cities, renamed Connecticut planning
  regions, etc.) simply miss and drop the AQI indicator for that county. Extend
  `_NAME_SUFFIXES` / `norm_county` if a known county is missing air quality.
- **State is the directory name**, taken verbatim from the census-us layout; the AQI
  `State` column must upper-case-match it.

## Related specs

- [data-sources](data-sources.md) — produces the ACS/PLACES/NRI GEOID dicts and the
  name-keyed AQI dict that join here.
- [composite-scoring](composite-scoring.md) — ranks over exactly the GEOID universe
  assembled here.
- [map-and-output](map-and-output.md) — simplifies and renders the assembled
  geometry.
