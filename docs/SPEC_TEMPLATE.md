<!-- SPEC TEMPLATE — every docs/<feature>.md follows this shape so the set reads
consistently. Delete this comment in real specs. Keep sections in this order;
omit a section only if it genuinely does not apply (say so in one line rather
than dropping the heading silently). Ground every claim in the actual FFL
docstrings / handler code / _lib internals — do not invent behaviour. -->

# <Feature Name>

**Namespace(s):** `livability.<ns>` · **FFL:** `src/livability/ffl/livability.ffl` ·
**Handlers:** `src/livability/handlers/*.py` · **Internals:** `src/livability/_lib.py`, `src/livability/storage.py`

## Overview
One or two paragraphs: what this feature is for, the request it answers, and where
it sits in the pipeline (fetch sources → normalize → score → render, etc.).

## How it works
The algorithm / data flow, step by step. Name the concrete functions and the shape
of the data at each stage (JSON row → indicator dict → percentile → GeoJSON feature
→ HTML). If a value judgment or a lossy join is involved, say so.

## Fan-out
Does it fan out across the fleet? If yes: what is the fan-out unit and which facet
drives it. If it is single-task, say "single-task — no fan-out" and why (e.g. one
national build, all counties in one pass, small input).

## Data & fields
The concrete indicators / sources / schema fields it touches — be specific
(ACS table IDs like `B19013_001E`, CDC PLACES `measureid=GHLTH`, FEMA NRI
`RISK_SCORE`, EPA `Median AQI`, the `INDICATORS` tuple, join keys). Name the join
mechanism (GEOID exact match, state+county name normalization). If the feature does
no data selection, say so.

## External libraries / binaries
Every non-stdlib dependency this feature relies on and what for — e.g. `requests`
(HTTP fetch), `shapely` (geometry simplify/mapping), `boto3` (S3 listing),
`maplibre-gl` (browser-side, CDN). Distinguish a **binary** dependency from a
**pip** one, and note browser-CDN assets separately.

## Facets & workflows
The key event facets and workflows, with signatures and a one-line purpose taken
from the FFL docstrings. Mark event facets (need a handler) vs pure facets, and
note `Effect`/`Cost`/`Timeout` mixins where present.

## Cache / output
The cache namespace under the storage backend (`cache/livability/cache/` +
`cache/livability/output/`), the source-cache filenames, and the output
artifact(s) and format (GeoJSON / HTML map / rankings HTML). Note whether outputs
go to local disk or MinIO/S3.

## Gotchas & notes
Known pitfalls, coverage gaps, sensitivity caveats, or non-obvious constraints
(worth capturing anything a future maintainer would trip on).

## Related specs
Links to the specs this feature composes with or depends on.
