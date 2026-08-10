"""Hypsometric + hillshade render of the DEM, styled to match the reference map's
green -> yellow -> orange -> red-brown -> grey ramp. Used to sanity-check that the
terrain base reproduces the CARTHAGO figure before any 3-D work.
"""
import numpy as np, rasterio
from matplotlib.colors import LinearSegmentedColormap
from PIL import Image

with rasterio.open("dem_sindh_z10.tif") as src:
    dem = src.read(1).astype(np.float32)
    bounds = src.bounds

dem = np.where(dem < -100, 0.0, dem)  # clamp nodata / bathymetry artefacts

# Ramp keyed on elevation, matching the reference figure's breaks.
stops = [
    (0,    "#8fbf7a"), (60,   "#a9cc84"), (120,  "#c8d98c"),
    (250,  "#e3d98f"), (450,  "#e8c274"), (700,  "#dd9a55"),
    (1000, "#c9743f"), (1400, "#a85434"), (1900, "#8a4a3c"),
    (2400, "#8e7f78"), (3300, "#d8d5d0"),
]
vmax = 3300.0
cmap = LinearSegmentedColormap.from_list(
    "sindh", [(e / vmax, c) for e, c in stops])

norm = np.clip(dem / vmax, 0, 1)
rgb = cmap(norm)[..., :3]

# Hillshade, 315 deg azimuth / 45 deg altitude, matching the figure's lighting.
# Row spacing varies with latitude in a Mercator tile mosaic; at this scale a
# mean spacing is close enough for a shading check.
lat_mid = (bounds.bottom + bounds.top) / 2
px_x = (bounds.right - bounds.left) / dem.shape[1] * 111320 * np.cos(np.radians(lat_mid))
px_y = (bounds.top - bounds.bottom) / dem.shape[0] * 110540
dy, dx = np.gradient(dem, px_y, px_x)
Z_EX = 2.0
slope = np.arctan(Z_EX * np.hypot(dx, dy))
aspect = np.arctan2(-dx, dy)
az, alt = np.radians(315.0), np.radians(45.0)
hs = (np.sin(alt) * np.cos(slope) +
      np.cos(alt) * np.sin(slope) * np.cos(az - aspect))
hs = np.clip(hs, 0, 1)

shaded = np.clip(rgb * (0.55 + 0.75 * hs[..., None]), 0, 1)
img = Image.fromarray((shaded * 255).astype(np.uint8))
img.thumbnail((1400, 1400), Image.LANCZOS)
img.save("dem_preview.png")
print("wrote dem_preview.png", img.size)
print(f"elev range after clamp: {dem.min():.0f} .. {dem.max():.0f} m")
