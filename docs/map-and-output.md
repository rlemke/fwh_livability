# Map Rendering & Output

**Internals:** `src/livability/_lib.py` — `build_livability_map()`,
`_render_html()`, `_render_rankings()`, `_attribution()`, `_DESC`, `RAMP` ·
**Storage:** `src/livability/storage.py` — `output_root()`, `open_write()`,
`localize()` · **Tests:** `tests/test_livability.py` —
`test_render_html_smoke`, `test_render_rankings_smoke`

## Overview

The visualization/output layer turns the scored county table into three artifacts:
a **national MapLibre choropleth** (`index.html`), a **top-50 rankings page**
(`rankings.html`), and the underlying **`counties.geojson`**. The design goal is
auditability — every rank is explainable, so each county's popup shows every
indicator's raw value *and* its percentile, and the map carries an "About this data"
modal disclosing the method, coverage, and limits.

## How it works

Inside `build_livability_map`, after scoring:

1. **Build features.** For each assembled county, a GeoJSON `Feature` is emitted
   with `properties` = `GEOID`, `NAME`, `STATE`, `score`, `pop`, `pcts`, `raw`
   (each `raw` value rounded to 1 dp), plus a `rank` back-filled from the sorted
   ranking. Geometry is passed through
   `shape(geom).simplify(0.004, preserve_topology=True)` then `mapping()` to shrink
   the payload while keeping topology.
2. **Render the map** (`_render_html`): a self-contained HTML doc embedding the
   FeatureCollection as `const FC=…` and the indicator metadata as `const INDS=…`.
   A CARTO Voyager raster basemap underlays a `fill` layer coloured by a green
   `RAMP` (0→100) via a MapLibre `interpolate` expression, with `score==null → #ccc`
   (grey). Interactions: click → the explainable popup (a `value | pctile` table
   over `INDS`), a county search box (`fitBounds` to a computed bbox), a "Top 50"
   link, and the "About this data" modal (`_DESC` + per-indicator coverage counts).
3. **Render the rankings** (`_render_rankings`): a plain HTML table of the top 50 —
   rank, county, score, and each county's two strongest and one weakest indicator
   labels (derived from its `pcts`), with a link back to the map.
4. **Attribution** (`_attribution`): a fixed footer crediting the Facetwork workflow
   `livability.workflows.BuildBestCountyMap`, linking the FFL source and repo, with a
   UTC timestamp — embedded in both pages.
5. **Write** all three artifacts to `output_root()` and return a
   `LivabilityMapResult`.

## Fan-out

**Single-task — no fan-out.** One national HTML build; all counties are in a single
FeatureCollection rendered in one pass.

## Data & fields

Per-feature `properties`: `GEOID`, `NAME`, `STATE`, `score` (or `null`), `pop`,
`rank`, `pcts` ({indicator: percentile}), `raw` ({indicator: raw value}). The
browser reads `INDS` = `[[key,label,format]…]` (from `INDICATORS`) to render the
popup rows and format values (`money`→`$`, `pct`→`%`, else plain). `RAMP` and
`NODATA='#cccccc'` define the colour scale. Coverage counts (per-indicator county
totals) are passed into the About modal.

## External libraries / binaries

- **`shapely`** (pip, `>=2.0`) — `shape`, `mapping`, `.simplify(0.004,
  preserve_topology=True)` for geometry payload reduction. The one substantive
  server-side render dependency.
- **stdlib** — `json` (embed FC/INDS), `html.escape` (rankings + attribution),
  `datetime` (attribution timestamp).
- **Browser-side, via CDN (not pip):** `maplibre-gl@4.7.1` (JS + CSS from unpkg) and
  CARTO Voyager raster basemap tiles. These load at view time in the browser; they
  are **not** Python dependencies and are not vendored.

## Facets & workflows

Rendering is the body of `livability.maps.BuildLivabilityMap`; its return
(`html_path`, `county_count`, `scored_count`, `top_county`, `top_score`) is produced
here. See [facets-and-handlers](facets-and-handlers.md) for the facet + workflow
signatures.

## Cache / output

Outputs go to `output_root()` (`cache/livability/output/` on S3, or
`<data_root>/livability-output/` locally; overridable with
`FW_LIVABILITY_OUTPUT_DIR`):

- `index.html` — the choropleth (returned as `html_path`).
- `rankings.html` — top-50 page.
- `counties.geojson` — the scored FeatureCollection (compact separators).

`storage.open_write` stages to a local temp file and finalizes to the backend on
close (object stores don't do partial writes); `localize` pulls remote inputs to a
temp path for reading (used by the AQI zip and geometry reads). On the fleet these
land in shared MinIO under `s3://afl-cache/cache/livability/output/`.

## Gotchas & notes

- **Popups double-parse props.** MapLibre may hand `pcts`/`raw` back as JSON
  *strings*; the popup code handles both string and object (`typeof … ==='string' ?
  JSON.parse : …`). Keep that guard if editing the template.
- **Simplify tolerance is a size/fidelity trade.** `0.004` degrees noticeably
  shrinks `counties.geojson`; tightening it inflates the embedded payload (the whole
  FC ships inside `index.html`).
- **CDN assets require network at view time.** The map is self-contained HTML but
  pulls MapLibre + basemap tiles from unpkg/CARTO in the browser — an offline viewer
  sees no basemap. (Server-side rendering has no such dependency.)
- **`_render_html`/`_render_rankings` are smoke-tested** (`test_render_*_smoke`)
  including a round-trip of the embedded `FC` JSON and the strongest-on label
  resolution — keep those invariants (the `const FC=` / `, INDS=` markers) if
  refactoring the template.
- **Grey legend entry is intentional** — `score==null` counties render `#ccc` with
  an "n/a" swatch; don't colour them at the ramp floor.

## Related specs

- [composite-scoring](composite-scoring.md) — produces `score`/`rank`/`pcts`/`raw`.
- [county-geometry](county-geometry.md) — supplies the geometry that is simplified
  and rendered.
- [facets-and-handlers](facets-and-handlers.md) — the facet whose body this is, and
  the storage backend wiring.
