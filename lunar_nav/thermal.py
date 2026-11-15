"""
Thermal environment (7): 1-D heat conduction in layered regolith.

Physics and parameters are those of Vasavada+2012 / Hayne+2017 (LRO Diviner):
  density   rho(z) = rho_d - (rho_d - rho_s) exp(-z/H)
  conduct.  K = Kc [1 + chi (T/350)^3],  Kc = Kd - (Kd-Ks)(rho_d-rho)/(rho_d-rho_s)
  heat cap. cp(T) = c0 + c1 T + c2 T^2 + c3 T^3 + c4 T^4
Surface: absorbed sunlight = emitted IR (+ conduction); base: geothermal flux.
The forcing uses the TRUE illumination series (Horizons Sun + LOLA horizon
shadowing + local tilt), so permanent / long shadow and polar low-sun geometry
are handled.  Not modelled: incidence-dependent albedo, scattered light and IR
from surrounding terrain (so shadowed floors come out colder than Diviner's
measured ~25-40 K cold traps).
"""
import numpy as np
from scipy.linalg import solve_banded

from . import config as C


def _grid(n=40, dz0=5e-4, growth=1.17):
    return np.concatenate([[0.0], np.cumsum(dz0 * growth ** np.arange(n - 1))])


def cp_of_T(T):
    c0, c1, c2, c3, c4 = C.CP_COEF
    return c0 + c1 * T + c2 * T ** 2 + c3 * T ** 3 + c4 * T ** 4


def simulate(q_abs, dt, cycles=4, h_param=C.H_PARAM, geothermal=C.GEOTHERMAL):
    """
    q_abs : absorbed solar flux (W/m^2) for ONE repeating lunar day, step dt (s).
    Returns the surface temperature (K) over the final cycle.
    """
    z = _grid()
    n = len(z)
    rho = C.RHO_D - (C.RHO_D - C.RHO_S) * np.exp(-z / h_param)
    kc = C.K_D - (C.K_D - C.K_S) * (C.RHO_D - rho) / (C.RHO_D - C.RHO_S)
    dzi = np.diff(z)
    hcv = np.empty(n)
    hcv[0] = dzi[0] / 2
    hcv[1:-1] = (dzi[:-1] + dzi[1:]) / 2
    hcv[-1] = dzi[-1] / 2
    eps_s = C.EMISSIVITY * C.SIGMA_SB
    T = np.full(n, float(np.clip((q_abs.mean() / eps_s) ** 0.25, 60.0, 300.0)))
    out = np.empty(len(q_abs))
    ab = np.zeros((3, n))
    for cyc in range(cycles):
        for i, q in enumerate(q_abs):
            k = kc * (1.0 + C.CHI * (T / 350.0) ** 3)
            G = 0.5 * (k[:-1] + k[1:]) / dzi
            Cn = rho * cp_of_T(T) * hcv / dt
            diag = Cn.copy()
            diag[:-1] += G
            diag[1:] += G
            rhs = Cn * T
            diag[0] += 4 * eps_s * T[0] ** 3
            rhs[0] += q + 3 * eps_s * T[0] ** 4
            rhs[-1] += geothermal
            ab[0, 1:] = -G
            ab[1] = diag
            ab[2, :-1] = -G
            T = solve_banded((1, 1), ab, rhs)
            if cyc == cycles - 1:
                out[i] = T[0]
    return out


def thermal_report(eph, ser, cfg, log=print):
    """Surface temperatures for the mission window and the full lunar day."""
    step_s = eph.step_h * 3600.0
    n_cyc = int(round(C.SYNODIC_DAYS * 24 / eph.step_h))
    n_cyc = min(n_cyc, eph.n)
    flux = ser["flux_factor"][:n_cyc] * C.SOLAR_CONSTANT / eph.sun_au[:n_cyc] ** 2 * (1 - C.BOND_ALBEDO)
    sub = 6                                  # sub-steps per sample -> dt = 600 s
    t_in = np.arange(n_cyc)
    t_fine = np.arange(n_cyc * sub) / sub
    q = np.interp(t_fine, t_in, flux, period=n_cyc)
    T = simulate(q, step_s / sub)
    T = T[::sub]                             # back to the ephemeris cadence
    nw = min(int(round(cfg.days * 24 / eph.step_h)) + 1, len(T))
    Tw = T[:nw]
    per_day = int(round(24 / eph.step_h))
    swing24 = max((Tw[i:i + per_day].max() - Tw[i:i + per_day].min())
                  for i in range(0, max(1, nw - per_day))) if nw > per_day else Tw.max() - Tw.min()
    lit = ser["sun_frac"][:nw] > 0.5
    return dict(T_series=T, t_max_window=float(Tw.max()), t_min_window=float(Tw.min()),
                t_mean_window=float(Tw.mean()), swing24=float(swing24),
                t_max_month=float(T.max()), t_min_month=float(T.min()),
                transitions=int(np.abs(np.diff(lit.astype(int))).sum()))
