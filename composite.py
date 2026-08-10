"""Build the 2022 Sindh flood composite from UNOSAT satellite flood extents.

Three problems this pipeline exists to solve, all found by inspecting the data:

1. SWATH COVERAGE. Any single date is one satellite swath covering ~20% of the map
   frame. Rendered alone it implies "dry" everywhere the sensor did not look. So we
   walk observations in date order and keep a running best-known state, updating only
   the pixels each pass actually observed, and we track a `known` mask so unobserved
   ground is never drawn as dry.

2. THE LAYERS ARE NOT ALL THE SAME KIND OF THING.
   - Water_Class 1 is confirmed flood water. Class 2 is "Flood-Affected Land /
     Possible Flood water" -- not confirmed water; three VIIRS layers use it and
     including them added tens of thousands of spurious km2. Class 99 is a cumulative
     maximum. Only class 1 enters the timeline.
   - Windows range from 0 to 61 days. A multi-week maximum is not a snapshot, so
     anything longer than MAX_WINDOW becomes a separate peak-extent envelope.
   - The archive mixes events and countries: South Sudan, Guyana, Nicaragua, and the
     2010 Pakistan flood (whose Area_ha field is corrupt at 1.6e10 km2) all appear
     under the same product names. Filtered by date window and by frame overlap.

3. THE SENSORS DISAGREE BY ~3x, AND THAT IS NOT AN ERROR. VIIRS at 375 m flags a whole
   pixel as flooded if part of it is wet, so it reads ~48,000 km2 in-frame where
   Sentinel-1 at 10 m reads ~15,000. Blending them into one running array produced
   step changes of +14,000 km2 that were pure sensor-switch artefact. So they are
   built as two independent tracks:
       viirs   - 375 m, weekly cadence, full-frame: the animation backbone
       highres - S1/S2/LS8 10-30 m, sparse and swath-limited: verified snapshots
   Each is internally consistent, so the shape of each curve is meaningful. They are
   cross-compared rather than merged.

Calibration, both checked against UNOSAT's own published areas:
  * OGR_ORGANIZE_POLYGONS=ONLY_CCW -- default ring-nesting is O(n^2) (~8 min/layer);
    ESRI uses CW-outer/CCW-hole. Verified IoU=1.0000 on the 27-Aug layer.
  * all_touched=False at full DEM res, then block-averaged to fractional coverage.
    all_touched=True inflates area +58% (15,907 vs UNOSAT's published 10,062 km2 for
    27 Aug) because these polygons are extremely fragmented.

Frame encoding: uint8 (H, W, 3)
    ch0 coverage  flood fraction * 254
    ch1 known     255 where some sensor has observed this pixel, else 0
    ch2 age       days since that pixel was last observed, / MAX_AGE_DAYS * 254

Age matters because carrying a stale reading forward looks identical to a fresh one.
The 29-Nov Sindh+Balochistan product refreshes only part of the frame, so north of its
footprint the composite kept showing the 20-Nov state and the boundary rendered as a
hard rectangular seam. Exposing age lets the renderer fade stale ground out instead of
asserting it.
"""
import os
os.environ.setdefault("OGR_ORGANIZE_POLYGONS", "ONLY_CCW")

import hashlib, json, re, zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import geopandas as gpd
import pyogrio
import rasterio
from rasterio.features import rasterize

import warnings
warnings.filterwarnings("ignore")

EXTRACT = Path("unosat/extracted"); EXTRACT.mkdir(parents=True, exist_ok=True)
CACHE = Path("cache_rast"); CACHE.mkdir(exist_ok=True)
OUT = Path("composite"); OUT.mkdir(exist_ok=True)

BLOCK = 2
MAX_WINDOW = 10
STALE_DAYS = 12
MAX_AGE_DAYS = 45.0   # age at which a carried-forward reading is fully faded out
WATER_CLASS_KEEP = {1}
EVENT_START = datetime(2022, 6, 1).date()
EVENT_END = datetime(2023, 6, 30).date()

TRACKS = {"viirs":   {"VIIRS", "MODIS", "Terra"},
          "highres": {"S1", "S2", "LS8"}}
RES_M = {"S1": 10, "S2": 10, "LS8": 30, "VIIRS": 375, "MODIS": 250, "Terra": 250}

with rasterio.open("dem_sindh_z10.tif") as src:
    B, FT, FSHAPE = src.bounds, src.transform, src.shape
H, W = FSHAPE[0] // BLOCK, FSHAPE[1] // BLOCK
FH, FW = H * BLOCK, W * BLOCK
LAT_MID = (B.bottom + B.top) / 2
PX_KM2 = ((B.right - B.left) / W * 111.32 * np.cos(np.radians(LAT_MID))) * \
         ((B.top - B.bottom) / H * 110.54)
print(f"grid {W} x {H}   {PX_KM2:.3f} km2/px\n")

shrink = lambda a: a[:FH, :FW].reshape(H, BLOCK, W, BLOCK).mean(axis=(1, 3))


def sha(p, chunk=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while (b := f.read(chunk)):
            h.update(b)
    return h.hexdigest()[:16]


gdbs, seen = [], {}
zips = sorted(Path("unosat/gdbs").glob("*.zip"))
for z in zips:
    d = sha(z)
    if d in seen:
        continue
    seen[d] = z.name
    dest = EXTRACT / d
    if not dest.exists():
        try:
            zipfile.ZipFile(z).extractall(dest)
        except zipfile.BadZipFile:
            print(f"  BAD ZIP {z.name}"); continue
    gdbs += list(dest.rglob("*.gdb"))
print(f"{len(gdbs)} unique geodatabases from {len(zips)} downloads")

PAT = re.compile(
    r"^(?P<sensor>[A-Za-z0-9_]+?)_(?P<d1>\d{8})(?:_(?P<d2>\d{8}))?_"
    r"(?P<kind>FloodExtent|AnalysisExtent|CloudObstruction)_(?P<region>.+)$")

obs = defaultdict(dict)
for g in gdbs:
    for row in pyogrio.list_layers(str(g)):
        name, geom = row[0], row[1]
        if geom is None or not (m := PAT.match(name)):
            continue
        d1 = datetime.strptime(m["d1"], "%Y%m%d").date()
        d2 = datetime.strptime(m["d2"], "%Y%m%d").date() if m["d2"] else d1
        if d2 < d1:
            try:
                d2 = d2.replace(year=d1.year)
            except ValueError:
                pass
            d2 = max(d1, d2)
        sensor = m["sensor"].split("_")[0]
        obs[(d2, d1, sensor, m["region"])][m["kind"]] = (str(g), name)


def rast(gdb, layer, classes=None):
    tag = "all" if classes is None else "c" + "".join(map(str, sorted(classes)))
    c = CACHE / f"{Path(gdb).parent.name}__{layer}__{W}x{H}_{tag}.npy"
    if c.exists():
        return np.load(c)
    g = gpd.read_file(gdb, layer=layer)
    if g.crs is not None:
        g = g.to_crs("EPSG:4326")
    if classes is not None and "Water_Class" in g.columns:
        g = g[g["Water_Class"].isin(classes)]
    geoms = [x for x in g.geometry if x is not None and not x.is_empty]
    a = (shrink(rasterize(((x, 1) for x in geoms), out_shape=(FH, FW), transform=FT,
                          fill=0, dtype="uint8", all_touched=False)).astype(np.float32)
         if geoms else np.zeros((H, W), np.float32))
    np.save(c, a)
    return a


# VIIRS/MODIS image the whole globe daily, so a weekly national composite really did
# observe everything in frame. Most of those layers ship no AnalysisExtent, and reading
# that absence as "only where water was found" leaves dry ground never refreshed --
# which is what drove staleness to 75-97% and understated the recession. Narrow-swath
# sensors get the conservative treatment instead, because for them a missing footprint
# genuinely does mean we cannot tell what was looked at.
WIDE_SWATH = {"VIIRS", "MODIS", "Terra"}


def load(k):
    d2, d1, sensor, region = k
    parts = obs[k]
    if "FloodExtent" not in parts:
        return None
    flood = rast(*parts["FloodExtent"], classes=WATER_CLASS_KEEP)
    if not flood.any():
        return None
    if "AnalysisExtent" in parts:
        seen_px = rast(*parts["AnalysisExtent"]) > 0.5
    elif sensor in WIDE_SWATH:
        seen_px = np.ones((H, W), bool)
    else:
        seen_px = flood > 0
    if "CloudObstruction" in parts:
        seen_px &= rast(*parts["CloudObstruction"]) <= 0.5
    return (flood, seen_px) if seen_px.any() else None


keys = [k for k in sorted(obs) if EVENT_START <= k[0] <= EVENT_END]
timeline = [k for k in keys if (k[0] - k[1]).days <= MAX_WINDOW]
envelopes = [k for k in keys if (k[0] - k[1]).days > MAX_WINDOW]


def build(track, sensors):
    tdir = OUT / track; tdir.mkdir(exist_ok=True)
    for old in tdir.glob("*.npy"):
        old.unlink()
    cov = np.zeros((H, W), np.float32)
    known = np.zeros((H, W), bool)
    age = np.full((H, W), np.datetime64("2000-01-01", "D"))
    frames, cache = [], {}
    print(f"\n--- {track} track ({', '.join(sorted(sensors))}) ---")
    for k in [x for x in timeline if x[2] in sensors]:
        d2, d1, sensor, region = k
        got = load(k)
        if got is None:
            continue
        flood, seen_px = got
        if not (seen_px & np.isfinite(flood)).any():
            continue
        cov[seen_px] = flood[seen_px]
        known |= seen_px
        age[seen_px] = np.datetime64(d2)
        cache[d2] = (cov.copy(), seen_px.copy())
        rec = dict(index=len(frames), date=d2.isoformat(), start=d1.isoformat(),
                   sensor=sensor, res_m=RES_M.get(sensor), region=region,
                   window_days=(d2 - d1).days,
                   flood_km2=round(float(cov.sum()) * PX_KM2, 1),
                   this_pass_km2=round(float(flood[seen_px].sum()) * PX_KM2, 1),
                   footprint_km2=round(int(seen_px.sum()) * PX_KM2, 1),
                   known_pct=round(100 * float(known.mean()), 1))
        frames.append(rec)
        age_days = (np.datetime64(d2) - age).astype("timedelta64[D]").astype(int)
        out = np.zeros((H, W, 3), np.uint8)
        out[..., 0] = np.round(cov * 254)
        out[..., 1] = np.where(known, 255, 0)
        out[..., 2] = np.round(np.clip(age_days / MAX_AGE_DAYS, 0, 1) * 254)
        np.save(tdir / f"frame_{rec['index']:03d}.npy", out)
        rec["stale_pct"] = round(100 * float(
            ((age_days > STALE_DAYS) & known).sum()) / max(int(known.sum()), 1), 1)
        print(f"  {d2}  {sensor:<6} {region[:22]:<22} "
              f"cum {rec['flood_km2']:>8,.0f} | pass {rec['this_pass_km2']:>8,.0f} "
              f"| known {rec['known_pct']:>5.1f}%")
    return frames, cache


viirs_frames, viirs_cache = build("viirs", TRACKS["viirs"])
hr_frames, hr_cache = build("highres", TRACKS["highres"])

# ── cross-validate: compare the two tracks inside each high-res footprint ────
print("\n--- cross-validation (inside each high-res footprint) ---")
checks = []
for k in [x for x in timeline if x[2] in TRACKS["highres"]]:
    d2 = k[0]
    got = load(k)
    if got is None:
        continue
    hr_flood, fp = got
    near = [d for d in viirs_cache if abs((d - d2).days) <= 7]
    if not near:
        continue
    vd = min(near, key=lambda d: abs((d - d2).days))
    v_cov = viirs_cache[vd][0]
    a_hr = float(hr_flood[fp].sum()) * PX_KM2
    a_v = float(v_cov[fp].sum()) * PX_KM2
    hb, vb = hr_flood[fp] > 0.5, v_cov[fp] > 0.5
    iou = float((hb & vb).sum()) / max(float((hb | vb).sum()), 1)
    checks.append(dict(highres_date=d2.isoformat(), sensor=k[2],
                       viirs_date=vd.isoformat(), lag_days=(vd - d2).days,
                       highres_km2=round(a_hr, 1), viirs_km2=round(a_v, 1),
                       ratio=round(a_v / a_hr, 2) if a_hr else None,
                       iou=round(iou, 3)))
    print(f"  {d2} {k[2]:<4} vs VIIRS {vd}  "
          f"{a_hr:>8,.0f} vs {a_v:>8,.0f} km2  ratio {a_v/max(a_hr,1e-9):>4.2f}  IoU {iou:.3f}")

# ── peak-extent envelope ────────────────────────────────────────────────────
env = np.zeros((H, W), np.float32)
env_src = []
for k in envelopes:
    got = load(k)
    if got:
        env = np.maximum(env, got[0])
        env_src.append(f"{k[2]} {k[1]}..{k[0]} {k[3]}")
if env.any():
    np.save(OUT / "envelope_max.npy", np.round(env * 254).astype(np.uint8))
    print(f"\nenvelope: {float(env.sum())*PX_KM2:,.0f} km2 from {len(env_src)} layers")
    for s in env_src:
        print("   ", s)

(OUT / "manifest.json").write_text(json.dumps(dict(
    width=W, height=H, bounds=[B.left, B.bottom, B.right, B.top], px_km2=PX_KM2,
    block=BLOCK, max_window_days=MAX_WINDOW, water_class_keep=sorted(WATER_CLASS_KEEP),
    res_m=RES_M, max_age_days=MAX_AGE_DAYS, stale_days=STALE_DAYS,
    tracks=dict(viirs=viirs_frames, highres=hr_frames),
    cross_validation=checks,
    envelope_km2=round(float(env.sum()) * PX_KM2, 1), envelope_sources=env_src), indent=1))

print(f"\nviirs {len(viirs_frames)} frames | highres {len(hr_frames)} frames -> {OUT}/")
