# fwh_livability

Facetwork domain: **"best county to live in"** — an equal-weight livability
composite for ~3,100 US counties, rendered as a national MapLibre choropleth
plus a top-50 rankings page. Every rank is auditable: the popup shows each
indicator's raw value and percentile.

## Feature specifications

Each feature has a spec in [**`docs/`**](docs/README.md) — how it works,
whether/how it **fans out**, the **data & fields** it touches, the **external
libraries** it uses, its **facets & workflows**, and its **cache/output**. Start
with the flagship [**Composite Livability Score**](docs/composite-scoring.md)
(signed percentiles + equal-weight, missing-aware mean); the full index is in
[`docs/README.md`](docs/README.md).

| Area | Specs |
|------|-------|
| **Flagship — scoring** | [composite-scoring](docs/composite-scoring.md) |
| **Data & joins** | [data-sources](docs/data-sources.md) · [county-geometry](docs/county-geometry.md) |
| **Rendering & runtime** | [map-and-output](docs/map-and-output.md) · [facets-and-handlers](docs/facets-and-handlers.md) |

## Method

Each indicator is percentile-ranked across all counties, direction-signed so
100 = good on every axis. The livability score is the **mean of the available
percentiles** (equal weights — a disclosed value judgment, not truth).
Counties with fewer than 6 available indicators are unscored (grey).

| # | Indicator | Source | Direction |
|---|-----------|--------|-----------|
| 1 | Median household income | Census ACS 5-yr (2023) | higher better |
| 2 | Poverty rate | ACS | lower better |
| 3 | Unemployment rate | ACS | lower better |
| 4 | Bachelor's degree or higher | ACS | higher better |
| 5 | Median rent as % of income | ACS | lower better |
| 6 | Broadband subscription | ACS | higher better |
| 7 | Fair/poor self-rated health | CDC PLACES (GHLTH) | lower better |
| 8 | Natural-hazard risk | FEMA National Risk Index (ArcGIS FeatureServer) | lower better |
| 9 | Median AQI | EPA AirData (monitored counties only) | lower better |

Known limits (also in the map's About popup): no crime indicator (no complete
open national county-level source — omitted rather than faked); small-county
ACS estimates carry wide margins of error; AQI joins by state+county name
(AirData has no FIPS), so some counties miss. County geometry is reused from
the census-us cached per-state metrics GeoJSON (same pattern as fwh_h1b).

## Run

```bash
pip install -e .
fw runner start --domain livability -- --log-format text
fw ffl run --primary src/livability/ffl/livability.ffl \
    --workflow livability.workflows.BuildBestCountyMap
```

`CENSUS_API_KEY` recommended (keyless ACS is capped at 500 req/day; this needs
one call). Outputs land at `cache/livability/output/{index,rankings}.html` on
the configured storage backend. Facets: `livability.maps.BuildLivabilityMap`
wrapped by `livability.workflows.BuildBestCountyMap`; `force=true` re-fetches
all four sources.
