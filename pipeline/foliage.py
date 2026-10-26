"""Step 6 - which trees are in autumn colour now.

For every tree pixel (ESA WorldCover class 10) the latest clear observation is
compared with the greenest one of the summer. A canopy counts as "in colour"
when all of these hold:
  - its colour now is yellow-orange rather than green: red close to or above
    green, (red - green) / (red + green) above about -0.05;
  - it has warmed clearly since summer (evergreens warm a little too in late
    October, because of the low sun, so the threshold sits above that);
  - the colour is vivid, red well above blue: haze and thin cloud turn the
    canopy grey, which would otherwise look like autumn;
  - NDVI is still high enough for leaves to be on the branches.
Larch, beech, chestnut and birch pass; spruce, fir and pine do not.
Checked by eye on true-colour crops of Valsugana beech woods, the larch belt
of Val di Peio and Fiemme, and the spruce forest of Paneveggio.

Writes data/work/foliage.tif (uint8, 0-200 means 0-100%):
  1 colour     per pixel, how strongly the canopy is in autumn colour now
  2 density    share of coloured canopy within ~150 m: what you see walking
  3 deciduous  per pixel, canopy that changed a lot since summer
  4 seen       200 where the latest observation is usable (no snow, haze, gaps)
"""
import numpy as np
import pyarrow.parquet as pq
import rasterio
import shapely
from pyproj import Transformer
from rasterio.features import rasterize
from scipy.ndimage import uniform_filter

from common import RAW, UTM, WORK

TREE = 10  # ESA WorldCover "tree cover"
SNOW = 11  # Sentinel-2 scene classification
WARM_NOW = (-0.08, 0.05)   # (red-green)/(red+green) now: still green .. fully yellow-orange
WARM_GAIN = (0.08, 0.20)   # warming since summer: evergreen drift .. clear seasonal change
VIVID = (0.15, 0.35)       # (red-blue)/(red+blue): grey haze .. saturated colour
LEAF_NDVI = (0.30, 0.50)   # NDVI now: bare branches and litter .. leaves on
HAZE_BLUE = 350            # blue reflectance x 10000 above which a forest pixel is not trusted
DENSITY_PX = 15            # 15 x 20 m = 300 m window, ~150 m either side of the trail


def ramp(x, lo, hi):
    return np.clip((x - lo) / (hi - lo), 0, 1)


def indices(b):
    """NDVI and warmth (red vs green) from a (B02, B03, B04, B06, B8A) reflectance stack."""
    green, red, nir = (b[i].astype(np.float32) for i in (1, 2, 4))
    ndvi = (nir - red) / np.maximum(nir + red, 1)
    warm = (red - green) / np.maximum(red + green, 1)
    return ndvi, warm


def vividness(b):
    red, blue = b[2].astype(np.float32), b[0].astype(np.float32)
    return (red - blue) / np.maximum(red + blue, 1)


def orchard_mask(transform, shape):
    """Orchards and vineyards from OpenStreetMap, rasterized on the grid."""
    tr = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)
    geoms = shapely.from_wkb(pq.read_table(RAW / "orchards.parquet", columns=["geometry"]).column(0).to_pylist())
    geoms = shapely.transform(geoms, lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1])))
    shapes = [(g, 1) for g in geoms if g.geom_type in ("Polygon", "MultiPolygon")]
    return rasterize(shapes, out_shape=shape, transform=transform, fill=0, dtype=np.uint8).astype(bool)


def compute(now, summer, scl, quality, landcover, orchards=None):
    ndvi_n, warm_n = indices(now)
    ndvi_s, warm_s = indices(summer)
    tree = landcover == TREE
    if orchards is not None:
        tree &= ~orchards
    seen = (quality > 0) & (scl != SNOW) & ~(tree & (now[0] > HAZE_BLUE))
    valid = tree & seen & (summer[4] > 0)
    colour = (ramp(warm_n, *WARM_NOW) * ramp(warm_n - warm_s, *WARM_GAIN)
              * ramp(vividness(now), *VIVID) * ramp(ndvi_n, *LEAF_NDVI))
    colour = np.where(valid, colour, 0).astype(np.float32)
    deciduous = valid & ((ndvi_s - ndvi_n > 0.12) | (warm_n - warm_s > 0.18))
    density = uniform_filter(colour, size=DENSITY_PX, mode="constant")
    return colour, density, deciduous, seen


def main():
    with rasterio.open(WORK / "s2_now.tif") as ds:
        now, profile = ds.read(), ds.profile
    with rasterio.open(WORK / "s2_summer.tif") as ds:
        summer = ds.read()
    with rasterio.open(WORK / "s2_now_meta.tif") as ds:
        quality, scl = ds.read(1), ds.read(3)
    with rasterio.open(WORK / "landcover.tif") as ds:
        landcover = ds.read(1)

    orchards = orchard_mask(profile["transform"], landcover.shape)
    colour, density, deciduous, seen = compute(now, summer, scl, quality, landcover, orchards)
    out = np.stack([colour * 200, density * 200, deciduous * 200, seen * 200]).round().astype(np.uint8)
    profile.update(count=4, dtype="uint8", nodata=None, compress="deflate", predictor=2)
    with rasterio.open(WORK / "foliage.tif", "w", **profile) as dst:
        dst.write(out)
    trees = (landcover == TREE) & ~orchards
    print(f"orchards and vineyards {orchards.mean():.1%} of the grid; tree pixels {trees.mean():.1%}; seen {seen[trees].mean():.1%}; deciduous {deciduous[trees].mean():.1%}; "
          f"in colour (>0.5) {np.mean(colour[trees & seen] > 0.5):.1%}")


if __name__ == "__main__":
    main()
