"""Administrative boundaries for the AOI, from the Pakistan COD-AB set on HDX.

COD-AB is OCHA's common operational dataset, so these are the same boundaries other
agencies publish against — worth preferring over GADM or OSM for anything that has to
line up with official reporting.

Layers written to web/assets/:
  provinces.geojson  ADM1, clipped to the AOI, with Sindh flagged
  districts.geojson  ADM2, clipped to the AOI
"""
import sys, zipfile
from pathlib import Path

import geopandas as gpd
import warnings
warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from aoi import WEST, SOUTH, EAST, NORTH, describe

OUT = ROOT / "web/assets"
CACHE = ROOT / "boundaries_cache"
print(describe())

gdb = str(next((CACHE / "ab").rglob("*.gdb")))


def dump(gdf, name, cols, tol=0.0015):
    if gdf.empty:
        print(f"  {name}: EMPTY, skipped"); return
    keep = [c for c in cols if c in gdf.columns]
    g = gdf[keep + ["geometry"]].copy()
    # Simplify for the browser; 0.0015 deg is ~150 m, far below what reads at this scale
    g["geometry"] = g.geometry.simplify(tol, preserve_topology=True)
    p = OUT / f"{name}.geojson"
    g.to_file(p, driver="GeoJSON")
    print(f"  {name}: {len(g)} features -> {p.stat().st_size/1024:.0f} KB")
    return g


def clipped(layer):
    g = gpd.read_file(gdb, layer=layer).to_crs("EPSG:4326")
    # .cx is a bounding-box filter, which keeps any province merely touching the AOI;
    # that is what we want, so the boundary line runs to the frame edge rather than
    # stopping where a polygon centroid happens to fall.
    return g.cx[WEST:EAST, SOUTH:NORTH]


prov = clipped("pak_admin1")
prov["is_sindh"] = prov["adm1_name"].str.contains("sindh", case=False, na=False)
print("provinces in AOI:", ", ".join(sorted(prov["adm1_name"].dropna())))
dump(prov, "provinces", ["adm1_name", "adm1_pcode", "is_sindh"])

dist = clipped("pak_admin2")
print(f"districts in AOI: {len(dist)}")
sindh_d = dist[dist["adm1_name"].str.contains("sindh", case=False, na=False)]
print("  of which Sindh:", len(sindh_d))
dump(dist, "districts", ["adm2_name", "adm2_pcode", "adm1_name"])

print("\nSindh districts in AOI:",
      ", ".join(sorted(sindh_d["adm2_name"].dropna())))
