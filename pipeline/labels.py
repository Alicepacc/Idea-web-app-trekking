"""Place names for the map: towns, peaks, passes, huts, lakes, glaciers, valleys, mountain groups.

Each label gets the zoom from which it is worth showing. MapLibre hides labels
that would overlap and keeps the more important ones, so the order matters:
importance comes from the data (settlement class and population, a peak's
distance to the nearest higher summit, the area of lakes and glaciers, the
length of valleys, whether a pass carries a road).

Called by export_web.py, which knows the points along the hikes. Writes
web/data/labels.json, most important first:
  {"p": [[kind, name, lon, lat, minzoom, ele, poi], ...],  points (poi: index in pois.json, or null)
   "l": [[kind, name, minzoom, polyline], ...]}           valleys, written along the valley floor
"""
import json
import re
import unicodedata

import numpy as np
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer
from scipy.spatial import cKDTree

from common import BBOX, RAW, UTM, WEB_DATA, display_name, encode_polyline
from score import project

to_wgs = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)

# tie-break between kinds shown from the same zoom: the first wins a crowded spot
KIND_ORDER = ["city", "range", "town", "peak", "lake", "pass", "valley", "rifugio", "village",
              "glacier", "bivacco", "malga", "hamlet"]
MAJOR_ROADS = ["primary", "secondary", "tertiary", "trunk"]
PASS_WORD = re.compile(r"^(passo|joch|giogo|sella|colle|pass\b)", re.I)
NOTCH_WORD = re.compile(r"^(forcell|forcol|bocc|scharte|furcia|forc)", re.I)
NOT_A_HUT = re.compile(r"hotel|albergo|gasthof|garni|pension|residence|chalet|apartment", re.I)
# words that say what a group is, not which one: they do not make two groups the same
GENERIC = {"gruppo", "gruppe", "dolomiti", "dolomiten", "monti", "monte", "catena", "massiccio", "prealpi",
           "altopiano", "alpi", "cima", "della", "delle", "dello", "grupa"}
LOWER = {"di", "d", "del", "della", "delle", "dei", "degli", "de", "da", "e", "la", "le", "il", "lo", "al", "alla"}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return set(re.findall(r"[a-z]{4,}", s)) - GENERIC


def title_case(s):
    """SAT writes names in capitals: "MONTI D'ANAUNIA" -> "Monti d'Anaunia", 'RIFUGIO "LANCIA"' -> 'Rifugio "Lancia"'."""
    def cap(p):
        m = re.search(r"[^\W\d_]", p)
        return p[:m.start()] + p[m.start()].upper() + p[m.start() + 1:] if m else p

    def word(w, first):
        low = w.lower()
        if not first and low.split("'")[0] in LOWER:
            head, _, rest = low.partition("'")
            return head + ("'" + cap(rest) if rest else "")
        return "'".join(cap(p) for p in low.split("'"))
    words = re.split(r"(\s+|-)", s.strip())
    return "".join(w if re.fullmatch(r"\s+|-", w) else word(w, i == 0) for i, w in enumerate(words))


def clean(name):
    """Display name without OSM's working notes ("Fedaia (bis)")."""
    return re.sub(r"\s*\((bis|ter|alt|old|vecchio)\)$", "", display_name(name) or "", flags=re.I)


def lonlat(g):
    lon, lat = to_wgs.transform(g.x, g.y)
    return round(lon, 5), round(lat, 5)


def peak_zoom(iso, ele):
    if iso >= 15000 and ele >= 2000:
        return 7.5
    for dist, z in ((8000, 8.5), (4000, 9.5), (2000, 10.5), (800, 11.5)):
        if iso >= dist:
            return z
    return 12.5


def isolation(xy, ele):
    """Distance to the nearest higher peak (50 km when there is none nearby)."""
    tree = cKDTree(xy)
    out = np.full(len(xy), 50000.0)
    for k in range(len(xy)):
        for r in (2000, 6000, 20000, 50000):
            near = [j for j in tree.query_ball_point(xy[k], r) if ele[j] > ele[k] + 0.5]
            if near:
                out[k] = min(np.hypot(*(xy[j] - xy[k])) for j in near)
                break
    return out


def area_zoom(area, steps):
    for a, z in steps:
        if area >= a:
            return z
    return steps[-1][1] + 1


def build(pois, poi_index, dem):
    region = project(shapely.from_wkb(pq.read_table(RAW / "boundary.parquet").column("geometry")[0].as_py()))
    zone = region.buffer(2000)
    shapely.prepare(zone)
    points, lines = [], []

    def add(kind, name, g, z, ele=None, poi=None, weight=0.0):
        points.append(dict(kind=kind, name=name, g=g, z=z, ele=ele, poi=poi, weight=weight))

    # --- peaks, passes, huts, lakes (the points the hikes use) ---
    geo = [shapely.from_wkb(p["geometry"]) for p in pois]
    ele = [p.get("ele") for p in pois]
    missing = [i for i, p in enumerate(pois) if p["kind"] in ("peak", "saddle") and not ele[i]]
    if missing:
        for i, z in zip(missing, dem.bilinear(np.array([[geo[i].x, geo[i].y] for i in missing]))):
            ele[i] = float(z)
    peaks = [i for i, p in enumerate(pois) if p["kind"] == "peak" and p["name"]]
    xy = np.array([[geo[i].x, geo[i].y] for i in peaks])
    pe = np.array([ele[i] for i in peaks])
    for i, iso in zip(peaks, isolation(xy, pe)):
        add("peak", clean(pois[i]["name"]), geo[i], peak_zoom(iso, ele[i]), round(ele[i]), poi_index.get(i), ele[i])

    roads = pq.read_table(RAW / "segments.parquet", columns=["class", "geometry"],
                          filters=[("class", "in", MAJOR_ROADS)])
    road_tree = shapely.STRtree(project(shapely.from_wkb(roads.column("geometry").to_pylist())))
    for i, p in enumerate(pois):
        if p["kind"] != "saddle" or not p["name"]:
            continue
        name = clean(p["name"])
        on_road = len(road_tree.query(geo[i], predicate="dwithin", distance=80)) > 0
        z = 9 if on_road else 10.5 if PASS_WORD.match(name) else 11.5 if NOTCH_WORD.match(name) else 12
        add("pass", name, geo[i], z, round(ele[i]), poi_index.get(i), ele[i])

    # huts on a hike come half a zoom earlier; the others (many are ski-run bars) wait for the close-up
    hut_zoom = {"rifugio": 11, "bivacco": 11.5, "malga": 12}
    for i, p in enumerate(pois):
        if p["kind"] in hut_zoom and p["name"] and not NOT_A_HUT.search(p["name"]):
            add(p["kind"], clean(p["name"]), geo[i], hut_zoom[p["kind"]] - (0.5 if i in poi_index else 0),
                round(ele[i]) if ele[i] else None, poi_index.get(i))
        elif p["kind"] == "lake" and p["name"]:
            area = p.get("area") or geo[i].area
            z = area_zoom(area, [(5e6, 8), (1e6, 9), (1e5, 10.5), (2e4, 11.5), (0, 12.5)])
            add("lake", clean(p["name"]), shapely.point_on_surface(geo[i]), z - (0.5 if i in poi_index else 0),
                None, poi_index.get(i), area)

    # --- towns, villages, hamlets ---
    loc = pq.read_table(RAW / "localities.parquet").to_pylist()
    lon0, lat0, lon1, lat1 = BBOX
    for r in loc:
        g0 = shapely.from_wkb(r["geometry"])
        if not r["name"] or not (lon0 <= g0.x <= lon1 and lat0 <= g0.y <= lat1):
            continue
        g = project(g0)
        cls, pop = r.get("class"), r["population"] or 0
        inside = zone.contains(g)
        if cls == "city":
            z = 7 if inside else 8
        elif cls == "town" or pop >= 5000:
            z = (7.5 if pop >= 15000 else 8) + (0 if inside else 1)
        elif not inside:
            continue  # outside the region only the towns, for orientation
        elif pop >= 1000:
            z = 9.5
        elif cls == "village" or pop:
            z = 10.5
        else:
            z = 12
        kind = "city" if cls == "city" else "town" if z <= 9 else "village" if z <= 10.5 else "hamlet"
        add(kind, clean(r["name"]), g, z, weight=pop)

    # --- glaciers and valleys ---
    for r in pq.read_table(RAW / "landforms.parquet").to_pylist():
        g = project(shapely.from_wkb(r["geometry"]))
        if not r["name"] or not zone.intersects(g):
            continue
        name = clean(r["name"])
        if r["class"] == "glacier" and g.area:
            add("glacier", name, shapely.point_on_surface(g), area_zoom(g.area, [(3e6, 10), (5e5, 11), (0, 12)]),
                weight=g.area)
        elif r["class"] == "valley" and g.geom_type == "LineString":
            z = area_zoom(g.length, [(12000, 9), (6000, 10), (3000, 11), (0, 12)])
            line = shapely.simplify(g, 25)
            lon, lat = to_wgs.transform(*shapely.get_coordinates(line).T)
            lines.append(dict(kind="valley", name=name, z=z, coords=list(zip(lon, lat)), weight=g.length))
        elif r["class"] == "valley":
            add("valley", name, shapely.point_on_surface(g), 11.5)

    # --- mountain groups: SAT's groups in Trentino, Overture's ranges elsewhere ---
    groups = mountain_groups()
    for name, g, n in groups:
        add("range", name, g, 8.5 if n >= 40 else 9.5, weight=n)
    for r in pq.read_table(RAW / "landforms.parquet").to_pylist():
        if r["class"] != "mountain_range" or not r["name"]:
            continue
        g = project(shapely.from_wkb(r["geometry"]))
        if not g.area or g.area > 5e9 or not zone.intersects(g):
            continue  # "Alps" and lines
        c = shapely.point_on_surface(g)
        name = clean(r["name"])
        # in Trentino SAT's names ("Lagorai") beat the classification's ("Dolomiti Meridionali di Fiemme")
        if any(g2.distance(c) < 12000 or (norm(name) & norm(n2) and g2.distance(c) < 25000) for n2, g2, _ in groups):
            continue
        add("range", name, c, (8.5 if g.area >= 3e8 else 9.5) + (0 if zone.contains(c) else 1), weight=g.area / 1e6)

    # same name close by (a village and its comune, a pass and the hamlet on it): keep the first
    rank = {k: i for i, k in enumerate(KIND_ORDER)}
    points.sort(key=lambda p: (p["z"], rank[p["kind"]], -p["weight"]))
    kept, seen = [], {}
    for p in points:
        key = (p["name"] or "").lower()
        if any(q.distance(p["g"]) < 2500 for q in seen.get(key, [])):
            continue
        seen.setdefault(key, []).append(p["g"])
        kept.append(p)
    lines.sort(key=lambda l: (l["z"], -l["weight"]))

    doc = {"p": [[p["kind"], p["name"], *lonlat(p["g"]), p["z"], p["ele"], p["poi"]] for p in kept],
           "l": [[l["kind"], l["name"], l["z"], encode_polyline(l["coords"])] for l in lines]}
    path = WEB_DATA / "labels.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
    counts = {}
    for p in kept:
        counts[p["kind"]] = counts.get(p["kind"], 0) + 1
    print(f"labels.json: {len(kept)} places, {len(lines)} valleys, {path.stat().st_size / 1e6:.1f} MB", counts)


def mountain_groups():
    """SAT's mountain groups (Trentino), placed at the middle of their trails: [(name, point, trails)]."""
    import shapefile
    path = RAW / "sat" / "shp" / "sentieri_tratte.shp"
    if not path.exists():
        return []
    r = shapefile.Reader(str(path.with_suffix("")), encoding="latin-1")
    mids = {}
    for rec, shp in zip(r.records(), r.shapes()):
        for grp in (rec["gr_mont"] or "").split(","):
            main = re.sub(r"\s+(sud|nord|est|ovest|centrale)$", "", grp.split(" / ")[0].strip(), flags=re.I)
            if main and shp.points:
                pts = np.array(shp.points)
                mids.setdefault(title_case(main), []).append(pts[len(pts) // 2])
    out = []
    for name, pts in mids.items():
        pts = np.array(pts)
        out.append((name, shapely.Point(np.median(pts[:, 0]), np.median(pts[:, 1])), len(pts)))
    return out
