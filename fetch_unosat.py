"""Download every UNOSAT flood-extent geodatabase covering the 2022 Pakistan floods.

HDX's HTML pages 403 on plain fetches, but its CKAN API answers fine with a browser
UA, and the files themselves live on unosat.org. Prefer .gdb (smaller than the SHP
equivalents, same content).
"""
import json, re, subprocess, sys
from pathlib import Path

OUT = Path("unosat/gdbs"); OUT.mkdir(parents=True, exist_ok=True)
UA = "Mozilla/5.0"
API = "https://data.humdata.org/api/3/action/"

def api(path):
    r = subprocess.run(["curl", "-sL", "--max-time", "90", "-A", UA, API + path],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)["result"]

KEEP = re.compile(r"water extent|water extents|flood evolution|floodwater depth", re.I)
SKIP = re.compile(r"punjab province.*202[45]|july 2024|2023 over pakistan|"
                  r"surface waters monitoring", re.I)
# The keyword search also returns same-worded products for other countries
# (e.g. FL20220526GUY, Lethem Region 9, Guyana). Require Pakistan explicitly.
PAK = re.compile(r"pakistan|sindh|balochistan|punjab", re.I)

seen, targets = set(), []
for q in ["satellite detected water extent Pakistan",
          "flood evolution assessment Pakistan",
          "floodwater depth Pakistan"]:
    for p in api(f"package_search?q={q.replace(' ', '+')}&rows=100")["results"]:
        name, title = p["name"], p.get("title", "")
        if (name in seen or not KEEP.search(title) or SKIP.search(title)
                or not PAK.search(title)):
            continue
        date = (p.get("dataset_date") or "")[1:11]
        if not ("2022" <= date[:4] <= "2023"):
            continue
        gdb = [r for r in p["resources"] if r["format"] == "Geodatabase"]
        if not gdb:
            continue
        seen.add(name)
        targets.append((date, title, gdb[0]["url"], int(gdb[0]["size"] or 0)))

targets.sort()

# Many HDX packages re-publish the identical file (e.g. FL20220808PAK_gdb.zip appears
# under five separate August package dates). Fetch each URL once.
by_url, order = {}, []
for date, title, url, size in targets:
    if url not in by_url:
        by_url[url] = (date, title, size)
        order.append(url)
print(f"{len(targets)} package entries -> {len(order)} distinct files "
      f"({sum(v[2] for v in by_url.values())/1e6:.0f} MB)\n")

failed = []
for url in order:
    date, title, size = by_url[url]
    dest = OUT / f"{date}_{re.sub(r'[^a-z0-9]+', '-', title.lower())[:48].strip('-')}.gdb.zip"
    if dest.exists() and dest.stat().st_size >= size > 0:
        print(f"  cached  {dest.name}")
        continue
    print(f"  fetch   {dest.name}  ({size/1e6:.1f} MB)")
    # -C - resumes a partial file; retries cover the mid-transfer drops that
    # killed the first run (curl exit 18).
    r = subprocess.run(["curl", "-sL", "--fail", "-C", "-", "--retry", "5",
                        "--retry-delay", "5", "--retry-all-errors",
                        "--max-time", "2400", "-A", UA, url, "-o", str(dest)])
    if r.returncode != 0:
        print(f"    FAILED rc={r.returncode}")
        failed.append((dest.name, url))

print("\ndone:", len(list(OUT.glob('*.zip'))), "files",
      f"{sum(f.stat().st_size for f in OUT.glob('*.zip'))/1e6:.0f} MB")
for n, u in failed:
    print("  FAILED:", n, u)
