# Sindh 2022 Flood — 3D Observed Replay

An interactive 3D reconstruction of the 2022 Indus flood across Sindh and south-east
Balochistan, built on satellite-observed flood extents rather than a scripted animation.

The timeline runs as one continuous calendar from **1 June 2022 to 28 February 2023**.
Everything from 21 July onward is measured; the stretch before it is a reconstruction
and is labelled as such on screen.

![Peak extent envelope](envelope_vs_reference.png)

---

## Running it

No build step. Serve the `web/` directory over HTTP — ES modules and `fetch` will not
work from `file://`.

```bash
python3 -m http.server 8777 --directory web
```

Then open <http://localhost:8777>.

Three.js r169 is vendored in `web/vendor/`, so there are no CDN dependencies and it
runs offline.

---

## What it shows

**Terrain** — SRTM elevation over 66.09–70.66°E, 24.53–29.84°N, drawn as a solid block
with a hypsometric and hillshade drape. Vertical exaggeration is adjustable and reads
as a true multiplier, because world units are kilometres.

**Two water tracks**, kept separate rather than merged:

| Track | Sensors | Resolution | Frames | Coverage |
|---|---|---|---|---|
| `viirs` | VIIRS / MODIS | 375 m | 12, weekly | Full frame |
| `highres` | Sentinel-1, Sentinel-2, Landsat-8 | 10–30 m | 5, August only | 22–40% of frame |

They disagree by roughly 1.3–1.7× on flooded area, and neither is wrong — VIIRS flags a
whole 375 m pixel if part of it is wet. Blending them into one series produced step
changes of +14,000 km² that were pure sensor-switch artefact, so each track is built
independently and cross-compared instead.

**Observed area, VIIRS track**

| Date | Flooded |
|---|---|
| 21 Jul 2022 | 13,458 km² |
| **31 Aug 2022** | **51,034 km² (peak)** |
| 17 Oct 2022 | 24,992 km² |
| 31 Dec 2022 | 10,464 km² |
| 28 Feb 2023 | 4,413 km² |

Peak-extent envelope over the whole event: **56,428 km²**.

**Cross-validation** — high-resolution vs VIIRS, inside each high-resolution footprint:

| Date | Sensor | High-res | VIIRS | Ratio | IoU |
|---|---|---|---|---|---|
| 27 Aug | S1 | 9,745 km² | 16,643 km² | 1.71 | 0.451 |
| 29 Aug | LS8 | 1,882 km² | 2,384 km² | 1.27 | 0.523 |
| 31 Aug | S2 | 11,271 km² | 17,304 km² | 1.54 | 0.581 |

---

## Reconstructed vs observed

The satellite record begins on 21 July 2022. The monsoon build-up and the hill-torrent
response before that date are **reconstructed**, and the app says so with a persistent
on-screen badge and a hatched band on the timeline.

- **Rainfall is measured.** CHIRPS daily totals drive rain intensity, cloud shadow, sky
  colour and ground wetness. Peak area-mean was 20.05 mm/day (24 Jul and 18 Aug 2022);
  the wettest cell took 679 mm over the season.
- **Channel geometry is measured topography.** Nai channels are traced from the DEM by
  priority-flood depression filling, D8 flow routing and flow accumulation — not drawn
  by hand.
- **Surge timing is a model.** A kinematic wave with celerity scaling as √slope, so
  torrents run fast down the Kirthar and Sulaiman ravines and slow on the plain.

Channels are restricted to terrain with more than 80 m of local relief. On the
irrigated plain the real drainage is canals and bunds a few metres deep, far below what
a 270 m DEM resolves, and routing there invents a dense dendritic network that looks
convincing and means nothing. The plain is left to the observed data.

---

## Honesty in the rendering

The satellite record is patchy, and the app is built not to paper over that:

- **Unobserved ground is never drawn as dry.** A single satellite pass covers roughly
  20% of the frame. Pixels no sensor has yet seen render as grey haze, and the HUD
  warns when a track's coverage falls below 60%.
- **Stale readings fade.** Each frame carries a per-pixel age; a carried-forward
  reading is drawn progressively paler so it cannot be mistaken for a fresh
  measurement.
- **Reconstruction is labelled**, continuously, for as long as it is on screen.

---

## Pipeline

Scripts are ordered; each writes into `web/assets/`.

| Script | Does |
|---|---|
| `fetch_dem.py` | Mosaics AWS Terrarium SRTM tiles over the frame → `dem_sindh_z10.tif` |
| `fetch_unosat.py` | Downloads UNOSAT flood geodatabases from HDX (~2.3 GB) |
| `composite.py` | Builds the two-track flood composite and the peak envelope |
| `fetch_chirps.py` | CHIRPS daily rainfall, clipped to frame → `chirps_frame.npz` |
| `flow_routing.py` | DEM flow routing → `arrival.png` (channels + arrival times) |
| `fetch_places.py` | GeoNames settlements in frame → `places.json` |
| `local_layers.py` | Clips local GIS layers (bunds, permanent water, breach points) |
| `build_web_assets.py` | Bakes terrain, textures, frames and manifest for the browser |

Large intermediates are gitignored and regenerable — see `.gitignore`.

---

## Data sources and attribution

| Data | Source | Licence |
|---|---|---|
| Flood extents | [UNOSAT](https://unosat.org) via [HDX](https://data.humdata.org) | CC BY 3.0 IGO |
| Elevation | SRTM via [AWS Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) | Public domain / ODbL for some sources |
| Rainfall | [CHIRPS v2.0](https://www.chc.ucsb.edu/data/chirps), UC Santa Barbara | Public domain |
| Settlements | [GeoNames](https://www.geonames.org) | CC BY 4.0 |
| Renderer | [three.js](https://threejs.org) r169 | MIT |

Bund alignments and 2022 breach candidates are derived from Sindh Irrigation Department
material and are included here for internal use. They are not open data — please do not
redistribute them without departmental clearance.

---

## Known limitations

- **VIIRS over-detects** relative to 10 m sensors by 1.3–1.7×. Use the high-resolution
  track where precision matters; use VIIRS for temporal shape and full coverage.
- **Labels do not test terrain occlusion**, so a town behind a ridge can still show its
  name at steep viewing angles.
- **Surge timing is uncalibrated.** No gauge data was available for the Nais, so the
  kinematic-wave timing is plausible but unverified.
- **The plain has no modelled hydraulics.** This is an observed replay, not a
  hydrodynamic model — there is no shallow-water solve, no bund overtopping physics and
  no breach routing.
