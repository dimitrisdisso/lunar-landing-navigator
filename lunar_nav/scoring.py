"""
Site ranking.

HARD constraints come from NASA documents (config.py).  The 0-100 score weights
are an engineering HEURISTIC (not NASA requirements) - change them freely.
"""
import numpy as np

from . import config as C


def _clip01(x):
    return float(np.clip(x, 0.0, 1.0))


def constraints(site, sky, cfg):
    cs = [
        ("Latitude within 6 deg of south pole", site["lat"] <= cfg.lat_limit),
        (f"Landing-zone slope <= {cfg.slope_max:.0f} deg ({cfg.zone_radius_m:.0f} m)", site["slope_max"] <= cfg.slope_max),
        (f"Obstacle-free radius >= {cfg.zone_radius_m:.0f} m", site["r_inscribed_m"] >= cfg.zone_radius_m),
        (f"Earth visible >= {C.EARTH_VIS_MIN_FRAC*100:.0f}% of mission", sky["earth_vis_frac"] >= C.EARTH_VIS_MIN_FRAC),
        (f"Sunlit >= {C.SUN_VIS_MIN_FRAC*100:.0f}% of mission", sky["sunlit_frac"] >= C.SUN_VIS_MIN_FRAC),
    ]
    return cs


def score(site, sky, thermal, cfg):
    mission_h = cfg.days * 24.0
    parts = {}
    rough = site["rough_m"]
    rough_term = 1.0 if not np.isfinite(rough) else 1 - _clip01(rough / 1.0)   # HEURISTIC 1 m rms = worst
    parts["Terrain & landing zone (30)"] = 30 * (
        0.30 * (1 - _clip01(site["slope_p95"] / cfg.slope_max))
        + 0.30 * _clip01(site["r_inscribed_m"] / 300.0)
        + 0.15 * rough_term
        + 0.15 * _clip01(site["d_crater_m"] / 300.0)
        + 0.10 * (1 - _clip01(site["eva_frac_over"] / 0.5)))
    parts["Illumination (25)"] = 25 * (0.6 * sky["sunlit_frac"]
                                       + 0.4 * _clip01(sky["longest_sunlit_h"] / mission_h))
    parts["Earth comms (20)"] = 20 * (0.7 * sky["earth_vis_frac"]
                                      + 0.3 * (1 - _clip01(sky["longest_outage_h"] / mission_h)))
    if thermal is not None:
        parts["Thermal (10)"] = 10 * (0.5 * _clip01((thermal["t_min_window"] - 60.0) / 140.0)
                                      + 0.5 * (1 - _clip01(thermal["swing24"] / 250.0)))
    else:
        parts["Thermal (10)"] = 5.0
    parts["Geologic stability (15)"] = 15 * (0.5 * _clip01(site["d_steep_m"] / 500.0)
                                             + 0.3 * (1 - _clip01(site["steep_frac_1km"] / 0.2))
                                             + 0.2 * (0.0 if site["tpi_m"] > 10 else 1.0))
    return float(sum(parts.values())), parts
