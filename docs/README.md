# Livability — Feature Specifications

This directory holds one **spec per feature** of the `fwh_livability` domain — the
equal-weight "best US county to live in" composite. Each document follows a common
shape ([`SPEC_TEMPLATE.md`](SPEC_TEMPLATE.md)) and states, for that feature: how it
works, whether/how it **fans out**, the **data & fields** it touches, the **external
libraries** it relies on, its **facets & workflows**, and its **cache/output**.
Claims are grounded in the FFL `/** … */` docstrings, the handler code, and the
`_lib`/`storage` internals — the source of truth for the facet remains its FFL
docstring; these specs are the feature-level narrative over the code.

**Start here:** [**Composite Livability Score**](composite-scoring.md) — the flagship
feature (signed percentiles + equal-weight, missing-aware mean over nine open
indicators), the method every rank on the map is auditable against.

## Method & scoring

| Spec | What it covers |
|------|----------------|
| [composite-scoring.md](composite-scoring.md) | **Flagship.** Per-indicator percentile ranking (direction-signed so 100 = good), equal-weight missing-aware mean, `MIN_INDICATORS=6` floor, ranking + top-50; why the ranking is single-task (percentiles are global). |

## Data & joins

| Spec | What it covers |
|------|----------------|
| [data-sources.md](data-sources.md) | The four sources feeding the nine indicators — Census ACS (6 indicators, table IDs), CDC PLACES (`GHLTH`), FEMA NRI (ArcGIS FeatureServer, paged `RISK_SCORE`), EPA AirData (median AQI, name-keyed); cache-first fetchers, `_num` suppression guard. |
| [county-geometry.md](county-geometry.md) | Reuse of the census-us cached per-state metrics GeoJSON; **AFF-style `0500000US*` → bare-FIPS** `normalize_geoid`; the AQI state+county **name** join (`norm_county`, `_NAME_SUFFIXES`); `boto3` S3 state discovery. |

## Rendering & runtime

| Spec | What it covers |
|------|----------------|
| [map-and-output.md](map-and-output.md) | GeoJSON → national MapLibre choropleth + explainable per-indicator popup, top-50 rankings page, attribution; `shapely` simplify; `counties.geojson`/`index.html`/`rankings.html` under `cache/livability/output/`. |
| [facets-and-handlers.md](facets-and-handlers.md) | The single event facet `BuildLivabilityMap` + `BuildBestCountyMap` workflow, handler registration/dispatch, `DomainPackage`/entry point, `runner_env` timeouts, storage-backend selection. |
| [ffl-examples.md](ffl-examples.md) | **Usage patterns.** A gallery of complete, compile-checked FFL examples over this domain's facet — minimal workflow, `$`-scoping, call-time mixins, `catch`, `when` coverage guards, cross-domain publish. |

---

*See also the repo [`README.md`](../README.md) (method table, indicator list, run
recipe) and — for the live/queryable interface — the MCP `fw_capabilities` /
`fw_describe_handler` tools, which introspect the deployed `livability.*` facets.*
