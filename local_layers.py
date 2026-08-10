"""Export the user's own GIS layers, clipped to the simulation frame, as compact
GeoJSON for the web app.

Sources found on this machine:
  Documents/sindh_bunds_official.geojson         168 named flood-protection bunds
  Documents/3-D Model .../indus_river_centerline.geojson
                                                 misnamed: actually PAK inland water
                                                 POLYGONS, used here as the permanent-
                                                 water mask so the Indus and the lakes
                                                 are not animated as new flood
  Desktop/kotri_gis/outputs/detected_breaches.geojson
                                                 SAR/MNDWI change-detection points,
                                                 2022-24, Kotri reach only (derived,
                                                 not an official breach register)
"""
import json
from pathlib import Path
import geopandas as gpd
import warnings
warnings.filterwarnings("ignore")

HOME = Path("/Users/CCS-CREATIVE")
OUT = Path("weblayers"); OUT.mkdir(exist_ok=True)
W, S, E, N = 66.3, 24.6, 70.6, 29.6

def dump(gdf, name, cols, tol=0.0):
    g = gdf.cx[W:E, S:N].copy()
    if g.empty:
        print(f"  {name}: EMPTY, skipped"); return
    keep = [c for c in cols if c in g.columns]
    g = g[keep + ["geometry"]]
    if tol:
        g["geometry"] = g.geometry.simplify(tol, preserve_topology=True)
    p = OUT / f"{name}.geojson"
    g.to_file(p, driver="GeoJSON")
    print(f"  {name}: {len(g)} feats -> {p.stat().st_size/1024:.0f} KB")

# --- flood protection bunds -------------------------------------------------
bunds = gpd.read_file(HOME / "Documents/sindh_bunds_official.geojson")
dump(bunds, "bunds", ["code", "name"], tol=0.0002)

# --- permanent water --------------------------------------------------------
wb = gpd.read_file(HOME / "Documents/3-D Model Kotri Barrage/HEC RAS Required"
                          "/01c_SRP_Surveyed_XSections/indus_river_centerline.geojson")
# Exact match: substring "Perennial" also matches "Non-Perennial/Intermittent".
perm = wb[wb.HYC_DESCRI == "Perennial/Permanent"]
dump(perm, "permanent_water", ["NAME", "HYC_DESCRI"], tol=0.0002)
dump(wb, "all_water_bodies", ["NAME", "HYC_DESCRI"], tol=0.0002)

# --- 2022 breach candidates -------------------------------------------------
br = gpd.read_file(HOME / "Desktop/kotri_gis/outputs/detected_breaches.geojson")
br22 = br[(br.year == 2022) & (br.label_strict == "breach")]
dump(br22, "breach_candidates_2022",
     ["bund_id", "bund_name", "km_start", "km_end", "sar_drop", "label_strict"])

print("\nNOTE: breach points are a derived change-detection product covering only the "
      "Kotri reach (67.6-68.5E). They are not the province-wide 2022 breach register, "
      "and do not cover the Dadu/Sehwan corridor where the reference map's red "
      "markers sit.")
