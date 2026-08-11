"""Bake the DEM, terrain texture and flood frames into assets the browser can load.

Outputs into web/assets/:
  terrain.bin       Float32 heights, MESH_H rows x MESH_W cols, row 0 = north edge.
                    Shipped raw rather than encoded in a PNG so there is no
                    quantisation or decode ambiguity in the elevation.
  terrain.jpg       hypsometric + hillshade drape, matching the reference figure.
  frames/*.png      per-frame water. R = flood coverage, G = observed mask,
                    B = age of the reading. Separate channels because a sentinel
                    value cannot survive bilinear filtering in a single channel.
  flow.png          downhill flow direction from the smoothed DEM, RG = (u, v) with
                    a 0.5 bias. Drives the direction the water surface drifts.
  noise.png         tileable value noise, advected along flow to animate the surface.
  scene.json        grid sizes, bounds, elevation range, frame lists, overlays.
"""
import json, shutil
from pathlib import Path

import numpy as np
import rasterio
from matplotlib.colors import LinearSegmentedColormap
from PIL import Image

ROOT = Path(__file__).parent
OUT = ROOT / "web/assets"
(OUT / "frames").mkdir(parents=True, exist_ok=True)

MESH_W = 512                       # vertex columns; rows derived from the grid aspect
TEX_MAX = 2048

manifest = json.loads((ROOT / "composite/manifest.json").read_text())
GW, GH = manifest["width"], manifest["height"]
BLK = manifest["block"]
# Keep vertex spacing near-isotropic: hardcoding rows meant an AOI change silently
# left the mesh sampling the grid at different densities in x and y.
MESH_H = int(round(MESH_W * GH / GW))

with rasterio.open(ROOT / "dem_sindh_z10.tif") as src:
    dem_full = src.read(1).astype(np.float32)
    B = src.bounds
# Terrarium tiles carry small negative values over water/nodata; nothing in this
# frame is genuinely below sea level, so clamp to 0 rather than dimple the plain.
dem_full = np.maximum(dem_full, 0.0)

# grid-resolution DEM, aligned to the flood rasters
dem_grid = dem_full[:GH*BLK, :GW*BLK].reshape(GH, BLK, GW, BLK).mean(axis=(1, 3))

# ── mesh heights ────────────────────────────────────────────────────────────
yi = np.linspace(0, GH - 1, MESH_H)
xi = np.linspace(0, GW - 1, MESH_W)
y0 = np.clip(yi.astype(int), 0, GH - 2); fy = (yi - y0)[:, None]
x0 = np.clip(xi.astype(int), 0, GW - 2); fx = (xi - x0)[None, :]
g = dem_grid
mesh = (g[np.ix_(y0, x0)] * (1-fy) * (1-fx) + g[np.ix_(y0+1, x0)] * fy * (1-fx) +
        g[np.ix_(y0, x0+1)] * (1-fy) * fx + g[np.ix_(y0+1, x0+1)] * fy * fx)
mesh = mesh.astype(np.float32)
mesh.tofile(OUT / "terrain.bin")
print(f"terrain.bin  {MESH_W}x{MESH_H}  {mesh.nbytes/1e6:.2f} MB  "
      f"elev {mesh.min():.0f}..{mesh.max():.0f} m")

# ── hypsometric + hillshade drape ───────────────────────────────────────────
stops = [(0,"#8fbf7a"),(60,"#a9cc84"),(120,"#c8d98c"),(250,"#e3d98f"),
         (450,"#e8c274"),(700,"#dd9a55"),(1000,"#c9743f"),(1400,"#a85434"),
         (1900,"#8a4a3c"),(2400,"#8e7f78"),(3300,"#d8d5d0")]
VMAX = 3300.0
cmap = LinearSegmentedColormap.from_list("sindh", [(e/VMAX, c) for e, c in stops])
rgb = cmap(np.clip(dem_grid / VMAX, 0, 1))[..., :3]

lat_mid = (B.bottom + B.top) / 2
px_x = (B.right - B.left) / GW * 111320 * np.cos(np.radians(lat_mid))
px_y = (B.top - B.bottom) / GH * 110540
dy, dx = np.gradient(dem_grid, px_y, px_x)
slope, aspect = np.arctan(2.0 * np.hypot(dx, dy)), np.arctan2(-dx, dy)
az, alt = np.radians(315.0), np.radians(45.0)
hs = np.clip(np.sin(alt)*np.cos(slope) + np.cos(alt)*np.sin(slope)*np.cos(az-aspect), 0, 1)
tex = np.clip(rgb * (0.55 + 0.75 * hs[..., None]), 0, 1)

img = Image.fromarray((tex * 255).astype(np.uint8))
img.thumbnail((TEX_MAX, TEX_MAX), Image.LANCZOS)
img.save(OUT / "terrain.jpg", quality=92, optimize=True)
print(f"terrain.jpg  {img.size}  {(OUT/'terrain.jpg').stat().st_size/1e6:.2f} MB")

# ── water frames: already (H, W, 3) = coverage / observed / age ─────────────
tracks = {}
for track in ("viirs", "highres"):
    lst = []
    for f in manifest["tracks"][track]:
        a = np.load(ROOT / "composite" / track / f"frame_{f['index']:03d}.npy")
        name = f"{track}_{f['index']:03d}.png"
        Image.fromarray(a, mode="RGB").save(OUT / "frames" / name, optimize=True)
        lst.append({**f, "file": f"frames/{name}"})
    tracks[track] = lst
    print(f"{track}: {len(lst)} frames")

# The envelope is a single-channel maximum; it has no age and is observed everywhere
# it has a value, so synthesise the other two channels.
env = np.load(ROOT / "composite/envelope_max.npy")
env3 = np.zeros(env.shape + (3,), np.uint8)
env3[..., 0] = env
env3[..., 1] = 255
Image.fromarray(env3, mode="RGB").save(OUT / "frames/envelope.png", optimize=True)


# ── flow field + noise, for the animated water surface ──────────────────────
def circ_blur(a, sigma):
    """Periodic Gaussian blur via FFT — keeps the noise tileable and the flow smooth."""
    ky = np.fft.fftfreq(a.shape[0])[:, None]
    kx = np.fft.fftfreq(a.shape[1])[None, :]
    g = np.exp(-2 * (np.pi * sigma) ** 2 * (kx ** 2 + ky ** 2))
    return np.real(np.fft.ifft2(np.fft.fft2(a) * g))


smooth = circ_blur(dem_grid, 6.0)
grow, gcol = np.gradient(smooth)           # rows increase southward
fu, fv = -gcol, grow                       # downhill; v increases north, so flip row
mag = np.hypot(fu, fv)
strong = mag > 1e-6
fu = np.where(strong, fu / np.maximum(mag, 1e-9), 0.0)
fv = np.where(strong, fv / np.maximum(mag, 1e-9), 0.0)
# On genuinely flat ground the gradient is noise, so blend toward the regional
# down-valley direction (the Indus runs broadly north to south).
w = np.clip(mag / np.percentile(mag[mag > 0], 60), 0, 1)[..., None]
flow = np.stack([fu, fv], -1) * w + np.array([0.0, -1.0]) * (1 - w)
flow /= np.maximum(np.linalg.norm(flow, axis=-1, keepdims=True), 1e-9)

fimg = np.zeros(dem_grid.shape + (3,), np.uint8)
fimg[..., 0] = np.round((flow[..., 0] * 0.5 + 0.5) * 255)
fimg[..., 1] = np.round((flow[..., 1] * 0.5 + 0.5) * 255)
Image.fromarray(fimg, mode="RGB").resize((512, 670), Image.LANCZOS).save(
    OUT / "flow.png", optimize=True)

rng = np.random.default_rng(7)
N = 256
noise = np.zeros((N, N))
amp = 1.0
for cells in (4, 8, 16, 32, 64):
    # sigma is a spatial std in PIXELS, so it must scale with the cell size.
    # (0.35 / cells gives sub-pixel blur, i.e. white noise, which minifies to flat grey.)
    layer = circ_blur(rng.random((N, N)), 0.35 * N / cells)
    layer = (layer - layer.min()) / max(np.ptp(layer), 1e-9)   # ndarray.ptp gone in numpy 2
    noise += amp * layer
    amp *= 0.55
noise = (noise - noise.min()) / np.ptp(noise)
Image.fromarray((noise * 255).astype(np.uint8), mode="L").save(OUT / "noise.png")
print(f"flow.png + noise.png written")

# ── areal masks: lakes and Nai sub-catchments ───────────────────────────────
# Baked as draped textures rather than drawn as outlines. THREE.LineBasicMaterial
# ignores `linewidth` on WebGL, so every outline renders 1 px wide -- and Manchar is
# only ~38 screen px across at the default camera, so a 1 px ring over blue flood
# water was invisible. A filled mask reads at any zoom.
from rasterio.features import rasterize as _rasterize
import geopandas as _gpd

lake_info = []
lp = OUT / "lakes.geojson"
if lp.exists():
    lg = _gpd.read_file(lp).to_crs("EPSG:4326")
    mask = _rasterize(((g, 255) for g in lg.geometry), out_shape=(GH, GW),
                      transform=rasterio.transform.from_bounds(
                          B.left, B.bottom, B.right, B.top, GW, GH),
                      fill=0, dtype="uint8", all_touched=True)
    Image.fromarray(mask, mode="L").save(OUT / "lakes_mask.png", optimize=True)
    lake_info = [dict(name=r["name"], area_km2=r["area_km2"]) for _, r in lg.iterrows()]
    print(f"lakes_mask.png: {int((mask > 0).sum()):,} cells")

sub_info = {}
sp = ROOT / "subcatchments.npy"
mp = OUT / "subcatchments_meta.json"
if sp.exists() and mp.exists():
    lab = np.load(sp)
    sub_info = json.loads(mp.read_text())
    # R = catchment id, G = 255 on a boundary cell so the shader can draw an edge
    edge = np.zeros_like(lab, bool)
    edge[:-1, :] |= lab[:-1, :] != lab[1:, :]
    edge[:, :-1] |= lab[:, :-1] != lab[:, 1:]
    rgb = np.zeros((GH, GW, 3), np.uint8)
    rgb[..., 0] = np.clip(lab, 0, 255).astype(np.uint8)
    rgb[..., 1] = np.where(edge & (lab > 0), 255, 0)
    Image.fromarray(rgb, mode="RGB").save(OUT / "subcatch.png", optimize=True)
    print(f"subcatch.png: {sub_info['count']} catchments, "
          f"{int((lab > 0).sum()):,} cells")

    # Also vectorise them. The raster drives the coloured fill, but picking, hover
    # popups and click-to-zoom all run off vector features, so a mask alone left the
    # Nai catchments as the only unpickable layer in the app.
    from rasterio.features import shapes as _shapes
    from shapely.geometry import shape as _shape, mapping as _mapping
    from shapely.ops import unary_union as _union
    tr = rasterio.transform.from_bounds(B.left, B.bottom, B.right, B.top, GW, GH)
    by_id = {}
    for geom, val in _shapes(lab.astype(np.int32), mask=(lab > 0), transform=tr):
        by_id.setdefault(int(val), []).append(_shape(geom))
    meta_by_id = {c["id"]: c for c in sub_info.get("catchments", [])}
    recs = []
    for cid, parts in sorted(by_id.items()):
        m = meta_by_id.get(cid, {})
        g = _union(parts).simplify(0.0015, preserve_topology=True)
        if g.is_empty:
            continue
        recs.append(dict(geometry=g, id=cid, name=m.get("name", f"catchment {cid}"),
                         area_km2=m.get("area_km2"), drains_to=m.get("drains_to")))
    if recs:
        _gpd.GeoDataFrame(recs, crs="EPSG:4326").to_file(
            OUT / "nai_subcatchments.geojson", driver="GeoJSON")
        print(f"nai_subcatchments.geojson: {len(recs)} polygons -> "
              f"{(OUT/'nai_subcatchments.geojson').stat().st_size/1024:.0f} KB")

# ── rainfall (CHIRPS) ───────────────────────────────────────────────────────
# Drives the monsoon act: per-day scalars set rain intensity, cloud cover and light,
# while the seasonal cumulative pattern darkens the ground where rain actually fell.
rain = {}
npz = ROOT / "chirps_frame.npz"
if npz.exists():
    z = np.load(npz)
    dates = [str(x) for x in z["dates"]]
    stack = z["rain"].astype(np.float32)          # (days, rows, cols) mm
    daily = [dict(date=d,
                  mean_mm=round(float(stack[i].mean()), 3),
                  p90_mm=round(float(np.percentile(stack[i], 90)), 2),
                  max_mm=round(float(stack[i].max()), 1))
             for i, d in enumerate(dates)]
    cum = stack.sum(axis=0)
    scale = float(np.percentile(cum, 99.5)) or 1.0
    wet = np.clip(cum / scale, 0, 1)
    Image.fromarray((wet * 255).astype(np.uint8), mode="L").resize(
        (256, 335), Image.LANCZOS).save(OUT / "wetness.png", optimize=True)
    # cumulative fraction of the season's rain that has fallen by each date
    tot = float(stack.sum()) or 1.0
    frac = np.cumsum(stack.sum(axis=(1, 2))) / tot
    for i, f in enumerate(frac):
        daily[i]["cum_frac"] = round(float(f), 4)
    rain = dict(file="wetness.png", season_cum_p995_mm=round(scale, 1),
                peak_daily_mean_mm=round(max(d["mean_mm"] for d in daily), 3),
                daily=daily)
    print(f"rain: {len(daily)} days, wettest cell {cum.max():.0f} mm season total")
else:
    print("rain: chirps_frame.npz not found, skipping")

# ── overlays ────────────────────────────────────────────────────────────────
overlays = {}
for name in ("bunds", "permanent_water", "breach_candidates_2022",
             "provinces", "districts", "cities", "lakes",
             "canals", "drains", "srp_subcatchments", "sindh_province",
             "nai_subcatchments"):
    src_p = ROOT / "weblayers" / f"{name}.geojson"
    if src_p.exists():
        shutil.copy(src_p, OUT / f"{name}.geojson")
        overlays[name] = f"{name}.geojson"
    elif (OUT / f"{name}.geojson").exists():
        overlays[name] = f"{name}.geojson"   # written directly by its own fetch script

scene = dict(
    mesh=dict(width=MESH_W, height=MESH_H),
    grid=dict(width=GW, height=GH),
    bounds=dict(west=B.left, south=B.bottom, east=B.right, north=B.top),
    elev=dict(min=float(mesh.min()), max=float(mesh.max())),
    px_km2=manifest["px_km2"],
    max_age_days=manifest["max_age_days"],
    stale_days=manifest["stale_days"],
    envelope_km2=manifest["envelope_km2"],
    cross_validation=manifest["cross_validation"],
    tracks=tracks,
    overlays=overlays,
    rain=rain,
    scenarios=(json.loads((OUT / "scenarios_index.json").read_text())
               if (OUT / "scenarios_index.json").exists() else {}),
    lakes=lake_info,
    subcatchments=sub_info,
    arrival=(json.loads((OUT / "arrival_meta.json").read_text())
             if (OUT / "arrival_meta.json").exists() else {}),
)
(OUT / "scene.json").write_text(json.dumps(scene, indent=1))
total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
print(f"\nscene.json written. assets total {total/1e6:.1f} MB")
