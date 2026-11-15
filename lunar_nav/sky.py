"""
Solar illumination (5), terrain shadowing (6) and Earth visibility (8).

Sun / Earth positions come from JPL Horizons; the local horizon comes from the
LOLA DEM (terrain.horizon_profile), so mountains and crater rims shadow the Sun
and block the line of sight to Earth exactly where the real topography says.
"""
import numpy as np

from .config import SUN_RADIUS_DEG, SOLAR_CONSTANT
from .geometry import sky_position


def _runs(mask, step_h):
    """Longest run of True (hours) and list of (start_idx, end_idx) runs."""
    runs, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            runs.append((i, j)); i = j + 1
        else:
            i += 1
    longest = max(((b - a + 1) * step_h for a, b in runs), default=0.0)
    return longest, runs


def sky_series(eph, site, cfg):
    lat, lon, h = site["lat"], site["lon"], site["elevation_m"]
    az_grid, hor = site["az"], site["horizon"]
    s_el, s_az = sky_position(lat, lon, h + cfg.antenna_height_m * 0, eph.sun_lat, eph.sun_lon)
    e_el, e_az = sky_position(lat, lon, h + cfg.antenna_height_m, eph.earth_lat, eph.earth_lon,
                              eph.earth_dist_m)
    s_el, s_az, e_el, e_az = s_el[0], s_az[0], e_el[0], e_az[0]
    hs = np.interp(s_az, np.append(az_grid, 360.0), np.append(hor, hor[0]))
    he = np.interp(e_az, np.append(az_grid, 360.0), np.append(hor, hor[0]))
    # fraction of the solar disc above the local horizon (linear across the disc)
    f_sun = np.clip((s_el - hs) / (2 * SUN_RADIUS_DEG) + 0.5, 0.0, 1.0)
    # incidence angle on the real (tilted) surface
    el_r, az_r = np.radians(s_el), np.radians(s_az)
    sv = np.stack([np.cos(el_r) * np.sin(az_r), np.cos(el_r) * np.cos(az_r), np.sin(el_r)], -1)
    nv = np.array([-site["dzdE"], -site["dzdN"], 1.0])
    nv /= np.linalg.norm(nv)
    cos_i = sv @ nv
    flux_factor = f_sun * np.clip(cos_i, 0.0, None)
    return dict(sun_el=s_el, sun_az=s_az, sun_horizon=hs, sun_frac=f_sun,
                incidence=np.degrees(np.arccos(np.clip(cos_i, -1, 1))), flux_factor=flux_factor,
                earth_el=e_el, earth_az=e_az, earth_horizon=he, earth_vis=e_el > he,
                flat_sun_el=s_el)


def sky_metrics(eph, ser, cfg):
    step = eph.step_h
    nw = int(round(cfg.days * 24 / step)) + 1
    lit = ser["sun_frac"] > 0.5
    vis = ser["earth_vis"]
    w = slice(0, nw)
    lw, _ = _runs(lit[w], step)
    ow, oruns = _runs(~vis[w], step)
    # sunrise / sunset events over the whole ~31-day series (UTC)
    events = []
    for i in range(1, len(lit)):
        if lit[i] != lit[i - 1]:
            events.append(("sunrise" if lit[i] else "sunset", eph.time_at(i)))
    inc = ser["incidence"][w][lit[w]]
    return dict(
        sunlit_frac=float(lit[w].mean()), longest_sunlit_h=lw,
        continuous_whole_mission=bool(lit[w].all()),
        sunlit_frac_month=float(lit.mean()),
        mean_incidence=float(inc.mean()) if inc.size else np.nan,
        max_incidence=float(inc.max()) if inc.size else np.nan,
        mean_horiz_incidence=float((90.0 - ser["sun_el"][w])[lit[w]].mean()) if inc.size else np.nan,
        sun_el_min=float(ser["sun_el"][w].min()), sun_el_max=float(ser["sun_el"][w].max()),
        sun_au=float(eph.sun_au[w].mean()),
        sun_flux=float(SOLAR_CONSTANT / eph.sun_au[w].mean() ** 2),
        earth_el_min=float(ser["earth_el"][w].min()), earth_el_max=float(ser["earth_el"][w].max()),
        earth_vis_frac=float(vis[w].mean()), longest_outage_h=ow,
        both_frac=float((lit & vis)[w].mean()),
        events=[e for e in events if (e[1] - eph.start).total_seconds() / 3600 <= cfg.days * 24 + 48][:6],
        shadow_by_terrain_h=float(((ser["sun_el"] > 0) & ~lit)[w].sum() * step),
    )
