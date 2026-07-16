"""Offline unit tests for the livability domain (no network)."""

from __future__ import annotations

import json

from livability._lib import (
    INDICATORS,
    MIN_INDICATORS,
    _render_html,
    _render_rankings,
    compute_scores,
    norm_county,
    percentiles,
)


def test_percentiles_direction_and_ties():
    vals = {"a": 10.0, "b": 20.0, "c": 20.0, "d": 40.0}
    up = percentiles(vals, +1)
    assert up["a"] == 0.0 and up["d"] == 100.0
    assert up["b"] == up["c"]  # ties share a percentile
    down = percentiles(vals, -1)
    assert down["a"] == 100.0 and down["d"] == 0.0


def test_percentiles_empty_and_single():
    assert percentiles({}, +1) == {}
    assert percentiles({"x": 5.0}, -1)["x"] in (0.0, 100.0)


def test_compute_scores_equal_weights_and_min_indicators():
    keys = [k for k, _l, _d, _f in INDICATORS]
    # county "good" best on every axis, "bad" worst; "thin" has too few indicators
    indicator_values = {}
    for i, k in enumerate(keys):
        _, _, direction, _ = INDICATORS[i]
        good_val, bad_val = (100.0, 1.0) if direction > 0 else (1.0, 100.0)
        vals = {"good": good_val, "bad": bad_val}
        if i < MIN_INDICATORS - 1:  # "thin" appears on too few indicators
            vals["thin"] = 50.0  # mid value — must not tie with (and lift) "bad"
        indicator_values[k] = vals
    scores = compute_scores(indicator_values)
    assert scores["good"]["score"] == 100.0
    assert scores["bad"]["score"] == 0.0
    assert scores["thin"]["score"] is None  # < MIN_INDICATORS -> unscored
    assert len(scores["good"]["pcts"]) == len(keys)


def test_norm_county_strips_suffixes():
    assert norm_county("Los Angeles County") == "LOS ANGELES"
    assert norm_county("East Baton Rouge Parish") == "EAST BATON ROUGE"
    assert norm_county("Fairbanks North Star Borough") == "FAIRBANKS NORTH STAR"
    assert norm_county("St. Mary's County") == "ST MARYS"


def _fc(score, rank=1):
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
        "properties": {"GEOID": "01001", "NAME": "Test County", "STATE": "Testland",
                       "score": score, "rank": rank, "pop": 12345,
                       "pcts": {"income": 88.0}, "raw": {"income": 60000.0}},
    }]}


def test_render_html_smoke():
    html = _render_html(_fc(77.7), scored=1, coverage={"income": 1})
    assert "<!doctype html>" in html and "maplibre-gl" in html
    assert "Best US counties" in html and "rankings.html" in html
    start = html.index("const FC=") + len("const FC=")
    end = html.index(", INDS=")
    assert json.loads(html[start:end])["features"][0]["properties"]["score"] == 77.7


def test_render_rankings_smoke():
    scores = {"01001": {"score": 77.7, "pcts": {"income": 88.0, "poverty": 70.0,
                                                "health": 20.0}, "raw": {}}}
    html = _render_rankings([(77.7, "Test County", "Testland", "01001")], scores, 1)
    assert "Top 50" in html and "Test County, Testland" in html
    assert "Median household income" in html  # strongest-on label resolved
