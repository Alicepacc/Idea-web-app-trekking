"""Step 7 - measure every route and score it on each criterion (0-100).

Each route is sampled every 20 m. From the samples:
  profile     length, ascent/descent, min/max elevation, walking time
  foliage     how much of the route runs through trees in autumn colour now
  panorama    how open the view is at the best point and along the route
  lakes       named lakes and ponds within 150 m
  huts        mountain huts, bivouacs and alpine pastures within 100 m
  summits     named peaks the route touches (within 60 m)
  access      parking, cable car or bus stop within 300 m of either end
  snow        share of the route under snow in the latest image

Writes data/work/routes_scored.parquet and data/work/pois.parquet.
"""
import re
import unicodedata
from datetime import date, timedelta

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import rasterio
import shapely
from pyproj import Transformer
from scipy.ndimage import map_coordinates

from common import RAW, UTM, WORK

STEP = 20.0
LAKE_DIST, HUT_DIST, PEAK_DIST, VIEW_DIST, ACCESS_DIST = 150.0, 100.0, 60.0, 100.0, 300.0
OPEN_DEG = 3.0  # a direction counts as open when the horizon is below 3 degrees
# Water bodies that are not lakes worth walking to.
NOT_LAKE = re.compile(r"kneipp|becken|vasca|piscina|lido|schwimm|bad\b|fischzucht|peschiera|itticolt|"
                      r"speicher|beschneiung|innevamento|depurat|kläranlage|feuerlösch|antincendio|biotop|tank", re.I)
EPOCH = date(2000, 1, 1)

to_utm = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)


def project(geoms):
    return shapely.transform(geoms, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])))


class Raster:
    def __init__(self, path, bands=None):
        with rasterio.open(path) as ds:
            self.data = ds.read(bands) if bands else ds.read()
            self.t = ds.transform

    def rc(self, xy):
        col = (xy[:, 0] - self.t.c) / self.t.a - 0.5
        row = (xy[:, 1] - self.t.f) / self.t.e - 0.5
        return row, col

    def nearest(self, xy, band=0):
        row, col = self.rc(xy)
        b = self.data[band]
        r = np.clip(np.round(row).astype(int), 0, b.shape[0] - 1)
        c = np.clip(np.round(col).astype(int), 0, b.shape[1] - 1)
        return b[r, c]

    def bilinear(self, xy, band=0):
        row, col = self.rc(xy)
        return map_coordinates(self.data[band], [row, col], order=1, mode="nearest")


def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def load_pois(region, dem):
    """Peaks, viewpoints, lakes, huts and access points inside the region (+2 km)."""
    zone = region.buffer(2000)
    shapely.prepare(zone)
    rows = []

    def add(kind, name, geom, **extra):
        rows.append(dict(kind=kind, name=name, geometry=geom, **extra))

    t = pq.read_table(RAW / "peaks.parquet").to_pydict()
    for g, n, cl, ele in zip(project(shapely.from_wkb(t["geometry"])), t["name"], t["class"], t["elevation"]):
        if n and zone.contains(g):
            add("peak" if cl != "saddle" else "saddle", n, g, ele=ele)

    t = pq.read_table(RAW / "viewpoints.parquet").to_pydict()
    for g, n in zip(project(shapely.from_wkb(t["geometry"])), t["name"]):
        g = shapely.point_on_surface(g)
        if zone.contains(g):
            add("viewpoint", n, g)

    t = pq.read_table(RAW / "lakes.parquet").to_pydict()
    for g, n, cl in zip(project(shapely.from_wkb(t["geometry"])), t["name"], t["class"]):
        if g.geom_type not in ("Polygon", "MultiPolygon") or not zone.intersects(g):
            continue
        if n and NOT_LAKE.search(n):
            continue
        # Unnamed ponds count only from half a hectare: smaller ones are mostly basins and puddles.
        if (n and g.area >= 1000) or g.area >= 5000:
            add("lake", n, g, area=float(g.area))

    t = pq.read_table(RAW / "access.parquet").to_pydict()
    for g, n, cl in zip(project(shapely.from_wkb(t["geometry"])), t["name"], t["class"]):
        g = shapely.point_on_surface(g)
        if zone.contains(g):
            kind = {"parking": "parking", "aerialway_station": "cablecar"}.get(cl, "bus")
            add(kind, n, g)

    # Huts: OSM buildings first, then Overture places not already present within 300 m.
    t = pq.read_table(RAW / "huts.parquet").to_pylist()
    t.sort(key=lambda r: r["source"] != "osm_building")
    huts = []
    for r in t:
        g = shapely.point_on_surface(project(shapely.from_wkb(r["geometry"])))
        if not zone.contains(g):
            continue
        ele = float(dem.bilinear(shapely.get_coordinates(g))[0])
        if not ele >= 600:  # skips urban namesakes ("Rifugio" bars and B&Bs in town)
            continue
        key = norm_name(r["name"])
        if any(h[0] == key and h[1].distance(g) < 300 for h in huts):
            continue
        if any(h[1].distance(g) < 40 for h in huts):
            continue
        huts.append((key, g))
        add(r["kind"], r["name"], g, ele=ele)
    return rows


def profile(parts_xyz):
    """Ascent, descent along the line direction; smoothing removes DEM noise."""
    up = down = 0.0
    for z in parts_xyz:
        if len(z) < 3:
            continue
        k = min(5, len(z))
        zs = np.convolve(np.pad(z, (k // 2, k - 1 - k // 2), mode="edge"), np.ones(k) / k, mode="valid")
        d = np.diff(zs)
        up += d[d > 0].sum()
        down -= d[d < 0].sum()
    return up, down


def walking_hours(km, up, down):
    """DIN 33466, the rule used by the Alpine clubs: 4 km/h, +300 m/h, -500 m/h."""
    horizontal = km / 4.0
    vertical = up / 300.0 + down / 500.0
    return max(horizontal, vertical) + min(horizontal, vertical) / 2


def clip01(x):
    return float(np.clip(x, 0.0, 1.0))


def main():
    routes = pq.read_table(WORK / "routes.parquet").to_pylist()
    region = project(shapely.from_wkb(pq.read_table(RAW / "boundary.parquet").column("geometry")[0].as_py()))

    dem = Raster(WORK / "dem20.tif")
    foliage = Raster(WORK / "foliage.tif")  # colour, density, deciduous, seen (0-200)
    meta = Raster(WORK / "s2_now_meta.tif")  # 1 quality, 2 date, 3 SCL
    with rasterio.open(WORK / "horizon.tif") as ds:
        hor = ds.read()
        pano = Raster.__new__(Raster)
        pano.t = ds.transform
    pano.data = np.stack([np.mean(hor < OPEN_DEG, axis=0), np.nanmean(hor, axis=0)]).astype(np.float32)
    del hor

    pois = load_pois(region, dem)
    pgeom = np.array([p["geometry"] for p in pois])
    tree = shapely.STRtree(pgeom)
    kinds = np.array([p["kind"] for p in pois])
    print(f"POIs: " + ", ".join(f"{k} {int((kinds == k).sum())}" for k in np.unique(kinds)))

    out = []
    for r in routes:
        geom = shapely.from_wkb(r["geometry"])
        parts = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
        xy_parts = []
        for p in parts:
            n = max(int(p.length // STEP), 1)
            xy_parts.append(shapely.get_coordinates(shapely.line_interpolate_point(p, np.linspace(0, p.length, n + 1))))
        xy = np.concatenate(xy_parts)

        z_parts = [dem.bilinear(a) for a in xy_parts]
        z = np.concatenate(z_parts)
        up, down = profile(z_parts)
        km = r["length_m"] / 1000
        loop = (r.get("roundtrip") == "yes") or (len(parts) == 1 and shapely.get_point(parts[0], 0).distance(
            shapely.get_point(parts[0], -1)) < 150)
        if not loop and down > up:  # walk it uphill: "ascent" is the climb of the hike
            up, down = down, up
            xy_parts = [a[::-1] for a in xy_parts[::-1]]

        # Foliage: density of coloured canopy within ~150 m of each sample
        dens = foliage.nearest(xy, 1).astype(np.float32) / 200
        colour = foliage.nearest(xy, 0).astype(np.float32) / 200
        decid = foliage.nearest(xy, 2).astype(np.float32) / 200
        known = foliage.nearest(xy, 3) > 0  # usable latest observation: no gap, snow or haze
        snow = meta.nearest(xy, 2) == 11
        obs = meta.nearest(xy, 1)
        f_share = float(np.mean(dens[known] >= 0.25)) if known.any() else 0.0
        f_mean = float(np.mean(dens[known])) if known.any() else 0.0
        # Routes seen only in part (clouds, deep shadow) get a proportionally weaker score.
        f_score = 100 * clip01(0.55 * f_share / 0.75 + 0.45 * f_mean / 0.45) * min(1.0, float(np.mean(known)) / 0.6)

        # Panorama: best open spot plus share of the route with open views
        open_frac = pano.nearest(xy, 0)
        best = float(np.quantile(open_frac, 0.95))
        open_share = float(np.mean(open_frac >= 0.5))
        p_score = 100 * clip01(0.65 * best / 0.75 + 0.35 * open_share / 0.4)

        near = {}
        for kind, dist in (("lake", LAKE_DIST), ("rifugio", HUT_DIST), ("bivacco", HUT_DIST), ("malga", HUT_DIST),
                           ("peak", PEAK_DIST), ("viewpoint", VIEW_DIST)):
            idx = tree.query(geom, predicate="dwithin", distance=dist)
            idx = [i for i in idx if kinds[i] == kind]
            near[kind] = idx
        lake_names = sorted({pois[i]["name"] for i in near["lake"] if pois[i]["name"]})
        n_lakes = len({pois[i]["name"] or i for i in near["lake"]})
        big = any(pois[i]["area"] >= 50000 for i in near["lake"])  # 5 ha: a lake you see from afar
        l_score = 0.0 if n_lakes == 0 else min(100.0, 50 + 15 * (n_lakes - 1) + 15 * bool(lake_names) + 15 * big)
        huts = {k: sorted({pois[i]["name"] for i in near[k]}) for k in ("rifugio", "bivacco", "malga")}
        h_score = 100.0 if huts["rifugio"] else 75.0 if huts["malga"] else 55.0 if huts["bivacco"] else 0.0
        peaks = sorted({(pois[i]["name"], pois[i].get("ele")) for i in near["peak"]}, key=lambda p: -(p[1] or 0))
        poi_ids = sorted({int(i) for k in ("lake", "rifugio", "bivacco", "malga", "peak") for i in near[k]})

        ends = [shapely.Point(xy_parts[0][0]), shapely.Point(xy_parts[-1][-1])]
        access = set()
        for e in ends:
            for i in tree.query(e, predicate="dwithin", distance=ACCESS_DIST):
                if kinds[i] in ("parking", "cablecar", "bus"):
                    access.add(str(kinds[i]))

        hours = walking_hours(km, up, down)
        cai = (r.get("cai_scale") or "").upper() or None
        family = (cai in (None, "T", "E") and up <= 500 and km <= 9 and float(np.nanmax(z)) <= 2300)

        obs_known = obs[known]
        out.append({
            "osm_id": r["osm_id"],
            "ref": r["ref"], "name": r["name"] or r["name_it"] or r["name_de"],
            "from": r["from"], "to": r["to"], "operator": r["operator"], "network": r["network"],
            "cai_scale": cai, "osmc_symbol": r["osmc_symbol"], "website": r["website"],
            "description": r["description_it"] or r["description"],
            "length_km": round(km, 2), "ascent_m": round(up), "descent_m": round(down),
            "ele_min": round(float(np.nanmin(z))), "ele_max": round(float(np.nanmax(z))),
            "hours": round(hours, 2), "loop": bool(loop), "family": bool(family),
            "foliage": round(f_score), "foliage_share": round(f_share, 3), "foliage_mean": round(f_mean, 3),
            "foliage_colour_max": round(float(np.quantile(colour, 0.95)), 3),
            "deciduous_share": round(float(np.mean(decid >= 0.5)), 3),
            "image_date": (EPOCH + timedelta(days=int(np.median(obs_known)))).isoformat() if len(obs_known) else None,
            "image_known": round(float(np.mean(known)), 3),
            "snow_share": round(float(np.mean(snow)), 3),
            "panorama": round(p_score), "open_best": round(best, 3), "open_share": round(open_share, 3),
            "lakes": round(l_score), "lake_names": lake_names, "n_lakes": n_lakes,
            "huts": round(h_score), "hut_rifugi": huts["rifugio"], "hut_malghe": huts["malga"],
            "hut_bivacchi": huts["bivacco"],
            "peaks": [p[0] for p in peaks], "peak_ele": [p[1] for p in peaks],
            "viewpoints": len(near["viewpoint"]), "poi_ids": poi_ids,
            "access": sorted(access),
            "complete": r["complete"], "parts": r["parts"],
            "geometry": shapely.to_wkb(shapely.MultiLineString([a for a in xy_parts if len(a) > 1])
                                       if len(xy_parts) > 1 else shapely.LineString(xy_parts[0])),
        })

    tb = pa.Table.from_pylist(out)
    pq.write_table(tb, WORK / "routes_scored.parquet", compression="zstd")
    pt = pa.Table.from_pylist([{"kind": p["kind"], "name": p["name"], "ele": p.get("ele"), "area": p.get("area"),
                                "geometry": shapely.to_wkb(p["geometry"])} for p in pois])
    pq.write_table(pt, WORK / "pois.parquet", compression="zstd")
    for col in ("foliage", "panorama", "lakes", "huts"):
        v = np.array(tb.column(col).to_pylist())
        print(f"{col:9s} >0: {np.mean(v > 0):.0%}  p50 {np.median(v):.0f}  p90 {np.quantile(v, 0.9):.0f}  max {v.max()}")


if __name__ == "__main__":
    main()
