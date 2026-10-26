"""Step 9 - map tiles, so the map stays sharp when zooming in.

The published page cannot load tiles from other servers, so it ships its own:
512 px WebP tiles in Web Mercator for zoom 7-12 (zoom 12 is about 13 m per
pixel here, close to the 10 m of Sentinel-2). Tiles are grouped into packs by
area, and the page downloads a pack only when you look at that area.

Layers:
  base       relief shading tinted by land cover, with lakes drawn sharp
  satellite  Sentinel-2 true colour of the latest clear days (10 m)
  foliage    trees in autumn colour now (transparent elsewhere)
  snow       snow in the latest images (transparent elsewhere)

Writes web/data/tiles/<pack>.webp and web/data/tiles/index.json (a pack is WebP tiles
end to end, named .webp because the page host serves only standard web file types):
  {"tileSize": 512, "minzoom": 7, "maxzoom": 12, "bounds": [...],
   "layers": {"base": {"z/x/y": [pack, offset, length], ...}, ...}}
"""
import io
import json
import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pyarrow.parquet as pq
import rasterio
import shapely
from PIL import Image
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import from_bounds as window_from_bounds

from common import BBOX, RAW, UTM, WEB_DATA, WORK

MERC = "EPSG:3857"
TILE = 512
MINZOOM, MAXZOOM = 7, 12
PACK_BLOCK = 4  # at zoom >= 11 a pack holds 4 x 4 tiles
WORLD = 2 * math.pi * 6378137.0
OUT = WEB_DATA / "tiles"

to_merc = Transformer.from_crs("EPSG:4326", MERC, always_xy=True)

# ESA WorldCover classes -> basemap tint
LANDCOVER_RGB = {
    10: (122, 150, 112), 20: (160, 166, 120), 30: (196, 204, 160), 40: (214, 210, 170), 50: (205, 198, 190),
    60: (206, 200, 186), 70: (240, 244, 247), 80: (150, 190, 214), 90: (150, 186, 170), 100: (190, 194, 174),
}
LAKE_RGB = (128, 176, 205)


def tile_bounds(z, x, y):
    span = WORLD / 2 ** z
    minx = -WORLD / 2 + x * span
    maxy = WORLD / 2 - y * span
    return minx, maxy - span, minx + span, maxy


def tile_range(z):
    def tx(lon):
        return int((lon + 180) / 360 * 2 ** z)

    def ty(lat):
        r = math.radians(lat)
        return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2 ** z)

    return range(tx(BBOX[0]), tx(BBOX[2]) + 1), range(ty(BBOX[3]), ty(BBOX[1]) + 1)


class Source:
    """A raster in the UTM grid, read once, cut and warped per tile."""

    def __init__(self, path, bands=None):
        with rasterio.open(path) as ds:
            self.data = ds.read(bands) if bands else ds.read()
            self.transform, self.nodata = ds.transform, ds.nodata

    def warp(self, bounds, size, resampling, dtype=np.float32, fill=0):
        """Warp to a size x size Web Mercator grid over bounds (minx, miny, maxx, maxy)."""
        ub = transform_bounds(MERC, UTM, *bounds, densify_pts=8)
        res = self.transform.a
        win = window_from_bounds(ub[0] - 3 * res, ub[1] - 3 * res, ub[2] + 3 * res, ub[3] + 3 * res, self.transform)
        win = win.round_offsets().round_lengths()
        h, w = self.data.shape[-2:]
        r0, c0 = max(int(win.row_off), 0), max(int(win.col_off), 0)
        r1, c1 = min(int(win.row_off + win.height), h), min(int(win.col_off + win.width), w)
        out = np.full((self.data.shape[0], size, size), fill, dtype)
        if r0 >= r1 or c0 >= c1:
            return out
        src = self.data[:, r0:r1, c0:c1]
        src_t = self.transform * self.transform.translation(c0, r0)
        reproject(src, out, src_transform=src_t, src_crs=UTM, src_nodata=self.nodata,
                  dst_transform=from_bounds(*bounds, size, size), dst_crs=MERC, dst_nodata=fill, resampling=resampling)
        return out


def hillshade(z, res, exaggeration):
    d_row, d_col = np.gradient(np.nan_to_num(z, nan=0.0).astype(np.float32), res)
    dzdx, dzdy = exaggeration * d_col, -exaggeration * d_row
    norm = np.sqrt(dzdx ** 2 + dzdy ** 2 + 1)
    alt = np.radians(40)
    shade = np.zeros(z.shape, np.float32)
    for az, wgt in ((315, 0.55), (270, 0.2), (0, 0.15), (225, 0.1)):
        a = np.radians(az)
        shade += wgt * (-dzdx * np.sin(a) * np.cos(alt) - dzdy * np.cos(a) * np.cos(alt) + np.sin(alt)) / norm
    return np.clip(shade, 0, 1)


def wash(rgb, inside):
    """Outside the region: washed out, so the eye stays on Trentino-Alto Adige."""
    gray = rgb.mean(axis=2, keepdims=True)
    return np.where(inside[..., None], rgb, 0.55 * gray + 0.45 * 238)


def webp(arr, quality, lossless=False):
    buf = io.BytesIO()
    Image.fromarray(arr, "RGBA" if arr.shape[2] == 4 else "RGB").save(
        buf, "WEBP", quality=quality, method=5, lossless=lossless)
    return buf.getvalue()


class Renderer:
    def __init__(self):
        self.dem = Source(WORK / "dem20.tif")
        self.lc = Source(WORK / "landcover.tif")
        self.rgb = Source(WORK / "s2_now_rgb10.tif")
        self.fol = Source(WORK / "foliage.tif", [1])
        with rasterio.open(WORK / "s2_now_meta.tif") as ds:
            self.snow = Source.__new__(Source)
            self.snow.data = (ds.read(3) == 11).astype(np.uint8)[None]
            self.snow.transform, self.snow.nodata = ds.transform, None
        region = shapely.from_wkb(pq.read_table(RAW / "boundary.parquet").column("geometry")[0].as_py())
        self.region = shapely.transform(region, lambda xy: np.column_stack(to_merc.transform(xy[:, 0], xy[:, 1])))
        self.zone = self.region.buffer(3000)
        shapely.prepare(self.zone)
        lakes = shapely.from_wkb(pq.read_table(RAW / "lakes.parquet", columns=["geometry"]).column(0).to_pylist())
        lakes = [g for g in lakes if g.geom_type in ("Polygon", "MultiPolygon")]
        self.lakes = shapely.transform(np.array(lakes), lambda xy: np.column_stack(to_merc.transform(xy[:, 0], xy[:, 1])))
        self.lake_tree = shapely.STRtree(self.lakes)

    def tiles(self):
        for z in range(MINZOOM, MAXZOOM + 1):
            xs, ys = tile_range(z)
            for x in xs:
                for y in ys:
                    if self.zone.intersects(shapely.box(*tile_bounds(z, x, y))):
                        yield z, x, y

    def inside(self, bounds):
        return rasterize([(self.region, 1)], out_shape=(TILE, TILE), transform=from_bounds(*bounds, TILE, TILE),
                         fill=0, dtype=np.uint8).astype(bool)

    def base(self, z, x, y):
        b = tile_bounds(z, x, y)
        px = (b[2] - b[0]) / TILE
        pad = 8
        padded = (b[0] - pad * px, b[1] - pad * px, b[2] + pad * px, b[3] + pad * px)
        elev = self.dem.warp(padded, TILE + 2 * pad, Resampling.bilinear, fill=np.nan)[0]
        lat = math.degrees(2 * math.atan(math.exp(((b[1] + b[3]) / 2) / 6378137.0)) - math.pi / 2)
        exaggeration = 2.2 if z <= 9 else 1.8 if z <= 10 else 1.5
        shade = hillshade(elev, px * math.cos(math.radians(lat)), exaggeration)[pad:-pad, pad:-pad]
        elev = elev[pad:-pad, pad:-pad]
        lc = self.lc.warp(b, TILE, Resampling.mode if z < 11 else Resampling.nearest, np.uint8)[0]
        rgb = np.full((TILE, TILE, 3), 220, np.float32)
        for cls, col in LANDCOVER_RGB.items():
            rgb[lc == cls] = col
        alt = np.clip((np.nan_to_num(elev, nan=0) - 200) / 3000, 0, 1)[..., None]
        rgb = rgb * (1 - 0.18 * alt) + 255 * 0.18 * alt
        idx = self.lake_tree.query(shapely.box(*b))
        if len(idx):
            water = rasterize([(g, 1) for g in self.lakes[idx]], out_shape=(TILE, TILE),
                              transform=from_bounds(*b, TILE, TILE), fill=0, dtype=np.uint8).astype(bool)
            rgb[water] = LAKE_RGB
        rgb = rgb * (0.48 + 0.62 * shade[..., None])
        rgb = wash(rgb, self.inside(b))
        return webp(np.clip(rgb, 0, 255).astype(np.uint8), 78)

    def satellite(self, z, x, y):
        b = tile_bounds(z, x, y)
        bands = self.rgb.warp(b, TILE, Resampling.average if z < MAXZOOM else Resampling.bilinear) / 1e4
        rgb = np.clip(np.moveaxis(bands, 0, -1) * 3.0, 0, 1) ** (1 / 1.5) * 255
        rgb = wash(rgb, self.inside(b))
        return webp(np.clip(rgb, 0, 255).astype(np.uint8), 80)

    def foliage(self, z, x, y):
        b = tile_bounds(z, x, y)
        a = self.fol.warp(b, TILE, Resampling.average if z < 11 else Resampling.bilinear)[0] / 200
        if a.max() < 0.02:
            return None
        lo, hi = np.array([240, 184, 52]), np.array([206, 88, 20])
        rgb = lo * (1 - a[..., None]) + hi * a[..., None]
        alpha = np.where(self.inside(b), np.clip(a * 1.3, 0, 0.9), 0) * 255
        return webp(np.dstack([rgb, alpha]).astype(np.uint8), 75)

    def snow_tile(self, z, x, y):
        b = tile_bounds(z, x, y)
        m = self.snow.warp(b, TILE, Resampling.average if z < 11 else Resampling.nearest)[0]
        if m.max() < 0.3:
            return None
        img = np.zeros((TILE, TILE, 4), np.uint8)
        img[..., :3] = (236, 246, 255)
        img[..., 3] = np.where(self.inside(b), np.clip(m, 0, 1) * 190, 0).astype(np.uint8)
        return webp(img, 80, lossless=True)


def pack_name(layer, z, x, y):
    if z <= 10:
        return f"{layer}-z{MINZOOM}-10"
    return f"{layer}-z{z}-{x // PACK_BLOCK}-{y // PACK_BLOCK}"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.webp"):
        old.unlink()
    r = Renderer()
    tiles = list(r.tiles())
    print(f"{len(tiles)} tiles per layer, zoom {MINZOOM}-{MAXZOOM}")
    index = {"tileSize": TILE, "minzoom": MINZOOM, "maxzoom": MAXZOOM, "bounds": list(BBOX), "layers": {}}
    for layer, fn in (("base", r.base), ("satellite", r.satellite), ("foliage", r.foliage), ("snow", r.snow_tile)):
        packs = defaultdict(list)
        with ThreadPoolExecutor(4) as ex:
            for (z, x, y), data in zip(tiles, ex.map(lambda t: fn(*t), tiles)):
                if data is not None:
                    packs[pack_name(layer, z, x, y)].append((f"{z}/{x}/{y}", data))
        entries, size = {}, 0
        for name, items in packs.items():
            offset = 0
            with open(OUT / f"{name}.webp", "wb") as f:
                for key, data in items:
                    f.write(data)
                    entries[key] = [name, offset, len(data)]
                    offset += len(data)
            size += offset
        index["layers"][layer] = entries
        print(f"{layer}: {len(entries)} tiles in {len(packs)} packs, {size / 1e6:.1f} MB")
    (OUT / "index.json").write_text(json.dumps(index, separators=(",", ":")))


if __name__ == "__main__":
    main()
