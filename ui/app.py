"""
Flask backend for the Lunar Landing Navigator UI.
Run:  python ui/app.py
Then open:  http://localhost:5000
"""
import json
import os
import sys
import time
import threading
from datetime import datetime

from flask import Flask, render_template, request, jsonify, Response, stream_with_context

# Make sure the parent directory (NASA/) is on the path so lunar_nav imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = Flask(__name__)

# In-memory job store (fine for single-user local app)
_jobs = {}
_jobs_lock = threading.Lock()


def _run_analysis(job_id, date_str, time_str, days):
    """Run the full navigator pipeline in a background thread, storing progress."""
    def emit(msg, data=None):
        with _jobs_lock:
            _jobs[job_id]["log"].append(msg)
            if data:
                _jobs[job_id]["result"] = data

    try:
        from lunar_nav.config import Config
        from lunar_nav.datasets import make_provider
        from lunar_nav.ephemeris import get_ephemeris
        from lunar_nav.terrain import analyze_site, screen_candidates
        from lunar_nav.sky import sky_series, sky_metrics
        from lunar_nav.thermal import thermal_report
        from lunar_nav.environment import soil_report, radiation_report, micrometeoroid_report
        from lunar_nav.scoring import constraints, score
        from lunar_nav import config as C

        # Parse arrival datetime — combine date + time
        arrival_str = f"{date_str} {time_str}"
        try:
            start_dt = datetime.strptime(arrival_str, "%Y-%m-%d %H:%M")
        except ValueError:
            start_dt = datetime.strptime(date_str, "%Y-%m-%d")

        cfg = Config(date=date_str, days=days, candidates=1, top=1,
                     do_donki=False, offline_ephemeris=False)

        emit(f"Fetching Sun / Earth geometry for {start_dt:%Y-%m-%d %H:%M} UTC…")
        eph = get_ephemeris(start_dt, days=max(31.0, days + 2), offline=False,
                            log=lambda m: emit(m))

        emit("Loading NASA LOLA terrain data…")
        provider = make_provider(cfg)

        emit("Screening south polar cap for candidate landing sites…")
        cands = screen_candidates(provider, eph, cfg, log=lambda m: emit(m))
        if not cands:
            emit("ERROR: No candidates passed screening for this date.")
            with _jobs_lock:
                _jobs[job_id]["status"] = "error"
            return

        env_soil = soil_report(cfg)
        env_rad = radiation_report(cfg, start_dt, log=lambda m: emit(m))
        env = dict(soil=env_soil, rad=env_rad, mmod=micrometeoroid_report(days))

        emit(f"Analysing {len(cands)} candidate site(s)…")
        results = []
        for i, (la, lo, sc) in enumerate(cands, 1):
            emit(f"  Candidate {i}/{len(cands)}: lat={la:.2f}°, lon={lo:.2f}°")
            site = analyze_site(provider, la, lo, cfg)
            ser = sky_series(eph, site, cfg)
            sky = sky_metrics(eph, ser, cfg)
            th = thermal_report(eph, ser, cfg)
            cs = constraints(site, sky, cfg)
            tot, parts = score(site, sky, th, cfg)
            results.append(dict(site=site, sky=sky, thermal=th, env=env,
                                constraints=cs, score=tot, parts=parts))

        results.sort(key=lambda r: (all(p for _, p in r["constraints"]), r["score"]),
                     reverse=True)

        emit("Building report…")
        output = _serialize_results(results, cfg, eph, provider, start_dt)
        emit("Done.", output)
        with _jobs_lock:
            _jobs[job_id]["status"] = "done"

    except Exception as ex:
        import traceback
        with _jobs_lock:
            _jobs[job_id]["log"].append(f"ERROR: {ex}\n{traceback.format_exc()}")
            _jobs[job_id]["status"] = "error"


def _sep_climatology(sep_data, mission_dt, days):
    """
    Replace the DONKI backward event-count with a statistical SEP climatology.

    Returns a SHORT plain-language string suitable for a single UI row.
    Full citation details go in the source tag.
    """
    import math
    from datetime import datetime as _dt

    sc25_max = _dt(2025, 3, 1)
    yrs_since_max = (mission_dt - sc25_max).days / 365.25

    if yrs_since_max < 0:
        phase = "ascending/near-maximum"
        rate_lo, rate_hi = 4.0, 8.0
    elif yrs_since_max < 2.0:
        phase = "early declining"
        rate_lo, rate_hi = 2.0, 5.0
    elif yrs_since_max < 4.0:
        phase = "mid declining"
        rate_lo, rate_hi = 1.0, 3.0
    else:
        phase = "late declining / near minimum"
        rate_lo, rate_hi = 0.5, 1.5

    p_lo = 1 - math.exp(-rate_hi * days / 365)
    p_hi = 1 - math.exp(-rate_lo * days / 365)

    return (
        f"SC25 {phase}. "
        f"P(≥1 event in {days:.1f} d) ≈ {p_lo*100:.1f}–{p_hi*100:.1f}% "
        f"({rate_lo:.0f}–{rate_hi:.0f} events/yr, Poisson). "
        f"⚠ Requires operational forecast before go/no-go."
    )


def _serialize_results(results, cfg, eph, provider, arrival_dt):
    """Convert pipeline output to a JSON-serialisable dict for the UI."""
    import numpy as np

    def _f(v):
        if v is None: return None
        if isinstance(v, float) and not np.isfinite(v): return None
        if isinstance(v, (np.floating, np.integer)): return float(v)
        return v

    # Safe formatter: returns "n/a" if value is None/nan, otherwise formats it.
    # Optional scale multiplier applied before formatting (e.g. scale=100 for fractions→%).
    def _fmt(v, spec="", suffix="", scale=1):
        v2 = _f(v)
        if v2 is None: return "n/a"
        try:
            return format(v2 * scale, spec) + suffix
        except (TypeError, ValueError):
            return "n/a"

    # Find the ephemeris index closest to the arrival time
    from datetime import timedelta
    arrival_offset_h = (arrival_dt - eph.start).total_seconds() / 3600.0
    arrival_idx = int(round(arrival_offset_h / eph.step_h))
    arrival_idx = max(0, min(arrival_idx, eph.n - 1))
    # Mid-mission index for average values
    mid_idx = arrival_idx + int(cfg.days * 24 / eph.step_h / 2)
    mid_idx = max(0, min(mid_idx, eph.n - 1))

    out = {
        "date":             cfg.date,
        "arrival_time":     arrival_dt.strftime("%H:%M"),
        "arrival_datetime": arrival_dt.strftime("%Y-%m-%d %H:%M"),
        "days":             cfg.days,
        "terrain_source":   provider.name,
        "ephemeris_source": eph.source,
        "arrival_sun_lon":  float(eph.sun_lon[arrival_idx]),
        # Full ephemeris series for the mission window (hourly, for the time slider)
        "eph_series": [
            {
                "sunLon":   float(eph.sun_lon[arrival_idx + i]),
                "sunLat":   float(eph.sun_lat[arrival_idx + i]),
                "earthLon": float(eph.earth_lon[arrival_idx + i]),
                "earthLat": float(eph.earth_lat[arrival_idx + i]),
            }
            for i in range(0, int(cfg.days * 24 / eph.step_h) + 2)
            if arrival_idx + i < eph.n
        ],
        "step_h": eph.step_h,
        "sites": []
    }

    for rank, r in enumerate(results, 1):
        s, k, t, e = r["site"], r["sky"], r["thermal"], r["env"]
        constraints_ok = all(p for _, p in r["constraints"])

        # Sun/Earth direction at arrival time (body-fixed lon/lat from Horizons)
        sun_lon   = float(eph.sun_lon[arrival_idx])
        sun_lat   = float(eph.sun_lat[arrival_idx])
        earth_lon = float(eph.earth_lon[arrival_idx])
        earth_lat = float(eph.earth_lat[arrival_idx])

        site_data = {
            "rank": rank,
            "lat": _f(s["lat"]),
            "lon": _f(s["lon"]),
            "score": _f(r["score"]),
            "constraints_ok": constraints_ok,
            "constraints": [{"name": n, "pass": bool(p)} for n, p in r["constraints"]],
            "score_parts": {k2: _f(v) for k2, v in r["parts"].items()},

            # For 3D globe
            "sun_lon": sun_lon,
            "sun_lat": sun_lat,
            "earth_lon": earth_lon,
            "earth_lat": earth_lat,

            # Left panel key stats
            "key": {
                "elevation_m":      _f(s["elevation_m"]),
                "sunlit_pct":       _f(k["sunlit_frac"] * 100),
                "earth_vis_pct":    _f(k["earth_vis_frac"] * 100),
                "longest_outage_h": _f(k["longest_outage_h"]),
                "t_min_c":          _f(t["t_min_window"] - 273.15) if t else None,
                "t_max_c":          _f(t["t_max_window"] - 273.15) if t else None,
                "t_mean_c":         _f(t["t_mean_window"] - 273.15) if t else None,
                "slope_mean":       _f(s["slope_mean"]),
                "slope_max":        _f(s["slope_max"]),
                "r_inscribed_m":    _f(s["r_inscribed_m"]),
                "safe_area_km2":    _f(s["safe_area_km2"]),
                "sun_el_min":       _f(k["sun_el_min"]),
                "sun_el_max":       _f(k["sun_el_max"]),
                "sun_flux":         _f(k["sun_flux"]),
                "both_frac_pct":    _f(k["both_frac"] * 100),
                "gcr_msv":          _f(e["rad"]["mission_msv"]),
            },

            # Right panel: all 11 sections
            "sections": {
                "1_terrain": {
                    "title": "1. Terrain Topography",
                    "items": [
                        ("Elevation", _fmt(s['elevation_m'], "+.0f", " m vs 1737.4 km sphere")),
                        ("Shape", f"{s['shape_class']} (TPI {_fmt(s['tpi_m'], '+.0f')} m)"),
                        ("Relief within 2 km", _fmt(s['relief_2km_m'], ".0f", " m")),
                        ("Slope within 100 m landing zone (mean / 95th / max)", f"{_fmt(s['slope_mean'],'.1f')} / {_fmt(s['slope_p95'],'.1f')} / {_fmt(s['slope_max'],'.1f')} °"),
                        ("Slope within 2 km EVA region (mean / 95th / max)", f"{_fmt(s.get('eva_slope_mean'),'.1f')} / {_fmt(s.get('eva_slope_p95'),'.1f')} / {_fmt(s.get('eva_slope_max'),'.1f')} °"),
                        (">20° slopes within 2 km EVA", _fmt(s['eva_frac_over'], ".0f", "% of area", scale=100)),
                        (">30° slopes within 2 km EVA", _fmt(s.get('eva_frac_over30'), ".0f", "%", scale=100)),
                        ("Nearest cliff/wall (>30°)", _fmt(s['d_steep_m'], ".0f", " m") if s['d_steep_m'] < 1e9 else "none in range"),
                    ]
                },
                "2_roughness": {
                    "title": "2. Surface Roughness",
                    "items": [
                        ("RMS height (100 m baseline)", f"{_fmt(s['rough_m'],'.2f')} m mean, {_fmt(s['rough_max_m'],'.2f')} m max" if s['rough_m'] is not None and np.isfinite(s['rough_m']) else "n/a"),
                        ("Sub-pixel scales", f"Below ~{s['pix_g']:.0f} m: use LROC NAC imagery"),
                    ]
                },
                "3_craters": {
                    "title": "3. Crater & Boulder Hazards",
                    "items": [
                        ("Resolved craters within 1 km (LOLA DEM, ≥240 m)", str(s['n_craters_1km'])),
                        ("Nearest resolved crater", _fmt(s['d_crater_m'], ".0f", " m") if s['d_crater_m'] < 1e9 else "none resolved (≥240 m)"),
                        ("LOLA-resolvable crater fraction (≥240 m) of landing zone", f"{s['crater_frac_zone']*100:.0f}%"),
                        ("Surface roughness proxy (100 m RMS, LOLA)",
                            f"{_fmt(s['rough_m'],'.2f')} m mean / {_fmt(s['rough_max_m'],'.2f')} m max in zone — "
                            + ("smooth, low sub-pixel hazard probability" if s.get('rough_m') is not None and np.isfinite(s.get('rough_m', float('nan'))) and s['rough_m'] < 0.5 else
                               "moderate roughness, boulders plausible" if s.get('rough_m') is not None and np.isfinite(s.get('rough_m', float('nan'))) and s['rough_m'] < 1.0 else
                               "high roughness, elevated boulder/hazard probability")
                            if s.get('rough_m') is not None and np.isfinite(s.get('rough_m', float('nan'))) else "roughness not available"),
                        ("Boulders / craters <240 m", "Below LOLA 80 m resolution — UNVERIFIED. Requires LROC NAC imagery (0.5–1.5 m/px) for safety certification."),
                    ]
                },
                "4_landing_zone": {
                    "title": "4. Landing-Zone Suitability",
                    "items": [
                        ("Obstacle-free radius (LOLA-resolvable only — provisional)",
                            _fmt(s['r_inscribed_m'], ".0f", " m") + " — excludes sub-80 m boulders/craters"),
                        ("Touchdown diameter (LOLA-resolvable only — provisional)",
                            _fmt(s['r_inscribed_m'], ".0f", " m", scale=2)),
                        ("Sub-80 m boulders / small craters",
                            "UNVERIFIED — LROC NAC imagery required for certification."),
                        ("Roughness indication",
                            (f"Max {_fmt(s['rough_max_m'],'.2f')} m RMS (100 m baseline) — "
                             + ("moderate, boulders plausible."
                                if s.get('rough_max_m') is not None and np.isfinite(s.get('rough_max_m', float('nan'))) and s['rough_max_m'] >= 0.5
                                else "low — sub-pixel hazards still unresolved."))
                            if s.get('rough_max_m') is not None and np.isfinite(s.get('rough_max_m', float('nan')))
                            else "roughness not available"),
                        ("Connected safe area (LOLA-only — provisional)",
                            _fmt(s['safe_area_km2'], ".2f", " km²")),
                        ("Safe area geometry",
                            (f"Irregular plateau {_fmt(s.get('safe_bbox_ns_m'),'.0f')} m (N–S) × "
                             f"{_fmt(s.get('safe_bbox_ew_m'),'.0f')} m (E–W), "
                             f"fill fraction {s.get('safe_bbox_fill', 0):.2f}. "
                             f"Inscribed disk ({_fmt(s['r_inscribed_m'],'.0f')} m) sits at touchdown point within this region.")
                            if s.get('safe_bbox_ns_m') else "geometry unavailable"),
                        ("Offset from screen centre", _fmt(s['center_offset_m'], ".0f", " m")),
                    ]
                },
                "5_illumination": {
                    "title": "5. Solar Illumination",
                    "items": [
                        ("Sun elevation range", f"{_fmt(k['sun_el_min'],'+.1f')} to {_fmt(k['sun_el_max'],'+.1f')} °"),
                        ("Sunlit fraction (stay)", _fmt(k['sunlit_frac'], ".0f", "%", scale=100)),
                        ("Longest continuous sunlight", _fmt(k['longest_sunlit_h'], ".0f", " h")),
                        ("Sunlit fraction (lunar day)", _fmt(k['sunlit_frac_month'], ".0f", "%", scale=100)),
                        ("Incidence — horizontal/zenith (lit)", _fmt(k.get('mean_horiz_incidence'), ".0f", "°")),
                        ("Incidence — terrain-relative, surface normal (lit)", _fmt(k['mean_incidence'], ".0f", "°")),
                        ("Solar flux", f"{_fmt(k['sun_flux'],'.0f')} W/m²  ({_fmt(k['sun_au'],'.4f')} AU)"),
                        ("Sunrise / sunset events", "; ".join(f"{ev[0]} {ev[1].strftime('%Y-%m-%d %H:%M')} UTC" for ev in k["events"]) or "none during stay"),
                    ]
                },
                "6_shadowing": {
                    "title": "6. Terrain Shadowing",
                    "items": [
                        ("Horizon elevation (min/mean/max)", f"{s['horizon'].min():+.1f} / {s['horizon'].mean():+.1f} / {s['horizon'].max():+.1f} °"),
                        ("Highest obstruction azimuth", f"{s['az'][s['horizon'].argmax()]:.0f} °"),
                        ("Hours Sun blocked by terrain", _fmt(k['shadow_by_terrain_h'], ".0f", " h")),
                    ]
                },
                "7_thermal": {
                    "title": "7. Thermal Environment",
                    "items": [
                        ("Surface temp during stay",
                            f"{_fmt(t['t_max_window'],'.0f')} K max / {_fmt(t['t_min_window'],'.0f')} K min "
                            f"({_fmt(t['t_max_window']-273.15,'.0f')} to {_fmt(t['t_min_window']-273.15,'.0f')} °C)"
                            if t else "not computed"),
                        ("Largest 24 h swing", _fmt(t['swing24'], ".0f", " K") if t else "n/a"),
                        ("Lit/dark transitions in stay", str(t['transitions']) if t else "n/a"),
                        ("Full lunar day range (outside stay / reference)",
                            f"{_fmt(t['t_min_month'],'.0f')} – {_fmt(t['t_max_month'],'.0f')} K  "
                            f"(night low reached only outside the {cfg.days:.1f}-day stay)" if t else "n/a"),
                    ]
                },
                "8_comms": {
                    "title": "8. Earth Visibility & Comms",
                    "items": [
                        ("Earth elevation range", f"{_fmt(k['earth_el_min'],'+.1f')} to {_fmt(k['earth_el_max'],'+.1f')} °"),
                        ("Earth visible (stay)", _fmt(k['earth_vis_frac'], ".0f", "%", scale=100)),
                        ("Longest comms outage", _fmt(k['longest_outage_h'], ".0f", " h")),
                        ("Sun AND Earth available", _fmt(k['both_frac'], ".0f", "% of stay", scale=100)),
                    ]
                },
                "9_soil": {
                    "title": "9. Surface & Soil Properties",
                    "items": [
                        ("Bulk density (surface / 10 cm / 1 m)", f"{e['soil']['density_surface']:.2f} / {e['soil']['density_10cm']:.2f} / {e['soil']['density_100cm']:.2f} g/cm³"),
                        ("Cohesion (surface)", f"{e['soil']['cohesion_surface_kpa'][0]}–{e['soil']['cohesion_surface_kpa'][1]} kPa"),
                        ("Cohesion (> 0.5 m depth)", f">{e['soil']['cohesion_deep_kpa']:.0f} kPa"),
                        ("Friction angle", f"{e['soil']['friction_deg'][0]:.0f}–{e['soil']['friction_deg'][1]:.0f} °"),
                        ("Regolith thickness", f"{e['soil']['thickness_m'][0]:.0f}–{e['soil']['thickness_m'][1]:.0f} m"),
                        ("Bearing capacity", f"{e['soil']['bearing_kpa'][0]:.0f}–{e['soil']['bearing_kpa'][1]:.0f} kPa (applied: {e['soil']['applied_kpa']:.1f} kPa)"),
                        ("Safety factor", f"{e['soil']['safety_factor'][0]:.1f}–{e['soil']['safety_factor'][1]:.1f}"),
                    ]
                },
                "10_geology": {
                    "title": "10. Geological Stability",
                    "items": [
                        (">30° slopes within 1 km", f"{s['steep_frac_1km']*100:.0f}%"),
                        ("Nearest >30° slope", _fmt(s['d_steep_m'], ".0f", " m") if s['d_steep_m'] < 1e9 else "none within 1 km at LOLA 80 m resolution"),
                        ("Slope-failure risk (LOLA proxy)", "LOW — no >30° slopes within 1 km" if s['steep_frac_1km'] == 0 else f"ELEVATED — {s['steep_frac_1km']*100:.0f}% of 1 km area exceeds 30°"),
                        ("Fractures / faults", "No measurement available at this site. GRAIL gravity (GRGM1200B) gives ~10 km resolution global context but cannot resolve metre-scale fractures. Requires LROC NAC stereo DEMs or ground-truth."),
                        ("Lava tubes / subsurface voids", "No measurement available. GRAIL gravity anomalies and Mini-RF radar are the relevant datasets but are not integrated here. Pit entrances visible in LROC NAC imagery would be a surface indicator."),
                    ]
                },
                "11_radiation": {
                    "title": "11. Radiation & Space Environment",
                    "items": [
                        ("GCR dose rate",
                            f"~{e['rad']['gcr_usv_day']:.0f} µSv/day (~{e['rad']['gcr_usv_hour']:.0f} µSv/h)"
                            f" [Chang'e-4 LND, Zhang+2020, doi:10.1126/sciadv.aaz1334 — solar min, global ref]"),
                        ("Mission dose (GCR)", f"~{e['rad']['mission_msv']:.1f} mSv over {cfg.days} days"),
                        ("ISS reference", f"{e['rad']['iss_usv_day']:.0f} µSv/day"),
                        ("Solar particle events (SEP)", _sep_climatology(e['rad']['sep'], arrival_dt, cfg.days)),
                        ("SEP model", "ESP (Jiggens+2012) · JPL-23 (Feynman+1993) · Whitman+2023"),
                        ("SEP data source", "DONKI historical tally — context only, not predictive. "
                            + (f"{e['rad']['sep']['n']} events to {e['rad']['sep']['end'].strftime('%Y-%m-%d')}"
                               if e['rad']['sep'] else "unavailable")),
                        ("Micrometeoroid flux (≥1 mm)", f"{e['mmod'][2][1]:.2e} impacts/m²/day"),
                        ("Micrometeoroid model & uncertainty", "Grün 1985 interplanetary flux @1 AU; no lunar focusing/shielding correction (±~2-3×)"),
                    ]
                },
            }
        }
        _SOURCES = {
            "1_terrain":      "[LOLA] NASA LRO LOLA DEM & slope (PGDA/GSFC)",
            "2_roughness":    "[LOLA] NASA LOLA roughness product (PGDA/GSFC)",
            "3_craters":      "[LOLA] DEM morphological closing (≥240 m craters) + LOLA roughness product. Sub-240 m hazards require LROC NAC.",
            "4_landing_zone": "[LOLA] NASA LOLA slope + DEM — LOLA-resolvable (≥80 m) only. Sub-80 m boulders/craters UNVERIFIED — requires LROC NAC.",
            "5_illumination": "[HORIZONS] NASA JPL Horizons + [LOLA] terrain horizon",
            "6_shadowing":    "[LOLA] NASA LOLA DEM horizon profile",
            "7_thermal":      "[MODEL] 1-D regolith conduction (Vasavada 2012 / Hayne 2017), forced by Horizons + LOLA",
            "8_comms":        "[HORIZONS] NASA JPL Horizons + [LOLA] terrain horizon",
            "9_soil":         "[REFERENCE] Lunar Sourcebook / NASA NTRS - not site-specific",
            "10_geology":     "[LOLA] NASA LOLA slope analysis. Fractures/lava tubes/faults: no measurement integrated — GRAIL and Mini-RF not accessible at pixel level.",
            "11_radiation":   "[REF] Chang'e-4 LND GCR (Zhang+2020) · [MODEL] ESP/JPL-23 SEP climatology · [MODEL] Grün 1985 meteoroid flux · [DONKI] historical SEP tally (context only)",
        }
        for _sk in _SOURCES:
            site_data["sections"][_sk]["source"] = _SOURCES[_sk]
        out["sites"].append(site_data)

    return out


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json()
    date_str = data.get("date", "")
    time_str = data.get("time", "00:00")
    days = float(data.get("days", 6.5))

    import re
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        return jsonify({"error": "Invalid date format"}), 400
    if not re.fullmatch(r"\d{2}:\d{2}", time_str):
        time_str = "00:00"

    job_id = f"{date_str}_{time_str}_{days}_{time.time()}"
    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "log": [], "result": None}

    t = threading.Thread(target=_run_analysis, args=(job_id, date_str, time_str, days), daemon=True)
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    return jsonify({
        "status": job["status"],
        "log": job["log"],
        "result": job["result"],
    })


if __name__ == "__main__":
    host = "0.0.0.0"
    port = 5000
    url = f"http://localhost:{port}"
    print("\n  Lunar Landing Navigator")
    print(f"  Open your browser at:  {url}")
    print(f"  Server listening on:   http://{host}:{port}\n")
    app.run(debug=False, host=host, port=port, threaded=True)
