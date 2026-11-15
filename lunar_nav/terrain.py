"""
Terrain analysis from NASA LOLA grids (categories 1, 2, 3, 4, 6, 10).

Everything here is computed from the DEM / slope / roughness rasters; nothing
is invented.  What LOLA cannot resolve (boulders, craters < ~300 m) is reported
as unresolved, not guessed.
"""
import numpy as np
from scipy import ndimage as ndi
from scipy.ndimage import map_coordinates

from .config import R_MOON
from .geometry import (lonlat_to_xy, xy_to_lonlat, scale_k, unit, east, north)


# --------------------------------------------------------------- horizon mask
def horizon_profile(dem, x0, y0, lon0, lat0, z0, h_ant, rmax, n_az=360):
    """Horizon elevation angle (deg) for each azimuth, with lunar curvature."""
    k = float(scale_k(lat0))
    step = dem.pix / k * 0.75
    r = np.arange(step, rmax, step)
    az = np.arange(n_az) * 360.0 / n_az
    ux, uy = np.sin(np.radians(lon0 + az)), np.cos(np.radians(lon0 + az))
    X = x0 + k * r[None, :] * ux[:, None]
    Y = y0 + k * r[None, :] * uy[:, None]
    Z = dem.sample(X, Y)
    ang = np.degrees(np.arctan2(Z - (z0 + h_ant) - r[None, :] ** 2 / (2 * R_MOON), r[None, :]))
    hor = np.nanmax(np.where(np.isfinite(ang), ang, -90.0), axis=1)
    dip = -np.degrees(np.arccos(R_MOON / (R_MOON + max(z0, 0) + h_ant)))
    return az, np.maximum(hor, dip)


# -------------------------------------------------------------- site analysis
def _disk(shape, r0, c0, rad_px):
    rr, cc = np.ogrid[:shape[0], :shape[1]]
    return (rr - r0) ** 2 + (cc - c0) ** 2 <= rad_px ** 2


def analyze_site(provider, lat, lon, cfg):
    """Full terrain assessment around a candidate; also refines the touchdown point."""
    x, y = lonlat_to_xy(lon, lat)
    k = float(scale_k(lat))
    half = cfg.horizon_range_m * k + 3000.0
    dem_full = provider.window("dem", x - half, x + half, y - half, y + half)
    slp_full = provider.window("slope", x - half, x + half, y - half, y + half)
    rgh_full = provider.window("rough", x - 8000, x + 8000, y - 8000, y + 8000)
    pix_g = dem_full.pix / k                                  # ground metres per pixel

    dem = dem_full.crop(x, y, 6000.0 * k)
    X, Y = dem.xy_grid()
    slp = slp_full.sample(X, Y)
    rgh = rgh_full.sample(X, Y) if rgh_full is not None else np.full_like(dem.a, np.nan)
    z = dem.a
    cr, cc = [float(v) for v in dem.rc(x, y)]

    # --- craters resolved by LOLA: closed depressions up to ~1.2 km across
    se = max(3, int(round(1200.0 / pix_g)))
    depth = ndi.grey_closing(np.nan_to_num(z, nan=np.nanmedian(z)), size=(se, se)) - z
    crater = ndi.binary_dilation(depth > 4.0, iterations=1)

    # --- safe mask & inscribed circle (obstacle clearance, usable area)
    safe = (slp <= cfg.slope_max) & ~crater & np.isfinite(z)
    edt = ndi.distance_transform_edt(safe) * pix_g
    search = _disk(z.shape, cr, cc, 1500.0 / pix_g)
    cand = np.where(search & safe, edt, -1.0)
    if cand.max() > 0:
        tr, tc = np.unravel_index(np.argmax(cand), cand.shape)
        # among near-ties prefer the one closest to the screening centre
        near = np.argwhere(cand >= 0.95 * cand.max())
        tr, tc = near[np.argmin(((near - [cr, cc]) ** 2).sum(1))]
    else:
        tr, tc = int(round(cr)), int(round(cc))
    xt, yt = dem.x0 + tc * dem.dx, dem.y0 + tr * dem.dy
    lon_t, lat_t = xy_to_lonlat(xt, yt)
    kt = float(scale_k(lat_t))
    r_in = float(edt[tr, tc])
    lab, _ = ndi.label(safe)
    area_km2 = (lab == lab[tr, tc]).sum() * pix_g ** 2 / 1e6 if lab[tr, tc] > 0 else 0.0

    # --- topography at touchdown
    z_t = float(z[tr, tc])
    zs = ndi.gaussian_filter(np.nan_to_num(z, nan=np.nanmedian(z)), 1.0)
    gy, gx = np.gradient(zs, dem.dy, dem.dx)                  # d/dy, d/dx (map metres)
    lam = np.radians(lon_t)
    dzdE = (gx * np.cos(lam) - gy * np.sin(lam)) * kt          # map -> ground metres
    dzdN = (gx * np.sin(lam) + gy * np.cos(lam)) * kt
    slope_grad = np.degrees(np.arctan(np.hypot(dzdE, dzdN)))
    zone = _disk(z.shape, tr, tc, cfg.zone_radius_m / pix_g)
    ring2k = _disk(z.shape, tr, tc, 2000.0 / pix_g)
    ring1k = _disk(z.shape, tr, tc, 1000.0 / pix_g)
    ring500 = _disk(z.shape, tr, tc, 500.0 / pix_g) & ~_disk(z.shape, tr, tc, 2)
    tpi = z_t - float(np.nanmean(z[ring500]))
    shape_class = "local crest / ridge" if tpi > 10 else "local depression" if tpi < -10 else "flat to gently sloping"
    sl = slp[zone]
    rg = rgh[zone]
    n_cr = int(ndi.label(crater & ring1k)[1])
    d_crater = (ndi.distance_transform_edt(~crater)[tr, tc]) * pix_g if crater.any() else np.inf
    steep = slp > 30.0
    d_steep = (ndi.distance_transform_edt(~steep)[tr, tc]) * pix_g if steep.any() else np.inf

    out = dict(
        lat=float(lat_t), lon=float(lon_t), x=float(xt), y=float(yt), pix_g=pix_g,
        elevation_m=z_t, relief_2km_m=float(np.nanmax(z[ring2k]) - np.nanmin(z[ring2k])),
        tpi_m=tpi, shape_class=shape_class,
        slope_mean=float(np.nanmean(sl)), slope_p95=float(np.nanpercentile(sl, 95)),
        slope_max=float(np.nanmax(sl)),
        eva_slope_mean=float(np.nanmean(slp[ring2k])),
        eva_slope_p95=float(np.nanpercentile(slp[ring2k], 95)),
        eva_slope_max=float(np.nanmax(slp[ring2k])),
        eva_frac_over30=float(np.nanmean(slp[ring2k] > 30.0)),
        slope_grad=float(slope_grad[tr, tc]),
        eva_frac_over=float(np.nanmean(slp[ring2k] > 20.0)),
        steep_frac_1km=float(np.nanmean(steep[ring1k])),
        rough_m=float(np.nanmean(rg)) if np.isfinite(rg).any() else np.nan,
        rough_max_m=float(np.nanmax(rg)) if np.isfinite(rg).any() else np.nan,
        n_craters_1km=n_cr, d_crater_m=float(d_crater),
        crater_frac_zone=float(crater[zone].mean()),
        r_inscribed_m=r_in, safe_area_km2=float(area_km2),
        d_steep_m=float(d_steep),
        center_offset_m=float(np.hypot(xt - x, yt - y) / k),
    )
    # --- safe-area bounding box (for display — explains the area vs inscribed-circle discrepancy)
    if lab[tr, tc] > 0:
        comp_rows, comp_cols = np.where(lab == lab[tr, tc])
        _row_span_m = float((comp_rows.max() - comp_rows.min()) * pix_g)
        _col_span_m = float((comp_cols.max() - comp_cols.min()) * pix_g)
        _bb_area_km2 = _row_span_m * _col_span_m / 1e6
        out["safe_bbox_ns_m"]   = _row_span_m
        out["safe_bbox_ew_m"]   = _col_span_m
        out["safe_bbox_fill"]   = float(area_km2 / _bb_area_km2) if _bb_area_km2 > 0 else 1.0
    else:
        out["safe_bbox_ns_m"]  = 0.0
        out["safe_bbox_ew_m"]  = 0.0
        out["safe_bbox_fill"]  = 0.0
    # --- horizon from touchdown (terrain shadowing + Earth line of sight)
    az, hor = horizon_profile(dem_full, xt, yt, lon_t, lat_t, z_t, cfg.antenna_height_m,
                              cfg.horizon_range_m)
    out["az"], out["horizon"] = az, hor
    # --- surface normal (east, north components) for incidence angle
    out["dzdE"], out["dzdN"] = float(dzdE[tr, tc]), float(dzdN[tr, tc])
    return out


# ------------------------------------------------------------------ screening
def screen_candidates(provider, eph, cfg, log=print):
    """
    OPTIMIZED: Coarse pass over the polar cap with faster horizon computation.
    """
    from . import cache
    
    # Check if we have cached candidates for this date/config
    cache_key = (eph.start.date().isoformat(), cfg.days, cfg.lat_limit, cfg.slope_max, cfg.candidates)
    cached = cache.get("screening", *cache_key)
    if cached is not None:
        log(f"  using cached screening results ({len(cached)} candidates)")
        return cached
    
    rho = 2 * R_MOON * np.tan(np.pi / 4 + np.radians(cfg.lat_limit) / 2)
    rho = min(rho, provider.half_extent)
    dec = cfg.screen_decimate
    dem = provider.window("dem", -rho, rho, -rho, rho, decimate=dec)
    slp = provider.window("slope", -rho, rho, -rho, rho, decimate=dec)
    X, Y = dem.xy_grid()
    lon, lat = xy_to_lonlat(X, Y)
    valid = (lat <= cfg.lat_limit) & np.isfinite(dem.a) & (slp.a <= cfg.slope_max)
    rows, cols = np.nonzero(valid)
    N = len(rows)
    log(f"  screening {N:,} cells (slope <= {cfg.slope_max:.0f} deg, lat <= {cfg.lat_limit:.0f})")
    if N == 0:
        return []
    lo, la = lon[rows, cols], lat[rows, cols]
    kk = scale_k(la)
    z0 = dem.a[rows, cols]

    # OPTIMIZATION: Reduce azimuth samples from 36 to 24 for faster horizon computation
    n_az = 24
    azs = np.arange(n_az) * 360.0 / n_az
    H = np.empty((n_az, N), dtype="float32")
    
    # OPTIMIZATION: Fewer, larger radius steps for horizon
    radii = []
    r = dem.pix / 1.0
    while r < 40_000.0:
        radii.append(r); r *= 1.2  # Changed from 1.12 to 1.2 for fewer steps
    
    for ia, az in enumerate(azs):
        best = np.full(N, -90.0, dtype="float32")
        ux, uy = np.sin(np.radians(lo + az)), np.cos(np.radians(lo + az))
        for rg in radii:
            pr = (rows + (kk * rg * uy) / dem.dy).astype("float32")
            pc = (cols + (kk * rg * ux) / dem.dx).astype("float32")
            zz = map_coordinates(dem.a, [pr, pc], order=1, mode="constant", cval=np.nan)
            ang = np.degrees(np.arctan2(zz - z0 - rg ** 2 / (2 * R_MOON), rg))
            best = np.fmax(best, ang)
        H[ia] = best

    U, E, Nn = unit(la, lo), east(lo), north(la, lo)
    
    # OPTIMIZATION: Sample time points more sparsely (every 12 hours instead of 6)
    t_idx = np.arange(0, int(cfg.days * 24 / eph.step_h) + 1, max(1, int(12 / eph.step_h)))
    
    lit = np.zeros(N); vis = np.zeros(N); both = np.zeros(N)
    ar = np.arange(N)
    for ti in t_idx:
        res = []
        for blat, blon in ((eph.sun_lat[ti], eph.sun_lon[ti]), (eph.earth_lat[ti], eph.earth_lon[ti])):
            b = unit(np.float64(blat), np.float64(blon))
            el = np.degrees(np.arcsin(np.clip(U @ b, -1, 1)))
            az = np.degrees(np.arctan2(E @ b, Nn @ b)) % 360.0
            f = az / (360.0 / n_az)
            i0 = np.floor(f).astype(int) % n_az
            i1 = (i0 + 1) % n_az
            w = f - np.floor(f)
            hor = H[i0, ar] * (1 - w) + H[i1, ar] * w
            res.append(el > hor)
        lit += res[0]; vis += res[1]; both += res[0] & res[1]
    nt = len(t_idx)
    lit, vis, both = lit / nt, vis / nt, both / nt
    score = 0.6 * both + 0.2 * lit + 0.2 * vis
    ok = (lit >= 0.05) & (vis >= 0.25)

    picked = []
    order = np.argsort(-np.where(ok, score, -1))
    sep = 8_000.0
    px, py = X[rows, cols], Y[rows, cols]
    for i in order:
        if not ok[i] or len(picked) >= cfg.candidates:
            break
        if all(np.hypot(px[i] - px[j], py[i] - py[j]) / kk[i] > sep for j in picked):
            picked.append(i)
    
    result = [(float(la[i]), float(lo[i]), float(score[i])) for i in picked]
    
    # Cache the screening results
    cache.put("screening", result, *cache_key)
    
    log(f"  {ok.sum():,} cells pass Sun/Earth screening; refining {len(picked)} candidates")
    return result
