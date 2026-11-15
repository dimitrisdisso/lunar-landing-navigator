"""Map projection and sky-geometry helpers (all vectorised, angles in degrees)."""
import numpy as np
from .config import R_MOON


# ------------------------------------------------ south polar stereographic
# PROJ: +proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +R=1737400  (x = rho sin(lon), y = rho cos(lon))
def lonlat_to_xy(lon, lat):
    lam, phi = np.radians(lon), np.radians(lat)
    rho = 2.0 * R_MOON * np.tan(np.pi / 4.0 + phi / 2.0)
    return rho * np.sin(lam), rho * np.cos(lam)


def xy_to_lonlat(x, y):
    rho = np.hypot(x, y)
    phi = 2.0 * np.arctan(rho / (2.0 * R_MOON)) - np.pi / 2.0
    lam = np.arctan2(x, y)
    return np.degrees(lam), np.degrees(phi)


def scale_k(lat):
    """Projection scale factor (map metres per ground metre); 1.0 at the pole."""
    return 2.0 / (1.0 - np.sin(np.radians(lat)))


def ground_dir_xy(az_deg, lon_deg):
    """Unit vector in map (x, y) for a ground azimuth (clockwise from local north)."""
    a = np.radians(np.asarray(lon_deg) + np.asarray(az_deg))
    return np.sin(a), np.cos(a)


# ------------------------------------------------------------- 3-D sky maths
def unit(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], -1)


def east(lon):
    lo = np.radians(lon)
    return np.stack([-np.sin(lo), np.cos(lo), np.zeros_like(lo)], -1)


def north(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    return np.stack([-np.sin(la) * np.cos(lo), -np.sin(la) * np.sin(lo), np.cos(la)], -1)


def sky_position(site_lat, site_lon, site_h, body_lat, body_lon, dist_m=None):
    """
    Elevation / azimuth (deg) of a body as seen from lunar surface sites.
    site_*: shape (S,), body_*: sub-body point per time, shape (T,).
    dist_m: body distance from Moon centre (T,) -> finite distance (Earth);
            None -> at infinity (Sun).   Returns (el, az) each (S, T).
    """
    site_lat = np.atleast_1d(np.asarray(site_lat, float))
    site_lon = np.atleast_1d(np.asarray(site_lon, float))
    site_h = np.broadcast_to(np.asarray(site_h, float), site_lat.shape)
    u, e, n = unit(site_lat, site_lon), east(site_lon), north(site_lat, site_lon)
    b = unit(np.atleast_1d(body_lat), np.atleast_1d(body_lon))
    if dist_m is None:
        d = np.broadcast_to(b[None, :, :], (len(site_lat), len(b), 3)).copy()
    else:
        d = dist_m[None, :, None] * b[None, :, :] - (R_MOON + site_h)[:, None, None] * u[:, None, :]
    d /= np.linalg.norm(d, axis=-1, keepdims=True)
    el = np.degrees(np.arcsin(np.clip(np.einsum("stk,sk->st", d, u), -1, 1)))
    az = np.degrees(np.arctan2(np.einsum("stk,sk->st", d, e), np.einsum("stk,sk->st", d, n))) % 360.0
    return el, az
