"""Settlement names for the map frame, from GeoNames (open, no authentication).

Coordinates come from the gazetteer rather than being eyeballed off the reference
figure. The traps this handles, all of them found on the rendered map:

  * Feature class A (ADM1/ADM2) records share their town's name but carry the whole
    district's population and its polygon centroid -- "Naushahro Firoz" ADM2 reports
    1.78 million against the town's 17,631. Only class P is accepted.
  * Class PPLX is a SECTION of a town, not a town. "Kot Malik Barkhurdar" is a Quetta
    neighbourhood with 69,359 people; it drew a town label 2.6 km from the Quetta one.
  * GeoNames spellings differ from the reference figure, so several towns look absent:
    Sehwan Sharif is "Sehwan", Bhan Saeedabad is "Bhan", Mohenjo-daro is "Moen jo
    Daro", Naushahro Feroze is "Naushahro Firoz", Kashmore is "Kashmor". ALIASES maps
    the gazetteer spelling to the name used on the map.
  * Diacritics split the de-duplication. Matching is on the ASCII name (column 2) but
    the label came from the UTF-8 name (column 1), and `best` was keyed on the label
    -- so "Shikarpur" and "Shīkārpur" were two different keys and BOTH were drawn,
    125 km apart. Labels are now unaccented and `best` is keyed on the final label.
  * Namesakes. Sindh has five villages called Kotri and four called Mehrabpur. Two
    rules keep them off the map: a curated name whose intended town lies OUTSIDE the
    AOI must not be curated at all (or it binds to a namesake that IS inside -- this
    put "Kotri" on a hamlet in Garhi Khairo, 290 km from the barrage), and where the
    tie-break by population picks the wrong record, PINS fixes the intended one by
    position (GeoNames gives Mehrabpur tehsil HQ pop 0 and the Garhi Khairo village
    35,263, so the village won).
  * Some GeoNames records are simply wrong. EXCLUDE drops them by id.
"""
import io, json, math, unicodedata, urllib.request, zipfile
from pathlib import Path


def unaccent(s):
    """Fold diacritics so 'Shīkārpur' and 'Shikarpur' collapse to one label."""
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))

ROOT = Path(__file__).parent
OUT = ROOT / "web/assets"
CACHE = ROOT / "geonames_PK.txt"
URL = "https://download.geonames.org/export/dump/PK.zip"
from aoi import WEST as W, SOUTH as S, EAST as E, NORTH as N
MIN_POP = 15000

# gazetteer spelling -> label shown on the map
ALIASES = {
    "moen jo daro": "Mohenjo-daro",
    "bhan": "Bhan Syedabad",
    "naushahro firoz": "Naushahro Feroze",
    "kashmor": "Kashmore",
    "usta muhammad": "Usta Mohammad",
    "dera allahyar": "Dera Allah Yar",
    "khairpur nathan shah": "K.N. Shah",
    "shahdad kot": "Shahdadkot",
    # Where GeoNames and the Department (and Google) spell a town differently, the
    # map follows local usage. Verified against Google Maps 2026-09-02.
    "kambar": "Qambar",
    "saddiqabad": "Sadiqabad",
    "dadhar": "Dhadar",
    "pad idan": "Padidan",
    "setharja old": "Setharja",
    "pir jo goth": "Pirjo Goth",
    # GeoNames writes the district town with a curly apostrophe; the Department
    # writes plain "Khairpur", and nothing else on the map competes for that name
    # now that the Ghotki namesake is excluded.
    "khairpur mir's": "Khairpur",
}

# Always keep these, whatever their recorded population — they are the places the
# 2022 flood narrative turns on, and several have no population in GeoNames at all.
#
# A name belongs here ONLY if its town is inside the AOI. Curating a name whose town
# is outside means the flag falls through to whatever namesake IS inside: "kotri"
# sat here after aoi.py raised the south edge to 26.20, and put a barrage label on a
# zero-population hamlet in Garhi Khairo taluka. See OUT_OF_FRAME below.
CURATED = {
    "sehwan", "bhan", "johi", "dadu", "mehar", "khairpur nathan shah", "kandiaro",
    "moro", "naushahro firoz", "nasirabad", "moen jo daro", "dokri", "ratodero",
    "larkana", "kambar", "shahdad kot", "warah", "shikarpur", "jacobabad",
    "thul", "kandhkot", "kashmor", "sukkur", "rohri", "pano aqil", "ghotki",
    "gambat", "ranipur", "nawabshah", "mehrabpur",
    "usta muhammad", "dera murad jamali", "dera allahyar",
    "sui", "rajanpur",
}

# Curated names retired because their town lies outside the AOI of aoi.py. Kept here,
# not deleted, so the reason survives the next time someone moves the south edge.
#   kotri, hyderabad, jamshoro, manjhand, matiari, sakrand, tando adam, sanghar
#     -- all below 26.20N, dropped when the south edge was raised on 2026-08-10.
#   qambar   -- the town IS on the map, as GeoNames spells it, "Kambar" (77,481).
#               Curating both spellings drew it twice, 54 km apart.
#   jhat pat -- the former name of Dera Allah Yar, which is curated. Drew it twice.
#   khairpur -- the district town is "Khairpur Mir's" in GeoNames and clears MIN_POP
#               on its own (191,044). Curating the bare name bound it to one of the
#               eleven zero-population villages also called Khairpur.
OUT_OF_FRAME = {
    "kotri", "hyderabad", "jamshoro", "manjhand", "matiari", "sakrand",
    "tando adam", "sanghar", "qambar", "jhat pat", "khairpur",
}

# Records that are wrong in the gazetteer itself, dropped by GeoNames id.
EXCLUDE = {
    11250625,  # "Arif Wala" at 26.325N 66.296E, in Bela tehsil, Lasbela. Carries the
               # real Arifwala's population (157,063) -- but real Arifwala is in
               # Pakpattan, Punjab, 73.07E 30.29N, 700 km outside this frame. The
               # population put it in the .big label tier over empty Kirthar foothills.
    1174357,   # "Khairpur" at 28.064N 69.704E with 40,083 people. That point is
               # Daharki, which the map already labels 1.2 km away.
}

# label -> (lon, lat) of the intended record, where the population tie-break picks
# the wrong namesake. Only records within PIN_KM of the pin are accepted.
PINS = {
    "Mehrabpur": (68.4196, 27.1033),  # tehsil HQ, Naushahro Feroze, on the Rohri-Kotri
                                      # line. GeoNames gives it pop 0, so the Garhi
                                      # Khairo village (35,263) won on population.
    "Sui": (69.1789, 28.6317),        # the gas-field town. GeoNames also holds "Sui"
                                      # 13.6 km east at 69.3167E, both with pop 0, so
                                      # the winner was whichever came first in the file.
}
PIN_KM = 5.0

if not CACHE.exists():
    print("downloading GeoNames PK ...")
    with urllib.request.urlopen(URL, timeout=180) as r:
        raw = zipfile.ZipFile(io.BytesIO(r.read())).read("PK.txt").decode("utf8", "replace")
    CACHE.write_text(raw)
raw = CACHE.read_text()

def km(lon1, lat1, lon2, lat2):
    return math.hypot((lon1 - lon2) * 111.32 * math.cos(math.radians(lat1)),
                      (lat1 - lat2) * 110.54)


best = {}
for line in raw.split("\n"):
    p = line.split("\t")
    if len(p) < 15 or p[6] != "P":            # populated places only, never class A
        continue
    if p[7] == "PPLX":                        # a district of a town, not a town
        continue
    try:
        gid = int(p[0])
        lat, lon, pop = float(p[4]), float(p[5]), int(p[14] or 0)
    except ValueError:
        continue
    if gid in EXCLUDE:
        continue
    if not (W <= lon <= E and S <= lat <= N):
        continue
    key = p[2].lower().strip()
    curated = key in CURATED
    if not curated and pop < MIN_POP:
        continue
    # Unaccent, or a diacritic spelling becomes a second key and the town is drawn
    # twice. Key `best` on the label itself, so aliased spellings collapse too.
    label = ALIASES.get(key, unaccent(p[1]))
    pin = PINS.get(label)
    if pin and km(lon, lat, pin[0], pin[1]) > PIN_KM:
        continue
    prev = best.get(label)
    if prev is None or pop > prev["pop"]:     # duplicate names: keep the largest
        best[label] = dict(name=label, lon=round(lon, 4), lat=round(lat, 4),
                           pop=pop, fcode=p[7], curated=curated)

rows = sorted(best.values(), key=lambda r: -r["pop"])
print(f"{len(rows)} places in frame ({sum(r['curated'] for r in rows)} curated)")

found = {r["name"].lower() for r in rows} | {k for k in ALIASES if ALIASES[k].lower()
                                             in {r['name'].lower() for r in rows}}
missing = sorted(c for c in CURATED
                 if c not in found and ALIASES.get(c, "").lower() not in found)
if missing:
    print("  still missing:", ", ".join(missing))

# Two towns under one point, or one town under two names, is the failure this file
# keeps producing. Say so at build time rather than on the rendered map.
for i in range(len(rows)):
    for j in range(i + 1, len(rows)):
        a, b = rows[i], rows[j]
        d = km(a["lon"], a["lat"], b["lon"], b["lat"])
        if d < 3.0:
            print(f"  WARNING: {a['name']} and {b['name']} are {d:.1f} km apart")
unpinned = sorted(set(PINS) - {r["name"] for r in rows})
if unpinned:
    print("  WARNING: pinned but not found:", ", ".join(unpinned))

(OUT / "places.json").write_text(json.dumps(rows, indent=1))
print(f"wrote web/assets/places.json ({(OUT/'places.json').stat().st_size/1024:.0f} KB)")
for r in rows[:18]:
    print(f"   {r['name']:<24} {r['pop']:>9,}  {r['lat']:.3f},{r['lon']:.3f}")
