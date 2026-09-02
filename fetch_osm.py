"""Lakes and built-up city extents from OpenStreetMap.

Two things the review asked for that no official open dataset covers at this scale:

  lakes.geojson   Manchar and Hamal. OSM has the geometry but leaves these polygons
                  UNNAMED, so a name query returns nothing. They are identified
                  instead by taking the largest water polygon near each known
                  coordinate, then labelled from the gazetteer.
  cities.geojson  landuse=residential footprints, dissolved per settlement and
                  matched to the nearest place in places.json. Replaces point labels
                  with real built-up extents.

Overpass times out on a 300,000 km2 bbox, so requests are tiled and retried across
mirrors.
"""
import json, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import Polygon, MultiPolygon, shape
from shapely.ops import unary_union
import warnings
warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from aoi import WEST, SOUTH, EAST, NORTH, describe

OUT = ROOT / "web/assets"
CACHE = ROOT / "osm_cache"; CACHE.mkdir(exist_ok=True)
print(describe())

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
]


def overpass(query, tag):
    """Run a query, caching by tag and rotating mirrors on failure."""
    cf = CACHE / f"{tag}.json"
    if cf.exists():
        return json.loads(cf.read_text())
    last = None
    for attempt in range(6):
        url = ENDPOINTS[attempt % len(ENDPOINTS)]
        try:
            req = urllib.request.Request(
                url, data=query.encode(), headers={"User-Agent": "sindh-flood-sim/1.0"})
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.loads(r.read())
            cf.write_text(json.dumps(data))
            return data
        except Exception as e:
            last = e
            time.sleep(5 + attempt * 5)
    print(f"  overpass failed for {tag}: {last}")
    return {"elements": []}


def polys(data):
    """Overpass 'out geom' -> shapely polygons (ways and multipolygon relations)."""
    out = []
    for e in data.get("elements", []):
        if e["type"] == "way" and e.get("geometry"):
            ring = [(p["lon"], p["lat"]) for p in e["geometry"]]
            if len(ring) >= 4:
                try:
                    out.append((Polygon(ring).buffer(0), e.get("tags", {})))
                except Exception:
                    pass
        elif e["type"] == "relation":
            outers = []
            for m in e.get("members", []):
                if m.get("role") in ("outer", "") and m.get("geometry"):
                    ring = [(p["lon"], p["lat"]) for p in m["geometry"]]
                    if len(ring) >= 4:
                        try:
                            outers.append(Polygon(ring).buffer(0))
                        except Exception:
                            pass
            if outers:
                out.append((unary_union(outers), e.get("tags", {})))
    return [(g, t) for g, t in out if not g.is_empty and g.is_valid]


def km2(geom):
    return gpd.GeoSeries([geom], crs="EPSG:4326").to_crs("ESRI:54009").area.iloc[0] / 1e6


# ── lakes ───────────────────────────────────────────────────────────────────
LAKES = [("Manchar Lake", 67.643, 26.438, 0.35),
         ("Hamal Lake",   67.633, 27.371, 0.40)]

feats = []
for name, lon, lat, pad in LAKES:
    q = (f"[out:json][timeout:240];("
         f'way["natural"="water"]({lat-pad},{lon-pad},{lat+pad},{lon+pad});'
         f'relation["natural"="water"]({lat-pad},{lon-pad},{lat+pad},{lon+pad});'
         f");out geom;")
    got = polys(overpass(q, "lake_" + name.split()[0].lower()))
    # Canals are also natural=water; drop them and anything tiny.
    cand = [(g, t) for g, t in got
            if t.get("water") != "canal" and t.get("waterway") is None and km2(g) > 5]
    if not cand:
        print(f"  {name}: no polygon found"); continue
    g, t = max(cand, key=lambda gt: km2(gt[0]))
    a = km2(g)
    feats.append(dict(geometry=g, name=name, area_km2=round(a, 1),
                      osm_name=t.get("name")))
    print(f"  {name}: {a:,.0f} km2 (osm name={t.get('name')!r}, "
          f"{len(cand)} candidates)")

if feats:
    gdf = gpd.GeoDataFrame(feats, crs="EPSG:4326")
    gdf["geometry"] = gdf.geometry.simplify(0.0004, preserve_topology=True)
    gdf.to_file(OUT / "lakes.geojson", driver="GeoJSON")
    print(f"  lakes.geojson -> {(OUT/'lakes.geojson').stat().st_size/1024:.0f} KB")

# ── city footprints ─────────────────────────────────────────────────────────
places = json.loads((OUT / "places.json").read_text())
print(f"\nmatching built-up areas to {len(places)} places")

NTILE = 4
lons = np.linspace(WEST, EAST, NTILE + 1)
lats = np.linspace(SOUTH, NORTH, NTILE + 1)
built = []
for i in range(NTILE):
    for j in range(NTILE):
        q = (f"[out:json][timeout:240];("
             f'way["landuse"="residential"]({lats[j]},{lons[i]},{lats[j+1]},{lons[i+1]});'
             f'relation["landuse"="residential"]({lats[j]},{lons[i]},{lats[j+1]},{lons[i+1]});'
             f");out geom;")
        got = polys(overpass(q, f"res_{i}_{j}"))
        built += [g for g, t in got]
    print(f"  tile row {i+1}/{NTILE}: {len(built)} polygons so far")

print(f"{len(built)} residential polygons total")

# Each residential polygon goes to its NEAREST place, once.
#
# Letting every place claim everything within a radius double-counts: Kot Malik
# Barkhurdar is a village next to Quetta, and both came back with the same 161 km2
# because both sat inside the radius of Quetta's built-up area. Nearest-place
# assignment gives each blob one owner.
city_feats = []
if built:
    bg = gpd.GeoSeries(built, crs="EPSG:4326")
    cent = bg.centroid
    plon = np.array([p["lon"] for p in places])
    plat = np.array([p["lat"] for p in places])
    cx = cent.x.to_numpy()[:, None]
    cy = cent.y.to_numpy()[:, None]
    d = np.hypot((cx - plon[None, :]) * np.cos(np.radians(plat[None, :])),
                 cy - plat[None, :]) * 111.32          # km, polygons x places
    owner = d.argmin(axis=1)
    best = d.min(axis=1)
    # A town's own built-up land is contiguous with its gazetteer point. At 12 km a
    # place also claimed the residential blobs of every UNLABELLED village around it
    # -- most villages in the frame have no owner in places.json, so they were all
    # absorbed by whichever labelled town was nearest and dissolved into its
    # footprint. That put 24 of 73 footprints more than 4 km from their own label;
    # Sita Road's 0.94 km2 "city" sat 8.3 km from Sita Road.
    MAX_KM = 3.0
    for pi, p in enumerate(places):
        mask = (owner == pi) & (best < MAX_KM)
        if not mask.any():
            continue
        merged = unary_union(list(bg[mask]))
        a = km2(merged)
        if a < 0.4:
            continue
        city_feats.append(dict(geometry=merged, name=p["name"],
                               pop=p["pop"], area_km2=round(a, 2)))

if city_feats:
    cg = gpd.GeoDataFrame(city_feats, crs="EPSG:4326")
    cg["geometry"] = cg.geometry.simplify(0.0003, preserve_topology=True)
    cg = cg.sort_values("area_km2", ascending=False)
    cg.to_file(OUT / "cities.geojson", driver="GeoJSON")
    print(f"{len(cg)} city footprints -> {(OUT/'cities.geojson').stat().st_size/1024:.0f} KB")
    for _, r in cg.head(12).iterrows():
        print(f"   {r['name']:<22}{r['area_km2']:>8.1f} km2")
else:
    print("no city footprints matched")
