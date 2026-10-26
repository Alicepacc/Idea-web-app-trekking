"""Step 4 - terrain: elevation model and a "how open is the view" raster.

Source: Copernicus DEM GLO-30 (30 m surface model) on AWS.

Writes to data/work/:
  dem20.tif     elevation on the shared 20 m UTM grid (profiles, hillshade)
  dem100.tif    elevation at 100 m, 30 km past the region (views, panoramas)
  horizon.tif   for every 100 m cell, the elevation angle of the horizon in 16
                directions (degrees). Low angles mean an open, far-reaching view:
                summits and ridges score well, valley floors and forests badly.
"""
import math

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject

from common import BBOX, UTM, WORK, gdal_env, grid

GLO30 = "/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/{n}/{n}.tif"
# The panorama raster extends 30 km past the region so views reach outside it.
PANO_MARGIN_DEG = 0.35
PANO_RES = 100.0
AZIMUTHS = 16
EARTH_R = 6371000.0
REFRACTION = 0.13


def tile_names(bbox):
    lon0, lat0, lon1, lat1 = bbox
    for lat in range(math.floor(lat0), math.floor(lat1) + 1):
        for lon in range(math.floor(lon0), math.floor(lon1) + 1):
            yield f"Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM"


def warp_dem(bbox, transform, width, height) -> np.ndarray:
    out = np.full((height, width), np.nan, np.float32)
    with rasterio.Env(**gdal_env()):
        for name in tile_names(bbox):
            with rasterio.open(GLO30.format(n=name)) as src:
                part = np.full_like(out, np.nan)
                reproject(rasterio.band(src, 1), part, dst_transform=transform, dst_crs=UTM,
                          dst_nodata=np.nan, resampling=Resampling.bilinear)
                out = np.where(np.isnan(out), part, out)
    return out


def save(path, arr, transform, nodata=None):
    count = 1 if arr.ndim == 2 else arr.shape[0]
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[-1], height=arr.shape[-2], count=count,
                       dtype=arr.dtype, crs=UTM, transform=transform, nodata=nodata,
                       compress="deflate", predictor=2 if arr.dtype.kind == "i" else 3, tiled=True) as dst:
        dst.write(arr if arr.ndim == 3 else arr[None])


def horizon_angles(z: np.ndarray, res: float) -> np.ndarray:
    """Horizon elevation angle (degrees) towards AZIMUTHS directions for every cell."""
    dists = np.concatenate([np.arange(res, 2000, res), np.arange(2000, 6000, 250), np.arange(6000, 30001, 500)])
    h, w = z.shape
    zf = np.where(np.isnan(z), -1e4, z).astype(np.float32)  # unknown terrain never blocks the view
    out = np.empty((AZIMUTHS, h, w), np.float32)
    for k in range(AZIMUTHS):
        az = 2 * math.pi * k / AZIMUTHS
        best = np.full((h, w), -np.inf, np.float32)
        for d in dists:
            dx = int(round(d * math.sin(az) / res))
            dy = int(round(-d * math.cos(az) / res))  # rows grow southwards
            if abs(dx) >= w or abs(dy) >= h:
                continue
            drop = d * d / (2 * EARTH_R) * (1 - REFRACTION)
            ys, yd = (slice(dy, h), slice(0, h - dy)) if dy >= 0 else (slice(0, h + dy), slice(-dy, h))
            xs, xd = (slice(dx, w), slice(0, w - dx)) if dx >= 0 else (slice(0, w + dx), slice(-dx, w))
            slope = (zf[ys, xs] - zf[yd, xd] - drop) / d
            np.maximum(best[yd, xd], slope, out=best[yd, xd])
        out[k] = np.degrees(np.arctan(best))
    out[:, np.isnan(z)] = np.nan
    return out


def main():
    transform, width, height = grid()
    dem = warp_dem(BBOX, transform, width, height)
    save(WORK / "dem20.tif", dem, transform, nodata=np.nan)
    print(f"dem20.tif {width}x{height}, {np.nanmin(dem):.0f}-{np.nanmax(dem):.0f} m")

    lon0, lat0, lon1, lat1 = BBOX
    m = PANO_MARGIN_DEG
    pano_bbox = (lon0 - m, lat0 - m, lon1 + m, lat1 + m)
    pad = 30000.0
    x0, y1 = transform.c - pad, transform.f + pad
    pw = int((width * transform.a + 2 * pad) // PANO_RES)
    ph = int((height * -transform.e + 2 * pad) // PANO_RES)
    ptransform = from_origin(x0, y1, PANO_RES, PANO_RES)
    z = warp_dem(pano_bbox, ptransform, pw, ph)
    save(WORK / "dem100.tif", z, ptransform, nodata=np.nan)
    hor = horizon_angles(z, PANO_RES)
    save(WORK / "horizon.tif", hor, ptransform, nodata=np.nan)
    print(f"horizon.tif {pw}x{ph}x{AZIMUTHS}, mean horizon {np.nanmean(hor):.1f} deg")


if __name__ == "__main__":
    main()
