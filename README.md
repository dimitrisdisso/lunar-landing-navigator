# Lunar Landing Navigator
**NASA Space Apps Challenge prototype**

Takes a date, queries real NASA data, and ranks south-polar landing sites
across 11 engineering categories.

---

## Quick start

```bash
# Install core dependencies
pip install numpy scipy

# Run in demo mode (synthetic terrain, no internet required)
python lunar_navigator.py 2027-03-15 --demo

# Run physics sanity checks
python lunar_navigator.py --selftest
```

---

## Real NASA data mode

Requires `rasterio` and an internet connection.

```bash
# Windows (easiest)
conda install -c conda-forge rasterio

# or pip
pip install rasterio

# Then run without --demo
python lunar_navigator.py 2027-03-15
```

Terrain files are streamed as cloud-optimised GeoTIFFs directly from
NASA PGDA/GSFC — no manual download needed.  To use local copies:

```bash
python lunar_navigator.py 2027-03-15 --local-dir ./lola_data
```

---

## Options

| Flag | Default | Meaning |
|------|---------|---------|
| `--demo` | off | Synthetic terrain + offline ephemeris |
| `--days N` | 6.5 | Surface stay length (days) |
| `--top N` | 3 | Sites printed in full detail |
| `--candidates N` | 8 | Sites refined after coarse screening |
| `--no-thermal` | off | Skip thermal simulation (faster) |
| `--no-donki` | off | Skip NASA DONKI solar-event query |
| `--lander-mass KG` | 7300 | Lander mass for bearing-capacity estimate |
| `--offline-ephemeris` | off | Analytic approx instead of JPL Horizons |
| `--selftest` | off | Run physics checks and exit |

---

## 11 assessed categories

| # | Category | Data source |
|---|----------|-------------|
| 1 | Terrain Topography | NASA LOLA (PGDA/GSFC) |
| 2 | Surface Roughness | NASA LOLA roughness product |
| 3 | Crater & Boulder Hazards | NASA LOLA DEM |
| 4 | Landing-Zone Suitability | NASA LOLA slope + DEM |
| 5 | Solar Illumination | JPL Horizons + LOLA horizon |
| 6 | Terrain Shadowing | LOLA DEM horizon profile |
| 7 | Thermal Environment | Hayne+2017 / Vasavada+2012 model |
| 8 | Earth Visibility & Comms | JPL Horizons + LOLA horizon |
| 9 | Surface & Soil Properties | Lunar Sourcebook (reference) |
| 10 | Geological Stability | NASA LOLA slope analysis |
| 11 | Radiation & Space Environment | Chang'e-4 LND + NASA DONKI |

---

## Hard constraints (Artemis III, NASA NTRS 20240009596)

- Latitude within 6° of south pole (≤ −84°)
- Landing-zone slope ≤ 10° over 100 m radius
- Obstacle-free radius ≥ 100 m
- Earth visible ≥ 25% of mission
- Sunlit ≥ 5% of mission

Sites that fail any constraint are reported but ranked below passing sites.

---

## File layout

```
NASA/
├── lunar_navigator.py      ← entry point (run this)
├── requirements.txt
├── README.md
└── lunar_nav/              ← package
    ├── __init__.py
    ├── config.py           constants, thresholds, NASA data URLs
    ├── datasets.py         LOLA raster provider + synthetic demo
    ├── ephemeris.py        JPL Horizons queries + offline fallback
    ├── geometry.py         polar stereographic projection + sky math
    ├── terrain.py          DEM analysis, horizon profiles, screening
    ├── sky.py              solar illumination + Earth visibility series
    ├── thermal.py          1-D regolith heat-conduction model
    ├── environment.py      soil properties, radiation, micrometeoroids
    ├── scoring.py          hard constraints + 0-100 heuristic score
    ├── report.py           formatted text output
    └── selftest.py         physics sanity checks
```
