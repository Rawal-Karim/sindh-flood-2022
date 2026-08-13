"""Pull the Sindh Irrigation Department's own SRP-SID DSS layers.

Portal: https://portal.srpsid-dss.gos.pk/ — SID / Sindh Resilience Project (World Bank),
"Flood Risk Assessment, Right Bank of Indus River". GeoServer, 901 WFS feature types.

This is the Department's authoritative data, so it replaces several things this project
had been deriving:
  results:canal_network   1,628 canal lines  -> review item 5
  results:drains             37 drain lines  -> review item 5 (RBOD-I/III, MNV, branches)
  results:subcatchments       6 sub-models   -> review item 9, replaces my DEM guess
  results:sindh_province      provincial boundary

The 42 modelling scenarios are 7 return periods (2.3/5/10/25/50/100/500 yr) x 2 climates
(present/future) x 3 embankment states (perfect / reduced capacity / breaches). Their
rasters are WMS-only (WCS is disabled), so they are fetched as georeferenced PNGs
rendered directly into the app's AOI bbox, which drapes them without reprojection.
"""
import itertools, json, re, sys, time, urllib.request
from pathlib import Path

import geopandas as gpd
import warnings
warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from aoi import WEST, SOUTH, EAST, NORTH, describe

OUT = ROOT / "web/assets"
CACHE = ROOT / "srp_cache"; CACHE.mkdir(exist_ok=True)
SCEN = OUT / "scenarios"; SCEN.mkdir(parents=True, exist_ok=True)
GS = "https://portal.srpsid-dss.gos.pk/geoserver"
UA = {"User-Agent": "Mozilla/5.0"}
print(describe())


def get(url, dest, timeout=600):
    if dest.exists() and dest.stat().st_size > 500:
        return True
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                        timeout=timeout) as r:
                dest.write_bytes(r.read())
            return True
        except Exception as e:
            if attempt == 3:
                print(f"    FAILED {dest.name}: {str(e)[:80]}")
                return False
            time.sleep(4 + attempt * 4)


def wfs(layer):
    dest = CACHE / (layer.replace(":", "_") + ".json")
    url = (f"{GS}/ows?service=WFS&version=2.0.0&request=GetFeature"
           f"&typeNames={layer}&outputFormat=application/json&srsName=EPSG:4326")
    return dest if get(url, dest) else None


def clip_dump(path, name, cols, tol=0.0002):
    g = gpd.read_file(path).to_crs("EPSG:4326")
    from shapely.geometry import box
    aoi = box(WEST, SOUTH, EAST, NORTH)
    g = g[g.intersects(aoi)].copy()
    g["geometry"] = g.geometry.intersection(aoi)
    g = g[~g.geometry.is_empty]
    keep = [c for c in cols if c in g.columns]
    g = g[keep + ["geometry"]]
    g["geometry"] = g.geometry.simplify(tol, preserve_topology=True)
    p = OUT / f"{name}.geojson"
    g.to_file(p, driver="GeoJSON")
    print(f"  {name}: {len(g)} features -> {p.stat().st_size/1024:.0f} KB")
    return g


print("\n-- vectors --")
if (f := wfs("results:canal_network")):
    clip_dump(f, "canals", ["name", "descriptio"])
if (f := wfs("results:drains")):
    clip_dump(f, "drains", ["Name", "descriptio"])
if (f := wfs("results:subcatchments")):
    sc = clip_dump(f, "srp_subcatchments", ["Name", "Model_Name", "Area"], tol=0.0005)
    print("     ", ", ".join(str(x) for x in sc["Name"]))
if (f := wfs("results:sindh_province")):
    clip_dump(f, "sindh_province", ["P_Name", "area"], tol=0.0005)

# ── scenario rasters: 42 runs x 4 result variables ──────────────────────────
RP = ["2.3", "5", "10", "25", "50", "100", "500"]
CLIM = ["present", "future"]
STATE = ["perfect", "redcapacity", "breaches"]
W_PX = 900
H_PX = int(round(W_PX * (NORTH - SOUTH) / (EAST - WEST) * 1.0))

# The portal publishes four result rasters per run.
VARIABLES = [
    dict(key="maxdepth",    label="Max inundation depth",     unit="m",     included=True),
    dict(key="duration",    label="Inundation duration",      unit="days",  included=True),
    dict(key="maxvelocity", label="Max flow velocity",        unit="m/s",   included=True),
    dict(key="vh",          label="Hazard (velocity x depth)", unit="m2/s", included=True),
]

n_runs = len(RP) * len(CLIM) * len(STATE)
print(f"\n-- {n_runs} runs x {len(VARIABLES)} variables = "
      f"{n_runs * len(VARIABLES)} rasters ({W_PX}x{H_PX}) --")

scenarios, missing = [], []
for rp, cl, st in itertools.product(RP, CLIM, STATE):
    # GeoServer keeps the dot: results:t3_2.3yrs_... Stripping it (giving "23yrs")
    # silently 404s the six 2.3-year runs. Keep the dot in the layer name and only
    # sanitise it for the local filename.
    tag = f"t3_{rp.replace('.', '_')}yrs_{cl}_{st}"
    files = {}
    for v in VARIABLES:
        layer = f"results:t3_{rp}yrs_{cl}_{st}_{v['key']}"
        dest = SCEN / f"{tag}__{v['key']}.png"
        url = (f"{GS}/wms?service=WMS&version=1.1.1&request=GetMap&layers={layer}"
               f"&bbox={WEST},{SOUTH},{EAST},{NORTH}&width={W_PX}&height={H_PX}"
               f"&srs=EPSG:4326&format=image/png&transparent=true")
        ok = get(url, dest, timeout=300)
        size = dest.stat().st_size if dest.exists() else 0
        if ok and size > 2000:
            files[v["key"]] = f"scenarios/{dest.name}"
        else:
            missing.append(f"{tag}/{v['key']}")
    if files:
        scenarios.append(dict(id=tag, return_period_yr=float(rp), climate=cl,
                              embankment=st, files=files))
        print(f"  {tag:<34}{' '.join(sorted(files))}")

got = sum(len(s['files']) for s in scenarios)
for v in VARIABLES:
    v["included"] = any(v["key"] in s["files"] for s in scenarios)

(OUT / "scenarios_index.json").write_text(json.dumps(dict(
    source="SRP-SID DSS (Sindh Irrigation Dept) results:* via WMS",
    portal="https://portal.srpsid-dss.gos.pk/",
    project="https://srpsid-dss.gos.pk/",
    variables=VARIABLES,
    bbox=[WEST, SOUTH, EAST, NORTH], width=W_PX, height=H_PX,
    note=("Model AOI covers the Indus right bank ~26.15-29.24N; areas outside it are "
          "transparent and are not modelled, not dry."),
    scenarios=scenarios), indent=1))
print(f"\n{len(scenarios)} runs, {got}/{n_runs*len(VARIABLES)} rasters -> scenarios_index.json")
if missing:
    print("missing:", ", ".join(missing[:12]), "..." if len(missing) > 12 else "")
