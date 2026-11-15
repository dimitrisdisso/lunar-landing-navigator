"""
Terrain data access.

RasterioProvider  - reads NASA LOLA GeoTIFFs (remote COG over HTTP range
                    requests, or local files).  Needs `pip install rasterio`.
SyntheticProvider - fake polar terrain (craters + fractal noise) so the whole
                    pipeline can be tested offline.  Output is NOT real Moon.
Both return GridWindow objects in south-polar-stereographic metres.
"""
import os
from dataclasses import dataclass

import numpy as np
from scipy import ndimage as ndi

from .config import PGDA_BASE, PRODUCTS


@dataclass
class GridWindow:
    a: np.ndarray          # 2-D data (row = y descending)
    x0: float              # x of pixel-centre [0, 0]
    y0: float              # y of pixel-centre [0, 0]
    dx: float              # +metres per column
    dy: float              # -metres per row (negative)

    @property
    def pix(self):
        return abs(self.dx)

    def rc(self, x, y):
        return (np.asarray(y) - self.y0) / self.dy, (np.asarray(x) - self.x0) / self.dx

    def sample(self, x, y, order=1):
        r, c = self.rc(x, y)
        return ndi.map_coordinates(self.a, [r, c], order=order, mode="constant", cval=np.nan)

    def crop(self, x, y, half):
        r, c = self.rc(x, y)
        n = int(np.ceil(half / self.pix))
        r0, c0 = int(round(float(r))) - n, int(round(float(c))) - n
        r0, c0 = max(r0, 0), max(c0, 0)
        sub = self.a[r0:int(round(float(r))) + n + 1, c0:int(round(float(c))) + n + 1]
        return GridWindow(sub, self.x0 + c0 * self.dx, self.y0 + r0 * self.dy, self.dx, self.dy)

    def xy_grid(self):
        ny, nx = self.a.shape
        return np.meshgrid(self.x0 + self.dx * np.arange(nx), self.y0 + self.dy * np.arange(ny))


# ------------------------------------------------------------------- real data
class RasterioProvider:
    name = "NASA LOLA (PGDA / GSFC)"

    def __init__(self, local_dir=""):
        try:
            import rasterio
            from rasterio.windows import from_bounds
            from rasterio.enums import Resampling
        except ImportError as e:
            raise SystemExit("Real NASA data needs rasterio:  pip install rasterio\n"
                             "(or run with --demo for synthetic terrain)") from e
        self._rio, self._from_bounds, self._rs = rasterio, from_bounds, Resampling
        self._env = rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                                 CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".TIF,.tif",
                                 GDAL_HTTP_MULTIRANGE="YES", VSI_CACHE="TRUE")
        self._env.__enter__()
        self.ds = {}
        for key, fname in PRODUCTS.items():
            src = os.path.join(local_dir, fname) if local_dir else "/vsicurl/" + PGDA_BASE + fname
            try:
                self.ds[key] = rasterio.open(src)
            except Exception as e:
                if key == "rough":
                    self.ds[key] = None           # roughness is optional
                else:
                    raise SystemExit(f"Cannot open {src}\n  {e}")
        b = self.ds["dem"].bounds
        self.half_extent = min(abs(b.left), abs(b.right), abs(b.bottom), abs(b.top))

    def window(self, product, xmin, xmax, ymin, ymax, decimate=1):
        ds = self.ds.get(product)
        if ds is None:
            return None
        win = self._from_bounds(xmin, ymin, xmax, ymax, ds.transform).round_offsets().round_lengths()
        shape = (max(1, int(win.height // decimate)), max(1, int(win.width // decimate)))
        arr = ds.read(1, window=win, out_shape=shape, boundless=True,
                      resampling=self._rs.average, masked=True)
        arr = np.ma.filled(arr.astype("float32"), np.nan)
        t = ds.window_transform(win)
        dx, dy = t.a * win.width / shape[1], t.e * win.height / shape[0]
        return GridWindow(arr, t.c + dx / 2, t.f + dy / 2, dx, dy)


# -------------------------------------------------------------- synthetic demo
class SyntheticProvider:
    name = "SYNTHETIC terrain (demo - not real Moon)"

    def __init__(self, half_m=200_000.0, pix=200.0, seed=11):
        rng = np.random.default_rng(seed)
        n = int(2 * half_m / pix)
        self.half_extent, self._pix, self._n, self._half = half_m, pix, n, half_m
        f = np.hypot(*np.meshgrid(np.fft.fftfreq(n), np.fft.fftfreq(n)))
        f[0, 0] = 1.0
        spec = (rng.normal(size=(n, n)) + 1j * rng.normal(size=(n, n))) * f ** -1.35
        spec[0, 0] = 0
        z = np.fft.ifft2(spec).real
        z = z / z.std() * 250.0
        xs = -half_m + pix * (np.arange(n) + 0.5)
        X, Y = np.meshgrid(xs, xs[::-1])
        # craters (power-law sizes) plus a few big ones near the pole
        for _ in range(900):
            D = 400.0 * (1 - rng.random()) ** (-1 / 1.6)
            D = min(D, 30_000.0)
            cx, cy = rng.uniform(-half_m, half_m, 2)
            self._crater(z, X, Y, cx, cy, D)
        for cx, cy, D in [(6e3, 4e3, 18e3), (-25e3, 10e3, 14e3), (20e3, -30e3, 9e3), (-8e3, -22e3, 6e3)]:
            self._crater(z, X, Y, cx, cy, D)
        self.dem = z.astype("float32")
        gy, gx = np.gradient(ndi.gaussian_filter(self.dem, 0.7), -pix, pix)
        self.slope = np.degrees(np.arctan(np.hypot(gx, gy))).astype("float32")
        hp = self.dem - ndi.uniform_filter(self.dem, 3)
        self.rough = (np.sqrt(ndi.uniform_filter(hp ** 2, 3)) + 0.05).astype("float32")
        self.x0 = xs[0]
        self.y0 = xs[-1]

    @staticmethod
    def _crater(z, X, Y, cx, cy, D):
        R = D / 2
        i0 = np.searchsorted(X[0], cx - 1.6 * R)
        i1 = np.searchsorted(X[0], cx + 1.6 * R)
        j0 = np.searchsorted(-Y[:, 0], -(cy + 1.6 * R))
        j1 = np.searchsorted(-Y[:, 0], -(cy - 1.6 * R))
        if i1 - i0 < 2 or j1 - j0 < 2:
            return
        r = np.hypot(X[j0:j1, i0:i1] - cx, Y[j0:j1, i0:i1] - cy) / R
        depth, rim = 0.17 * D, 0.03 * D
        bowl = np.where(r < 1, -depth * (1 - r ** 2) + rim * r ** 4, 0.0)
        ejecta = np.where((r >= 1) & (r < 1.6), rim * (1.6 - r) / 0.6 * np.exp(-(r - 1) * 2), 0.0)
        z[j0:j1, i0:i1] += bowl + ejecta

    def window(self, product, xmin, xmax, ymin, ymax, decimate=1):
        arr = {"dem": self.dem, "slope": self.slope, "rough": self.rough}[product]
        p = self._pix
        c0 = int(np.floor((xmin + self._half) / p)); c1 = int(np.ceil((xmax + self._half) / p))
        r0 = int(np.floor((self._half - ymax) / p)); r1 = int(np.ceil((self._half - ymin) / p))
        c0, r0 = max(c0, 0), max(r0, 0)
        c1, r1 = min(c1, self._n), min(r1, self._n)
        sub = arr[r0:r1, c0:c1]
        if decimate > 1:
            h, w = (sub.shape[0] // decimate) * decimate, (sub.shape[1] // decimate) * decimate
            sub = sub[:h, :w].reshape(h // decimate, decimate, w // decimate, decimate).mean((1, 3))
        d = p * decimate
        return GridWindow(sub.astype("float32"), -self._half + c0 * p + d / 2,
                          self._half - r0 * p - d / 2, d, -d)


def make_provider(cfg):
    if cfg.demo:
        return SyntheticProvider()
    try:
        return RasterioProvider(cfg.local_dir)
    except SystemExit as exc:
        if "rasterio" in str(exc).lower():
            import sys
            print("WARNING: rasterio not available; falling back to synthetic terrain. "
                  "Install rasterio for real NASA LOLA terrain.", file=sys.stderr)
            return SyntheticProvider()
        raise
