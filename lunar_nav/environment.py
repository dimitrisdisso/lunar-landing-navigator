"""
Surface & soil properties (9), radiation & space environment (11).

IMPORTANT HONESTY NOTE: no NASA dataset gives site-specific soil strength or
radiation at metre scale.  Values here are NASA/Apollo/Chang'e-4 reference
measurements and standard engineering models.  They are labelled REFERENCE or
MODEL in the report, never presented as site measurements.
"""
import json
import math
import os
import urllib.request
from datetime import datetime, timedelta

import numpy as np

from . import config as C


# --------------------------------------------------------------- soil (REFERENCE)
def regolith_density(z_cm):
    """Bulk density g/cm^3 vs depth (Lunar Sourcebook fit, NTRS 20220014634)."""
    z = np.asarray(z_cm, float)
    return 1.92 * (z + 12.2) / (z + 18.0)


def _bearing(phi_deg, c_pa, rho_kgm3, B):
    """Vesic bearing capacity of a circular footing on the surface (Df = 0), Pa."""
    phi = math.radians(phi_deg)
    Nq = math.exp(math.pi * math.tan(phi)) * math.tan(math.pi / 4 + phi / 2) ** 2
    Nc = (Nq - 1) / math.tan(phi)
    Ng = 2 * (Nq + 1) * math.tan(phi)
    sc, sg = 1 + Nq / Nc, 0.6
    gamma = rho_kgm3 * C.G_MOON
    return c_pa * Nc * sc + 0.5 * gamma * B * Ng * sg


def soil_report(cfg, terrain_class="highland"):
    B = cfg.footpad_diameter_m
    area = math.pi * B ** 2 / 4
    p_applied = cfg.lander_mass_kg * C.G_MOON / (cfg.n_footpads * area)
    lo = _bearing(C.FRICTION_ANGLE_DEG[0], C.COHESION_SURFACE_KPA[0] * 1e3, 1300.0, B)
    hi = _bearing(C.FRICTION_ANGLE_DEG[1], C.COHESION_SURFACE_KPA[1] * 1e3, 1500.0, B)
    return dict(
        density_surface=float(regolith_density(0)), density_10cm=float(regolith_density(10)),
        density_100cm=float(regolith_density(100)), density_deep=1.92,
        cohesion_surface_kpa=C.COHESION_SURFACE_KPA, cohesion_deep_kpa=C.COHESION_DEEP_KPA,
        friction_deg=C.FRICTION_ANGLE_DEG,
        thickness_m=(10.0, 15.0) if terrain_class == "highland" else (4.0, 5.0),
        applied_kpa=p_applied / 1e3, bearing_kpa=(lo / 1e3, hi / 1e3),
        safety_factor=(lo / p_applied, hi / p_applied),
    )


# --------------------------------------------------------- micrometeoroids (MODEL)
def gruen_flux(m_g):
    """Gruen+1985 cumulative interplanetary flux at 1 AU: particles/m^2/s of mass >= m (grams)."""
    f1 = (2.2e3 * m_g ** 0.306 + 15.0) ** -4.38
    f2 = 1.3e-9 * (m_g + 1e11 * m_g ** 2 + 1e27 * m_g ** 4) ** -0.36
    f3 = 1.3e-16 * (m_g + 1e6 * m_g ** 2) ** -0.85
    return f1 + f2 + f3


def micrometeoroid_report(days):
    rows = []
    for m, label in ((1e-9, "~1 ng (~10 um dust)"), (1e-6, "~1 ug (~100 um)"),
                     (1e-3, "~1 mg (~1 mm)"), (1.0, "~1 g (~6 mm)")):
        per_m2_day = gruen_flux(m) * 86400.0
        rows.append((label, per_m2_day, per_m2_day * days))
    return rows


# --------------------------------------------------------------- radiation
def radiation_report(cfg, date, log=print):
    """Radiation report with cached DONKI data."""
    from . import cache
    
    rep = dict(
        gcr_usv_day=C.GCR_DOSE_EQ_USV_DAY, gcr_usv_hour=C.GCR_DOSE_EQ_USV_DAY / 24.0,
        iss_usv_day=C.ISS_DOSE_EQ_USV_DAY,
        mission_msv=C.GCR_DOSE_EQ_USV_DAY * cfg.days / 1000.0,
        sep=None,
    )
    
    if cfg.do_donki:
        # Check cache first
        cache_key = (date.date().isoformat() if isinstance(date, datetime) else str(date),)
        cached_sep = cache.get("donki_sep", *cache_key)
        
        if cached_sep is not None:
            rep["sep"] = cached_sep
        else:
            try:
                sep_data = donki_sep(date)
                rep["sep"] = sep_data
                # Cache for future queries
                cache.put("donki_sep", sep_data, *cache_key)
            except Exception as e:
                log(f"  ! DONKI unavailable ({e}) - continuing without SEP data")
    
    return rep


def _donki_query(start, end, key):
    """Single DONKI SEP query. Returns parsed list or raises."""
    url = (f"{C.NASA_API}/DONKI/SEP"
           f"?startDate={start:%Y-%m-%d}&endDate={end:%Y-%m-%d}&api_key={key}")
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode() or "[]")


# DONKI max query window = 60 days per request.
# We fire 4 parallel 55-day chunks (~220 days / ~7 months of history).
_DONKI_CHUNK = 55    # days per request (safe margin under the 60-day limit)
_DONKI_N     = 4     # number of parallel chunks


def donki_sep(landing_date):
    """
    Fetch NASA DONKI solar energetic particle events.

    For past landing dates  : queries chunks covering ~7 months up to that date.
    For future landing dates: queries the most recent ~7 months of available data
                              (up to today) and labels the result as a historical
                              baseline - there is no forecast of future SEP events.
    """
    import threading as _th
    key      = os.environ.get("NASA_API_KEY") or C.NASA_API_KEY
    # "Latest available" DONKI data is today, not a hard-coded past date; a fixed
    # date would silently under-count SEP events for any run made after that date.
    now = datetime.utcnow()
    is_future = isinstance(landing_date, datetime) and landing_date > now
    query_end = now if is_future else landing_date

    # Build chunk boundaries (newest first)
    chunks = []
    end = query_end
    for _ in range(_DONKI_N):
        start = end - timedelta(days=_DONKI_CHUNK)
        chunks.append((start, end))
        end = start

    # Fire all chunks in parallel
    results  = [None] * len(chunks)
    errors   = [None] * len(chunks)

    def _fetch(i, s, e):
        try:
            results[i] = _donki_query(s, e, key)
        except Exception as ex:
            errors[i] = ex

    threads = [_th.Thread(target=_fetch, args=(i, s, e)) for i, (s, e) in enumerate(chunks)]
    for t in threads: t.start()
    for t in threads: t.join()

    all_times = []
    for r in results:
        if r:
            for ev in r:
                t = ev.get("eventTime", "")[:10]
                if t:
                    all_times.append(t)

    # If every chunk failed, raise so the caller can log it
    if not any(results) and any(errors):
        raise RuntimeError(f"DONKI: {errors[0]}")

    times = sorted(set(all_times))
    period_start = chunks[-1][0]
    return dict(n=len(times), start=period_start, end=query_end,
                latest=times[-1] if times else None, future=is_future)
