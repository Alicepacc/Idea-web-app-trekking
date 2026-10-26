"""Step 12 - named panoramas: which summits you see from the best spot of each hike.

For every hike, the point with the most open horizon is its viewpoint. From
there the skyline is traced in all directions on the 100 m terrain model
(earth curvature and refraction included), and every named summit within
70 km is checked for a clear line of sight. Labels go to the visible summits
that shape the skyline, highest first, a few degrees apart.

Writes data/work/panorama.json:
  {"<hike id>": {"vp": [lon, lat, ele], "sky": "<base64 int8, 0.2 deg units, one per 2 deg>",
                 "pk": [[name, ele, azimuth, angle, km], ...], "on": "<summit you stand on>"}}
"""
import base64
import json
import sys

import numpy as np
import pyarrow.parquet as pq
import rasterio
import shapely
from pyproj import Transformer
from scipy.ndimage import map_coordinates
from scipy.spatial import cKDTree

from common import RAW, UTM, WORK
from itineraries import display_name
from score import OPEN_DEG

EYE = 1.7
R_EFF = 6371000.0 / (1 - 0.13)   # earth radius stretched by standard refraction
MAX_M = 70000.0
AZ_STEP = 2
SKY_UNIT = 0.2                     # degrees per stored step
MIN_PEAK_ELE = 1500
MAX_LABELS = 14
MIN_SEPARATION = 5.0               # degrees between labels

to_wgs = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
to_utm = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)


class Grid:
    def __init__(self, path, data=None):
        with rasterio.open(path) as ds:
            self.z = ds.read(1).astype(np.float32) if data is None else data
            self.t = ds.transform
        self.zf = np.nan_to_num(self.z, nan=-1000.0)

    def sample(self, x, y):
        col = (x - self.t.c) / self.t.a - 0.5
        row = (y - self.t.f) / self.t.e - 0.5
        return map_coordinates(self.zf, [row, col], order=1, mode="constant", cval=-1000.0)


def angle(dz, d):
    return np.degrees(np.arctan2(dz - d * d / (2 * R_EFF), d))


def main():
    dem = Grid(WORK / "dem100.tif")
    dem20 = Grid(WORK / "dem20.tif")
    with rasterio.open(WORK / "horizon.tif") as ds:
        openness = Grid.__new__(Grid)
        openness.t = ds.transform
        openness.zf = np.mean(ds.read() < OPEN_DEG, axis=0).astype(np.float32)

    peaks = pq.read_table(RAW / "peaks.parquet").to_pylist()
    pk = [p for p in peaks if p["class"] == "peak" and p["name"]]
    pxy = np.array(to_utm.transform([shapely.from_wkb(p["geometry"]).x for p in pk],
                                    [shapely.from_wkb(p["geometry"]).y for p in pk])).T
    pele = np.array([p["elevation"] if p["elevation"] else np.nan for p in pk], np.float32)
    pele = np.where(np.isnan(pele), dem.sample(pxy[:, 0], pxy[:, 1]), pele)
    keep = pele >= MIN_PEAK_ELE
    pk, pxy, pele = [p for p, k in zip(pk, keep) if k], pxy[keep], pele[keep]
    tree = cKDTree(pxy)
    print(f"{len(pk)} named summits above {MIN_PEAK_ELE} m")

    az = np.radians(np.arange(0, 360, AZ_STEP))
    dist = np.geomspace(300, MAX_M, 220)
    out = {}
    rows = pq.read_table(WORK / "itineraries.parquet", columns=["id", "geometry"]).to_pylist()
    if len(sys.argv) > 1:  # quick test on the first N hikes
        rows = rows[:int(sys.argv[1])]
    for r in rows:
        line = shapely.from_wkb(r["geometry"])
        pts = shapely.get_coordinates(shapely.line_interpolate_point(line, np.arange(0, line.length, 100)))
        if not len(pts):
            continue
        o = openness.sample(pts[:, 0], pts[:, 1])
        z = dem20.sample(pts[:, 0], pts[:, 1])
        best = int(np.lexsort((-z, -o))[0])
        vx, vy = pts[best]
        zv = float(z[best]) + EYE

        # skyline: highest terrain angle in each direction
        sx = vx + np.outer(np.sin(az), dist)
        sy = vy + np.outer(np.cos(az), dist)
        sky = angle(dem.sample(sx.ravel(), sy.ravel()).reshape(sx.shape) - zv, dist[None, :]).max(axis=1)

        # summits within 70 km that reach the skyline, then a clear line of sight to each
        labels, on = [], None
        idx = np.array(tree.query_ball_point((vx, vy), r=MAX_M), int)
        if len(idx):
            dx, dy = pxy[idx, 0] - vx, pxy[idx, 1] - vy
            d = np.hypot(dx, dy)
            mine = idx[(d < 400) & (pele[idx] >= zv - 60)]
            if len(mine):
                on = display_name(pk[int(mine[np.argmax(pele[mine])])]["name"])
            a = (np.degrees(np.arctan2(dx, dy)) + 360) % 360
            target = angle(pele[idx] - zv, np.maximum(d, 1))
            near_sky = (d >= 400) & (target >= sky[np.round(a / AZ_STEP).astype(int) % len(sky)] - 0.8)
            idx, dx, dy, d, a, target = idx[near_sky], dx[near_sky], dy[near_sky], d[near_sky], a[near_sky], target[near_sky]
            if len(idx):
                u = np.linspace(0, 1, 160)[None, :]
                f = 0.02 + (1 - 300 / d[:, None] - 0.02) * u
                tz = dem.sample((vx + dx[:, None] * f).ravel(), (vy + dy[:, None] * f).ravel()).reshape(f.shape)
                terrain = angle(tz - zv, d[:, None] * f).max(axis=1)
                ok = target >= terrain - 0.05
                labels = [(display_name(pk[i]["name"]), float(pele[i]), float(az_), float(t), float(dd) / 1000)
                          for i, az_, t, dd in zip(idx[ok], a[ok], target[ok], d[ok])]
        # label the summits that draw the skyline, highest first, spread out
        chosen = []
        for name, ele, a_, ang, km in sorted(labels, key=lambda t: -t[1]):
            if any(min(abs(a_ - c[2]), 360 - abs(a_ - c[2])) < MIN_SEPARATION for c in chosen):
                continue
            chosen.append((name, round(ele), round(a_, 1), round(ang, 2), round(km, 1)))
            if len(chosen) >= MAX_LABELS:
                break
        lon, lat = to_wgs.transform(vx, vy)
        q = np.clip(np.round(sky / SKY_UNIT), -127, 127).astype(np.int8)
        out[str(r["id"])] = {"vp": [round(lon, 5), round(lat, 5), round(zv - EYE)],
                             "sky": base64.b64encode(q.tobytes()).decode(), "pk": chosen, "on": on,
                             "nvis": len(labels)}
    (WORK / "panorama.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    counts = np.array([v["nvis"] for v in out.values()])
    print(f"panorama.json: {len(out)} viewpoints; visible summits per viewpoint p50 {np.median(counts):.0f}, "
          f"p90 {np.percentile(counts, 90):.0f}")


if __name__ == "__main__":
    main()
