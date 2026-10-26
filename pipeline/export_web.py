"""Step 8 - export what the web prototype needs into web/data/.

The published page cannot load map tiles from other servers, so the
basemap is rendered here from the terrain and land-cover data:
  base.webp       relief shading tinted by land cover, lakes, region mask
  satellite.webp  Sentinel-2 true colour of the latest clear days
  foliage.webp    trees in autumn colour now (transparent elsewhere)
  snow.webp       snow cover in the latest images
  routes.json     every route with its metrics, scores and geometry
  pois.json       huts, lakes, peaks shown on the map
All images are in Web Mercator over BBOX, so the page can place them with
their four corners.
"""
import json
from datetime import date

import numpy as np
import pyarrow.parquet as pq
import rasterio
import shapely
from PIL import Image
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from rasterio.warp import reproject

from common import BBOX, RAW, UTM, WEB_DATA, WORK

MERC = "EPSG:3857"
WIDTH = 4096

to_merc = Transformer.from_crs("EPSG:4326", MERC, always_xy=True)
utm_to_wgs = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
utm_to_merc = Transformer.from_crs(UTM, MERC, always_xy=True)


def merc_grid():
    x0, y0 = to_merc.transform(BBOX[0], BBOX[1])
    x1, y1 = to_merc.transform(BBOX[2], BBOX[3])
    height = int(round(WIDTH * (y1 - y0) / (x1 - x0)))
    return from_bounds(x0, y0, x1, y1, WIDTH, height), WIDTH, height


def to_merc_grid(path, band, resampling, dtype=np.float32, nodata=None):
    t, w, h = merc_grid()
    out = np.zeros((h, w), dtype)
    if nodata is not None:
        out[:] = nodata
    with rasterio.open(path) as src:
        reproject(rasterio.band(src, band), out, dst_transform=t, dst_crs=MERC, resampling=resampling,
                  src_nodata=src.nodata, dst_nodata=nodata)
    return out


def hillshade(z, res, exaggeration=1.6):
    """Multi-directional hillshade (0..1); res is the ground size of a pixel.

    Illumination is the dot product of the surface normal with the light
    direction; light comes mostly from the north-west, as on printed maps.
    """
    d_row, d_col = np.gradient(np.nan_to_num(z, nan=0.0).astype(np.float32), res)
    dzdx, dzdy = exaggeration * d_col, -exaggeration * d_row  # x east, y north (rows grow southwards)
    norm = np.sqrt(dzdx ** 2 + dzdy ** 2 + 1)
    alt = np.radians(40)
    shade = np.zeros(z.shape, np.float32)
    for az, wgt in ((315, 0.55), (270, 0.2), (0, 0.15), (225, 0.1)):
        a = np.radians(az)
        lx, ly, lz = np.sin(a) * np.cos(alt), np.cos(a) * np.cos(alt), np.sin(alt)
        shade += wgt * (-dzdx * lx - dzdy * ly + lz) / norm
    return np.clip(shade, 0, 1)


# ESA WorldCover classes -> basemap tint
LANDCOVER_RGB = {
    10: (122, 150, 112), 20: (160, 166, 120), 30: (196, 204, 160), 40: (214, 210, 170), 50: (205, 198, 190),
    60: (206, 200, 186), 70: (240, 244, 247), 80: (150, 190, 214), 90: (150, 186, 170), 100: (190, 194, 174),
}


def region_mask(t, w, h):
    region = shapely.from_wkb(pq.read_table(RAW / "boundary.parquet").column("geometry")[0].as_py())
    region = shapely.transform(region, lambda xy: np.column_stack(to_merc.transform(xy[:, 0], xy[:, 1])))
    return rasterize([(region, 1)], out_shape=(h, w), transform=t, fill=0, dtype=np.uint8).astype(bool)


def save_webp(arr, name, quality=82, lossless=False):
    mode = "RGBA" if arr.shape[2] == 4 else "RGB"
    Image.fromarray(arr, mode).save(WEB_DATA / name, "WEBP", quality=quality, method=6, lossless=lossless)
    print(f"{name}: {(WEB_DATA / name).stat().st_size / 1e6:.1f} MB")


def basemap(inside):
    t, w, h = merc_grid()
    z = to_merc_grid(WORK / "dem20.tif", 1, Resampling.bilinear, nodata=np.nan)
    lat_mid = (BBOX[1] + BBOX[3]) / 2
    shade = hillshade(z, t.a * np.cos(np.radians(lat_mid)))
    lc = to_merc_grid(WORK / "landcover.tif", 1, Resampling.mode, dtype=np.uint8, nodata=0)
    rgb = np.full((h, w, 3), 220, np.float32)
    for cls, col in LANDCOVER_RGB.items():
        rgb[lc == cls] = col
    # Lighter with altitude, like the hypsometric tints of a paper map
    alt = np.clip((np.nan_to_num(z, nan=0) - 200) / 3000, 0, 1)[..., None]
    rgb = rgb * (1 - 0.18 * alt) + 255 * 0.18 * alt
    lakes = lake_mask(t, w, h)
    rgb[lakes] = (128, 176, 205)
    rgb = rgb * (0.48 + 0.62 * shade[..., None])
    # Outside the region: washed out, so the eye stays on Trentino-Alto Adige
    gray = rgb.mean(axis=2, keepdims=True)
    outside = ~inside[..., None]
    rgb = np.where(outside, 0.55 * gray + 0.45 * 238, rgb)
    save_webp(np.clip(rgb, 0, 255).astype(np.uint8), "base.webp", quality=80)


def lake_mask(t, w, h):
    tb = pq.read_table(RAW / "lakes.parquet").to_pydict()
    geoms = shapely.from_wkb(tb["geometry"])
    geoms = [g for g in geoms if g.geom_type in ("Polygon", "MultiPolygon")]
    geoms = shapely.transform(np.array(geoms), lambda xy: np.column_stack(to_merc.transform(xy[:, 0], xy[:, 1])))
    return rasterize([(g, 1) for g in geoms], out_shape=(h, w), transform=t, fill=0, dtype=np.uint8).astype(bool)


def satellite(inside):
    bands = [to_merc_grid(WORK / "s2_now.tif", b, Resampling.average) / 1e4 for b in (3, 2, 1)]
    rgb = np.stack(bands, -1)
    rgb = np.clip(rgb * 3.0, 0, 1) ** (1 / 1.5)
    rgb[~inside] = 0.55 * rgb[~inside].mean(axis=1, keepdims=True) + 0.45 * 0.93
    save_webp((rgb * 255).astype(np.uint8), "satellite.webp", quality=78)


def overlays(inside):
    a = to_merc_grid(WORK / "foliage.tif", 1, Resampling.average) / 200
    # gold for light colouring, deep orange where the canopy is fully turned
    ramp_lo, ramp_hi = np.array([240, 184, 52]), np.array([206, 88, 20])
    rgb = ramp_lo * (1 - a[..., None]) + ramp_hi * a[..., None]
    alpha = np.where(inside, np.clip(a * 1.3, 0, 0.9), 0)
    save_webp(np.dstack([rgb, alpha * 255]).astype(np.uint8), "foliage.webp", quality=72)

    scl = to_merc_grid(WORK / "s2_now_meta.tif", 3, Resampling.mode, dtype=np.uint16)
    snow = (scl == 11) & inside
    img = np.zeros(snow.shape + (4,), np.uint8)
    img[snow] = (236, 246, 255, 190)
    save_webp(img, "snow.webp", quality=85)


def encode_polyline(coords):
    """Google polyline encoding, precision 1e-5 (lon/lat pairs in, lat-first out)."""
    out, prev = [], (0, 0)
    for lon, lat in coords:
        cur = (int(round(lat * 1e5)), int(round(lon * 1e5)))
        for v in (cur[0] - prev[0], cur[1] - prev[1]):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev = cur
    return "".join(out)


def route_profiles(geom, dem, fol, n=48):
    """Elevation (m) and foliage density (0-9) at n points along the route."""
    parts = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
    line = parts[0] if len(parts) == 1 else shapely.LineString(np.concatenate([shapely.get_coordinates(p) for p in parts]))
    pts = shapely.get_coordinates(shapely.line_interpolate_point(line, np.linspace(0, line.length, n)))
    z = dem.bilinear(pts)
    f = fol.nearest(pts, 1).astype(np.float32) / 200
    return [int(round(v)) for v in z], "".join(str(int(min(9, round(v * 9 / 0.6)))) for v in f)


def routes_json(poi_index):
    from score import Raster

    dem = Raster(WORK / "dem20.tif")
    fol = Raster(WORK / "foliage.tif")
    rows = pq.read_table(WORK / "routes_scored.parquet").to_pylist()
    out = []
    for r in rows:
        geom = shapely.from_wkb(r.pop("geometry"))
        simple = shapely.simplify(geom, 8.0)
        parts = list(simple.geoms) if simple.geom_type == "MultiLineString" else [simple]
        enc = []
        for p in parts:
            xy = shapely.get_coordinates(p)
            lon, lat = utm_to_wgs.transform(xy[:, 0], xy[:, 1])
            enc.append(encode_polyline(zip(lon, lat)))
        z, f = route_profiles(geom, dem, fol)
        start = shapely.get_coordinates(geom)[0]
        lon, lat = utm_to_wgs.transform(start[0], start[1])
        out.append({
            "id": r["osm_id"], "ref": r["ref"], "name": r["name"], "from": r["from"], "to": r["to"],
            "op": r["operator"], "cai": r["cai_scale"], "sym": r["osmc_symbol"], "web": r["website"],
            "km": r["length_km"], "up": r["ascent_m"], "down": r["descent_m"], "emin": r["ele_min"],
            "emax": r["ele_max"], "h": r["hours"], "loop": r["loop"], "fam": r["family"],
            "sc": {"foliage": r["foliage"], "panorama": r["panorama"], "lakes": r["lakes"], "huts": r["huts"]},
            "fs": r["foliage_share"], "dec": r["deciduous_share"], "snow": r["snow_share"],
            "img": r["image_date"], "known": r["image_known"], "open": r["open_best"],
            "lk": r["lake_names"], "nl": r["n_lakes"], "hr": r["hut_rifugi"], "hm": r["hut_malghe"],
            "hb": r["hut_bivacchi"], "pk": [[n, e] for n, e in zip(r["peaks"], r["peak_ele"])],
            "vp": r["viewpoints"], "acc": r["access"], "start": [round(lon, 5), round(lat, 5)],
            "pz": z, "pf": f, "g": enc,
            "pi": [poi_index[i] for i in r["poi_ids"] if i in poi_index],
        })
    meta = json.loads((WORK / "composite_info.json").read_text()) if (WORK / "composite_info.json").exists() else {}
    places, towns = places_json()
    doc = {"generated": date.today().isoformat(), "bbox": BBOX, **meta, "places": places, "towns": towns, "routes": out}
    (WEB_DATA / "routes.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
    print(f"routes.json: {len(out)} routes, {(WEB_DATA / 'routes.json').stat().st_size / 1e6:.1f} MB")


# Towns labelled on the map, with the name hikers use in Italian.
TOWNS = {
    "Trento": "Trento", "Bolzano - Bozen": "Bolzano", "Meran - Merano": "Merano", "Rovereto": "Rovereto",
    "Brixen - Bressanone": "Bressanone", "Bruneck - Brunico": "Brunico", "Riva del Garda": "Riva del Garda",
    "Pergine Valsugana": "Pergine", "Cles": "Cles", "Borgo Valsugana": "Borgo Valsugana",
    "Sterzing - Vipiteno": "Vipiteno", "Schlanders - Silandro": "Silandro", "Mals - Malles Venosta": "Malles",
    "Cavalese": "Cavalese", "Canazei": "Canazei", "Tione di Trento": "Tione", "Madonna di Campiglio": "Campiglio",
    "Urtijëi - St. Ulrich in Gröden - Ortisei": "Ortisei", "Innichen - San Candido": "San Candido", "Fiera di Primiero": "Primiero",
    "Malè": "Malè", "Moena": "Moena", "Sand in Taufers - Campo Tures": "Campo Tures", "Fondo": "Fondo",
}


def places_json():
    """Localities for the "start from" search and the town labels."""
    region = shapely.from_wkb(pq.read_table(RAW / "boundary.parquet").column("geometry")[0].as_py())
    shapely.prepare(region)
    rows = pq.read_table(RAW / "localities.parquet").to_pylist()
    places, seen, towns = [], set(), []
    for r in sorted(rows, key=lambda r: -(r["population"] or 0)):
        g = shapely.from_wkb(r["geometry"])
        if not r["name"] or not region.contains(g):
            continue
        key = r["name"].lower()
        if key not in seen:
            seen.add(key)
            places.append([r["name"], round(g.x, 4), round(g.y, 4)])
        if r["name"] in TOWNS and TOWNS[r["name"]] not in [t[0] for t in towns]:
            towns.append([TOWNS[r["name"]], round(g.x, 4), round(g.y, 4)])
    places.sort(key=lambda p: p[0].lower())
    return places, towns


def pois_json():
    """Named huts, lakes and peaks near at least one route; returns score index -> export index."""
    rows = pq.read_table(WORK / "pois.parquet").to_pylist()
    used = {i for ids in pq.read_table(WORK / "routes_scored.parquet", columns=["poi_ids"]).column(0).to_pylist()
            for i in ids}
    out, index = [], {}
    for i, p in enumerate(rows):
        if i not in used or not p["name"]:
            continue
        g = shapely.from_wkb(p["geometry"])
        if p["kind"] == "lake":
            g = shapely.point_on_surface(g)
        lon, lat = utm_to_wgs.transform(g.x, g.y)
        index[i] = len(out)
        out.append([p["kind"], p["name"], round(lon, 5), round(lat, 5), round(p["ele"]) if p.get("ele") else None])
    (WEB_DATA / "pois.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"pois.json: {len(out)} points")
    return index


def main():
    t, w, h = merc_grid()
    inside = region_mask(t, w, h)
    basemap(inside)
    satellite(inside)
    overlays(inside)
    routes_json(pois_json())
    x0, y0 = BBOX[0], BBOX[1]
    corners = [[BBOX[0], BBOX[3]], [BBOX[2], BBOX[3]], [BBOX[2], BBOX[1]], [BBOX[0], BBOX[1]]]
    (WEB_DATA / "images.json").write_text(json.dumps({"corners": corners, "width": w, "height": h}))


if __name__ == "__main__":
    main()
