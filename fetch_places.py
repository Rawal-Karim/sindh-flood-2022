"""Settlement names for the map frame, from GeoNames (open, no authentication).

Coordinates come from the gazetteer rather than being eyeballed off the reference
figure. Two traps this handles:

  * Feature class A (ADM1/ADM2) records share their town's name but carry the whole
    district's population and its polygon centroid -- "Naushahro Firoz" ADM2 reports
    1.78 million against the town's 17,631. Only class P is accepted.
  * GeoNames spellings differ from the reference figure, so several towns look absent:
    Sehwan Sharif is "Sehwan", Bhan Saeedabad is "Bhan", Mohenjo-daro is "Moen jo
    Daro", Naushahro Feroze is "Naushahro Firoz", Kashmore is "Kashmor". ALIASES maps
    the gazetteer spelling to the name used on the map.
"""
import io, json, urllib.request, zipfile
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "web/assets"
CACHE = ROOT / "geonames_PK.txt"
URL = "https://download.geonames.org/export/dump/PK.zip"
W, S, E, N = 66.094, 24.527, 70.664, 29.841
MIN_POP = 15000

# gazetteer spelling -> label shown on the map
ALIASES = {
    "moen jo daro": "Mohenjo-daro",
    "bhan": "Bhan Saeedabad",
    "naushahro firoz": "Naushahro Feroze",
    "kashmor": "Kashmore",
    "usta muhammad": "Usta Mohammad",
    "dera allahyar": "Dera Allah Yar",
    "khairpur nathan shah": "K.N. Shah",
    "shahdad kot": "Shahdadkot",
}

# Always keep these, whatever their recorded population — they are the places the
# 2022 flood narrative turns on, and several have no population in GeoNames at all.
CURATED = {
    "sehwan", "bhan", "johi", "dadu", "mehar", "khairpur nathan shah", "kandiaro",
    "moro", "naushahro firoz", "nasirabad", "moen jo daro", "dokri", "ratodero",
    "larkana", "kambar", "qambar", "shahdad kot", "warah", "shikarpur", "jacobabad",
    "thul", "kandhkot", "kashmor", "sukkur", "rohri", "pano aqil", "ghotki",
    "khairpur", "gambat", "ranipur", "nawabshah", "hyderabad", "kotri", "jamshoro",
    "manjhand", "usta muhammad", "dera murad jamali", "dera allahyar", "jhat pat",
    "sui", "rajanpur", "sanghar", "matiari", "sakrand", "tando adam",
}

if not CACHE.exists():
    print("downloading GeoNames PK ...")
    with urllib.request.urlopen(URL, timeout=180) as r:
        raw = zipfile.ZipFile(io.BytesIO(r.read())).read("PK.txt").decode("utf8", "replace")
    CACHE.write_text(raw)
raw = CACHE.read_text()

best = {}
for line in raw.split("\n"):
    p = line.split("\t")
    if len(p) < 15 or p[6] != "P":            # populated places only, never class A
        continue
    try:
        lat, lon, pop = float(p[4]), float(p[5]), int(p[14] or 0)
    except ValueError:
        continue
    if not (W <= lon <= E and S <= lat <= N):
        continue
    key = p[2].lower().strip()
    curated = key in CURATED
    if not curated and pop < MIN_POP:
        continue
    label = ALIASES.get(key, p[1])
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

(OUT / "places.json").write_text(json.dumps(rows, indent=1))
print(f"wrote web/assets/places.json ({(OUT/'places.json').stat().st_size/1024:.0f} KB)")
for r in rows[:18]:
    print(f"   {r['name']:<24} {r['pop']:>9,}  {r['lat']:.3f},{r['lon']:.3f}")
