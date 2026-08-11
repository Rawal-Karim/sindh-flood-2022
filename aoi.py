"""Single definition of the area of interest.

Every script imports these bounds. Previously fetch_dem.py and fetch_places.py each
carried their own hardcoded rectangle while the rest derived theirs from the DEM,
which is exactly how extents drift apart.

Revised 2026-08-10 on departmental review:
  * north edge raised from 29.84 to 32.20 to take in the FULL Sulaiman Range. Checked
    against the terrain rather than assumed: ground above 1000 m east of 68.5E runs
    68.55-70.15E continuously from 29N to 32N, so the range is covered by extending
    north and the existing east edge already contains it.
  * south edge raised from 24.53 to 26.20, making Qazi Ahmad (26.303N, 68.103E) the
    downstream limit of the AOI. This drops Kotri, Hyderabad, Karachi and the delta.
    Sehwan (26.425N) and Manchar Lake (26.438N) sit only ~15 km inside this boundary.
  * east and west unchanged: the Kirthar western flank and the Sulaiman ridge both
    fall inside 66.09-70.66E.
"""

WEST, SOUTH, EAST, NORTH = 66.09, 26.20, 70.66, 32.20

# Landmarks the AOI is defined against, kept here so the reasoning stays checkable.
LANDMARKS = {
    "Qazi Ahmad": (68.103, 26.303),      # downstream limit of the AOI
    "Manchhar Lake": (67.643, 26.438),
    "Hamal Lake": (67.633, 27.371),
    "Sehwan": (67.861, 26.425),
    "Takht-e-Sulaiman": (70.00, 31.60),  # highest point of the Sulaiman Range
}

DEM_ZOOM = 10          # AWS Terrarium tile zoom for the DEM mosaic
DEM_FILE = "dem_sindh_z10.tif"


def bounds():
    return WEST, SOUTH, EAST, NORTH


def describe():
    import math
    w_km = (EAST - WEST) * 111.32 * math.cos(math.radians((SOUTH + NORTH) / 2))
    h_km = (NORTH - SOUTH) * 110.54
    return (f"AOI {WEST}-{EAST}E, {SOUTH}-{NORTH}N  "
            f"({w_km:.0f} x {h_km:.0f} km)")


if __name__ == "__main__":
    print(describe())
    for name, (lon, lat) in LANDMARKS.items():
        inside = WEST <= lon <= EAST and SOUTH <= lat <= NORTH
        print(f"  {'in ' if inside else 'OUT'} {name:<18} {lat:.3f}N {lon:.3f}E")
