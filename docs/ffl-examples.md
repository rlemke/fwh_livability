# FFL Examples — `livability`

Every numbered scenario is a **complete, compilable FFL file**. Copy one into
`my.ffl` and run it:

```bash
fw ffl run --primary my.ffl \
  --library ~/fw_handlers/fwh_livability/src/livability/ffl/livability.ffl \
  --workflow my.livability.<WorkflowName>
```

A runner serving the `livability` namespace must be up
(`fw runner start --domain livability`). Every block below is compile-checked
against `src/livability/ffl/livability.ffl`.

New to the language? Start with the
[FFL grammar](https://github.com/rlemke/facetwork/blob/main/docs/reference/language/grammar.md)
and the [canonical examples](https://github.com/rlemke/facetwork/tree/main/examples/canonical).

---

## The facets at a glance

| Declaration | Signature | Does |
|---|---|---|
| `livability.maps.BuildLivabilityMap` | `(force: Boolean = false) => (html_path, county_count, scored_count, top_county, top_score)` | Equal-weight county composite over ACS + PLACES + NRI + AQI → choropleth + top-50 rankings |
| `livability.workflows.BuildBestCountyMap` | `(force: Boolean = false) => (status, html_path, county_count, scored_count, top_county, top_score)` | The shipped entry point |

`BuildLivabilityMap` is an `event facet`: it runs in a handler on a runner, not in
the compiler. Its `with Effect(kind = "external")` / `with Cost(tier = "expensive")`
mixins are what `fw_capabilities(effect=…, max_cost=…)` filters on.

---

## 1. Run what ships — no FFL to write

```bash
fw ffl seed --include livability

fw ffl run --primary ~/fw_handlers/fwh_livability/src/livability/ffl/livability.ffl \
  --workflow livability.workflows.BuildBestCountyMap \
  --inputs '{"force": false}'
```

Write FFL when you want a different shape of run — your own error handling, a
coverage guard on the composite, or composition with another domain.

## 2. The smallest workflow you can write

Every FFL workflow needs a `namespace`, a `use` per namespace it calls into, and a
`yield` back to itself.

```ffl
namespace my.livability {

    use livability.maps

    /** Build the county livability composite. */
    workflow MyLivabilityMap() => (html_path: String, scored: Int) andThen {

        map = livability.maps.BuildLivabilityMap(force = false)

        yield MyLivabilityMap(html_path = map.html_path, scored = map.scored_count)
    }
}
```

Three rules visible above: `=>` sits on the **same line** as the closing `)` of the
parameter list; references are always `step.field` (never a bare step name); and a
workflow ends by yielding to itself.

## 3. Parameters, `$`, and expressions

`$` means "my immediate container" — inside a workflow body that's the workflow, so
`$.force` is its parameter. `++` concatenates strings.

```ffl
namespace my.livability {

    use livability.maps

    /** Rebuild and report which county came out on top. */
    workflow BestCountyHeadline(force: Boolean = false) => (headline: String, html_path: String) andThen {

        map = livability.maps.BuildLivabilityMap(force = $.force)

        yield BestCountyHeadline(
            headline = "top county: " ++ map.top_county,
            html_path = map.html_path)
    }
}
```

Run with `--inputs '{"force": true}'`.

## 4. Call-time mixins — timeouts and retries

The facet ships `with Timeout(minutes = 30)`; the **call site** can override it for
one particular use without forking the facet. Nine indicator sources means nine
chances for a slow upstream.

```ffl
namespace my.livability {

    use livability.maps

    /** Give the full nine-indicator rebuild room, and retry flaky sources. */
    workflow ResilientLivabilityMap() => (html_path: String) andThen {

        map = livability.maps.BuildLivabilityMap(force = true) with Timeout(minutes = 90) with Retry(maxAttempts = 3, backoffSeconds = 120)

        yield ResilientLivabilityMap(html_path = map.html_path)
    }
}
```

## 5. Survive a failed source — `catch`

`catch` runs when its step errors after retries are exhausted. Yielding from the
catch block ends the run with a partial result instead of a hard failure.

```ffl
namespace my.livability {

    use livability.maps

    /** Report a partial result rather than failing the run. */
    workflow BestEffortLivabilityMap() => (status: String, html_path: String) andThen {

        map = livability.maps.BuildLivabilityMap(force = true) catch {
            yield BestEffortLivabilityMap(status = "source_fetch_failed", html_path = "")
        }

        yield BestEffortLivabilityMap(status = "completed", html_path = map.html_path)
    }
}
```

## 6. Branch on a result — `when`

A `when` block hangs off the step it inspects: inside a case `$` is that step and
`$$` reaches the workflow's parameters. Every `when` needs a default case, last,
and conditions must be real `Boolean`s (no truthy coercion).

```ffl
namespace my.livability {

    use livability.maps

    /** Counties with < 6 indicators go unscored — guard on the scored share. */
    workflow VerifiedLivabilityMap(min_scored: Int = 2500) => (status: String, html_path: String, top: String) andThen {

        map = livability.maps.BuildLivabilityMap() andThen when {
            case $.scored_count >= $$.min_scored => {
                yield VerifiedLivabilityMap(status = "good_coverage", html_path = $.html_path, top = $.top_county)
            }
            case _ => {
                yield VerifiedLivabilityMap(status = "thin_coverage", html_path = $.html_path, top = $.top_county)
            }
        }
    }
}
```

## 7. Reuse the shipped workflow

Workflows compose like facets — wrap `BuildBestCountyMap` rather than forking it.

```ffl
namespace my.livability {

    use livability.workflows

    /** Wrap the shipped workflow and reshape its result. */
    workflow LivabilityWithHeadline() => (headline: String) andThen {

        built = livability.workflows.BuildBestCountyMap(force = false)

        yield LivabilityWithHeadline(
            headline = built.status ++ " — top county " ++ built.top_county)
    }
}
```

## 8. Compose across domains — publish the map

Facets from different domains compose in one workflow as long as some runner in
the fleet serves each namespace. `census.Publish` is the generic publisher the map
domains share.

```ffl
namespace my.livability {

    use livability.maps
    use census.Publish

    /** Render, then push to the public maps site. */
    workflow LivabilityPublish(repo: String = "rlemke/facetwork-maps") => (pages_url: String) andThen {

        map = livability.maps.BuildLivabilityMap()

        published = census.Publish.PublishWebBundle(
            repo = $.repo,
            prefixes = ["livability/output"],
            dests = ["us/livability"],
            labels = ["Best county to live in"],
            landing_title = "Facetwork maps")

        yield LivabilityPublish(pages_url = published.pages_url)
    }
}
```

Compile that one with `--library ~/fw_handlers/fwh_census_us/src/census_us/ffl/census.ffl`
as well.

---

## Cheat sheet

| You want to… | Write |
|---|---|
| Read a workflow/step parameter | `$.name` (`$$.name` one level out) |
| Read a previous step's result | `stepname.field` |
| Order two independent steps | reference a field of the first from the second |
| More time / retries for one call | `… with Timeout(minutes = 90) with Retry(maxAttempts = 3, backoffSeconds = 120)` |
| Handle a step failure | `step = Facet(…) catch { yield … }` |
| Branch | `step = Facet(…) andThen when { case <bool> => { … } case _ => { … } }` |
| Fan out over a list | `workflow W(items: Json) … andThen foreach i in $.items { … }` |
| Concatenate strings | `a ++ b` |

**Validate before you run:** `afl my.ffl --check` or MCP `fw_validate`. Every error
carries a `rule_id` — fetch `fw://docs/rules/{rule_id}` for a wrong/right pair.

## See also

- [`docs/README.md`](README.md) — per-feature specs for this domain
- [FFL grammar](https://github.com/rlemke/facetwork/blob/main/docs/reference/language/grammar.md) ·
  [canonical examples](https://github.com/rlemke/facetwork/tree/main/examples/canonical) ·
  [relative `$`-scoping](https://github.com/rlemke/facetwork/blob/main/docs/architecture/ffl-relative-scoping.md)
- `src/livability/ffl/livability.ffl` — the source of truth for every signature above
