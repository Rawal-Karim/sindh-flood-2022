"""Derive the hill-torrent network from the DEM and bake a wave arrival time.

This replaces hand-drawn Nai polylines with channels traced from topography:

  1. Priority-flood depression fill so water never stalls in a sink.
  2. D8 flow direction to the steepest downslope neighbour.
  3. Flow accumulation in descending-elevation order (each cell drains to exactly one
     neighbour, so one pass over sorted cells is enough).
  4. Kinematic-wave arrival time propagated downstream: celerity rises with the square
     root of slope, so torrents rip down the Kirthar and Sulaiman ravines and then slow
     abruptly on the Kachho plain -- which is what makes the animation read correctly.

Outputs arrival.png:  R = arrival time (normalised), G = channel strength from
log flow accumulation, B = slope-derived speed, plus arrival_meta.json for the scale.

The channel geometry is real. The timing is a model, not an observation.
"""
import json
import heapq
import re
from pathlib import Path

import numpy as np
import rasterio
from numba import njit
from PIL import Image

ROOT = Path(__file__).parent

with rasterio.open(ROOT / "dem_sindh_z10.tif") as src:
    dem_full = np.maximum(src.read(1).astype(np.float32), 0.0)
    B = src.bounds
BLOCK = 2
GH, GW = dem_full.shape[0] // BLOCK, dem_full.shape[1] // BLOCK
dem = dem_full[:GH*BLOCK, :GW*BLOCK].reshape(GH, BLOCK, GW, BLOCK).mean(axis=(1, 3))
dem = np.ascontiguousarray(dem.astype(np.float32))
print(f"grid {GW} x {GH}")

LAT_MID = (B.bottom + B.top) / 2
DX = (B.right - B.left) / GW * 111320 * np.cos(np.radians(LAT_MID))   # metres
DY = (B.top - B.bottom) / GH * 110540
print(f"cell {DX:.0f} x {DY:.0f} m")


EPS = 1e-3   # metres of enforced gradient across filled flats


def fill_depressions(z):
    """Priority-flood *with epsilon*. A plain fill leaves perfectly flat surfaces, and
    on a flat D8 finds no downslope neighbour at all -- flow terminates on the spot and
    accumulation collapses (measured: max 324 km2 across the whole frame). Adding a
    monotone epsilon as the flood advances guarantees every filled cell has a downhill
    path to its outlet."""
    h, w = z.shape
    out = np.full_like(z, np.inf)
    closed = np.zeros((h, w), bool)
    pq = []
    for i in range(h):
        for j in (0, w - 1):
            heapq.heappush(pq, (z[i, j], i, j)); closed[i, j] = True; out[i, j] = z[i, j]
    for j in range(w):
        for i in (0, h - 1):
            if not closed[i, j]:
                heapq.heappush(pq, (z[i, j], i, j)); closed[i, j] = True; out[i, j] = z[i, j]
    nb = ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1))
    while pq:
        e, i, j = heapq.heappop(pq)
        for di, dj in nb:
            ni, nj = i + di, j + dj
            if 0 <= ni < h and 0 <= nj < w and not closed[ni, nj]:
                closed[ni, nj] = True
                out[ni, nj] = max(z[ni, nj], e + EPS)
                heapq.heappush(pq, (out[ni, nj], ni, nj))
    return out


# The priority flood is pure-Python heapq over ~4.6M cells and dominates runtime, so
# cache it against the grid size — tuning the catchment thresholds below should not
# cost a full refill each time.
FILL_CACHE = ROOT / f"_zfill_{GW}x{GH}.npy"
if FILL_CACHE.exists():
    zf = np.load(FILL_CACHE)
    print(f"filled DEM from cache {FILL_CACHE.name}")
else:
    print("filling depressions ...")
    zf = fill_depressions(dem)
    np.save(FILL_CACHE, zf)
print(f"  raised {(zf > dem + 1e-3).sum():,} cells, max {float((zf-dem).max()):.1f} m")


@njit(cache=True)
def d8(z, dx, dy):
    h, w = z.shape
    rec = np.full((h, w), -1, np.int64)      # receiver cell index, -1 = outlet
    slope = np.zeros((h, w), np.float32)
    di = (-1, 1, 0, 0, -1, -1, 1, 1)
    dj = (0, 0, -1, 1, -1, 1, -1, 1)
    for i in range(h):
        for j in range(w):
            best, bi, bj = 0.0, -1, -1
            for k in range(8):
                ni, nj = i + di[k], j + dj[k]
                if ni < 0 or ni >= h or nj < 0 or nj >= w:
                    continue
                d = np.sqrt((di[k] * dy) ** 2 + (dj[k] * dx) ** 2)
                s = (z[i, j] - z[ni, nj]) / d
                if s > best:
                    best, bi, bj = s, ni, nj
            if bi >= 0:
                rec[i, j] = bi * w + bj
                slope[i, j] = best
    return rec, slope


print("d8 flow directions ...")
rec, slope = d8(zf, DX, DY)


@njit(cache=True)
def accumulate(rec, order):
    n = rec.size
    acc = np.ones(n, np.float32)
    for t in range(n):                        # descending elevation
        c = order[t]
        r = rec[c]
        if r >= 0:
            acc[r] += acc[c]
    return acc


order = np.argsort(-zf.ravel()).astype(np.int64)
print("flow accumulation ...")
acc = accumulate(rec.ravel(), order).reshape(GH, GW)
print(f"  max accumulation {acc.max():,.0f} cells "
      f"({acc.max()*DX*DY/1e6:,.0f} km2)")


@njit(cache=True)
def arrival(rec, order, slope, dx, dy, w, v_min, v_max, s_ref):
    """Kinematic wave: v = clamp(v_max * sqrt(s/s_ref), v_min, v_max). Propagated in
    descending-elevation order so each cell is resolved before its receiver."""
    n = rec.size
    t = np.zeros(n, np.float32)
    for k in range(n):
        c = order[k]
        r = rec[c]
        if r < 0:
            continue
        ci, cj = c // w, c % w
        ri, rj = r // w, r % w
        d = np.sqrt(((ri - ci) * dy) ** 2 + ((rj - cj) * dx) ** 2)
        s = slope[ci, cj]
        v = v_max * np.sqrt(s / s_ref)
        if v < v_min:
            v = v_min
        elif v > v_max:
            v = v_max
        cand = t[c] + d / v
        if cand > t[r]:                       # slowest inflow sets the visible front
            t[r] = cand
    return t


print("arrival times ...")
t_arr = arrival(rec.ravel(), order, slope, DX, DY, GW,
                v_min=0.35, v_max=4.5, s_ref=0.02).reshape(GH, GW)

# Channels only: below the accumulation threshold is hillslope, not a Nai.
#
# Restricted to the mountain front and upper piedmont. On the irrigated plain the
# real drainage is canals and bunds a few metres deep, far below what a 270 m DEM
# resolves, so epsilon-fill routing there invents a dense dendritic network that
# looks convincing and means nothing. Above the front the terrain is genuinely
# dissected and D8 traces the actual Kirthar and Sulaiman ravines. The plain is left
# to the observed flood data, which is what measured it.
ACC_MIN = 120
RELIEF_MIN_M = 80
# Local relief over ~2.4 km, not absolute elevation: the Thar dune field on the eastern
# margin also sits above 70 m, and routing across dunes produces channel-shaped noise.
# Dissected mountain terrain carries >80 m of local relief; dune fields carry 20-40.
from scipy.ndimage import maximum_filter, minimum_filter
relief = maximum_filter(dem, 9) - minimum_filter(dem, 9)
chan = (acc >= ACC_MIN) & (relief > RELIEF_MIN_M)
print(f"  channel cells {chan.sum():,}  ({100*chan.mean():.2f}% of frame); "
      f"discarded {int(((acc >= ACC_MIN) & (dem <= RELIEF_MIN_M)).sum()):,} plain cells")

t_vis = np.where(chan, t_arr, np.nan)
tmax = float(np.nanpercentile(t_vis, 95.0))
print(f"  arrival p50 {np.nanpercentile(t_vis,50)/3600:.1f} h  "
      f"p95 {tmax/3600:.1f} h  max {np.nanmax(t_vis)/3600:.1f} h")

# Strength is scaled against the largest channel *inside the mask*, not the frame-wide
# maximum, which sits on the plain and would flatten every mountain Nai to near zero.
acc_max_chan = float(acc[chan].max())
strength = np.clip((np.log10(np.maximum(acc, 1)) - np.log10(ACC_MIN)) /
                   (np.log10(acc_max_chan) - np.log10(ACC_MIN)), 0, 1)
strength = np.where(chan, 0.24 + strength * 0.76, 0.0)

# Channels are one cell wide. Sampled onto a 512 x 670 mesh they fall between texels
# and alias away entirely, so dilate by one cell: strength takes the neighbourhood
# maximum and arrival the neighbourhood minimum, which widens each channel without
# letting the leading edge lag behind the true front.
from scipy.ndimage import grey_dilation
t_norm = np.clip(np.nan_to_num(t_vis, nan=np.inf) / tmax, 0, 1)
strength_d = grey_dilation(strength, size=3)
t_fill = np.where(chan, t_norm, 1.0)
t_d = -grey_dilation(-t_fill, size=3)          # dilation of the negative = erosion
t_d = np.where(strength_d > 0, t_d, 1.0)

out = np.zeros((GH, GW, 3), np.uint8)
out[..., 0] = np.clip(t_d, 0, 1) * 254
out[..., 1] = np.clip(strength_d, 0, 1) * 255
out[..., 2] = np.clip(slope / 0.06, 0, 1) * 254
print(f"  after dilation: {int((strength_d > 0).sum()):,} rendered cells "
      f"({100*(strength_d > 0).mean():.2f}% of frame)")
Image.fromarray(out, mode="RGB").save(ROOT / "web/assets/arrival.png", optimize=True)

(ROOT / "web/assets/arrival_meta.json").write_text(json.dumps(dict(
    width=GW, height=GH, acc_min=ACC_MIN, relief_min_m=RELIEF_MIN_M,
    t_max_seconds=tmax, cell_m=[DX, DY],
    channel_cells=int(chan.sum())), indent=1))
print("wrote web/assets/arrival.png + arrival_meta.json")


# ── sub-catchments ──────────────────────────────────────────────────────────
# Pour points are the Sindh Irrigation Department gauging stations (and the Nai Gaj
# dam axis), taken from the Department's own Nai definitions rather than a gazetteer.
# A gauge is the right outlet for a catchment: the area above it is exactly the
# drainage the gauge measures.
#
# Two earlier approaches failed and are worth not repeating. Deriving outlets from
# "where a channel leaves dissected terrain" gave one catchment of 47,502 km2, because
# the epsilon fill lays an artificial channel along the foot of the range that merges
# every Nai outlet into a single trunk. Anchoring to GeoNames names instead matched
# "nai" as a substring and picked up Pashto names in the far north (Kuchanai,
# Karkanai); with a whole-word match, Gaj and Naing failed to snap at all because the
# search was restricted to the dissected-channel mask and the gauges sit on valley
# floors where local relief falls below the threshold.
@njit(cache=True)
def label_upstream(rec, order_asc, pour_ids):
    n = rec.size
    lab = np.zeros(n, np.int32)
    for k in range(n):
        c = order_asc[k]
        if pour_ids[c] > 0:
            lab[c] = pour_ids[c]
        else:
            r = rec[c]
            if r >= 0:
                lab[c] = lab[r]
    return lab


NAIS = json.loads((ROOT / "nais_sid.json").read_text())
SNAP_KM = 5.0                      # gauge coordinates are approximate
ACC_FLOOR = 60                     # cells; ignore hillslope pixels when snapping

recg = rec.ravel()
accg = acc.ravel()
pour = np.zeros(GH * GW, np.int32)
pour_meta = {}
pid = 0
rad = int(round(SNAP_KM * 1000 / min(DX, DY)))

for nai in sorted(NAIS, key=lambda n: n["name"]):
    lon, lat = nai["lon"], nai["lat"]
    if not (B.left <= lon <= B.right and B.bottom <= lat <= B.top):
        print(f"  {nai['name']}: outside AOI, skipped")
        continue
    col = int((lon - B.left) / (B.right - B.left) * GW)
    row = int((B.top - lat) / (B.top - B.bottom) * GH)
    r0, r1 = max(row - rad, 0), min(row + rad + 1, GH)
    c0, c1 = max(col - rad, 0), min(col + rad + 1, GW)
    # Snap to the largest drainage nearby, NOT to the dissected-channel mask: gauges
    # sit on valley floors where local relief drops below the mountain threshold.
    win = acc[r0:r1, c0:c1]
    if win.max() < ACC_FLOOR:
        print(f"  {nai['name']}: no drainage within {SNAP_KM} km, skipped")
        continue
    # Nearest substantial channel, not the largest in the window. Taking the maximum
    # lets the snap jump onto a different, bigger river several km away, which both
    # inflates that catchment and starves the intended one.
    thresh = max(ACC_FLOOR, 0.25 * win.max())
    rr, cc = np.nonzero(win >= thresh)
    dist = np.hypot((rr + r0 - row) * DY, (cc + c0 - col) * DX)
    k = int(dist.argmin())
    dr, dc = int(rr[k]), int(cc[k])
    idx = (r0 + dr) * GW + (c0 + dc)
    pid += 1
    pour[idx] = pid
    pour_meta[pid] = dict(name=nai["name"], drains_to=nai.get("to"),
                          kind=nai.get("kind"), lon=lon, lat=lat,
                          snap_km=round(float(np.hypot((dr + r0 - row) * DY,
                                                       (dc + c0 - col) * DX)) / 1000, 2),
                          acc_cells=float(accg[idx]))

print(f"\n{pid} Nai pour points snapped (search radius {SNAP_KM} km = {rad} cells)")

order_asc = np.argsort(zf.ravel()).astype(np.int64)
labels = label_upstream(recg, order_asc, pour).reshape(GH, GW)

names = {L: m["name"] for L, m in pour_meta.items()}
areas = {int(L): float((labels == L).sum()) * DX * DY / 1e6 for L in range(1, pid + 1)}
print(f"{'Nai':<16}{'area km2':>11}  {'snap':>6}  drains to")
for L in sorted(areas, key=lambda k: -areas[k]):
    m = pour_meta[L]
    print(f"  {m['name']:<14}{areas[L]:>11,.0f}  {m['snap_km']:>5.1f}k  {m['drains_to']}")

np.save(ROOT / "subcatchments.npy", labels.astype(np.int32))
(ROOT / "web/assets/subcatchments_meta.json").write_text(json.dumps(dict(
    count=pid, snap_km=SNAP_KM, source="Sindh Irrigation Dept gauging stations",
    catchments=[dict(id=int(L), **pour_meta[L], area_km2=round(areas[L], 1))
                for L in range(1, pid + 1)]), indent=1))
print("wrote subcatchments.npy + subcatchments_meta.json")
