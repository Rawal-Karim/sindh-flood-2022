"""Fetch CHIRPS daily rainfall for the monsoon season and clip it to the map frame.

CHIRPS v2.0 daily, 0.05 deg, open HTTP with no authentication. Used to drive rain
intensity and spatial distribution in the animation's monsoon act, so the rain on
screen corresponds to rainfall that actually fell that day rather than being decoration.

Output: chirps_frame.npz  -- dates (YYYY-MM-DD) and a (days, rows, cols) float32 stack
of daily mm, clipped to the DEM frame.
"""
import concurrent.futures as cf
import gzip, io
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

ROOT = Path(__file__).parent
CACHE = ROOT / "chirps_cache"; CACHE.mkdir(exist_ok=True)

START, END = date(2022, 6, 1), date(2022, 9, 30)
URL = ("https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05/"
       "{y}/chirps-v2.0.{y}.{m:02d}.{d:02d}.tif.gz")

with rasterio.open(ROOT / "dem_sindh_z10.tif") as src:
    B = src.bounds

days = []
d = START
while d <= END:
    days.append(d); d += timedelta(days=1)
print(f"{len(days)} days, {START} .. {END}")


def grab(d):
    dst = CACHE / f"{d:%Y%m%d}.npy"
    if dst.exists():
        return d, np.load(dst)
    url = URL.format(y=d.year, m=d.month, d=d.day)
    import urllib.request
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                raw = gzip.decompress(r.read())
            break
        except Exception:
            if attempt == 3:
                print(f"  FAILED {d}"); return d, None
    with rasterio.MemoryFile(raw) as mem, mem.open() as ds:
        win = from_bounds(B.left, B.bottom, B.right, B.top, ds.transform)
        a = ds.read(1, window=win).astype(np.float32)
        a[a < 0] = 0.0                      # CHIRPS nodata is -9999
    np.save(dst, a)
    return d, a


out, shape = {}, None
with cf.ThreadPoolExecutor(8) as ex:
    for i, (d, a) in enumerate(ex.map(grab, days), 1):
        if a is None:
            continue
        out[d] = a
        shape = a.shape
        if i % 20 == 0:
            print(f"  {i}/{len(days)}")

got = sorted(out)
stack = np.stack([out[d] for d in got])
print(f"stack {stack.shape}  max daily {stack.max():.0f} mm  "
      f"season total {stack.sum(axis=0).max():.0f} mm at wettest cell")

np.savez_compressed(ROOT / "chirps_frame.npz",
                    dates=np.array([d.isoformat() for d in got]),
                    rain=stack,
                    bounds=np.array([B.left, B.bottom, B.right, B.top]))
print(f"wrote chirps_frame.npz  ({(ROOT/'chirps_frame.npz').stat().st_size/1e6:.1f} MB)")
