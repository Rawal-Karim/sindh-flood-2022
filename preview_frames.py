"""Contact sheet of the composite, plus the peak-extent envelope rendered on its own
for direct comparison against the reference CARTHAGO figure.
"""
import json, sys
from pathlib import Path
import numpy as np, rasterio
from matplotlib.colors import LinearSegmentedColormap
from PIL import Image, ImageDraw

OUT = Path("composite")
meta = json.loads((OUT / "manifest.json").read_text())
W, H, BLK = meta["width"], meta["height"], meta["block"]

with rasterio.open("dem_sindh_z10.tif") as src:
    dem = np.where(src.read(1) < -100, 0.0, src.read(1)).astype(np.float32)
    B = src.bounds
dem = dem[:H*BLK, :W*BLK].reshape(H, BLK, W, BLK).mean(axis=(1, 3))

stops = [(0,"#8fbf7a"),(60,"#a9cc84"),(120,"#c8d98c"),(250,"#e3d98f"),
         (450,"#e8c274"),(700,"#dd9a55"),(1000,"#c9743f"),(1400,"#a85434"),
         (1900,"#8a4a3c"),(2400,"#8e7f78"),(3300,"#d8d5d0")]
vmax = 3300.0
cmap = LinearSegmentedColormap.from_list("s", [(e/vmax, c) for e, c in stops])
lat_mid = (B.bottom + B.top)/2
dy, dx = np.gradient(dem, (B.top-B.bottom)/H*110540,
                     (B.right-B.left)/W*111320*np.cos(np.radians(lat_mid)))
slope, aspect = np.arctan(2.0*np.hypot(dx, dy)), np.arctan2(-dx, dy)
az, alt = np.radians(315.0), np.radians(45.0)
hs = np.clip(np.sin(alt)*np.cos(slope)+np.cos(alt)*np.sin(slope)*np.cos(az-aspect), 0, 1)
BASE = np.clip(cmap(np.clip(dem/vmax, 0, 1))[..., :3]*(0.55+0.75*hs[..., None]), 0, 1)
WATER = np.array([0.10, 0.47, 0.85])


def render(cov, unknown=None):
    img = BASE.copy()
    if unknown is not None and unknown.any():
        g = img.mean(axis=2, keepdims=True)
        img[unknown] = img[unknown]*0.30 + g[unknown]*0.70
    a = cov[..., None]
    return np.clip(img*(1-a) + WATER*a, 0, 1)


def to_img(arr, size=None):
    im = Image.fromarray((arr*255).astype(np.uint8))
    return im.resize(size, Image.LANCZOS) if size else im


# ── contact sheet of the VIIRS animation backbone ───────────────────────────
frames = meta["tracks"]["viirs"]
TH = 300; TW = int(TH * W / H)
tiles = []
for f in frames:
    a = np.load(OUT / "viirs" / f"frame_{f['index']:03d}.npy")
    unk = a == 255
    cov = np.where(unk, 0, a).astype(np.float32)/254.0
    t = to_img(render(cov, unk), (TW, TH))
    d = ImageDraw.Draw(t)
    d.rectangle([0, 0, TW-1, 17], fill=(10, 22, 45))
    d.text((4, 4), f"{f['date']}  {f['sensor']}", fill=(195, 225, 252))
    d.rectangle([0, TH-15, TW-1, TH-1], fill=(10, 22, 45))
    d.text((4, TH-13), f"{f['flood_km2']:,.0f} km2", fill=(150, 200, 240))
    tiles.append(t)

cols = 6
rows = (len(tiles)+cols-1)//cols
sheet = Image.new("RGB", (cols*TW, rows*TH), (8, 16, 34))
for i, t in enumerate(tiles):
    sheet.paste(t, ((i % cols)*TW, (i//cols)*TH))
sheet.save("composite_contactsheet.png")
print("wrote composite_contactsheet.png", sheet.size)

# ── peak-extent envelope, full size, for comparison with the reference map ──
env = np.load(OUT / "envelope_max.npy").astype(np.float32)/254.0
im = to_img(render(env))
im.thumbnail((1500, 1500), Image.LANCZOS)
im.save("envelope_vs_reference.png")
print("wrote envelope_vs_reference.png", im.size, f"({meta['envelope_km2']:,.0f} km2)")

print("\nVIIRS track (375 m, weekly, full frame):")
for f in frames:
    print(f"  {f['date']}  {f['sensor']:<6} {f['flood_km2']:>9,.0f} km2")
print("\nHigh-res track (10-30 m, swath-limited):")
for f in meta["tracks"]["highres"]:
    print(f"  {f['date']}  {f['sensor']:<6} {f['flood_km2']:>9,.0f} km2 "
          f"(frame coverage {f['known_pct']}%)")
print("\nCross-validation:")
for c in meta["cross_validation"]:
    print(f"  {c['highres_date']} {c['sensor']:<4} vs VIIRS {c['viirs_date']}: "
          f"ratio {c['ratio']}  IoU {c['iou']}")
