#!/usr/bin/env python3
"""
Lunar Landing Navigator  -  NASA Space Apps Challenge prototype

Usage examples
--------------
    # Fully synthetic terrain, no internet needed:
    python lunar_navigator.py 2027-03-15 --demo

    # Real NASA LOLA terrain + JPL Horizons ephemeris (needs internet + rasterio):
    python lunar_navigator.py 2027-03-15

    # Real data, local GeoTIFFs already downloaded:
    python lunar_navigator.py 2027-03-15 --local-dir ./lola_data

    # Physics sanity checks:
    python lunar_navigator.py --selftest

Categories assessed (11)
------------------------
  1  Terrain Topography         elevation, slopes, ridges, cliffs
  2  Surface Roughness          small-scale texture, RMS height
  3  Crater & Boulder Hazards   resolved craters, obstacle clearance
  4  Landing-Zone Suitability   usable area, touchdown circle, flatness
  5  Solar Illumination         Sun position, sunlit fraction, sunrise/set
  6  Terrain Shadowing          horizon obstruction, mountain shadows
  7  Thermal Environment        surface temperature, thermal cycling
  8  Earth Visibility           line of sight, comm availability
  9  Surface & Soil Properties  density, cohesion, bearing strength
 10  Geological Stability       steep terrain, slope-failure potential
 11  Radiation & Space Env.     GCR dose, SEP events, micrometeoroids

Data sources
------------
  Terrain : NASA LRO LOLA  (PGDA/GSFC, Barker+2023 PSJ 4 183)
  Geometry: NASA JPL Horizons API  (ssd.jpl.nasa.gov)
  Solar events: NASA DONKI API  (api.nasa.gov)
  Thermal model: Vasavada+2012 / Hayne+2017 JGR
  Radiation ref: Zhang+2020 Sci. Adv. (Chang'e-4 LND)
  Soil ref: Lunar Sourcebook (Heiken+1991), NTRS 20220014634
"""
import argparse
import re
import sys
import time
from datetime import datetime

from lunar_nav import config as C
from lunar_nav.config import Config
from lunar_nav.datasets import make_provider
from lunar_nav.ephemeris import get_ephemeris
from lunar_nav.terrain import analyze_site, screen_candidates
from lunar_nav.sky import sky_series, sky_metrics
from lunar_nav.thermal import thermal_report
from lunar_nav.environment import soil_report, radiation_report, micrometeoroid_report
from lunar_nav.scoring import constraints, score
from lunar_nav import report


def parse():
    ap = argparse.ArgumentParser(
        description="Lunar landing site navigator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("date", nargs="?", help="Target landing date  YYYY-MM-DD (UTC)")
    ap.add_argument("--days",         type=float, default=C.MISSION_DAYS,
                    help="Surface stay length in days (default %(default)s)")
    ap.add_argument("--top",          type=int,   default=3,
                    help="Number of sites to print in detail (default %(default)s)")
    ap.add_argument("--candidates",   type=int,   default=3,
                    help="Candidates to refine after coarse screening (default %(default)s)")
    ap.add_argument("--demo",         action="store_true",
                    help="Synthetic terrain + offline ephemeris – no internet or rasterio needed")
    ap.add_argument("--local-dir",    default="",
                    help="Folder with pre-downloaded LOLA GeoTIFFs")
    ap.add_argument("--offline-ephemeris", action="store_true",
                    help="Use analytic ephemeris approximation instead of JPL Horizons")
    ap.add_argument("--screen-decimate", type=int, default=4,
                    help="Pixel decimation during coarse screening (higher = faster, less precise)")
    ap.add_argument("--no-thermal",   action="store_true",
                    help="Skip 1-D thermal simulation (faster)")
    ap.add_argument("--no-donki",     action="store_true",
                    help="Skip NASA DONKI solar-event query")
    ap.add_argument("--lander-mass",  type=float, default=7300.0,
                    help="Lander mass in kg, used for bearing-capacity estimate (default %(default)s)")
    ap.add_argument("--footpad-diameter", type=float, default=0.94,
                    help="Footpad diameter in metres (default %(default)s)")
    ap.add_argument("--footpads",     type=int,   default=4,
                    help="Number of footpads (default %(default)s)")
    ap.add_argument("--antenna-height", type=float, default=5.0,
                    help="Antenna height above ground in metres for Earth-visibility calc (default %(default)s)")
    ap.add_argument("--selftest",     action="store_true",
                    help="Run physics sanity checks and exit")
    return ap.parse_args()


def main():
    a = parse()

    if a.selftest:
        from lunar_nav.selftest import run
        sys.exit(run())

    # --- get date ---
    date = a.date or input("Enter landing date (YYYY-MM-DD UTC): ").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        sys.exit("Date must be in the format YYYY-MM-DD, e.g. 2027-03-15")

    cfg = Config(
        date=date,
        days=a.days,
        top=a.top,
        demo=a.demo,
        local_dir=a.local_dir,
        screen_decimate=(2 if a.demo and a.screen_decimate == 4 else a.screen_decimate),
        do_thermal=not a.no_thermal,
        do_donki=not a.no_donki and not a.demo,
        offline_ephemeris=a.offline_ephemeris or a.demo,
        candidates=a.candidates,
        lander_mass_kg=a.lander_mass,
        footpad_diameter_m=a.footpad_diameter,
        n_footpads=a.footpads,
        antenna_height_m=a.antenna_height,
    )

    start = datetime.strptime(date, "%Y-%m-%d")
    t0 = time.time()
    log = lambda m: print(m, file=sys.stderr)

    log("[1/5] Fetching Sun / Earth geometry (ephemeris)…")
    eph = get_ephemeris(start, days=max(31.0, cfg.days + 2),
                        offline=cfg.offline_ephemeris, log=log)

    log("[2/5] Loading terrain data…")
    provider = make_provider(cfg)

    log("[3/5] Screening south polar cap for candidate sites…")
    cands = screen_candidates(provider, eph, cfg, log=log)
    if not cands:
        sys.exit("No cell passed the slope / Sun / Earth screening for this date.\n"
                 "Try a different date or run with --demo to verify the pipeline.")

    # environment data is not site-specific – compute once
    env_soil = soil_report(cfg)
    env_rad  = radiation_report(cfg, start, log=log)
    env      = dict(soil=env_soil, rad=env_rad, mmod=micrometeoroid_report(cfg.days))

    log(f"[4/5] Detailed analysis of {len(cands)} candidate site(s)…")
    results = []
    for i, (la, lo, sc) in enumerate(cands, 1):
        log(f"   candidate {i}/{len(cands)}  lat={la:.2f}  lon={lo:.2f}")
        site = analyze_site(provider, la, lo, cfg)
        ser  = sky_series(eph, site, cfg)
        sky  = sky_metrics(eph, ser, cfg)
        th   = thermal_report(eph, ser, cfg) if cfg.do_thermal else None
        cs   = constraints(site, sky, cfg)
        tot, parts = score(site, sky, th, cfg)
        results.append(dict(site=site, sky=sky, thermal=th, env=env,
                            constraints=cs, score=tot, parts=parts))

    # sort: sites that pass ALL hard constraints first, then by score
    results.sort(
        key=lambda r: (all(p for _, p in r["constraints"]), r["score"]),
        reverse=True)

    log(f"[5/5] Generating report…  (total time {time.time() - t0:.0f} s)")
    report.header(cfg, eph, provider)
    report.summary(results)
    for rank, r in enumerate(results[:cfg.top], 1):
        report.site_report(rank, r, cfg, eph)

    print("\n" + "=" * 78)
    print(" Data tags used in this report:")
    print("   [LOLA]      NASA LRO laser altimeter grids (PGDA/GSFC, Barker+2023)")
    print("   [Horizons]  NASA JPL Horizons ephemeris API")
    print("   [MODEL]     Published physical model driven by NASA data")
    print("   [REFERENCE] Published mission measurement – not site-specific")
    print("   [DONKI]     NASA Space Weather Database of Notifications (api.nasa.gov)")
    print("=" * 78)


if __name__ == "__main__":
    main()
