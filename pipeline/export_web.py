"""Step 14 - export what the web page needs into web/data/ (map tiles come from tiles.py).

  hikes.json  every hike: metrics, scores, weekend weather, photo credits, official sources, geometry;
              plus places, town labels, the SAT trails and suggested hikes they refer to, and data dates
  details/    per hike, loaded when its card opens: profile, panorama, webcams
  pois.json   huts, lakes and peaks along the hikes, for the map
  labels.json place names for the map (labels.py)
  photos/     thumbnails of the goals (written by photos.py)
"""
import json
from datetime import date

import numpy as np
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

import labels
from common import BBOX, RAW, UTM, WEB_DATA, WORK, display_name, encode_polyline
from score import Raster

utm_to_wgs = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
PROFILE_POINTS = 60
DETAIL_CHUNK = 250  # hikes per details file


def profile(line, dem, fol, n=PROFILE_POINTS):
    """Elevation (m) and coloured-tree density (0-9) at n points along the hike."""
    pts = shapely.get_coordinates(shapely.line_interpolate_point(line, np.linspace(0, line.length, n)))
    z = dem.bilinear(pts)
    f = fol.nearest(pts, 1).astype(np.float32) / 200
    return [int(round(v)) for v in z], "".join(str(int(min(9, round(v * 9 / 0.6)))) for v in f)


# Towns labelled on the map, with the name hikers use in Italian.
TOWNS = {
    "Trento": "Trento", "Bolzano - Bozen": "Bolzano", "Meran - Merano": "Merano", "Rovereto": "Rovereto",
    "Brixen - Bressanone": "Bressanone", "Bruneck - Brunico": "Brunico", "Riva del Garda": "Riva del Garda",
    "Pergine Valsugana": "Pergine", "Cles": "Cles", "Borgo Valsugana": "Borgo Valsugana",
    "Sterzing - Vipiteno": "Vipiteno", "Schlanders - Silandro": "Silandro", "Mals - Malles Venosta": "Malles",
    "Cavalese": "Cavalese", "Canazei": "Canazei", "Tione di Trento": "Tione", "Madonna di Campiglio": "Campiglio",
    "Urtijëi - St. Ulrich in Gröden - Ortisei": "Ortisei", "Innichen - San Candido": "San Candido",
    "Fiera di Primiero": "Primiero", "Malè": "Malè", "Moena": "Moena", "Sand in Taufers - Campo Tures": "Campo Tures",
    "Fondo": "Fondo",
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


def pois_json(rows_pois, used):
    """Named huts, lakes and peaks along at least one hike; returns score index -> export index."""
    out, index = [], {}
    for i in sorted(used):
        p = rows_pois[i]
        if not p["name"]:
            continue
        g = shapely.from_wkb(p["geometry"])
        if p["kind"] == "lake":
            g = shapely.point_on_surface(g)
        lon, lat = utm_to_wgs.transform(g.x, g.y)
        index[i] = len(out)
        out.append([p["kind"], display_name(p["name"]), round(lon, 5), round(lat, 5), round(p["ele"]) if p.get("ele") else None])
    (WEB_DATA / "pois.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"pois.json: {len(out)} points")
    return index


class Official:
    """SAT trails and suggested hikes a hike refers to, listed once in hikes.json and referenced by index."""

    def __init__(self, doc):
        self.sat, self.lts = doc.get("sat", []), doc.get("lts", [])
        self.sat_ix, self.lts_ix, self.sat_out, self.lts_out = {}, {}, [], []

    def _sat(self, i):
        if i not in self.sat_ix:
            t = self.sat[i]
            self.sat_ix[i] = len(self.sat_out)
            self.sat_out.append([t["ref"], t["name"], t["start"], t["end"], t["diff"], t["t_go"], t["t_back"], t["maint"]])
        return self.sat_ix[i]

    def _lts(self, i):
        if i not in self.lts_ix:
            h = self.lts[i]
            self.lts_ix[i] = len(self.lts_out)
            self.lts_out.append([h["title"], h["assoc"], h["url"], h["themes"], h["open"], h["changed"]])
        return self.lts_ix[i]

    def of(self, m):
        """[share on SAT trails, [[trail, share of the hike, share of the trail walked], ...], [suggested hikes]]."""
        if not m:
            return None
        titles, lts = set(), []
        for i in m.get("lts", []):
            if self.lts[i]["title"].lower() not in titles:  # the same walk is often listed for summer and winter
                titles.add(self.lts[i]["title"].lower())
                lts.append(self._lts(i))
        return [m.get("sat_share", 0), [[self._sat(t), a, b] for t, a, b in m.get("sat", [])], lts]


class Weather:
    """Weekend forecast at a hike: nearest grid point, temperature read at the hike's top."""

    def __init__(self):
        path = WORK / "weather.json"
        self.doc = json.loads(path.read_text()) if path.exists() else None
        if self.doc:
            self.xy = np.array(self.doc["points"])

    def at(self, lon, lat, ele_top):
        if not self.doc:
            return None
        i = int(np.argmin(np.hypot(self.xy[:, 0] - lon, (self.xy[:, 1] - lat) * 1.45)))
        lo, hi = self.doc["low"], self.doc["high"]
        k_top = np.clip((ele_top - 1000) / 1500, -0.3, 1.3)
        high_up = ele_top > 1800
        days = []
        for d in range(len(self.doc["days"])):
            t_lo, t_hi = lo["temperature_2m_max"][d][i], hi["temperature_2m_max"][d][i]
            tmax = None if t_lo is None or t_hi is None else round(t_lo + k_top * (t_hi - t_lo))
            src = hi if high_up else lo
            sun = lo["sunshine_duration"][d][i]
            days.append([src["weather_code"][d][i], tmax, lo["precipitation_sum"][d][i],
                         lo["precipitation_probability_max"][d][i], src["wind_gusts_10m_max"][d][i],
                         lo["freezing_level"][d][i], round(sun / 3600, 1) if sun is not None else None,
                         src["snowfall_sum"][d][i]])
        return days


def names(xs):
    """Italian forms, without the duplicates bilingual names leave behind."""
    return list(dict.fromkeys(display_name(x) for x in xs))


def main():
    dem = Raster(WORK / "dem20.tif")
    fol = Raster(WORK / "foliage.tif")
    rows = pq.read_table(WORK / "itineraries.parquet").to_pylist()
    pois = pq.read_table(WORK / "pois.parquet").to_pylist()
    load = lambda name: json.loads((WORK / name).read_text()) if (WORK / name).exists() else {}
    photos, webcams, panoramas = load("photos.json"), load("webcams.json"), load("panorama.json")
    official, matches = load("official.json"), load("official_match.json")
    refs = Official(official)
    weather = Weather()
    used = {i for r in rows for i in r["poi_ids"]} | {r["goal_poi"] for r in rows}
    poi_index = pois_json(pois, used)
    labels.build(pois, poi_index, dem)

    out, details = [], {}
    for r in rows:
        line = shapely.from_wkb(r["geometry"])
        xy = shapely.get_coordinates(shapely.simplify(line, 12.0))
        lon, lat = utm_to_wgs.transform(xy[:, 0], xy[:, 1])
        z, f = profile(line, dem, fol)
        goal = shapely.point_on_surface(shapely.from_wkb(pois[r["goal_poi"]]["geometry"]))
        glon, glat = utm_to_wgs.transform(goal.x, goal.y)
        fam = (r["hours"] <= 4.5 and r["ascent"] <= 550 and r["difficulty"] in (None, "T", "E")
               and r["ele_max"] <= 2300 and r["road_share"] <= 0.5)
        ph = photos.get(str(r["goal_poi"]))
        cams = [[*webcams["cams"][c][:2], sees, km] for c, sees, km in webcams.get("hikes", {}).get(str(r["id"]), [])]
        pano = panoramas.get(str(r["id"]))
        out.append({
            "id": r["id"], "kind": r["kind"], "hn": r["head_name"], "hk": r["head_kind"],
            "st": [round(lon[0], 5), round(lat[0], 5)], "gk": r["goal_kind"], "gn": display_name(r["goal_name"]),
            "ge": round(r["goal_ele"]) if r["goal_ele"] else None, "gp": poi_index.get(r["goal_poi"]),
            "km": r["km"], "up": r["ascent"], "down": r["descent"], "h": r["hours"], "emax": r["ele_max"],
            "emin": min(z), "diff": r["difficulty"], "refs": r["refs"], "road": r["road_share"],
            "marked": r["marked_share"], "fam": fam,
            "sc": {"foliage": r["foliage"], "panorama": r["panorama"], "lakes": r["lakes"], "huts": r["huts"]},
            "fs": r["foliage_share"], "seen": r["seen_share"], "snow": r["snow_share"], "open": r["open_best"],
            "lk": names(r["lake_names"]), "nl": r["n_lakes"], "hr": names(r["rifugi"]), "hm": names(r["malghe"]),
            "hb": names(r["bivacchi"]), "pk": [[display_name(n), e] for n, e in zip(r["peaks"], r["peak_ele"])],
            "pi": [poi_index[i] for i in r["poi_ids"] if i in poi_index],
            "ph": [ph.get("pack"), ph.get("offset"), ph.get("length"), ph["author"], ph["license"], ph["page"],
                   ph["path"]] if ph else None,
            "wx": weather.at(glon, glat, r["ele_max"]), "pn": len(pano["pk"]) if pano else 0,
            "ncam": len(cams), "of": refs.of(matches.get(str(r["id"]))), "g": encode_polyline(zip(lon, lat)),
        })
        chunk = (r["id"] - 1) // DETAIL_CHUNK
        details.setdefault(chunk, {})[r["id"]] = {
            "pz": z, "pf": f, "cam": cams,
            "pano": [pano["vp"], pano["sky"], pano["pk"], pano["on"]] if pano else None}
    meta = load("composite_info.json")
    places, towns = places_json()
    doc = {"generated": date.today().isoformat(), "bbox": BBOX, "detail_chunk": DETAIL_CHUNK, **meta,
           "weather_days": weather.doc["days"] if weather.doc else [],
           "weather_issued": weather.doc["issued"] if weather.doc else None,
           "places": places, "towns": towns, "sat": refs.sat_out, "lts": refs.lts_out, "hikes": out}
    (WEB_DATA / "hikes.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
    ddir = WEB_DATA / "details"
    ddir.mkdir(exist_ok=True)
    for old in ddir.glob("*.json"):
        old.unlink()
    for chunk, items in details.items():
        (ddir / f"d{chunk}.json").write_text(json.dumps(items, ensure_ascii=False, separators=(",", ":")))
    print(f"hikes.json: {len(out)} hikes, {(WEB_DATA / 'hikes.json').stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
