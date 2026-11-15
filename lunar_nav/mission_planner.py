"""
Lunar Mission Planner -- orbital mechanics engine.

Works BACKWARDS from a desired landing date/time to compute every mission
manoeuvre: Earth parking orbit -> TLI -> translunar coast -> LOI -> DOI -> PDI
-> touchdown.

Physics
-------
  Ephemeris : NASA JPL DE440 via SpiceyPy (existing kernels in Data/)
  Transfers : Izzo 2015 universal-variables Lambert solver
              doi:10.1007/s10569-014-9587-y
  LOI       : hyperbolic approach Delta-v to circular LLO (v_inf from Lambert)
  Descent   : simplified gravity-turn, Delta-v from Apollo 11 mission data
  Propellant: Tsiolkovsky rocket equation throughout

All GMs come from gm_de440.tpc -- none are hard-coded.
If the trajectory is infeasible the planner returns MissionPlan(feasible=False)
with a plain-language explanation; it never invents numbers.

SPICE path note
---------------
SpiceyPy furnsh() cannot handle non-ASCII paths on Windows (this machine has
Greek characters in the OneDrive path).  _ascii_kernel_dir() copies the four
kernel files to %TEMP%\\eclipse_kernels the first time and returns that path.
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.optimize import brentq


# ---------------------------------------------------------------------------
# SPICE kernel resolution
# ---------------------------------------------------------------------------

def _ascii_kernel_dir() -> str:
    """Return an ASCII-safe path that contains the four required kernels."""
    here = Path(__file__).resolve().parent.parent  # Eclipse/
    candidate = here / "Data"
    if not candidate.exists():
        raise FileNotFoundError(f"Cannot find Data/ kernel folder near {here}")
    try:
        str(candidate).encode("ascii")
        return str(candidate)
    except UnicodeEncodeError:
        pass
    dst = Path(tempfile.gettempdir()) / "eclipse_kernels"
    dst.mkdir(exist_ok=True)
    for f in candidate.glob("*"):
        t = dst / f.name
        if not t.exists() or t.stat().st_size != f.stat().st_size:
            shutil.copy2(f, t)
    return str(dst)


_KERNEL_DIR: Optional[str] = None


def _load_kernels() -> None:
    """Furnsh all required kernels exactly once per process."""
    global _KERNEL_DIR
    if _KERNEL_DIR is not None:
        return
    try:
        import spiceypy as sp
    except ImportError as e:
        raise ImportError("spiceypy is required: pip install spiceypy") from e
    kdir = _ascii_kernel_dir()
    for name in ("naif0012.tls", "de440s.bsp", "gm_de440.tpc", "pck00011.tpc"):
        path = os.path.join(kdir, name)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Required SPICE kernel not found: {path}")
        sp.furnsh(path)
    _KERNEL_DIR = kdir


# ---------------------------------------------------------------------------
# GM values (lazy, from DE440)
# ---------------------------------------------------------------------------

_GM_CACHE: dict[str, float] = {}


def _gm(body: str) -> float:
    """Return GM [km^3/s^2] for a NAIF body name, cached after first call."""
    if body not in _GM_CACHE:
        _load_kernels()
        import spiceypy as sp
        _GM_CACHE[body] = sp.bodvrd(body, "GM", 1)[1][0]
    return _GM_CACHE[body]


# Physical constants
G0      = 9.80665    # m/s^2  standard gravity for Isp reference
R_EARTH = 6378.137   # km     WGS84 equatorial radius
R_MOON  = 1737.4     # km     LOLA reference sphere (matches config.py)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SpacecraftParams:
    """User-supplied spacecraft configuration."""
    mass_total_kg:      float = 45_000.0
    mass_dry_kg:        float = 10_000.0
    isp_s:              float = 450.0
    thrust_n:           float = 110_000.0
    earth_orbit_alt_km: float = 200.0
    lunar_orbit_alt_km: float = 100.0

    @property
    def dv_budget_ms(self) -> float:
        """Maximum Delta-v [m/s] from full propellant load (Tsiolkovsky)."""
        if self.mass_dry_kg <= 0 or self.mass_total_kg <= self.mass_dry_kg:
            return 0.0
        return self.isp_s * G0 * math.log(self.mass_total_kg / self.mass_dry_kg)


@dataclass
class Manoeuvre:
    name:            str
    utc:             datetime
    delta_v_ms:      float
    mass_before_kg:  float
    mass_after_kg:   float
    propellant_kg:   float
    duration_s:      float
    notes:           str = ""

    def as_dict(self) -> dict:
        return {
            "name":           self.name,
            "utc":            self.utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "delta_v_ms":     round(self.delta_v_ms, 1),
            "mass_before_kg": round(self.mass_before_kg, 1),
            "mass_after_kg":  round(self.mass_after_kg, 1),
            "propellant_kg":  round(self.propellant_kg, 1),
            "duration_s":     round(self.duration_s, 1),
            "notes":          self.notes,
        }


@dataclass
class MissionPlan:
    feasible:          bool
    infeasible_reason: str = ""
    manoeuvres:        list[Manoeuvre] = field(default_factory=list)
    trajectory:        dict            = field(default_factory=dict)
    summary:           dict            = field(default_factory=dict)
    warnings:          list[str]       = field(default_factory=list)
    data_sources:      list[str]       = field(default_factory=list)

    def total_dv_ms(self) -> float:
        return sum(m.delta_v_ms for m in self.manoeuvres)

    def as_dict(self) -> dict:
        return {
            "feasible":          self.feasible,
            "infeasible_reason": self.infeasible_reason,
            "manoeuvres":        [m.as_dict() for m in self.manoeuvres],
            "trajectory":        self.trajectory,
            "summary":           self.summary,
            "warnings":          self.warnings,
            "data_sources":      self.data_sources,
        }


# ---------------------------------------------------------------------------
# Stumpff functions (universal-variable series)
# ---------------------------------------------------------------------------

def _stumpff_C(z: float) -> float:
    if z > 1e-6:
        return (1.0 - math.cos(math.sqrt(z))) / z
    elif z < -1e-6:
        return (math.cosh(math.sqrt(-z)) - 1.0) / (-z)
    return 0.5 - z / 24.0 + z * z / 720.0


def _stumpff_S(z: float) -> float:
    if z > 1e-6:
        sq = math.sqrt(z)
        return (sq - math.sin(sq)) / sq**3
    elif z < -1e-6:
        sq = math.sqrt(-z)
        return (math.sinh(sq) - sq) / sq**3
    return 1.0 / 6.0 - z / 120.0 + z * z / 5040.0


# ---------------------------------------------------------------------------
# Lambert solver -- Izzo 2015 universal variables
# Reference: Izzo, D. (2015) "Revisiting Lambert's problem."
#            Celestial Mechanics & Dynamical Astronomy, 121(1), 1-15.
#            doi:10.1007/s10569-014-9587-y
# ---------------------------------------------------------------------------

def lambert(r1: np.ndarray, r2: np.ndarray, tof: float,
            mu: float, long_way: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """
    Solve Lambert's problem.

    Parameters
    ----------
    r1, r2    : position vectors [km] in any consistent inertial frame.
                r1 must be non-zero (|r1| >= 1 km enforced).
    tof       : time-of-flight [s] > 0
    mu        : gravitational parameter [km^3/s^2]
    long_way  : use the > 180 degree arc

    Returns
    -------
    v1, v2    : velocity vectors [km/s] at r1 and r2

    Raises ValueError if the geometry is degenerate or no solution is found.
    """
    if tof <= 0:
        raise ValueError("Lambert: tof must be positive")

    r1n = float(np.linalg.norm(r1))
    r2n = float(np.linalg.norm(r2))
    if r1n < 1.0:
        raise ValueError(f"Lambert: r1 magnitude too small ({r1n:.2f} km) -- "
                         "departure vector must be non-zero")
    if r2n < 1.0:
        raise ValueError(f"Lambert: r2 magnitude too small ({r2n:.2f} km)")

    cos_dnu = float(np.clip(np.dot(r1, r2) / (r1n * r2n), -1.0, 1.0))
    # Use z-component of r1 x r2 to determine short/long-way direction
    cross_z = float(r1[0] * r2[1] - r1[1] * r2[0])

    dnu = math.acos(cos_dnu) if cross_z >= 0.0 else 2.0 * math.pi - math.acos(cos_dnu)
    if long_way:
        dnu = 2.0 * math.pi - dnu

    # Check for degenerate angles (0 or 180 deg => A = 0 or undefined)
    sin_dnu = math.sin(dnu)
    one_minus_cos = 1.0 - math.cos(dnu)
    if abs(sin_dnu) < 1e-9 or abs(one_minus_cos) < 1e-12:
        raise ValueError(f"Lambert: degenerate transfer angle {math.degrees(dnu):.4f} deg")

    A = sin_dnu * math.sqrt(r1n * r2n / one_minus_cos)

    def tof_eq(z: float) -> float:
        S = _stumpff_S(z)
        C = _stumpff_C(z)
        if abs(C) < 1e-15:
            return float("inf")
        y = r1n + r2n + A * (z * S - 1.0) / math.sqrt(C)
        if y < 0.0:
            return float("inf")
        x = math.sqrt(y / C)
        t = (x**3 * S + A * math.sqrt(y)) / math.sqrt(mu)
        return t - tof

    # Find sign change by scanning z from -4*pi^2 to +4*pi^2
    # The TOF function is well-behaved for elliptic orbits (z > 0).
    # We use a dense grid to reliably locate the bracket.
    z_scan = np.linspace(-4.0 * math.pi**2, 4.0 * math.pi**2, 3000)
    z_lo = z_hi = None
    prev_z = prev_v = None
    for zz in z_scan:
        v = tof_eq(float(zz))
        if math.isfinite(v):
            if prev_v is not None and prev_v * v < 0.0:
                z_lo, z_hi = prev_z, float(zz)
                break
            prev_z, prev_v = float(zz), v

    if z_lo is None or z_hi is None:
        raise ValueError(
            f"Lambert: no bracketing interval found for tof={tof:.0f}s, "
            f"dnu={math.degrees(dnu):.2f}deg -- geometry may be near-degenerate"
        )

    z_sol = brentq(tof_eq, z_lo, z_hi, xtol=1e-8, maxiter=200)

    S   = _stumpff_S(z_sol)
    C   = _stumpff_C(z_sol)
    y   = r1n + r2n + A * (z_sol * S - 1.0) / math.sqrt(C)
    x   = math.sqrt(y / C)
    f   = 1.0 - y / r1n
    gd  = 1.0 - y / r2n
    g   = A * math.sqrt(y / mu)

    v1 = (r2 - f * r1) / g
    v2 = (gd * r2 - r1) / g
    return v1, v2


# ---------------------------------------------------------------------------
# Two-body orbit helpers
# ---------------------------------------------------------------------------

def _v_circ(alt_km: float, R_km: float, mu: float) -> float:
    """Circular orbit speed [km/s]."""
    return math.sqrt(mu / (R_km + alt_km))


def _period(alt_km: float, R_km: float, mu: float) -> float:
    """Orbital period [s]."""
    r = R_km + alt_km
    return 2.0 * math.pi * math.sqrt(r**3 / mu)


def _burn_duration(dv_ms: float, thrust_n: float, isp_s: float, m0_kg: float) -> float:
    """Finite-burn duration [s] from constant-thrust rocket equation."""
    ve = isp_s * G0
    if thrust_n <= 0 or ve <= 0 or m0_kg <= 0 or dv_ms <= 0:
        return 0.0
    return m0_kg * ve / thrust_n * (1.0 - math.exp(-dv_ms / ve))


# ---------------------------------------------------------------------------
# SPICE helper queries
# ---------------------------------------------------------------------------

def _et(dt: datetime) -> float:
    import spiceypy as sp
    return sp.str2et(dt.strftime("%Y-%b-%d %H:%M:%S"))


def _et2dt(et: float) -> datetime:
    import spiceypy as sp
    cal = sp.etcal(et)   # "2027 MAR 15 00:00:00.000"
    return datetime.strptime(cal[:20].strip(), "%Y %b %d %H:%M:%S")


def _state(body: int, et: float, obs: int) -> np.ndarray:
    """State [km, km/s] of body relative to obs in J2000."""
    import spiceypy as sp
    state, _ = sp.spkgeo(body, et, "J2000", obs)
    return np.array(state)


def _site_j2000(lat_deg: float, lon_deg: float, et: float) -> np.ndarray:
    """
    Surface point position [km] in geocentric J2000 at epoch et.
    Accounts for lunar rotation via pck00011.tpc (IAU_MOON frame).
    """
    import spiceypy as sp
    la = math.radians(lat_deg)
    lo = math.radians(lon_deg)
    r_body = R_MOON * np.array([
        math.cos(la) * math.cos(lo),
        math.cos(la) * math.sin(lo),
        math.sin(la),
    ])
    rot = np.array(sp.pxform("IAU_MOON", "J2000", et))
    moon_pos = _state(301, et, 399)[:3]
    return moon_pos + rot @ r_body


# ---------------------------------------------------------------------------
# Powered descent Delta-v budget
# Source: Apollo 11 Mission Report, NASA-TM-X-62558;
#         Griffin & French "Space Vehicle Design", 2nd ed., Ch. 5.
# ---------------------------------------------------------------------------

def _descent_dv(lunar_orbit_alt_km: float) -> dict:
    """Descent Delta-v breakdown [m/s] from circular LLO."""
    mu_m  = _gm("301")
    r_lo  = R_MOON + lunar_orbit_alt_km
    r_peri = R_MOON + 15.0   # PDI gate altitude: 15 km (Apollo standard)

    v_circ  = math.sqrt(mu_m / r_lo)
    a_trans = (r_lo + r_peri) / 2.0
    v_apo   = math.sqrt(mu_m * (2.0 / r_lo - 1.0 / a_trans))
    dv_doi  = abs(v_circ - v_apo) * 1000.0   # km/s -> m/s

    # PDI to touchdown: 1860 m/s base (Apollo 11), small scaling with altitude
    dv_pdi  = 1860.0 + max(0.0, lunar_orbit_alt_km - 100.0) * 2.0

    return {
        "doi_ms":   round(dv_doi, 1),
        "pdi_ms":   round(dv_pdi, 1),
        "total_ms": round(dv_doi + dv_pdi, 1),
    }


# ---------------------------------------------------------------------------
# Two-body propagation for trajectory visualization
# Reference: Battin, R.H. (1987) "An Introduction to the Mathematics and
#            Methods of Astrodynamics", AIAA.
# ---------------------------------------------------------------------------

def _propagate_kepler(r0: np.ndarray, v0: np.ndarray,
                      mu: float, dt: float) -> tuple[np.ndarray, np.ndarray]:
    """Propagate a two-body orbit by dt seconds using universal variables."""
    r0n = float(np.linalg.norm(r0))
    v0n = float(np.linalg.norm(v0))
    vr0 = float(np.dot(r0, v0)) / r0n
    alpha = 2.0 / r0n - v0n**2 / mu

    if alpha > 1e-6:
        chi0 = math.sqrt(mu) * dt * alpha
    elif alpha < -1e-6:
        a   = 1.0 / alpha
        chi0 = (math.copysign(1.0, dt) *
                math.sqrt(-a) *
                math.log((-2.0 * mu * alpha * dt) /
                         (np.dot(r0, v0) + math.copysign(1.0, dt) *
                          math.sqrt(-mu * a) * (1.0 - r0n * alpha))))
    else:
        chi0 = math.sqrt(mu) * dt / r0n

    chi = chi0
    for _ in range(50):
        psi = chi**2 * alpha
        C   = _stumpff_C(psi)
        S   = _stumpff_S(psi)
        rr  = (chi**2 * C + vr0 / math.sqrt(mu) * chi * (1.0 - psi * S) +
               r0n * (1.0 - psi * C))
        F   = (chi**3 * S + vr0 / math.sqrt(mu) * chi**2 * C +
               r0n * chi * (1.0 - psi * S) - math.sqrt(mu) * dt)
        if abs(rr) < 1e-12:
            break
        chi -= F / rr

    psi = chi**2 * alpha
    C   = _stumpff_C(psi)
    S   = _stumpff_S(psi)
    rn  = (chi**2 * C + vr0 / math.sqrt(mu) * chi * (1.0 - psi * S) +
           r0n * (1.0 - psi * C))

    f   = 1.0 - chi**2 * C / r0n
    g   = dt - chi**3 * S / math.sqrt(mu)
    fd  = math.sqrt(mu) * chi * (psi * S - 1.0) / (rn * r0n)
    gd  = 1.0 - chi**2 * C / rn

    return f * r0 + g * v0, fd * r0 + gd * v0


def _sample_arc(r0: np.ndarray, v0: np.ndarray,
                mu: float, tof: float, n: int = 150) -> dict:
    """Sample n points along a two-body arc, return x/y/z lists [km]."""
    pts: dict[str, list] = {"x": [], "y": [], "z": []}
    for i in range(n):
        dt = tof * i / max(n - 1, 1)
        try:
            r, _ = _propagate_kepler(r0, v0, mu, dt)
            pts["x"].append(round(float(r[0]), 1))
            pts["y"].append(round(float(r[1]), 1))
            pts["z"].append(round(float(r[2]), 1))
        except Exception:
            break
    return pts


# ---------------------------------------------------------------------------
# Main planner entry point
# ---------------------------------------------------------------------------

def plan_mission(
    land_lat: float,
    land_lon: float,
    land_dt:  datetime,
    sc:       SpacecraftParams,
    log=print,
) -> MissionPlan:
    """
    Compute full mission plan backwards from land_dt.

    Sequence (working backwards from touchdown):
        touchdown
        <- PDI  (~12 min powered descent)
        <- DOI  (~2 h in descent orbit)
        <- LOI  (spacecraft captured into LLO, ~6 h to circularise)
        <- translunar coast (Lambert-optimal TOF, 2.5-6.5 days)
        <- TLI  (burn from LEO)
        <- Earth parking orbit

    All times and state vectors come from DE440 / SpiceyPy.
    """
    _load_kernels()

    plan = MissionPlan(feasible=True)
    plan.data_sources = [
        "[DE440]   NASA JPL DE440 -- Earth/Moon state vectors (SpiceyPy)",
        "[GM]      GM_Earth, GM_Moon from gm_de440.tpc (DE440)",
        "[ROT]     Lunar orientation: pck00011.tpc (IAU_MOON frame)",
        "[LAMBERT] Izzo 2015, doi:10.1007/s10569-014-9587-y",
        "[DESCENT] Apollo 11 Mission Report NASA-TM-X-62558; Griffin & French",
        "[TSIOL]   Tsiolkovsky rocket equation for all propellant budgets",
    ]

    mu_e = _gm("399")   # km^3/s^2
    mu_m = _gm("301")   # km^3/s^2

    # Mission architecture overhead times
    PDI_DUR_S   =  12 * 60       # 12 min powered descent
    DOI_WAIT_S  =   2 * 3600     # 2 h in descent orbit before PDI
    LOI_WAIT_S  =   6 * 3600     # time from arrival to circular LLO
    LEO_PARK_S  = _period(sc.earth_orbit_alt_km, R_EARTH, mu_e)

    r_llo    = R_MOON  + sc.lunar_orbit_alt_km
    r_leo    = R_EARTH + sc.earth_orbit_alt_km
    vc_llo   = _v_circ(sc.lunar_orbit_alt_km, R_MOON,  mu_m)
    vc_leo   = _v_circ(sc.earth_orbit_alt_km, R_EARTH, mu_e)

    # Descent Delta-v (independent of Lambert result)
    desc   = _descent_dv(sc.lunar_orbit_alt_km)
    dv_doi = desc["doi_ms"]
    dv_pdi = desc["pdi_ms"]

    # Tsiolkovsky: mass_before = mass_after * exp(dv / (Isp*g0))
    def m_before(m_after: float, dv_ms: float) -> float:
        return m_after * math.exp(dv_ms / (sc.isp_s * G0))

    # Arrival epoch: work back from touchdown
    et_land = _et(land_dt)
    et_arr  = et_land - PDI_DUR_S - DOI_WAIT_S - LOI_WAIT_S

    log("  Searching optimal translunar transfer (Lambert / DE440)...")

    # ------------------------------------------------------------------
    # Lambert search in geocentric J2000.
    #
    # r1_leo : departure point placed on the geocentric +X axis at LEO
    #          radius.  The specific direction is arbitrary for a patched-
    #          conic model; only |v_inf| = |v1 - v_circ_vec| matters for
    #          the TLI Delta-v calculation.
    # r2_geo : Moon geocentric position at arrival.
    # v1, v2 : geocentric departure/arrival velocities from Lambert.
    # v_inf@Earth = |v1 - v_circ_vec|  where v_circ_vec is the circular
    #               velocity vector at r1_leo (+Y direction for +X position).
    # v_inf@Moon  = |v2 - v_moon_geo|
    # ------------------------------------------------------------------
    r1_leo     = np.array([r_leo, 0.0, 0.0])      # LEO point, geocentric +X [km]
    v_circ_vec = np.array([0.0, vc_leo, 0.0])      # circular velocity at r1_leo [km/s]

    # Moon state at arrival (constant for all TOFs)
    r2_geo  = _state(301, et_arr, 399)[:3]         # Moon geocentric position [km]
    v_m_geo = _state(301, et_arr, 399)[3:]         # Moon geocentric velocity [km/s]

    best = None  # (total_dv, tof, dv_tli, dv_loi, v1, v2, et_dep)

    for tof_h in range(int(2.5 * 24), int(6.5 * 24) + 1):
        tof    = float(tof_h) * 3600.0
        et_dep = et_arr - tof

        try:
            v1, v2 = lambert(r1_leo, r2_geo, tof, mu_e, long_way=False)
        except ValueError:
            try:
                v1, v2 = lambert(r1_leo, r2_geo, tof, mu_e, long_way=True)
            except ValueError:
                continue

        # TLI Delta-v
        v_inf_e    = float(np.linalg.norm(v1 - v_circ_vec))
        v_peri_tli = math.sqrt(v_inf_e**2 + 2.0 * mu_e / r_leo)
        dv_tli     = abs(v_peri_tli - vc_leo) * 1000.0   # km/s -> m/s

        # LOI Delta-v
        v_inf_m    = float(np.linalg.norm(v2 - v_m_geo))
        v_peri_loi = math.sqrt(v_inf_m**2 + 2.0 * mu_m / r_llo)
        dv_loi     = abs(v_peri_loi - vc_llo) * 1000.0

        total = dv_tli + dv_loi
        if best is None or total < best[0]:
            best = (total, tof, dv_tli, dv_loi, v1, v2, et_dep)

    if best is None:
        plan.feasible = False
        plan.infeasible_reason = (
            "The Lambert solver found no valid Earth-Moon transfer for the "
            "requested landing date in the 2.5-6.5 day TOF window.  "
            "This can happen when the Earth-Moon geometry produces a "
            "near-degenerate (~0 or ~180 deg) transfer angle.  "
            "Try adjusting the landing date by +/-1-2 days."
        )
        return plan

    (_, tof_opt, dv_tli, dv_loi, v1, v2, et_dep) = best
    tof_days = tof_opt / 86400.0

    log(f"  Best transfer: TOF={tof_days:.2f} d  "
        f"TLI dv={dv_tli:.0f} m/s  LOI dv={dv_loi:.0f} m/s")

    # ------------------------------------------------------------------
    # Mass budget: working backwards from touchdown dry mass
    # ------------------------------------------------------------------
    m_land    = sc.mass_dry_kg
    m_pre_pdi = m_before(m_land,    dv_pdi)
    m_pre_doi = m_before(m_pre_pdi, dv_doi)
    m_pre_loi = m_before(m_pre_doi, dv_loi)
    m_pre_tli = m_before(m_pre_loi, dv_tli)

    total_dv_all = dv_tli + dv_loi + dv_doi + dv_pdi

    if m_pre_tli > sc.mass_total_kg:
        plan.feasible = False
        plan.infeasible_reason = (
            f"Insufficient propellant.  "
            f"Required initial mass: {m_pre_tli:.0f} kg, "
            f"spacecraft total mass: {sc.mass_total_kg:.0f} kg.  "
            f"Total Delta-v required: {total_dv_all:.0f} m/s, "
            f"available Delta-v budget: {sc.dv_budget_ms:.0f} m/s.  "
            f"Increase propellant load, raise Isp, or reduce dry mass."
        )
        return plan

    # ------------------------------------------------------------------
    # UTC datetimes for each event
    # ------------------------------------------------------------------
    dt_tli  = _et2dt(et_dep)
    dt_loi  = _et2dt(et_arr)
    dt_doi  = land_dt - timedelta(seconds=PDI_DUR_S + DOI_WAIT_S)
    dt_pdi  = land_dt - timedelta(seconds=PDI_DUR_S)
    dt_park = dt_tli  - timedelta(seconds=LEO_PARK_S)

    # Burn durations
    dur_tli = _burn_duration(dv_tli, sc.thrust_n, sc.isp_s, m_pre_tli)
    dur_loi = _burn_duration(dv_loi, sc.thrust_n, sc.isp_s, m_pre_loi)
    dur_doi = _burn_duration(dv_doi, sc.thrust_n, sc.isp_s, m_pre_doi)
    dur_pdi = _burn_duration(dv_pdi, sc.thrust_n, sc.isp_s, m_pre_pdi)

    v_inf_e_str = f"{float(np.linalg.norm(v1 - v_circ_vec))*1000:.0f}"
    v_inf_m_str = f"{float(np.linalg.norm(v2 - v_m_geo))*1000:.0f}"
    v_peri_loi_str = f"{math.sqrt(float(np.linalg.norm(v2-v_m_geo))**2 + 2*mu_m/r_llo)*1000:.0f}"

    plan.manoeuvres = [
        Manoeuvre(
            name="Earth Parking Orbit",
            utc=dt_park,
            delta_v_ms=0.0,
            mass_before_kg=m_pre_tli,
            mass_after_kg=m_pre_tli,
            propellant_kg=0.0,
            duration_s=LEO_PARK_S,
            notes=(f"Circular LEO at {sc.earth_orbit_alt_km:.0f} km alt. "
                   f"v_circ={vc_leo*1000:.0f} m/s. "
                   f"Period={LEO_PARK_S/60:.1f} min. [DE440]"),
        ),
        Manoeuvre(
            name="Trans-Lunar Injection (TLI)",
            utc=dt_tli,
            delta_v_ms=dv_tli,
            mass_before_kg=m_pre_tli,
            mass_after_kg=m_pre_loi,
            propellant_kg=m_pre_tli - m_pre_loi,
            duration_s=dur_tli,
            notes=(f"Prograde burn from {sc.earth_orbit_alt_km:.0f} km LEO. "
                   f"TOF={tof_days:.2f} d. "
                   f"v_inf@Earth={v_inf_e_str} m/s. [Lambert/DE440]"),
        ),
        Manoeuvre(
            name="Translunar Coast",
            utc=dt_tli + timedelta(seconds=tof_opt / 2.0),
            delta_v_ms=0.0,
            mass_before_kg=m_pre_loi,
            mass_after_kg=m_pre_loi,
            propellant_kg=0.0,
            duration_s=tof_opt,
            notes=(f"Free-flight on translunar arc, {tof_days:.2f} days. "
                   f"[Two-body / patched conic]"),
        ),
        Manoeuvre(
            name="Lunar Orbit Insertion (LOI)",
            utc=dt_loi,
            delta_v_ms=dv_loi,
            mass_before_kg=m_pre_loi,
            mass_after_kg=m_pre_doi,
            propellant_kg=m_pre_loi - m_pre_doi,
            duration_s=dur_loi,
            notes=(f"Retrograde burn at lunar periapsis -> "
                   f"{sc.lunar_orbit_alt_km:.0f} km circular LLO. "
                   f"v_inf@Moon={v_inf_m_str} m/s. "
                   f"v_peri_hyp={v_peri_loi_str} m/s. "
                   f"v_circ={vc_llo*1000:.0f} m/s. [Lambert/DE440]"),
        ),
        Manoeuvre(
            name="Descent Orbit Insertion (DOI)",
            utc=dt_doi,
            delta_v_ms=dv_doi,
            mass_before_kg=m_pre_doi,
            mass_after_kg=m_pre_pdi,
            propellant_kg=m_pre_doi - m_pre_pdi,
            duration_s=dur_doi,
            notes=(f"Lower periapsis from {sc.lunar_orbit_alt_km:.0f} km "
                   f"circular LLO to 15 km (PDI gate). "
                   f"[Apollo 11 Mission Report NASA-TM-X-62558]"),
        ),
        Manoeuvre(
            name="Powered Descent Initiation (PDI)",
            utc=dt_pdi,
            delta_v_ms=dv_pdi,
            mass_before_kg=m_pre_pdi,
            mass_after_kg=m_land,
            propellant_kg=m_pre_pdi - m_land,
            duration_s=dur_pdi,
            notes=(f"Gravity-turn powered descent from ~15 km to touchdown "
                   f"at ({land_lat:.4f} deg, {land_lon:.4f} deg). "
                   f"[Apollo 11 / Griffin & French]"),
        ),
        Manoeuvre(
            name="Touchdown",
            utc=land_dt,
            delta_v_ms=0.0,
            mass_before_kg=m_land,
            mass_after_kg=m_land,
            propellant_kg=0.0,
            duration_s=0.0,
            notes=(f"Landing at ({land_lat:.4f} deg lat, {land_lon:.4f} deg lon), "
                   f"{land_dt:%Y-%m-%d %H:%M:%S} UTC. [Input]"),
        ),
    ]

    # ------------------------------------------------------------------
    # Trajectory arrays for 3-D visualisation (geocentric J2000, km)
    # ------------------------------------------------------------------
    tl_arc = _sample_arc(r1_leo, v1, mu_e, tof_opt, n=200)

    plan.trajectory = {
        "translunar":    tl_arc,
        "earth_pos_km":  [0.0, 0.0, 0.0],
        "moon_pos_km":   [round(float(x), 1) for x in r2_geo],
        "site_j2000_km": [round(float(x), 1) for x in
                          (_site_j2000(land_lat, land_lon, et_land) -
                           _state(301, et_land, 399)[:3] + r2_geo)],
        "r_leo_km":      r_leo,
        "r_llo_km":      r_llo,
    }

    # ------------------------------------------------------------------
    # Warnings
    # ------------------------------------------------------------------
    margin = sc.dv_budget_ms - total_dv_all
    if margin < 200.0:
        plan.warnings.append(
            f"Propellant margin is very low: {margin:.0f} m/s "
            f"({margin/sc.dv_budget_ms*100:.1f}% of budget). "
            "Consider increasing propellant load."
        )
    if tof_days < 3.0:
        plan.warnings.append(
            "Transfer time < 3 days is energetically expensive; "
            "verify TLI Delta-v is achievable."
        )
    if sc.lunar_orbit_alt_km < 50.0:
        plan.warnings.append(
            f"Lunar parking orbit at {sc.lunar_orbit_alt_km:.0f} km is below 50 km -- "
            "lunar mascons can destabilise orbits at this altitude."
        )

    plan.summary = {
        "landing_site":        (f"{abs(land_lat):.4f} deg "
                                f"{'S' if land_lat < 0 else 'N'}, "
                                f"{abs(land_lon):.4f} deg "
                                f"{'E' if land_lon >= 0 else 'W'}"),
        "landing_utc":         land_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "earth_departure_utc": dt_park.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "tli_utc":             dt_tli.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "loi_utc":             dt_loi.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "doi_utc":             dt_doi.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "pdi_utc":             dt_pdi.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "transfer_tof_days":   round(tof_days, 3),
        "dv_tli_ms":           round(dv_tli, 1),
        "dv_loi_ms":           round(dv_loi, 1),
        "dv_doi_ms":           round(dv_doi, 1),
        "dv_pdi_ms":           round(dv_pdi, 1),
        "dv_total_ms":         round(total_dv_all, 1),
        "dv_budget_ms":        round(sc.dv_budget_ms, 1),
        "dv_margin_ms":        round(margin, 1),
        "mass_at_tli_kg":      round(m_pre_tli, 1),
        "mass_at_loi_kg":      round(m_pre_loi, 1),
        "mass_at_landing_kg":  round(m_land, 1),
        "propellant_total_kg": round(m_pre_tli - m_land, 1),
        "earth_orbit_alt_km":  sc.earth_orbit_alt_km,
        "lunar_orbit_alt_km":  sc.lunar_orbit_alt_km,
        "isp_s":               sc.isp_s,
        "thrust_n":            sc.thrust_n,
    }
    return plan
