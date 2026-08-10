"""Mosaic AWS Terrarium SRTM tiles over the Sindh / SE-Balochistan map extent.

Terrarium encoding: elevation_m = (R * 256 + G + B / 256) - 32768
Output: a float32 GeoTIFF in EPSG:4326 ready for meshing + hypsometric texturing.
"""
import io, math, concurrent.futures as cf
import numpy as np, requests
from PIL import Image
import rasterio
from rasterio.transform import from_bounds

# Extent read off the reference map: Kirthar/Sulaiman front east to the Indus
# left bank, Dera Bugti/Rajanpur in the north down to Kotri.
W, S, E, N = 66.3, 24.6, 70.6, 29.6
Z = 10

def deg2tile(lon, lat, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y

def tile2deg(x, y, z):
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat

x0f, y0f = deg2tile(W, N, Z)
x1f, y1f = deg2tile(E, S, Z)
X0, Y0, X1, Y1 = int(x0f), int(y0f), int(x1f), int(y1f)
nx, ny = X1 - X0 + 1, Y1 - Y0 + 1
print(f"z{Z}: {nx} x {ny} = {nx*ny} tiles -> {nx*256} x {ny*256} px")

URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
sess = requests.Session()

def grab(args):
    tx, ty = args
    for attempt in range(4):
        try:
            r = sess.get(URL.format(z=Z, x=tx, y=ty), timeout=30)
            if r.status_code == 404:
                return tx, ty, np.zeros((256, 256), np.float32)
            r.raise_for_status()
            a = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"), np.float32)
            return tx, ty, (a[..., 0] * 256.0 + a[..., 1] + a[..., 2] / 256.0) - 32768.0
        except Exception:
            if attempt == 3:
                raise
    raise RuntimeError

mosaic = np.zeros((ny * 256, nx * 256), np.float32)
jobs = [(tx, ty) for ty in range(Y0, Y1 + 1) for tx in range(X0, X1 + 1)]
done = 0
with cf.ThreadPoolExecutor(16) as ex:
    for tx, ty, arr in ex.map(grab, jobs):
        r, c = (ty - Y0) * 256, (tx - X0) * 256
        mosaic[r:r + 256, c:c + 256] = arr
        done += 1
        if done % 40 == 0:
            print(f"  {done}/{len(jobs)}")

# Exact bounds of the assembled tile grid (Web-Mercator tile edges -> lon/lat)
wl, nl = tile2deg(X0, Y0, Z)
el, sl = tile2deg(X1 + 1, Y1 + 1, Z)
print(f"bounds  W={wl:.4f} S={sl:.4f} E={el:.4f} N={nl:.4f}")
print(f"elev    min={mosaic.min():.0f} m  max={mosaic.max():.0f} m  mean={mosaic.mean():.0f} m")

# Note: rows are equally spaced in Mercator y, not latitude. Written here with a
# plain lon/lat transform for inspection; the mesh builder reprojects properly.
prof = dict(driver="GTiff", height=mosaic.shape[0], width=mosaic.shape[1], count=1,
            dtype="float32", crs="EPSG:4326",
            transform=from_bounds(wl, sl, el, nl, mosaic.shape[1], mosaic.shape[0]),
            compress="deflate", predictor=2, tiled=True)
with rasterio.open("dem_sindh_z10.tif", "w", **prof) as dst:
    dst.write(mosaic, 1)
print("wrote dem_sindh_z10.tif")
