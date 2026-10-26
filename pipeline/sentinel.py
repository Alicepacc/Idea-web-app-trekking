"""Step 5 - Sentinel-2 composites on the shared 20 m grid.

Source: Sentinel-2 L2A cloud-optimized GeoTIFFs on AWS (Element 84 archive).

Two composites are built from the six 100 km tiles covering the region:
  now     per pixel, the most recent clear observation in the last weeks:
          what the forest looks like today (leaf colour, snow).
  summer  per pixel, the greenest clear observation of July-August:
          the reference that tells deciduous trees from evergreens.

Writes to data/work/:
  s2_now.tif       B02 B03 B04 B06 B8A, reflectance x 10000 (int16)
  s2_now_rgb10.tif B04 B03 B02 at 10 m, same dates as s2_now (for the map)
  s2_now_meta.tif  1: quality (2 sunlit, 1 terrain shadow, 0 no data)
                   2: observation date (days since 2000-01-01)  3: SCL class
  s2_summer.tif    B02 B03 B04 B06 B8A of the greenest summer observation
  landcover.tif    ESA WorldCover 2021 class (10 = tree cover)

Usage: python sentinel.py [--end YYYY-MM-DD] [--days 18]
"""
import argparse
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import numpy as np
import rasterio
import rasterio.errors
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject
from rasterio.windows import Window
from scipy.ndimage import maximum_filter

from common import UTM, WORK, gdal_env, grid

TILES = ["32TPR", "32TQR", "32TPS", "32TQS", "32TPT", "32TQT"]
BUCKET = "https://sentinel-cogs.s3.us-west-2.amazonaws.com"
BANDS = ["B02", "B03", "B04", "B06", "B8A"]
RGB10 = ["B04", "B03", "B02"]  # true colour at the native 10 m, for the map
BOA_OFFSET = 1000  # baseline >= 04.00 stores reflectance * 10000 + 1000, unless the archive removed it
SUNLIT = (4, 5, 6, 11)  # vegetation, bare, water, snow
SHADOW = (2,)  # dark area: mostly terrain shadow when the sun is low
SUMMER_VALID = (4, 5, 6)
CLOUDY = (3, 8, 9, 10)  # cloud shadow, clouds, cirrus
CLOUD_BUFFER_PX = 5  # 100 m: cloud edges and haze are often misclassified as clear
VERY_CLOUDY = 60  # scenes above this cloud cover only fill gaps left by clearer ones
# Clear late-October forest has blue reflectance around 0.01; above 0.028 the canopy is seen
# through haze or thin cloud that the scene classification missed, which fakes autumn colours.
HAZE_BLUE = 280
EPOCH = date(2000, 1, 1)
WORLDCOVER = "/vsicurl/https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/" \
             "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"


def http_get(url, attempts=5):
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, OSError):
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)


def list_prefixes(prefix):
    xml = http_get(f"{BUCKET}/?list-type=2&delimiter=/&prefix={prefix}").decode()
    return re.findall(r"<Prefix>([^<]+)</Prefix>", xml)[1:]


def scenes(tile, start, end, max_cloud):
    """STAC items of one tile between start and end (inclusive)."""
    utm, band, sq = tile[:2], tile[2], tile[3:]
    months = sorted({(d.year, d.month) for d in (start + timedelta(n) for n in range((end - start).days + 1))})
    folders = [f for y, m in months for f in list_prefixes(f"sentinel-s2-l2a-cogs/{utm}/{band}/{sq}/{y}/{m}/")]

    def item(folder):
        name = folder.rstrip("/").split("/")[-1]
        try:
            return json.loads(http_get(f"{BUCKET}/{folder}{name}.json"))
        except urllib.error.HTTPError:  # a few archive folders have no item file
            return None

    with ThreadPoolExecutor(8) as ex:
        items = list(ex.map(item, folders))
    out = []
    for folder, it in zip(folders, items):
        if it is None:
            continue
        p = it["properties"]
        when = datetime.fromisoformat(p["datetime"].replace("Z", "+00:00"))
        if start <= when.date() <= end and p.get("eo:cloud_cover", 100) <= max_cloud:
            needs_offset = p.get("s2:processing_baseline", "00") >= "04.00" and not p.get("earthsearch:boa_offset_applied")
            out.append({"tile": tile, "date": when.date(), "cloud": p["eo:cloud_cover"],
                        "offset": BOA_OFFSET if needs_offset else 0, "base": f"{BUCKET}/{folder}"})
    return out


def read_band(url, out_shape, attempts=4):
    for attempt in range(attempts):
        try:
            with rasterio.Env(**gdal_env()), rasterio.open("/vsicurl/" + url) as ds:
                return ds.read(1, out_shape=out_shape, resampling=Resampling.average), ds.transform
        except rasterio.errors.RasterioIOError:
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)


def placement(tile_transform, shape, gtransform, gw, gh):
    """Slices that map a tile array onto the shared grid."""
    col = int(round((tile_transform.c - gtransform.c) / gtransform.a))
    row = int(round((gtransform.f - tile_transform.f) / -gtransform.e))
    h, w = shape
    r0, c0 = max(row, 0), max(col, 0)
    r1, c1 = min(row + h, gh), min(col + w, gw)
    if r0 >= r1 or c0 >= c1:
        return None
    return (slice(r0, r1), slice(c0, c1)), (slice(r0 - row, r1 - row), slice(c0 - col, c1 - col))


def read_scl(sc, grid_args, tree=None):
    """The scene classification layer, cut to the part that falls on the grid.

    Pixels near clouds, and forest pixels seen through haze, are marked 3
    (cloud shadow) so that the composite takes them from another date.
    """
    scl, tt = read_band(sc["base"] + "SCL.tif", (5490, 5490))
    place = placement(tt, scl.shape, *grid_args)
    if place is None:
        return None
    gsl, tsl = place
    scl = scl[tsl]
    near_cloud = maximum_filter(np.isin(scl, CLOUDY).astype(np.uint8), size=2 * CLOUD_BUFFER_PX + 1) > 0
    bad = near_cloud & ~np.isin(scl, CLOUDY)
    if tree is not None:
        blue = read_band(sc["base"] + "B02.tif", (5490, 5490))[0][tsl].astype(np.int32) - sc["offset"]
        bad |= (scl == 4) & tree[gsl] & (blue > HAZE_BLUE)
    scl = np.where(bad, 3, scl).astype(np.uint8)
    return gsl, tsl, scl


def read_bands(sc, tsl):
    with ThreadPoolExecutor(len(BANDS)) as ex:
        arrs = list(ex.map(lambda b: read_band(sc["base"] + f"{b}.tif", (5490, 5490))[0][tsl], BANDS))
    return np.clip(np.stack(arrs).astype(np.int32) - sc["offset"], -32000, 32000).astype(np.int16)


def read_rgb10(sc, tsl, take, attempts=4):
    """True-colour bands at 10 m, only over the bounding box of what this scene contributes."""
    rows, cols = np.flatnonzero(take.any(axis=1)), np.flatnonzero(take.any(axis=0))
    r0, r1, c0, c1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    win = Window(2 * (tsl[1].start + c0), 2 * (tsl[0].start + r0), 2 * (c1 - c0), 2 * (r1 - r0))

    def one(band):
        for attempt in range(attempts):
            try:
                with rasterio.Env(**gdal_env()), rasterio.open(f"/vsicurl/{sc['base']}{band}.tif") as ds:
                    return ds.read(1, window=win)
            except rasterio.errors.RasterioIOError:
                if attempt == attempts - 1:
                    raise
                time.sleep(2 ** attempt)

    with ThreadPoolExecutor(len(RGB10)) as ex:
        arrs = list(ex.map(one, RGB10))
    out = np.empty((len(arrs),) + arrs[0].shape, np.int16)
    for k, a in enumerate(arrs):  # stays in 16 bits: a full tile is 0.7 GB per scene
        np.minimum(a, 32000, out=a)
        out[k] = a.astype(np.int16) - sc["offset"]
        np.maximum(out[k], 0, out=out[k])
    return (r0, r1, c0, c1), out


def with_scl(items, grid_args, tree=None):
    with ThreadPoolExecutor(8) as ex:
        scls = list(ex.map(lambda s: read_scl(s, grid_args, tree), items))
    return [(s, r) for s, r in zip(items, scls) if r is not None]


def composite_now(items, gtransform, gw, gh, tree):
    """Plan the composite from the cheap SCL layers, then fetch bands only where needed."""
    grid_args = (gtransform, gw, gh)
    # Clearer scenes first, then newest first; an older sunlit observation still
    # replaces a newer shadowed one.
    order = sorted(items, key=lambda s: (s["cloud"] > VERY_CLOUDY, -s["date"].toordinal()))
    scenes_scl = with_scl(order, grid_args, tree)
    quality = np.zeros((gh, gw), np.uint8)
    plan = []
    for sc, (gsl, tsl, scl) in scenes_scl:
        q = quality[gsl]
        sunlit = np.isin(scl, SUNLIT)
        take = (sunlit & (q < 2)) | (np.isin(scl, SHADOW) & (q < 1))
        n = int(take.sum())
        print(f"  {sc['tile']} {sc['date']} cloud {sc['cloud']:.0f}%: +{n / 1e6:.1f} Mpx", flush=True)
        if n < 2000:
            continue
        q[take] = np.where(sunlit, 2, 1)[take]
        plan.append((sc, gsl, tsl, scl, take))
    del scenes_scl  # only the planned scenes are kept

    bands = np.zeros((len(BANDS), gh, gw), np.int16)
    quality[:] = 0
    when = np.zeros((gh, gw), np.uint16)
    scl_out = np.zeros((gh, gw), np.uint8)
    with ThreadPoolExecutor(3) as ex:
        for (sc, gsl, tsl, scl, take), b in zip(plan, ex.map(lambda p: read_bands(p[0], p[2]), plan)):
            for k in range(len(BANDS)):
                bands[k][gsl][take] = b[k][take]
            quality[gsl][take] = np.where(np.isin(scl, SUNLIT), 2, 1).astype(np.uint8)[take]
            when[gsl][take] = (sc["date"] - EPOCH).days
            scl_out[gsl][take] = scl[take]
    # keep only what the 10 m pass needs, with the masks packed to bits
    plan = [(sc, gsl, tsl, np.packbits(take), take.shape) for sc, gsl, tsl, scl, take in plan]
    return bands, quality, when, scl_out, plan


def composite_rgb10(plan, gw, gh):
    """The same choice of date per pixel, at 10 m, for a sharp true-colour map."""
    rgb10 = np.zeros((len(RGB10), 2 * gh, 2 * gw), np.int16)
    for sc, gsl, tsl, packed, shape in plan:
        take = np.unpackbits(packed, count=shape[0] * shape[1]).reshape(shape).astype(bool)
        (r0, r1, c0, c1), b = read_rgb10(sc, tsl, take)
        m10 = np.repeat(np.repeat(take[r0:r1, c0:c1], 2, axis=0), 2, axis=1)
        y0, x0 = 2 * (gsl[0].start + r0), 2 * (gsl[1].start + c0)
        target = rgb10[:, y0:y0 + m10.shape[0], x0:x0 + m10.shape[1]]
        for k in range(len(RGB10)):
            target[k][m10] = b[k][m10]
        del b, m10, take
    return rgb10


def composite_summer(items, gtransform, gw, gh, per_tile=6):
    bands = np.zeros((len(BANDS), gh, gw), np.int16)
    best = np.full((gh, gw), -2.0, np.float32)
    chosen = []
    for tile in TILES:
        chosen += sorted((s for s in items if s["tile"] == tile), key=lambda s: s["cloud"])[:per_tile]
    scenes_scl = with_scl(chosen, (gtransform, gw, gh))
    with ThreadPoolExecutor(3) as ex:
        for (sc, (gsl, tsl, scl)), b in zip(scenes_scl, ex.map(lambda p: read_bands(p[0], p[1][1]), scenes_scl)):
            red, nir = b[2].astype(np.float32), b[4].astype(np.float32)
            ndvi = (nir - red) / np.maximum(nir + red, 1)
            take = np.isin(scl, SUMMER_VALID) & (ndvi > best[gsl])
            for k in range(len(BANDS)):
                bands[k][gsl][take] = b[k][take]
            best[gsl][take] = ndvi[take]
            print(f"  summer {sc['tile']} {sc['date']} cloud {sc['cloud']:.0f}%: {take.sum() / 1e6:.1f} Mpx greener", flush=True)
    return bands


def landcover(gtransform, gw, gh):
    out = np.zeros((gh, gw), np.uint8)
    with rasterio.Env(**gdal_env()):
        for tile in ("N45E009", "N45E012"):
            with rasterio.open(WORLDCOVER.format(tile=tile)) as src:
                part = np.zeros_like(out)
                reproject(rasterio.band(src, 1), part, dst_transform=gtransform, dst_crs=UTM,
                          src_nodata=0, dst_nodata=0, resampling=Resampling.mode)
                out = np.where(out == 0, part, out)
    return out


def save(path, arr, transform, nodata=None):
    arr = arr if arr.ndim == 3 else arr[None]
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[2], height=arr.shape[1], count=arr.shape[0],
                       dtype=arr.dtype, crs=UTM, transform=transform, nodata=nodata,
                       compress="deflate", predictor=2, tiled=True) as dst:
        dst.write(arr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", type=date.fromisoformat, default=date.today())
    ap.add_argument("--days", type=int, default=18)
    ap.add_argument("--summer-year", type=int, default=None)
    ap.add_argument("--only", nargs="+", choices=["now", "summer", "landcover"],
                    default=["now", "summer", "landcover"])
    args = ap.parse_args()
    end = args.end
    start = end - timedelta(days=args.days)
    year = args.summer_year or (end.year if end.month >= 9 else end.year - 1)
    gtransform, gw, gh = grid()

    if "landcover" in args.only or not (WORK / "landcover.tif").exists():
        save(WORK / "landcover.tif", landcover(gtransform, gw, gh), gtransform, nodata=0)
    if "now" in args.only:
        with rasterio.open(WORK / "landcover.tif") as ds:
            tree = ds.read(1) == 10
        composite_now_step(start, end, gtransform, gw, gh, tree)
    if "summer" in args.only:
        print(f"summer {year}")
        items = [s for t in TILES for s in scenes(t, date(year, 7, 1), date(year, 8, 31), max_cloud=40)]
        save(WORK / "s2_summer.tif", composite_summer(items, gtransform, gw, gh), gtransform)
    print("done")


def composite_now_step(start, end, gtransform, gw, gh, tree):
    print(f"now: {start} .. {end}")
    items = [s for t in TILES for s in scenes(t, start, end, max_cloud=95)]
    bands, quality, when, scl, plan = composite_now(items, gtransform, gw, gh, tree)
    save(WORK / "s2_now.tif", bands, gtransform)
    del bands
    save(WORK / "s2_now_meta.tif", np.stack([quality.astype(np.uint16), when, scl.astype(np.uint16)]), gtransform)
    print(f"now: sunlit {np.mean(quality == 2):.1%}, shadow {np.mean(quality == 1):.1%}, "
          f"missing {np.mean(quality == 0):.1%}")
    days, counts = np.unique(when[quality > 0], return_counts=True)
    used = [(EPOCH + timedelta(days=int(d))).isoformat() for d, c in zip(days, counts) if c > 0.002 * when.size]
    (WORK / "composite_info.json").write_text(json.dumps({
        "window": [start.isoformat(), end.isoformat()], "image_dates": used,
        "sunlit_share": round(float(np.mean(quality == 2)), 3)}))
    del quality, when, scl
    save(WORK / "s2_now_rgb10.tif", composite_rgb10(plan, gw, gh), from_origin(gtransform.c, gtransform.f, 10, 10))


if __name__ == "__main__":
    main()
