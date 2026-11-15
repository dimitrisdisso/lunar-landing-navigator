"""
Basic physics sanity checks.  Run via:  python lunar_navigator.py --selftest
Each test prints PASS / FAIL and returns 0 (all pass) or 1 (any fail).
"""
import math
import numpy as np


def _check(name, got, expected, tol=0.01):
    err = abs(got - expected) / (abs(expected) + 1e-30)
    ok = err < tol
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got:.6g}, expected {expected:.6g} (err {err*100:.2f}%)")
    return ok


def run():
    results = []

    # 1. Stereographic projection: pole maps to origin
    from .geometry import lonlat_to_xy, xy_to_lonlat
    x, y = lonlat_to_xy(0.0, -90.0)
    results.append(_check("pole -> origin x", x, 0.0, tol=1e-6))
    results.append(_check("pole -> origin y", y, 0.0, tol=1e-6))

    # 2. Round-trip lon/lat -> xy -> lon/lat
    lon0, lat0 = -45.0, -85.0
    xr, yr = lonlat_to_xy(lon0, lat0)
    lon1, lat1 = xy_to_lonlat(xr, yr)
    results.append(_check("round-trip lat", lat1, lat0, tol=1e-6))
    results.append(_check("round-trip lon", lon1, lon0, tol=1e-6))

    # 3. Scale factor at pole = 1
    from .geometry import scale_k
    results.append(_check("scale_k at -90", float(scale_k(-90.0)), 1.0, tol=1e-4))

    # 4. Thermal: permanently dark surface settles well below 100 K (cold trap)
    from .thermal import simulate
    q_zero = np.zeros(720)
    T = simulate(q_zero, 3600.0, cycles=3)
    results.append(_check("dark-surface T < 100 K", float(T.mean() < 100.0), 1.0, tol=0.01))

    # 5. Thermal: sunlit surface heats above 300 K
    from .config import SOLAR_CONSTANT, BOND_ALBEDO
    q_full = np.full(720, SOLAR_CONSTANT * (1 - BOND_ALBEDO))
    T2 = simulate(q_full, 3600.0, cycles=4)
    results.append(_check("lit-surface T > 300 K (got {:.0f} K)".format(T2.mean()), float(T2.mean() > 300), 1.0, tol=0.01))

    # 6. Bearing capacity: safety factor > 1 for Apollo-class lander
    from .environment import soil_report
    class _Cfg:
        lander_mass_kg = 7300.0; footpad_diameter_m = 0.94; n_footpads = 4
    so = soil_report(_Cfg())
    sf_lo = so["safety_factor"][0]
    results.append(_check("bearing safety factor > 1", float(sf_lo > 1.0), 1.0, tol=0.01))

    # 7. GCR dose accumulates linearly with days
    from .config import GCR_DOSE_EQ_USV_DAY
    expected_msv = GCR_DOSE_EQ_USV_DAY * 6.5 / 1000.0
    from .environment import radiation_report
    from datetime import datetime
    class _Cfg2:
        days = 6.5; do_donki = False
    rd = radiation_report(_Cfg2(), datetime(2027, 3, 15))
    results.append(_check("GCR mission dose (mSv)", rd["mission_msv"], expected_msv, tol=1e-4))

    # 8. Offline ephemeris: sun_lon changes over synodic period
    from .ephemeris import offline_ephemeris
    from datetime import datetime
    eph = offline_ephemeris(datetime(2027, 3, 15), days=29.53)
    drift = float(np.ptp(eph.sun_lon))
    results.append(_check("sun_lon range > 180 deg over synodic month", float(drift > 180), 1.0, tol=0.01))

    n_pass = sum(results)
    n_fail = len(results) - n_pass
    print(f"\n  {n_pass}/{len(results)} tests passed" + (f"  <-- {n_fail} FAILED" if n_fail else "  -- all OK"))
    return 0 if n_fail == 0 else 1
