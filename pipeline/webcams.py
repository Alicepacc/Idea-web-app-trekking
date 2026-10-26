"""Step 13 - webcams that look at each hike.

foto-webcam.eu lists its cameras with position, viewing direction, field of
view and viewing radius. A camera "sees" a hike when its goal, or a point of
the hike, falls inside that sector. Otherwise the nearest camera within 15 km
still shows the weather and snow of the area. The page links to the camera
pages: the images belong to their operators.

Writes data/work/webcams.json:
  {"cams": [[title, link, lon, lat, ele], ...], "hikes": {"<id>": [[cam, sees, km], ...]}}
"""
import json
import math
import time
import urllib.request

import numpy as np
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

from common import BBOX, RAW, UTM, WORK

LIST = "https://www.foto-webcam.eu/webcam/include/metadata.php"
UA = {"User-Agent": "SentieriAutunno/0.1 (hiking prototype; github.com/Alicepacc/Idea-web-app-trekking)"}
NEAR_KM = 15

to_utm = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)


def cameras():
    cache = RAW / "foto_webcam.json"
    if not cache.exists() or time.time() - cache.stat().st_mtime > 86400:
        with urllib.request.urlopen(urllib.request.Request(LIST, headers=UA), timeout=60) as r:
            cache.write_bytes(r.read())
    cams = json.loads(cache.read_text())["cams"]
    lon0, lat0, lon1, lat1 = BBOX
    return [c for c in cams if not c.get("offline") and not c.get("hidden")
            and lon0 - 0.3 <= c["longitude"] <= lon1 + 0.3 and lat0 - 0.2 <= c["latitude"] <= lat1 + 0.2]


def main():
    cams = cameras()
    cxy = np.array([to_utm.transform(c["longitude"], c["latitude"]) for c in cams])
    direction = np.array([c.get("direction") or 0 for c in cams], float)
    sector = np.array([c.get("sector") or 0 for c in cams], float)
    radius = np.array([(c.get("radius_km") or 10) * 1000 for c in cams], float)

    pois = pq.read_table(WORK / "pois.parquet", columns=["geometry"]).column(0).to_pylist()
    rows = pq.read_table(WORK / "itineraries.parquet", columns=["id", "goal_poi", "geometry"]).to_pylist()
    hikes = {}
    for r in rows:
        line = shapely.from_wkb(r["geometry"])
        pts = shapely.get_coordinates(shapely.line_interpolate_point(line, np.arange(0, line.length, 1000)))
        goal = shapely.get_coordinates(shapely.point_on_surface(shapely.from_wkb(pois[r["goal_poi"]])))
        pts = np.vstack([goal, pts])
        dx = pts[:, None, 0] - cxy[None, :, 0]
        dy = pts[:, None, 1] - cxy[None, :, 1]
        dist = np.hypot(dx, dy)
        bearing = (np.degrees(np.arctan2(dx, dy)) + 360) % 360
        off = np.abs((bearing - direction[None, :] + 180) % 360 - 180)
        inside = (dist <= radius[None, :]) & (off <= sector[None, :] / 2) & (sector[None, :] > 0)
        sees_goal, sees_any = inside[0], inside.any(axis=0)
        nearest = dist.min(axis=0)
        order = np.lexsort((nearest, ~sees_any, ~sees_goal))
        picks = []
        for i in order[:6]:
            if sees_any[i] or nearest[i] <= NEAR_KM * 1000:
                picks.append([int(i), bool(sees_any[i]), round(float(nearest[i]) / 1000, 1)])
            if len(picks) == 2:
                break
        if picks:
            hikes[str(r["id"])] = picks
    doc = {"cams": [[c["title"], c["link"], round(c["longitude"], 5), round(c["latitude"], 5), c.get("elevation")]
                    for c in cams], "hikes": hikes}
    (WORK / "webcams.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
    seeing = sum(1 for v in hikes.values() if v[0][1])
    print(f"webcams.json: {len(cams)} cameras; {len(hikes)} hikes with a webcam, {seeing} framed by one")


if __name__ == "__main__":
    main()
