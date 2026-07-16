"""Event facet handler for the livability domain — thin layer over ``_lib``."""

from __future__ import annotations

import os
from typing import Any

from .._lib import build_livability_map

MAPS = "livability.maps"


def handle_build_livability_map(params: dict[str, Any]) -> dict[str, Any]:
    """Equal-weight county livability composite -> choropleth + top-50 rankings."""
    step_log = params.get("_step_log")
    try:
        res = build_livability_map(force=bool(params.get("force")))
        if step_log:
            step_log(
                f"BuildLivabilityMap: {res.scored_count}/{res.county_count} counties scored; "
                f"#1 = {res.top_county} ({res.top_score}) -> {res.html_path}",
                level="success",
            )
        return {
            "html_path": res.html_path,
            "county_count": res.county_count,
            "scored_count": res.scored_count,
            "top_county": res.top_county,
            "top_score": res.top_score,
        }
    except Exception as exc:
        if step_log:
            step_log(f"BuildLivabilityMap: {exc}", level="error")
        raise


_DISPATCH: dict[str, Any] = {
    f"{MAPS}.BuildLivabilityMap": handle_build_livability_map,
}


def handle(payload: dict) -> dict:
    return _DISPATCH[payload["_facet_name"]](payload)


def register_handlers(runner) -> None:
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
            timeout_ms=0,  # blocking multi-source fetch (cached after first run)
        )


def register_poller(poller) -> None:
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)
