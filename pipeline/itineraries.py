"""Step 8 - real hikes: loops and out-and-back walks from a trailhead to a goal.

The signed OSM routes are stretches between two signposts; a hike starts from
a car park, a cable car or a bus stop, reaches a goal and comes back. So:

1. Walkable network: every Overture segment a hiker may use, split at its
   connectors (junctions). Each edge gets its length, climb and descent from
   the DEM, and what lies along it: trees in colour, open views, snow.
2. Walking cost per direction: 4 km/h on the flat, 300 m/h up, 500 m/h down
   (the DIN 33466 rates), times a preference for signed trails over tracks
   and over asphalt roads.
3. Trailheads: car parks and bus stops next to the trail network, merged when
   closer than 500 m. Cable car stations are left out: their seasonal opening
   is unknown, and in late autumn most lifts are closed. Goals: huts, alpine pastures,
   bivouacs, named peaks, lakes and viewpoints.
4. For each trailhead, the cheapest way to each goal within reach. The way
   back avoids the way out (its edges cost five times more): when the two
   overlap little, and the loop is not much longer nor more on asphalt than
   going back the same way, the hike is a loop; otherwise it is out-and-back.
5. Each hike is scored like the routes (foliage, panorama, lakes, huts); the
   best few per trailhead are kept and near-duplicates removed.

Writes data/work/itineraries.parquet.
"""
import multiprocessing as mp
import re
from collections import defaultdict

import numpy as np
import pyarrow as pa
import rasterio
import pyarrow.parquet as pq
import shapely
import shapely.ops
from pyproj import Transformer
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from common import RAW, UTM, WORK
from score import OPEN_DEG, Raster, clip01, walking_hours

STEP = 25.0
# Walking preference: what a hiker would rather walk on. Missing classes are not walkable.
CLASS_FACTOR = {
    "path": 1.0, "footway": 1.0, "steps": 1.05, "bridleway": 1.0, "track": 1.08, "unknown": 1.2,
    "cycleway": 1.2, "pedestrian": 1.25, "living_street": 1.3, "service": 1.35, "unclassified": 1.35,
    "residential": 1.45, "tertiary": 1.9, "secondary": 2.8, "primary": 4.5,
}
ROAD = {"service", "unclassified", "residential", "tertiary", "secondary", "primary", "living_street", "pedestrian"}
SIGNED_BONUS = 0.85
MIN_HOURS, MAX_HOURS = 0.6, 3.6       # one-way walking cost to a goal
BACK_PENALTY = 5.0                    # cost multiplier on the way out, when searching the way back
LOOP_MAX_OVERLAP = 0.3
LOOP_MAX_TIME = 1.5                   # loop time / out-and-back time
LOOP_MAX_EXTRA_ROAD = 0.1             # extra share of asphalt a loop may add
GOALS_PER_TRAILHEAD = 6
KEEP_PER_TRAILHEAD = 2
MIN_INSIDE = 0.7                      # share of a hike inside the region
GOAL_WEIGHT = {"rifugio": 1.0, "peak": 1.0, "lake": 1.0, "malga": 0.85, "bivacco": 0.75, "viewpoint": 0.7}
DIFF_ORDER = {"T": 1, "E": 2, "EE": 3, "EEA": 4}

to_utm = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)


def project(geoms):
    return shapely.transform(geoms, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])))


def norm_diff(c):
    c = (c or "").upper()
    for key in ("EEA", "EAI", "EE", "E", "T"):
        if c.startswith(key):
            return "EEA" if key == "EAI" else key
    return None


# --- 1. network --------------------------------------------------------------------------------

def signed_ways():
    """OSM way id -> (refs, hardest CAI difficulty) for ways on signed hiking routes."""
    info = defaultdict(lambda: [set(), 0])
    for r in pq.read_table(RAW / "osm_routes.parquet").to_pylist():
        tags = dict(r["tags"])
        ref = tags.get("ref")
        d = DIFF_ORDER.get(norm_diff(tags.get("cai_scale")), 0)
        for m in r["members"] or []:
            if m["type"] == "way":
                rec = info[m["ref"]]
                if ref and tags.get("network") in ("lwn", "rwn", None):
                    rec[0].add(ref)
                rec[1] = max(rec[1], d)
    return info


def build_network():
    seg = pq.read_table(RAW / "segments.parquet", columns=["class", "osm_ways", "connectors", "geometry"]).to_pydict()
    signed = signed_ways()
    keep = [i for i, (cls, conns) in enumerate(zip(seg["class"], seg["connectors"]))
            if cls in CLASS_FACTOR and conns and len(conns) >= 2]
    lines = project(shapely.from_wkb([seg["geometry"][i] for i in keep]))
    node_id = {}
    edges_geom, rows = [], []
    for i, line in zip(keep, lines):
        cls, ways = seg["class"][i], seg["osm_ways"][i]
        factor = CLASS_FACTOR[cls]
        conns = sorted(seg["connectors"][i], key=lambda c: c["at"])
        for a, b in zip(conns[:-1], conns[1:]):
            if b["at"] <= a["at"]:
                continue
            part = line if (a["at"] == 0 and b["at"] == 1) else shapely.ops.substring(line, a["at"], b["at"], normalized=True)
            if part.length < 1:
                continue
            # OSM ways covering this stretch decide whether it is a signed trail
            refs, diff = set(), 0
            for w in ways or []:
                overlap = min(w["end"], b["at"]) - max(w["start"], a["at"])
                if overlap > 0.5 * (b["at"] - a["at"]) and w["way"] in signed:
                    refs |= signed[w["way"]][0]
                    diff = max(diff, signed[w["way"]][1])
            u = node_id.setdefault(a["connector_id"], len(node_id))
            v = node_id.setdefault(b["connector_id"], len(node_id))
            edges_geom.append(part)
            rows.append((u, v, factor, cls in ROAD, bool(refs) or diff > 0, diff, ";".join(sorted(refs))))
    u, v, factor, road, marked, diff, refs = zip(*rows)
    return dict(u=np.array(u), v=np.array(v), factor=np.array(factor), road=np.array(road), marked=np.array(marked),
                diff=np.array(diff, np.int8), refs=np.array(refs, object), geom=np.array(edges_geom, object),
                n_nodes=len(node_id))


def edge_samples(net):
    """Sample every edge every ~25 m: climb, descent and what is along it."""
    geoms = shapely.segmentize(net["geom"], STEP)
    xy, idx = shapely.get_coordinates(geoms, return_index=True)
    dem = Raster(WORK / "dem20.tif")
    fol = Raster(WORK / "foliage.tif")
    meta = Raster(WORK / "s2_now_meta.tif")
    z = dem.bilinear(xy)
    # light smoothing along each edge removes DEM noise that would inflate the climb
    same_prev = np.r_[False, idx[1:] == idx[:-1]]
    same_next = np.r_[idx[:-1] == idx[1:], False]
    zp = np.where(same_prev, np.r_[z[:1], z[:-1]], z)
    zn = np.where(same_next, np.r_[z[1:], z[-1:]], z)
    zs = (zp + z + zn) / 3
    dz = np.where(same_prev, zs - np.r_[zs[:1], zs[:-1]], 0)
    n = len(net["geom"])
    up = np.bincount(idx, weights=np.clip(dz, 0, None), minlength=n)
    down = np.bincount(idx, weights=np.clip(-dz, 0, None), minlength=n)
    dens = fol.nearest(xy, 1).astype(np.float32) / 200
    seen = fol.nearest(xy, 3) > 0
    snow = meta.nearest(xy, 2) == 11
    with rasterio.open(WORK / "horizon.tif") as ds:
        hor = ds.read()
        pano = Raster.__new__(Raster)
        pano.t = ds.transform
    pano.data = np.mean(hor < OPEN_DEG, axis=0)[None].astype(np.float32)
    del hor
    opn = pano.nearest(xy, 0)
    count = np.bincount(idx, minlength=n)
    net.update(
        length=shapely.length(net["geom"]), up=up, down=down, zmax=np.full(n, -1e9),
        n_pts=count, n_seen=np.bincount(idx, weights=seen, minlength=n),
        n_col=np.bincount(idx, weights=seen & (dens >= 0.25), minlength=n),
        sum_dens=np.bincount(idx, weights=np.where(seen, dens, 0), minlength=n),
        n_snow=np.bincount(idx, weights=snow, minlength=n),
        n_open=np.bincount(idx, weights=opn >= 0.5, minlength=n),
    )
    np.maximum.at(net["zmax"], idx, z)
    best = np.full(n, -1.0)
    np.maximum.at(best, idx, opn)
    net["open_max"] = best


def cost_graph(net):
    """Directed graph in walking hours times preference; parallel edges keep the cheapest."""
    km = net["length"] / 1000
    f = net["factor"] * np.where(net["marked"], SIGNED_BONUS, 1.0)
    fwd = (km / 4 + net["up"] / 300 + net["down"] / 500) * f
    bwd = (km / 4 + net["down"] / 300 + net["up"] / 500) * f
    src = np.r_[net["u"], net["v"]]
    dst = np.r_[net["v"], net["u"]]
    w = np.r_[fwd, bwd]
    eid = np.r_[np.arange(len(km)), np.arange(len(km))]
    order = np.lexsort((w, dst, src))
    src, dst, w, eid = src[order], dst[order], w[order], eid[order]
    first = np.r_[True, (src[1:] != src[:-1]) | (dst[1:] != dst[:-1])]
    src, dst, w, eid = src[first], dst[first], np.maximum(w[first], 1e-6), eid[first]
    n = net["n_nodes"]
    g = csr_matrix((w, (src, dst)), shape=(n, n))
    # csr keeps (src, dst) sorted, so data position i is pair i
    return g, eid, src, dst


# --- 2. trailheads and goals --------------------------------------------------------------------

def node_xy(net):
    xy = np.zeros((net["n_nodes"], 2))
    starts = shapely.get_coordinates(shapely.get_point(net["geom"], 0))
    ends = shapely.get_coordinates(shapely.get_point(net["geom"], -1))
    xy[net["u"]] = starts
    xy[net["v"]] = ends
    return xy


def display_name(name):
    """Pick the Italian form of bilingual South Tyrolean names ("Meran - Merano" -> "Merano")."""
    if not name or " - " not in name:
        return name
    parts = [p.strip() for p in name.split(" - ")]
    plain = [p for p in parts if not re.search(r"[äöüßëÄÖÜ]", p)] or parts
    vowel = [p for p in plain if p[-1:].lower() in "aeiou"]
    return (vowel or plain)[-1]


def places(net, xy, tree):
    pois = pq.read_table(WORK / "pois.parquet").to_pylist()
    for p in pois:
        p["geometry"] = shapely.from_wkb(p["geometry"])
    loc = pq.read_table(RAW / "localities.parquet").to_pylist()
    loc_xy = np.array([to_utm.transform(*shapely.get_coordinates(shapely.from_wkb(r["geometry"]))[0]) for r in loc])
    loc_tree = cKDTree(loc_xy)

    def snap(pt, radius):
        d, i = tree.query(pt, distance_upper_bound=radius)
        return None if np.isinf(d) else int(i)

    named = [p for p in pois if p["kind"] in ("rifugio", "malga", "bivacco", "peak", "saddle") and p["name"]]
    named_xy = np.array([[p["geometry"].x, p["geometry"].y] for p in named])
    named_tree = cKDTree(named_xy)

    # a trailhead must be close to the signed trail network, not just to any road
    mid = shapely.get_coordinates(shapely.line_interpolate_point(net["geom"][net["marked"]], 0.5, normalized=True))
    marked_tree = cKDTree(mid)
    heads, priority = [], {"parking": 1, "bus": 2}
    for p in pois:
        if p["kind"] not in priority:
            continue
        pt = (p["geometry"].x, p["geometry"].y)
        node = snap(pt, 150)
        if node is None or marked_tree.query(pt)[0] > 600:
            continue
        name = p["name"]
        if not name:
            d, i = named_tree.query(pt)
            if d < 400:
                name = named[i]["name"]
            else:
                d, i = loc_tree.query(pt)
                name = loc[i]["name"] if d < 6000 else None
        name = display_name(name)
        if p["kind"] == "parking" and name:
            name = re.sub(r"^(Parcheggio|Parkplatz|P\.?)\s+", "", name, flags=re.I)
        heads.append(dict(kind=p["kind"], name=name, node=node, xy=pt, named=bool(p["name"])))
    heads.sort(key=lambda h: (priority[h["kind"]], not h["named"], h["name"] is None))
    kept, kept_xy = [], []
    for h in heads:
        if kept_xy and np.min(np.hypot(*(np.array(kept_xy) - h["xy"]).T)) < 500:
            continue
        kept.append(h)
        kept_xy.append(h["xy"])

    goals = []
    for i, p in enumerate(pois):
        kind = p["kind"]
        if kind not in GOAL_WEIGHT:
            continue
        g = p["geometry"]
        if kind == "lake":
            if not p["name"] and (p["area"] or 0) < 10000:
                continue
            near = tree.query_ball_point((g.centroid.x, g.centroid.y), r=np.sqrt(p["area"] / np.pi) + 150)
            near = [n for n in near if shapely.distance(g, shapely.Point(xy[n])) <= 150]
            if not near:
                continue
            node = min(near, key=lambda n: shapely.distance(g, shapely.Point(xy[n])))
        else:
            if not p["name"]:
                continue
            node = snap((g.x, g.y), 200 if kind == "peak" else 120)
            if node is None:
                continue
        goals.append(dict(kind=kind, name=p["name"], node=node, poi=i, ele=p.get("ele")))
    return kept, goals, pois


# --- 3. hikes ------------------------------------------------------------------------------------

G = {}  # shared with worker processes (fork)


def path_edges(pred, src_node, dst_node, pos_of):
    """Edges (as data positions) of the predecessor path src -> dst."""
    out, n = [], dst_node
    while n != src_node:
        p = pred[n]
        if p < 0:
            return None
        out.append(pos_of(p, n))
        n = p
    return out[::-1]


def hikes_from(h_index):
    g, eid, net, goals_at = G["g"], G["eid"], G["net"], G["goals_at"]
    head = G["heads"][h_index]
    s = head["node"]
    dist, pred = dijkstra(g, directed=True, indices=s, return_predecessors=True, limit=MAX_HOURS)
    indptr, indices = g.indptr, g.indices

    def pos_of(a, b):
        row = slice(indptr[a], indptr[a + 1])
        return indptr[a] + int(np.flatnonzero(indices[row] == b)[0])

    cands = []
    for node, goal_list in goals_at.items():
        d = dist[node]
        if MIN_HOURS <= d <= MAX_HOURS:
            for goal in goal_list:
                # prefer goals that make a half-day to day hike
                fit = 1 - abs(d - 1.8) / 3
                cands.append((GOAL_WEIGHT[goal["kind"]] * fit, node, goal))
    cands.sort(key=lambda c: -c[0])
    out, seen_goals = [], set()
    for _, node, goal in cands:
        if len(out) >= GOALS_PER_TRAILHEAD:
            break
        key = (goal["kind"], goal["name"] or goal["poi"])
        if key in seen_goals:
            continue
        seen_goals.add(key)
        go = path_edges(pred, s, node, pos_of)
        if not go:
            continue
        # the way back avoids the way out
        data = g.data.copy()
        go_e = eid[go]
        back_pos_penalty = np.isin(eid, go_e)
        data[back_pos_penalty] *= BACK_PENALTY
        g2 = csr_matrix((data, indices, indptr), shape=g.shape)
        dist2, pred2 = dijkstra(g2, directed=True, indices=node, return_predecessors=True, limit=dist[node] * 2.2 + 0.3)
        back = path_edges(pred2, node, s, pos_of) if np.isfinite(dist2[s]) else None
        if back:
            go_len = net["length"][go_e].sum()
            shared = net["length"][np.intersect1d(go_e, eid[back])].sum()
            if shared / go_len > LOOP_MAX_OVERLAP:
                back = None
        reverse = [pos_of(G["dst"][p], G["src"][p]) for p in go[::-1]]
        out.append(dict(head=h_index, goal=goal, go=go, loop_back=back, reverse=reverse))
    return out


def summarize(h, net, eid, src, dst):
    """Metrics of a hike from its directed edges."""
    pos = np.array(h["go"] + h["back"])
    e = eid[pos]
    forward = net["u"][e] == src[pos]
    length = net["length"][e]
    up = np.where(forward, net["up"][e], net["down"][e])
    down = np.where(forward, net["down"][e], net["up"][e])
    km = length.sum() / 1000
    n_pts, n_seen = net["n_pts"][e].sum(), net["n_seen"][e].sum()
    seen_share = n_seen / max(n_pts, 1)
    f_share = net["n_col"][e].sum() / max(n_seen, 1)
    f_mean = net["sum_dens"][e].sum() / max(n_seen, 1)
    foliage = 100 * clip01(0.55 * f_share / 0.75 + 0.45 * f_mean / 0.45) * min(1.0, seen_share / 0.6)
    best_open = net["open_max"][e].max()
    open_share = net["n_open"][e].sum() / max(n_pts, 1)
    panorama = 100 * clip01(0.65 * best_open / 0.75 + 0.35 * open_share / 0.4)
    diff = int(net["diff"][e].max())
    refs = sorted({r for rs in net["refs"][e] for r in rs.split(";") if r}, key=lambda r: (len(r), r))
    return dict(km=km, up=float(up.sum()), down=float(down.sum()), hours=walking_hours(km, up.sum(), down.sum()),
                ele_max=float(net["zmax"][e].max()), foliage=foliage, f_share=f_share, seen=seen_share,
                snow=net["n_snow"][e].sum() / max(n_pts, 1), panorama=panorama, open_best=best_open,
                road=float(length[net["road"][e]].sum() / max(length.sum(), 1)),
                marked=float(length[net["marked"][e]].sum() / max(length.sum(), 1)),
                diff={v: k for k, v in DIFF_ORDER.items()}.get(diff), refs=refs[:8], edges=e, forward=forward)


def geometry(h, net):
    """The hike as one line in walking order (UTM)."""
    coords = []
    for e, fwd in zip(h["edges"], h["forward"]):
        c = shapely.get_coordinates(net["geom"][e])
        coords.append(c if fwd else c[::-1])
    return shapely.LineString(np.concatenate(coords))


def main():
    net = build_network()
    edge_samples(net)
    print(f"network: {len(net['geom'])} edges, {net['n_nodes']} junctions, {net['length'].sum() / 1000:.0f} km, "
          f"{net['length'][net['marked']].sum() / 1000:.0f} km signed")
    g, eid, src, dst = cost_graph(net)
    xy = node_xy(net)
    tree = cKDTree(xy)
    heads, goals, pois = places(net, xy, tree)
    goals_at = defaultdict(list)
    for gl in goals:
        goals_at[gl["node"]].append(gl)
    print(f"{len(heads)} trailheads, {len(goals)} goals")

    G.update(g=g, eid=eid, net=net, goals_at=goals_at, heads=heads, src=src, dst=dst)
    with mp.get_context("fork").Pool(4) as pool:
        raw = [h for hs in pool.imap_unordered(hikes_from, range(len(heads)), chunksize=8) for h in hs]
    print(f"{len(raw)} candidate hikes")

    region = project(shapely.from_wkb(pq.read_table(RAW / "boundary.parquet").column("geometry")[0].as_py()))
    shapely.prepare(region)
    poi_geom = np.array([p["geometry"] for p in pois])
    poi_kind = np.array([p["kind"] for p in pois])
    poi_tree = shapely.STRtree(poi_geom)
    rows = []
    for h in raw:
        m = summarize(dict(go=h["go"], back=h["reverse"]), net, eid, src, dst)
        h["kind"] = "outback"
        if h["loop_back"]:
            ml = summarize(dict(go=h["go"], back=h["loop_back"]), net, eid, src, dst)
            if ml["hours"] <= LOOP_MAX_TIME * m["hours"] and ml["road"] <= max(0.25, m["road"] + LOOP_MAX_EXTRA_ROAD):
                m, h["kind"] = ml, "loop"
        if m["km"] < 2 or m["hours"] > 9:
            continue
        line = geometry(m, net)
        # share inside the region from points along the way (out-and-back lines overlap themselves)
        pts = shapely.get_coordinates(shapely.line_interpolate_point(line, np.arange(0, line.length, 100)))
        if np.mean(shapely.contains_xy(region, pts[:, 0], pts[:, 1])) < MIN_INSIDE:
            continue  # mostly outside Trentino-Alto Adige
        near = defaultdict(list)
        for kind, dist in (("lake", 150), ("rifugio", 100), ("malga", 100), ("bivacco", 100), ("peak", 60)):
            idx = [i for i in poi_tree.query(line, predicate="dwithin", distance=dist) if poi_kind[i] == kind]
            near[kind] = idx
        lakes = sorted({pois[i]["name"] for i in near["lake"] if pois[i]["name"]})
        n_lakes = len({pois[i]["name"] or i for i in near["lake"]})
        big = any((pois[i]["area"] or 0) >= 50000 for i in near["lake"])
        lake_score = 0.0 if not n_lakes else min(100.0, 50 + 15 * (n_lakes - 1) + 15 * bool(lakes) + 15 * big)
        huts = {k: sorted({pois[i]["name"] for i in near[k] if pois[i]["name"]}) for k in ("rifugio", "malga", "bivacco")}
        hut_score = 100.0 if huts["rifugio"] else 75.0 if huts["malga"] else 55.0 if huts["bivacco"] else 0.0
        peaks = sorted({(pois[i]["name"], pois[i].get("ele")) for i in near["peak"] if pois[i]["name"]},
                       key=lambda p: -(p[1] or 0))
        head, goal = heads[h["head"]], h["goal"]
        quality = (m["foliage"] + m["panorama"] + lake_score + hut_score) / 4 * (1 - 0.5 * m["road"])
        rows.append(dict(
            head=h["head"], head_kind=head["kind"], head_name=head["name"], goal_kind=goal["kind"],
            goal_name=goal["name"], goal_ele=goal["ele"], goal_poi=goal["poi"], kind=h["kind"],
            km=round(m["km"], 2), ascent=round(m["up"]), descent=round(m["down"]), hours=round(m["hours"], 2),
            ele_max=round(m["ele_max"]), difficulty=m["diff"], refs=m["refs"], road_share=round(m["road"], 3),
            marked_share=round(m["marked"], 3), foliage=round(m["foliage"]), foliage_share=round(float(m["f_share"]), 3),
            seen_share=round(float(m["seen"]), 3), snow_share=round(float(m["snow"]), 3),
            panorama=round(m["panorama"]), open_best=round(float(m["open_best"]), 3), lakes=round(lake_score),
            lake_names=lakes, n_lakes=n_lakes, huts=round(hut_score), rifugi=huts["rifugio"], malghe=huts["malga"],
            bivacchi=huts["bivacco"], peaks=[p[0] for p in peaks], peak_ele=[p[1] for p in peaks],
            poi_ids=sorted({int(i) for v in near.values() for i in v}), quality=round(quality, 1),
            edges=m["edges"].tolist(), geometry=shapely.to_wkb(shapely.simplify(line, 2.0)),
        ))
    print(f"{len(rows)} hikes after length limits")

    # Per trailhead: the best overall and the best for each criterion; then drop
    # near-duplicates (same goal, mostly the same path).
    by_head = defaultdict(list)
    for r in rows:
        by_head[r["head"]].append(r)
    kept = []
    for hs in by_head.values():
        pick = sorted(hs, key=lambda r: -r["quality"])[:KEEP_PER_TRAILHEAD]
        for crit in ("foliage", "panorama", "lakes", "huts"):
            best = max(hs, key=lambda r: (r[crit], r["quality"]))
            if best[crit] > 0:
                pick.append(best)
        kept += list({id(r): r for r in pick}.values())
    kept.sort(key=lambda r: -r["quality"])
    final, by_goal = [], defaultdict(list)
    for r in kept:
        es = set(r["edges"])
        key = (r["goal_kind"], r["goal_name"] or r["goal_poi"])
        if any(len(es & o) > 0.6 * min(len(es), len(o)) for o in by_goal[key]):
            continue
        by_goal[key].append(es)
        final.append(r)
    for i, r in enumerate(final):
        r["id"] = i + 1
        del r["edges"]
    tb = pa.Table.from_pylist(final)
    pq.write_table(tb, WORK / "itineraries.parquet", compression="zstd")
    loops = sum(r["kind"] == "loop" for r in final)
    print(f"itineraries.parquet: {len(final)} hikes ({loops} loops), from {len(by_head)} trailheads")


if __name__ == "__main__":
    main()
