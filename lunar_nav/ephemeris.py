"""
Sun / Earth geometry as seen from the Moon.

LIVE  : NASA JPL Horizons.  One request returns, for the Moon seen from the
        geocentre, the sub-observer (= sub-Earth) point, the sub-solar point,
        the Moon-Sun range and the Earth-Moon range, all in the Moon's
        body-fixed frame.  Local Sun/Earth elevation at ANY surface site then
        follows from spherical geometry (see geometry.sky_position).
DEMO  : analytic approximation with correct periods but arbitrary phases -
        only for testing the pipeline without internet.  NOT for real results.
"""
import math
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from .config import AU_KM, SYNODIC_DAYS

HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"


@dataclass
class Ephemeris:
    start: datetime
    step_h: float
    sun_lon: np.ndarray
    sun_lat: np.ndarray
    earth_lon: np.ndarray
    earth_lat: np.ndarray
    earth_dist_m: np.ndarray
    sun_au: np.ndarray
    source: str

    @property
    def n(self):
        return len(self.sun_lon)

    def hours(self):
        return np.arange(self.n) * self.step_h

    def time_at(self, i):
        return self.start + timedelta(hours=float(i) * self.step_h)


def _norm(tok):
    return re.sub(r"[^a-z]", "", tok.lower())


def fetch_horizons(start, days=31.0, step_min=60):
    stop = start + timedelta(days=days)
    q = {
        "format": "text", "COMMAND": "'301'", "OBJ_DATA": "'NO'", "MAKE_EPHEM": "'YES'",
        "EPHEM_TYPE": "'OBSERVER'", "CENTER": "'500@399'",
        "START_TIME": f"'{start:%Y-%m-%d %H:%M}'", "STOP_TIME": f"'{stop:%Y-%m-%d %H:%M}'",
        "STEP_SIZE": f"'{step_min}m'", "QUANTITIES": "'14,15,19,20'",
        "CSV_FORMAT": "'YES'", "ANG_FORMAT": "'DEG'",
    }
    url = HORIZONS_URL + "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)
    with urllib.request.urlopen(url, timeout=60) as r:
        text = r.read().decode()
    return parse_horizons(text, start, step_min / 60.0)


def parse_horizons(text, start, step_h):
    if "$$SOE" not in text:
        raise RuntimeError("Horizons returned no ephemeris:\n" + text[:500])
    head, rest = text.split("$$SOE", 1)
    body = rest.split("$$EOE")[0]
    hdr = [l for l in head.splitlines() if l.strip() and not l.lstrip().startswith("*")][-1]
    cols = [_norm(c) for c in hdr.split(",")]

    def ix(name):
        if name not in cols:
            raise RuntimeError(f"Horizons column '{name}' not found in header: {hdr}")
        return cols.index(name)

    i_ol, i_ob = ix("obssublon"), ix("obssublat")
    i_sl, i_sb = ix("sunsublon"), ix("sunsublat")
    i_r, i_d = ix("r"), ix("delta")
    rows = [[c.strip() for c in l.split(",")] for l in body.strip().splitlines() if l.strip()]
    g = lambda i: np.array([float(r[i]) for r in rows])
    el, eb, sl, sb, r, d = g(i_ol), g(i_ob), g(i_sl), g(i_sb), g(i_r), g(i_d)
    # unit detection: AU vs km
    r_au = r / AU_KM if r.mean() > 1000 else r
    d_km = d * AU_KM if d.mean() < 10 else d
    # longitude sign: the sub-solar point must move WEST, i.e. east-longitude decreases
    drift = np.mean(np.diff(np.unwrap(np.radians(sl))))
    if drift > 0:
        sl, el = -sl, -el
    return Ephemeris(start, step_h, sl, sb, el, eb, d_km * 1000.0, r_au, "NASA JPL Horizons (live)")


def offline_ephemeris(start, days=31.0, step_min=60):
    """DEMO ONLY: right periods, arbitrary phases (errors of several degrees)."""
    n = int(days * 24 * 60 / step_min) + 1
    t = np.arange(n) * step_min / 1440.0                           # days since start
    t0 = (start - datetime(2000, 1, 6, 18, 14)).total_seconds() / 86400.0
    d = t0 + t
    sun_lon = (180.0 - 360.0 * d / SYNODIC_DAYS + 180.0) % 360.0 - 180.0
    sun_lat = 1.54 * np.sin(2 * np.pi * d / 346.62)
    earth_lat = 6.68 * np.sin(2 * np.pi * d / 27.2122)
    earth_lon = 7.9 * np.sin(2 * np.pi * d / 27.5546)
    dist = 384400e3 * (1 - 0.0549 * np.cos(2 * np.pi * d / 27.5546))
    doy = (start.timetuple().tm_yday + t)
    sun_au = 1.0 - 0.0167 * np.cos(2 * np.pi * (doy - 3) / 365.25)
    return Ephemeris(start, step_min / 60.0, sun_lon, sun_lat, earth_lon, earth_lat, dist, sun_au,
                     "OFFLINE DEMO approximation (not NASA data)")


def get_ephemeris(start, days=31.0, offline=False, log=print):
    """
    Return Sun/Earth geometry for the mission window with caching.

    offline=True  -> analytic approximation (demo only): correct periods but arbitrary
                     phases, errors of several degrees - labelled "not NASA data".
    offline=False -> NASA JPL Horizons (live). Cached for repeated queries.
    """
    from . import cache
    
    # Round start time to nearest hour for cache key consistency
    cache_start = start.replace(minute=0, second=0, microsecond=0)
    cache_key = (cache_start.isoformat(), int(days), offline)
    
    # Check cache first
    cached = cache.get("ephemeris", *cache_key)
    if cached is not None:
        return cached
    
    # Fetch new data
    if offline:
        eph = offline_ephemeris(start, days)
    else:
        try:
            eph = fetch_horizons(start, days)
        except Exception as e:
            raise RuntimeError(
                "NASA JPL Horizons ephemeris is unavailable (%s); the offline fallback "
                "is only accurate to several degrees and is not used for real results. "
                "Check your connection, or re-run with --demo for a synthetic test." % e
            ) from e
    
    # Cache the result
    cache.put("ephemeris", eph, *cache_key)
    return eph
