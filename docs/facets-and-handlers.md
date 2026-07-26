# Facets, Handlers & Domain Wiring

**Namespaces:** `livability.maps`, `livability.workflows` ·
**FFL:** `src/livability/ffl/livability.ffl` ·
**Handlers:** `src/livability/handlers/{livability_handlers,__init__}.py` ·
**Package:** `src/livability/__init__.py`, `src/livability/storage.py`,
`pyproject.toml`

## Overview

This spec documents the runtime surface — the single event facet, its workflow
wrapper, how the handler is registered and dispatched, and how the package is
discovered and configured on the fleet. Unlike large multi-facet domains, livability
exposes exactly **one** capability; the plumbing is intentionally thin.

## How it works

- **FFL** declares `livability.maps.BuildLivabilityMap` (event facet) and
  `livability.workflows.BuildBestCountyMap` (workflow), the latter `use`-ing the
  former and simply forwarding `force` in and the results out via `andThen` + `yield`.
- **Discovery.** `pyproject.toml` registers the `facetwork.domains` entry point
  `livability = "livability:domain"`; `src/livability/__init__.py` builds a
  `DomainPackage(name="livability", ffl_dir=…/ffl, register_handlers=…,
  runner_env=…)`. `fw runner start --domain livability` loads the FFL and handlers
  from this.
- **Registration.** `handlers/__init__.py:register_all_registry_handlers` calls
  `livability_handlers.register_handlers`, which registers
  `livability.maps.BuildLivabilityMap` against `handle` with `timeout_ms=0`
  (blocking multi-source fetch — relies on the global execution timeout, not
  heartbeats).
- **Dispatch.** `handle(payload)` looks up `payload["_facet_name"]` in `_DISPATCH`
  and calls `handle_build_livability_map`, which invokes
  `build_livability_map(force=…)`, logs a success/error line via `_step_log`, and
  returns the five result fields. A `register_poller` shim is also provided for the
  AgentPoller model.

## Fan-out

**Single-task — no fan-out.** One workflow, one facet, one national build task. See
[composite-scoring](composite-scoring.md) for why the ranking is intrinsically
global and cannot be sharded.

## Data & fields

**Facet — `livability.maps.BuildLivabilityMap`** (event):

```
BuildLivabilityMap(force: Boolean = false)
  => (html_path: String, county_count: Int, scored_count: Int,
      top_county: String, top_score: Double)
  with Effect(kind="external") with Cost(tier="expensive")
  with Timeout(minutes=30)
```

`Effect(external)` = it hits the network (four sources); `Cost(expensive)` +
`Timeout(30 min)` reflect the NRI paging + AQI zip on a cold cache (cached after
first run). `force=true` re-fetches all four sources.

**Workflow — `livability.workflows.BuildBestCountyMap`** (`force: Boolean = false`)
returns the same five fields plus `status: String` (`"completed"`); it calls the
facet and yields the results. This is the entry point for `fw ffl run`.

## External libraries / binaries

- **`facetwork`** — `DomainPackage` (entry-point contract),
  `facetwork.runtime.storage` + `facetwork.config.get_output_base` (via
  `storage.py`).
- Handler itself pulls no third-party deps directly; the work deps (`requests`,
  `shapely`, `boto3`) live in `_lib`/`storage` — see the other specs.

## Facets & workflows

The two above are the entire FFL surface. There are **no pure/helper facets**, no
mixin-composed facet library — the domain is one map. The facet's Effect/Cost/Timeout
mixins are the only mixins present.

## Cache / output

The handler returns `html_path` pointing at `cache/livability/output/index.html`.
Storage backend selection lives in `storage.py`: `_data_root()` = `FW_DATA_ROOT` or
`get_output_base()`; remote (`s3://`) roots route cache/output under
`cache/livability/{cache,output}/`, local roots under
`livability-{cache,output}/`. `runner_env` on the `DomainPackage` raises
`FW_TASK_EXECUTION_TIMEOUT_MS` to 30 min and `FW_STUCK_TIMEOUT_MS` to 35 min so the
cold-cache multi-fetch isn't reaped.

## Gotchas & notes

- **`timeout_ms=0` is deliberate** — the fetch blocks (no heartbeat can fire), so
  the handler opts out of the per-task heartbeat timeout and relies on the global
  execution timeout, which `runner_env` widens to 30 min. Don't set a finite
  `timeout_ms` here.
- **One facet, served by one handler** — no RegistryRunner "unknown facet" surprises,
  but also no fallback: if the census-us geometry cache is empty the *handler*
  raises (see [county-geometry](county-geometry.md)), it is not a routing miss.
- **Namespace = task list.** Tasks route on the `livability` top-level namespace, so
  a `--domain livability` runner polls exactly this work (per the framework's
  intrinsic task-list routing).
- **Both registration shims exist** (`register_handlers` for RegistryRunner,
  `register_poller` for AgentPoller) — keep both if adding facets.

## Related specs

- [composite-scoring](composite-scoring.md) — the flagship computation behind the
  facet.
- [data-sources](data-sources.md) · [county-geometry](county-geometry.md) ·
  [map-and-output](map-and-output.md) — the three stages the handler orchestrates.
