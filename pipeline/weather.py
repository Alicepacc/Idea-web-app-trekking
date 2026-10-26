"""Step 10 - weekend weather from Open-Meteo.

The page cannot call weather services itself, so the forecast is fetched here
and shipped with the data. A grid of points about 8 km apart covers the region;
each point is asked at 1000 m and at 2500 m, so temperatures can be read at the
height a hike actually reaches. Open-Meteo data is CC BY 4.0; its free API is
for non-commercial use (a public launch needs their commercial plan).

Writes data/work/weather.json:
  {"days": [...], "issued": ..., "points": [[lon, lat], ...],
   "low": {var: [[day0 point0, point1...], [day1 ...]]}, "high": {...}}
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pyarrow.parquet as pq
import shapely

from common import RAW, WORK

API = "https://api.open-meteo.com/v1/forecast"
STEP_DEG = (0.075, 0.105)  # lat, lon: about 8 km
LEVELS = {"low": 1000, "high": 2500}
DAILY = ["weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_sum",
         "precipitation_probability_max", "snowfall_sum", "wind_speed_10m_max", "wind_gusts_10m_max",
         "sunshine_duration"]
UA = {"User-Agent": "SentieriAutunno/0.1 (hiking prototype; github.com/Alicepacc/Idea-web-app-trekking)"}


def weekend(today=None):
    """The coming Saturday and Sunday (today and tomorrow when it is already the weekend)."""
    today = today or date.today()
    if today.weekday() == 6:
        return [today]
    sat = today + timedelta(days=(5 - today.weekday()) % 7)
    return [sat, sat + timedelta(days=1)]


def grid_points():
    region = shapely.from_wkb(pq.read_table(RAW / "boundary.parquet").column("geometry")[0].as_py())
    zone = region.buffer(0.05)
    shapely.prepare(zone)
    lon0, lat0, lon1, lat1 = zone.bounds
    pts = [(round(lon, 3), round(lat, 3))
           for lat in np.arange(lat0, lat1, STEP_DEG[0]) for lon in np.arange(lon0, lon1, STEP_DEG[1])
           if zone.contains(shapely.Point(lon, lat))]
    return pts


def fetch(points, elevation, days, batch_size=20):
    """Open-Meteo counts each location as one call (600 per minute on the free API), so go slowly."""
    out = []
    for i in range(0, len(points), batch_size):
        batch = points[i:i + batch_size]
        q = {
            "latitude": ",".join(str(p[1]) for p in batch), "longitude": ",".join(str(p[0]) for p in batch),
            "elevation": ",".join([str(elevation)] * len(batch)), "daily": ",".join(DAILY),
            "hourly": "freezing_level_height", "timezone": "Europe/Rome",
            "start_date": days[0].isoformat(), "end_date": days[-1].isoformat(),
        }
        url = f"{API}?{urllib.parse.urlencode(q)}"
        for attempt in range(6):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                    data = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == 5:
                    raise
                time.sleep(65)  # minute quota used up
            except OSError:
                if attempt == 5:
                    raise
                time.sleep(2 ** attempt)
        out += data if isinstance(data, list) else [data]
        time.sleep(2.5)
    return out


def main():
    days = weekend()
    points = grid_points()
    doc = {"days": [d.isoformat() for d in days], "points": points,
           "issued": datetime.now(timezone.utc).isoformat(timespec="minutes")}
    for level, ele in LEVELS.items():
        res = fetch(points, ele, days)
        vals = {}
        for var in DAILY:
            vals[var] = [[r["daily"][var][k] for r in res] for k in range(len(days))]
        # zero termico: mean freezing level between 9 and 17
        fl = []
        for k, d in enumerate(days):
            row = []
            for r in res:
                hours = [i for i, t in enumerate(r["hourly"]["time"]) if t.startswith(d.isoformat()) and 9 <= int(t[11:13]) <= 17]
                v = [r["hourly"]["freezing_level_height"][i] for i in hours if r["hourly"]["freezing_level_height"][i] is not None]
                row.append(round(sum(v) / len(v)) if v else None)
            fl.append(row)
        vals["freezing_level"] = fl
        doc[level] = vals
    (WORK / "weather.json").write_text(json.dumps(doc, separators=(",", ":")))
    print(f"weather.json: {len(points)} points, days {doc['days']}")


if __name__ == "__main__":
    main()
