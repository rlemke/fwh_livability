"""livability domain internals.

Equal-weight county livability composite from four open, county-keyed sources:

1. Census ACS 5-yr (2023) — income, poverty, unemployment, education,
   rent burden, broadband (one API call, all counties).
2. CDC PLACES — GHLTH (fair/poor self-rated health, crude prevalence).
3. FEMA National Risk Index — composite natural-hazard RISK_SCORE.
4. EPA AirData — annual median AQI by county (monitor coverage is partial;
   joined by state+county NAME, misses become missing data).

Each indicator is percentile-ranked across all counties (direction-signed so
100 = good on every axis); the livability score is the MEAN of the available
percentiles (equal weights), requiring >= MIN_INDICATORS present. Geometry is
the census-us cached per-state county GeoJSON (same reuse as fwh_h1b).
Outputs: index.html (choropleth, explainable popups), rankings.html (top 50),
counties.geojson.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import zipfile
from dataclasses import dataclass

import requests

from . import storage as cstore

logger = logging.getLogger(__name__)

ACS_YEAR = 2023
ACS_URL = (
    f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"
    "?get=NAME,B19013_001E,B17001_002E,B17001_001E,B23025_005E,B23025_003E,"
    "B15003_001E,B15003_022E,B15003_023E,B15003_024E,B15003_025E,"
    "B25071_001E,B28002_004E,B28002_001E,B01003_001E&for=county:*"
)
PLACES_URL = (
    "https://data.cdc.gov/resource/swc5-untb.json"
    "?measureid=GHLTH&datavaluetypeid=CrdPrv"
    "&$select=locationid,data_value&$limit=4000"
)
# FEMA's own ArcGIS org — the static hazards.fema.gov zip sits behind bot
# protection, but the FeatureServer is the same NRI data, keyless + paged.
NRI_URL = ("https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/"
           "National_Risk_Index_Counties/FeatureServer/0/query")
AQI_YEAR = 2024
AQI_URL = f"https://aqs.epa.gov/aqsweb/airdata/annual_aqi_by_county_{AQI_YEAR}.zip"
FFL_URL = "https://github.com/rlemke/fwh_livability/blob/main/src/livability/ffl/livability.ffl"

CENSUS_METRICS_PREFIX = "cache/census-us/output/metrics"
MIN_INDICATORS = 6  # counties with fewer available indicators get no score

# key, label, direction (+1 higher-is-better / -1 lower-is-better), format
INDICATORS: list[tuple[str, str, int, str]] = [
    ("income", "Median household income", +1, "money"),
    ("poverty", "Poverty rate", -1, "pct"),
    ("unemployment", "Unemployment rate", -1, "pct"),
    ("education", "Bachelor's degree or higher", +1, "pct"),
    ("rent_burden", "Median rent as % of income", -1, "pct"),
    ("broadband", "Broadband subscription", +1, "pct"),
    ("health", "Fair/poor self-rated health (CDC PLACES)", -1, "pct"),
    ("hazard_risk", "Natural-hazard risk (FEMA NRI)", -1, "score"),
    ("air_quality", f"Median AQI {AQI_YEAR} (EPA)", -1, "score"),
]


@dataclass
class LivabilityMapResult:
    html_path: str
    county_count: int
    scored_count: int
    top_county: str
    top_score: float


# ---------------------------------------------------------------------------
# Fetchers (cache-aware).
# ---------------------------------------------------------------------------


def _fetch_cached(url: str, cache_name: str, *, force: bool = False) -> str:
    path = cstore.join(cstore.cache_root(), cache_name)
    if not force and cstore.exists(path):
        return path
    logger.info("downloading %s -> %s", url, path)
    resp = requests.get(url, timeout=600)
    resp.raise_for_status()
    with cstore.open_write(path, "wb") as f:
        f.write(resp.content)
    return path


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v < 0 else v  # ACS sentinel negatives = suppressed


def fetch_acs(*, force: bool = False) -> dict[str, dict]:
    """GEOID -> {income, poverty, unemployment, education, rent_burden,
    broadband, population} from one ACS county:* call."""
    url = ACS_URL
    key = os.environ.get("CENSUS_API_KEY")
    if key:
        url += f"&key={key}"
    path = _fetch_cached(url, f"acs_{ACS_YEAR}_counties.json", force=force)
    with cstore.open_read(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    hdr = rows[0]
    ix = {name: hdr.index(name) for name in hdr}
    out: dict[str, dict] = {}
    for r in rows[1:]:
        geoid = r[ix["state"]] + r[ix["county"]]
        g = lambda v: _num(r[ix[v]])  # noqa: E731
        pov_n, pov_d = g("B17001_002E"), g("B17001_001E")
        un_n, un_d = g("B23025_005E"), g("B23025_003E")
        ed_d = g("B15003_001E")
        ed_n = sum(x for x in (g("B15003_022E"), g("B15003_023E"),
                               g("B15003_024E"), g("B15003_025E")) if x is not None)
        bb_n, bb_d = g("B28002_004E"), g("B28002_001E")
        out[geoid] = {
            "income": g("B19013_001E"),
            "poverty": (100 * pov_n / pov_d) if pov_n is not None and pov_d else None,
            "unemployment": (100 * un_n / un_d) if un_n is not None and un_d else None,
            "education": (100 * ed_n / ed_d) if ed_d else None,
            "rent_burden": g("B25071_001E"),
            "broadband": (100 * bb_n / bb_d) if bb_n is not None and bb_d else None,
            "population": g("B01003_001E"),
        }
    if not out:
        raise RuntimeError("ACS county fetch parsed to zero rows")
    return out


def fetch_places(*, force: bool = False) -> dict[str, float]:
    """GEOID -> fair/poor self-rated health prevalence (CDC PLACES GHLTH)."""
    path = _fetch_cached(PLACES_URL, "places_ghlth.json", force=force)
    with cstore.open_read(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    out = {}
    for r in rows:
        v = _num(r.get("data_value"))
        loc = str(r.get("locationid") or "").zfill(5)
        if v is not None and len(loc) == 5:
            out[loc] = v
    if not out:
        raise RuntimeError("CDC PLACES GHLTH fetch parsed to zero rows")
    return out


def fetch_nri(*, force: bool = False) -> dict[str, float]:
    """GEOID -> FEMA National Risk Index composite RISK_SCORE (paged query)."""
    path = cstore.join(cstore.cache_root(), "nri_counties.json")
    if force or not cstore.exists(path):
        feats: list[dict] = []
        offset = 0
        while True:
            resp = requests.get(NRI_URL, params={
                "where": "1=1", "outFields": "STCOFIPS,RISK_SCORE",
                "returnGeometry": "false", "f": "json",
                "resultOffset": offset, "resultRecordCount": 2000,
            }, timeout=120)
            resp.raise_for_status()
            page = resp.json().get("features") or []
            feats.extend(page)
            if len(page) < 2000:
                break
            offset += 2000
        with cstore.open_write(path, "w") as f:
            json.dump([ft.get("attributes") or {} for ft in feats], f)
    with cstore.open_read(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    out: dict[str, float] = {}
    for row in rows:
        v = _num(row.get("RISK_SCORE"))
        fips = str(row.get("STCOFIPS") or "").zfill(5)
        if v is not None and len(fips) == 5:
            out[fips] = v
    if not out:
        raise RuntimeError("FEMA NRI query produced zero counties")
    return out


_NAME_SUFFIXES = (" COUNTY", " PARISH", " BOROUGH", " CENSUS AREA",
                  " MUNICIPALITY", " CITY AND BOROUGH", " PLANNING REGION")


def norm_county(name: str) -> str:
    n = (name or "").upper().strip()
    for suf in _NAME_SUFFIXES:
        n = n.removesuffix(suf)
    return n.replace(".", "").replace("'", "").strip()


def fetch_aqi(*, force: bool = False) -> dict[tuple[str, str], float]:
    """(STATE_UPPER, COUNTY_NORM) -> median AQI. Partial coverage (monitored
    counties only); joined by name because AirData carries no FIPS."""
    path = _fetch_cached(AQI_URL, f"aqi_{AQI_YEAR}.zip", force=force)
    out: dict[tuple[str, str], float] = {}
    with zipfile.ZipFile(cstore.localize(path)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        with zf.open(name) as f:
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                v = _num(row.get("Median AQI"))
                if v is not None:
                    out[(str(row.get("State", "")).upper().strip(),
                         norm_county(row.get("County", "")))] = v
    if not out:
        raise RuntimeError("EPA AQI parse produced zero counties")
    return out


# ---------------------------------------------------------------------------
# County geometry — reuse the census-us cached per-state metrics GeoJSON.
# ---------------------------------------------------------------------------


def assemble_counties() -> list[dict]:
    counties: list[dict] = []
    for state in _list_census_states():
        path = _census_metrics_path(state)
        if not cstore.exists(path):
            continue
        with cstore.open_read(path) as f:
            fc = json.load(f)
        for ft in fc.get("features") or []:
            p = ft.get("properties") or {}
            counties.append({
                "geoid": p.get("GEOID"), "name": p.get("NAME"), "state": state,
                "geometry": ft.get("geometry"),
            })
    return counties


def _list_census_states() -> list[str]:
    import boto3
    data_root = cstore._data_root()
    if cstore.is_remote(data_root):
        bucket = data_root.split("://", 1)[1].split("/", 1)[0]
        s3 = boto3.client(
            "s3", endpoint_url=os.environ.get("FW_S3_ENDPOINT"),
            aws_access_key_id=os.environ.get("FW_S3_ACCESS_KEY"),
            aws_secret_access_key=os.environ.get("FW_S3_SECRET_KEY"),
        )
        states = set()
        for pg in s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=CENSUS_METRICS_PREFIX + "/"
        ):
            for o in pg.get("Contents", []):
                if o["Key"].endswith("/metrics.geojson"):
                    states.add(o["Key"].split("/")[-2])
        return sorted(states)
    base = cstore.join(data_root, CENSUS_METRICS_PREFIX)
    if os.path.isdir(base):
        return sorted(d for d in os.listdir(base)
                      if os.path.exists(os.path.join(base, d, "metrics.geojson")))
    return []


def _census_metrics_path(state: str) -> str:
    return cstore.join(cstore._data_root(), CENSUS_METRICS_PREFIX, state, "metrics.geojson")


# ---------------------------------------------------------------------------
# Composite score — signed percentiles, equal weights, missing-aware.
# ---------------------------------------------------------------------------


def percentiles(values: dict[str, float], direction: int) -> dict[str, float]:
    """GEOID -> percentile 0..100 where 100 is BEST for this indicator."""
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    if n == 0:
        return {}
    out: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        pct = 100.0 * (i + (j - i) / 2.0) / max(n - 1, 1)
        for k in range(i, j + 1):
            out[items[k][0]] = pct if direction > 0 else 100.0 - pct
        i = j + 1
    return out


def compute_scores(indicator_values: dict[str, dict[str, float]]) -> dict[str, dict]:
    """{indicator: {geoid: raw}} -> {geoid: {score, pcts: {ind: pct}, raw: {...}}}.

    Equal weights: score = mean of the available signed percentiles; counties
    with fewer than MIN_INDICATORS available indicators get score=None.
    """
    pct_by_ind = {
        key: percentiles(indicator_values.get(key, {}), direction)
        for key, _, direction, _ in INDICATORS
    }
    geoids = set()
    for vals in indicator_values.values():
        geoids.update(vals)
    out: dict[str, dict] = {}
    for g in geoids:
        pcts = {k: pct_by_ind[k][g] for k, _, _, _ in INDICATORS if g in pct_by_ind[k]}
        raw = {k: indicator_values[k][g] for k in indicator_values if g in indicator_values[k]}
        score = round(sum(pcts.values()) / len(pcts), 1) if len(pcts) >= MIN_INDICATORS else None
        out[g] = {"score": score, "pcts": {k: round(v, 1) for k, v in pcts.items()}, "raw": raw}
    return out


# ---------------------------------------------------------------------------
# Build.
# ---------------------------------------------------------------------------


def build_livability_map(*, force: bool = False) -> LivabilityMapResult:
    from shapely.geometry import mapping, shape

    counties = assemble_counties()
    if not counties:
        raise RuntimeError(
            f"no county geometry in the census-us cache ({CENSUS_METRICS_PREFIX}) — "
            "run a census map first"
        )
    acs = fetch_acs(force=force)
    places = fetch_places(force=force)
    nri = fetch_nri(force=force)
    aqi = fetch_aqi(force=force)

    aqi_by_geoid: dict[str, float] = {}
    for c in counties:
        key = (str(c["state"]).upper().strip(), norm_county(c["name"]))
        if key in aqi:
            aqi_by_geoid[c["geoid"]] = aqi[key]

    indicator_values: dict[str, dict[str, float]] = {
        k: {} for k, _, _, _ in INDICATORS
    }
    for g, vals in acs.items():
        for k in ("income", "poverty", "unemployment", "education", "rent_burden", "broadband"):
            if vals.get(k) is not None:
                indicator_values[k][g] = vals[k]
    indicator_values["health"] = places
    indicator_values["hazard_risk"] = nri
    indicator_values["air_quality"] = aqi_by_geoid

    scores = compute_scores(indicator_values)

    feats, ranked = [], []
    for c in counties:
        g = c["geoid"]
        s = scores.get(g, {"score": None, "pcts": {}, "raw": {}})
        pop = (acs.get(g) or {}).get("population")
        props = {
            "GEOID": g, "NAME": c["name"], "STATE": c["state"],
            "score": s["score"], "pop": pop,
            "pcts": s["pcts"], "raw": {k: round(v, 1) for k, v in s["raw"].items()},
        }
        geom = shape(c["geometry"]).simplify(0.004, preserve_topology=True)
        feats.append({"type": "Feature", "geometry": mapping(geom), "properties": props})
        if s["score"] is not None:
            ranked.append((s["score"], c["name"], c["state"], g))
    ranked.sort(reverse=True)
    rank_by_geoid = {g: i + 1 for i, (_s, _n, _st, g) in enumerate(ranked)}
    for ft in feats:
        ft["properties"]["rank"] = rank_by_geoid.get(ft["properties"]["GEOID"])

    fc = {"type": "FeatureCollection", "features": feats}
    coverage = {k: len(v) for k, v in indicator_values.items()}
    html = _render_html(fc, len(ranked), coverage)
    rankings = _render_rankings(ranked[:50], scores, len(ranked))

    out_dir = cstore.output_root()
    html_path = cstore.join(out_dir, "index.html")
    with cstore.open_write(cstore.join(out_dir, "counties.geojson"), "w") as f:
        json.dump(fc, f, separators=(",", ":"))
    with cstore.open_write(cstore.join(out_dir, "rankings.html"), "w") as f:
        f.write(rankings)
    with cstore.open_write(html_path, "w") as f:
        f.write(html)
    top = ranked[0] if ranked else (0.0, "n/a", "n/a", "")
    return LivabilityMapResult(html_path, len(feats), len(ranked),
                               f"{top[1]}, {top[2]}", top[0])


# ---------------------------------------------------------------------------
# Render.
# ---------------------------------------------------------------------------


def _attribution() -> str:
    from datetime import UTC, datetime
    from html import escape

    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    call = "livability.workflows.BuildBestCountyMap"
    repo = FFL_URL.split("/blob/")[0]
    return (
        '<div style="position:fixed;bottom:10px;left:10px;z-index:9999;'
        "background:rgba(255,255,255,0.92);border-radius:6px;padding:6px 10px;"
        "box-shadow:0 1px 4px rgba(0,0,0,0.2);font:11px system-ui,sans-serif;color:#444;"
        'max-width:470px">Generated by Facetwork workflow '
        '<code style="background:#f0f0f0;padding:0 3px;border-radius:3px">'
        f"{escape(call)}</code> &middot; "
        f'<a href="{escape(FFL_URL)}" target="_blank" rel="noopener" '
        'style="color:#1565c0;text-decoration:none">view FFL</a>'
        f' &middot; <a href="{escape(repo)}" target="_blank" rel="noopener" '
        'style="color:#1565c0;text-decoration:none">source repo</a>'
        f" &middot; generated {ts}</div>"
    )


_DESC = (
    "Equal-weight livability composite per county: each indicator is "
    "percentile-ranked across ~3,100 US counties (signed so 100 = good), and "
    "the score is the mean of available percentiles. <b>Weights are a value "
    "judgment</b> — equal weighting is a disclosed default, not truth. "
    "Indicators: ACS income / poverty / unemployment / education / rent burden "
    "/ broadband (2023 5-yr), CDC PLACES fair-poor health, FEMA National Risk "
    "Index, EPA median AQI (monitored counties only — missing indicators are "
    "dropped from that county's mean; fewer than 6 available = no score, "
    "grey). Small-county ACS estimates carry wide margins of error. No crime "
    "indicator: there is no complete open national county-level source."
)


def _render_html(fc: dict, scored: int, coverage: dict[str, int]) -> str:
    fc_js = json.dumps(fc, separators=(",", ":"))
    ind_js = json.dumps([[k, label, fmt] for k, label, _d, fmt in INDICATORS])
    cov = ", ".join(f"{k}:{v}" for k, v in coverage.items())
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Best US counties to live in — equal-weight composite</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet">
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<style>
  html,body,#map{{margin:0;height:100%;width:100%;font-family:system-ui,sans-serif}}
  .panel{{position:absolute;z-index:1;background:rgba(255,255,255,.94);padding:10px 12px;
    border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.3);font-size:12px}}
  #ctl{{top:10px;left:10px;max-width:350px}}
  #ctl h3{{margin:0 0 6px;font-size:14px}}
  #legend{{bottom:18px;right:10px}} #legend .scale{{display:flex;margin-top:4px}}
  #legend .scale div{{display:flex;flex-direction:column;align-items:center;font-size:10px}}
  #legend .scale span{{width:34px;height:12px}}
  .maplibregl-popup-content{{max-width:320px;font-size:12px}}
  .maplibregl-popup-content h4{{margin:0 0 4px;font-size:13px}}
  table.m{{border-collapse:collapse;margin-top:4px}} table.m td{{padding:1px 6px 1px 0}}
  table.m td.v{{text-align:right}}
  .rsearch{{position:absolute;top:10px;left:50%;transform:translateX(-50%);z-index:6;width:300px;max-width:70%}}
  .rsearch input{{width:100%;box-sizing:border-box;padding:7px 11px;border:1px solid #aaa;border-radius:6px;font-size:13px;box-shadow:0 2px 6px rgba(0,0,0,.2)}}
  .rsearch .res{{background:#fff;border-radius:0 0 6px 6px;box-shadow:0 2px 6px rgba(0,0,0,.2);max-height:240px;overflow:auto}}
  .rsearch .res div{{padding:6px 11px;cursor:pointer;font-size:12px;border-top:1px solid #f0f0f0}}
  .rsearch .res div:hover{{background:#f3f3f3}}
  .infobtn{{margin-top:8px;width:100%;padding:7px;font-size:13px;cursor:pointer;background:#fff8e1;border:1px solid #f6c343;border-radius:4px;color:#5d4b00}}
  .infobtn:hover{{background:#fff2c4}}
  .rankbtn{{margin-top:6px;width:100%;padding:7px;font-size:13px;cursor:pointer;background:#e8f5e9;border:1px solid #66bb6a;border-radius:4px;color:#1b5e20;text-align:center;display:block;text-decoration:none;box-sizing:border-box}}
  .rankbtn:hover{{background:#d8eed9}}
  .modal{{position:absolute;inset:0;z-index:5;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center}}
  .modalcard{{background:#fff;max-width:480px;margin:16px;padding:18px 20px 20px;border-radius:10px;box-shadow:0 4px 24px rgba(0,0,0,.4);position:relative;font:13px/1.5 system-ui,sans-serif;max-height:80vh;overflow:auto}}
  .modalcard h2{{margin:0 0 8px;font-size:16px}} .modalbody{{color:#333}} .modalbody b{{color:#111}}
  .modalclose{{position:absolute;top:6px;right:10px;border:none;background:none;font-size:24px;line-height:1;cursor:pointer;color:#999}} .modalclose:hover{{color:#444}}
</style></head>
<body>
<div id="map"></div>
<div class="rsearch"><input id="rsin" placeholder="Find a county..." autocomplete="off"><div class="res" id="rsres"></div></div>
<div id="ctl" class="panel">
  <h3>Best US counties to live in</h3>
  <div style="color:#555">Equal-weight composite of 9 open indicators, percentile-ranked across
  {scored:,} scored counties. Click a county for its full breakdown.</div>
  <a class="rankbtn" href="rankings.html">&#127942; Top 50 counties</a>
  <button id="infobtn" class="infobtn">&#8505;&#65039; About this data</button>
</div>
<div id="legend" class="panel"><b>Livability score</b><div class="scale" id="lgscale"></div></div>
<div id="infomodal" class="modal"><div class="modalcard"><button id="infoclose" class="modalclose">&times;</button><h2>About this data</h2><div class="modalbody">{_DESC}<br><br>Indicator coverage (counties): {cov}.</div></div></div>
{_attribution()}
<script>
const FC={fc_js}, INDS={ind_js};
const RAMP=[[0,'#f7fcf5'],[0.25,'#c7e9c0'],[0.5,'#74c476'],[0.75,'#238b45'],[1,'#00441b']];
const NODATA='#cccccc';
const fmtv=(v,f)=>v==null?'&mdash;':(f==='money'?'$'+Math.round(v).toLocaleString():(f==='pct'?v.toFixed(1)+'%':v.toFixed(1)));
const map=new maplibregl.Map({{container:'map',style:{{version:8,
  sources:{{bm:{{type:'raster',tiles:['https://a.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}.png','https://b.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}.png'],tileSize:256,attribution:'&copy; OpenStreetMap &copy; CARTO &middot; Census ACS &middot; CDC PLACES &middot; FEMA NRI &middot; EPA AirData'}}}},
  layers:[{{id:'bm',type:'raster',source:'bm'}}]}},center:[-96,38],zoom:3.4}});
map.addControl(new maplibregl.NavigationControl());
const sc=document.getElementById('lgscale');
RAMP.forEach(r=>{{const d=document.createElement('div');d.innerHTML=`<span style="background:${{r[1]}}"></span>${{Math.round(r[0]*100)}}`;sc.appendChild(d);}});
const nd=document.createElement('div');nd.innerHTML=`<span style="background:${{NODATA}}"></span>n/a`;sc.appendChild(nd);
function colorExpr(){{
  const expr=['interpolate',['linear'],['get','score']];
  RAMP.forEach(r=>expr.push(r[0]*100,r[1]));
  return ['case',['==',['get','score'],null],NODATA,expr];
}}
function popup(e){{
  const p=e.features[0].properties||{{}};
  const pcts=typeof p.pcts==='string'?JSON.parse(p.pcts):(p.pcts||{{}});
  const raw=typeof p.raw==='string'?JSON.parse(p.raw):(p.raw||{{}});
  let rows='';
  INDS.forEach(([k,label,f])=>{{
    const pv=pcts[k],rv=raw[k];
    rows+=`<tr><td>${{label}}</td><td class="v">${{fmtv(rv,f)}}</td><td class="v">${{pv==null?'&mdash;':Math.round(pv)}}</td></tr>`;
  }});
  const hdr=p.score==null?'no score (insufficient data)':`score <b>${{p.score}}</b> &middot; rank <b>#${{p.rank}}</b>`;
  new maplibregl.Popup({{closeButton:true,maxWidth:'330px'}}).setLngLat(e.lngLat)
    .setHTML(`<h4>${{p.NAME}}, ${{p.STATE}}</h4>${{hdr}}${{p.pop?` &middot; pop ${{Math.round(p.pop).toLocaleString()}}`:''}}
    <table class="m"><tr><td></td><td class="v"><b>value</b></td><td class="v"><b>pctile</b></td></tr>${{rows}}</table>`).addTo(map);
}}
map.on('load',()=>{{
  map.addSource('d',{{type:'geojson',data:FC}});
  map.addLayer({{id:'fill',type:'fill',source:'d',paint:{{'fill-color':colorExpr(),'fill-opacity':0.8}}}});
  map.addLayer({{id:'line',type:'line',source:'d',paint:{{'line-color':'#888','line-width':0.15}}}});
  map.on('click','fill',popup);
  map.on('mouseenter','fill',()=>map.getCanvas().style.cursor='pointer');
  map.on('mouseleave','fill',()=>map.getCanvas().style.cursor='');
  const inp=document.getElementById('rsin'),res=document.getElementById('rsres');
  function bbox(g){{let a=[180,90,-180,-90];const w=c=>{{if(typeof c[0]==='number'){{a[0]=Math.min(a[0],c[0]);a[1]=Math.min(a[1],c[1]);a[2]=Math.max(a[2],c[0]);a[3]=Math.max(a[3],c[1]);}}else c.forEach(w);}};w(g.coordinates);return a;}}
  inp.addEventListener('input',()=>{{const q=inp.value.trim().toLowerCase();res.innerHTML='';if(q.length<2)return;
    FC.features.map(f=>({{n:`${{f.properties.NAME}}, ${{f.properties.STATE}}`,f}})).filter(x=>x.n.toLowerCase().includes(q)).slice(0,12).forEach(x=>{{
      const d=document.createElement('div');d.textContent=x.n+(x.f.properties.rank?` (#${{x.f.properties.rank}})`:'');
      d.addEventListener('click',()=>{{const b=bbox(x.f.geometry);
        map.fitBounds([[b[0],b[1]],[b[2],b[3]]],{{padding:40,maxZoom:9,duration:700}});res.innerHTML='';inp.value=x.n;}});
      res.appendChild(d);}});}});
  document.addEventListener('click',e=>{{if(!e.target.closest('.rsearch'))res.innerHTML='';}});
}});
const _im=document.getElementById('infomodal');
if(_im){{const ib=document.getElementById('infobtn'),ic=document.getElementById('infoclose');
 if(ib)ib.onclick=()=>{{_im.style.display='flex';}};
 if(ic)ic.onclick=()=>{{_im.style.display='none';}};
 _im.onclick=e=>{{if(e.target===_im)_im.style.display='none';}};}}
</script></body></html>"""


def _render_rankings(top: list[tuple], scores: dict[str, dict], scored: int) -> str:
    from html import escape

    rows = []
    for i, (score, name, state, g) in enumerate(top, 1):
        pcts = scores.get(g, {}).get("pcts", {})
        best = sorted(pcts.items(), key=lambda kv: -kv[1])[:2]
        worst = sorted(pcts.items(), key=lambda kv: kv[1])[:1]
        label = {k: lab for k, lab, _d, _f in INDICATORS}
        strengths = ", ".join(label[k] for k, _ in best)
        weakness = ", ".join(label[k] for k, _ in worst)
        rows.append(
            f"<tr><td class='r'>{i}</td><td>{escape(name)}, {escape(state)}</td>"
            f"<td class='r'>{score}</td><td>{escape(strengths)}</td>"
            f"<td>{escape(weakness)}</td></tr>"
        )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Top 50 US counties — equal-weight livability</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{{font-family:system-ui,sans-serif;margin:24px auto;max-width:900px;padding:0 16px;color:#222}}
 h1{{font-size:20px}} .sub{{color:#555;font-size:13px;margin-bottom:14px}}
 table{{border-collapse:collapse;width:100%;font-size:13px}}
 th,td{{padding:6px 8px;border-bottom:1px solid #eee;text-align:left}} td.r,th.r{{text-align:right}}
 tr:hover{{background:#f6fbf6}} a{{color:#1565c0}}
</style></head><body>
<h1>&#127942; Top 50 US counties to live in — equal-weight composite</h1>
<div class="sub">Mean of 9 signed indicator percentiles across {scored:,} scored counties
(<a href="index.html">back to the map</a>). {_DESC}</div>
<table><tr><th class="r">#</th><th>County</th><th class="r">Score</th><th>Strongest on</th><th>Weakest on</th></tr>
{''.join(rows)}
</table>{_attribution()}</body></html>"""
