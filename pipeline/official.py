"""Step 7b - official trails and suggested hikes, from the bodies that maintain them.

Open data say which walks are the established ones, without copying anybody's
guidebook or blog:

- SAT (Società degli Alpinisti Tridentini) publishes its whole trail network,
  updated monthly, under the Open Database License: for each of its ~1,080
  trails the number, name, start and end localities, official walking times,
  difficulty and the section that maintains it.
- In South Tyrol the tourism associations publish ~3,000 suggested hikes in
  the province's Open Data Hub (LTS data): title, start and arrival point,
  themes (families, pushchairs, irrigation-channel walks...) and whether the
  association marks them open. Records under CC0 only; texts are not used.

  official.py fetch   (before itineraries.py) downloads both: data/work/official.json.
                      The suggested hikes' start and arrival points become trailheads
                      and goals of the hike search.
  official.py match   (after itineraries.py) finds which hikes follow a SAT trail
                      or a suggested hike: data/work/official_match.json
                      {"<hike id>": {"sat": [[trail index, share of the hike, share of the trail walked], ...],
                                     "sat_share": share of the hike on SAT trails, "lts": [suggested hike index, ...]}}
"""
import io
import json
import re
import sys
import time
import unicodedata
import urllib.request
import zipfile
from collections import defaultdict

import numpy as np
import pyarrow.parquet as pq
import shapefile
import shapely
from pyproj import Transformer
from scipy.spatial import cKDTree

from common import RAW, UTM, WORK
from labels import title_case

SAT_ZIP = "https://sentieri.sat.tn.it/download/sentierisat.shp.zip"
SAT_SHEET = "https://sentieri.sat.tn.it/schede-sentieri?numero={}"
ODH = ("https://tourism.api.opendatahub.com/v1/ODHActivityPoi?pagesize=200&pagenumber={}&odhtagfilter=wandern"
       "&fields=Id,Detail.it.Title,Detail.de.Title,GpsPoints,LTSTags,LicenseInfo.License,IsOpen,Active,LastChange,"
       "ContactInfos.it.Url,LocationInfo.TvInfo.Name.it")
UA = {"User-Agent": "SentieriAutunno/0.1 (hiking prototype; github.com/Alicepacc/Idea-web-app-trekking)"}
# association themes worth showing, by their Italian name in the data
THEMES = {
    "Escursioni per famiglie": "per famiglie", "Adatto per passeggino": "adatto al passeggino",
    "Percorsi tematici": "percorso tematico", "Sentieri di roggia": "sentiero lungo una roggia",
    "Sentieri a lunga distanza": "lunga percorrenza", "Escursioni per diversamente abili": "accessibile",
    "Escursioni invernali": "anche d'inverno", "Ciaspolate": "con le ciaspole", "Pellegrinaggio": "pellegrinaggio",
}
NEAR_SAT = 30          # m: a hike point this close to a SAT trail is on it
START_RADIUS = 1200    # m between a suggested hike's start and our trailhead
END_RADIUS = 400       # m between its arrival point and our goal
GENERIC = {"giro", "lago", "laghi", "malga", "malghe", "rifugio", "sentiero", "escursione", "passeggiata", "monte",
           "passo", "valle", "punta", "baita", "hutte", "huette", "alm", "see",
           "cima", "alta", "alto", "via", "verso", "dalla", "dalle", "della", "delle", "degli", "sopra", "sotto",
           "tour", "anello", "circolare", "invernale", "escursioni", "percorso", "tematico", "nella", "lungo"}

to_utm = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)


def get(url, attempts=4):
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
                return r.read()
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(5 * (attempt + 1))


def words(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return set(re.findall(r"[a-z]{4,}", s)) - GENERIC


def hours(t):
    """SAT times are "hh:mm"."""
    m = re.match(r"(\d+):(\d+)", t or "")
    return round(int(m.group(1)) + int(m.group(2)) / 60, 2) if m else None


def maintainers(s):
    """"SEZ. SAT ROVERETO, RIF. ALPE POZZA" -> "Sezione SAT Rovereto, Rif. Alpe Pozza"."""
    s = title_case(s or "")
    s = re.sub(r"\bSez\.\s*Sat\b", "Sezione SAT", s)
    return re.sub(r"\b(Sat|Cai|Sosat|O\.c\.)(?=\W|$)", lambda m: m.group(1).upper(), s)


def sat_trails():
    raw = RAW / "sat"
    raw.mkdir(parents=True, exist_ok=True)
    zipfile.ZipFile(io.BytesIO(get(SAT_ZIP))).extractall(raw / "shp")
    # ETRS89 / UTM 32N: the same grid as the pipeline's within a metre
    r = shapefile.Reader(str(raw / "shp" / "sentieri_tratte"), encoding="latin-1")
    out = []
    for rec, shp in zip(r.records(), r.shapes()):
        if not shp.points:
            continue
        line = shapely.simplify(shapely.LineString(shp.points), 5)
        out.append(dict(ref=rec["numero"].strip(), name=title_case(rec["denominaz"]) or None,
                        start=title_case(rec["loc_inizio"]), end=title_case(rec["loc_fine"]), diff=rec["difficolta"].strip(),
                        t_go=hours(rec["t_andata"]), t_back=hours(rec["t_ritorno"]),
                        group=title_case(rec["gr_mont"]), maint=maintainers(rec["competenza"]),
                        coords=np.round(shapely.get_coordinates(line), 1).tolist()))
    return out


def suggested_hikes():
    items, page = [], 1
    while True:
        doc = json.loads(get(ODH.format(page)))
        items += doc["Items"]
        if page >= doc["TotalPages"]:
            break
        page += 1
    out = []
    for it in items:
        if it.get("LicenseInfo.License") != "CC0" or not it.get("Active"):
            continue
        pts = it.get("GpsPoints") or {}
        start = pts.get("startingpoint") or pts.get("startingandarrivalpoint") or pts.get("position")
        if not start:
            continue
        end = pts.get("arrivalpoint")
        xy = lambda p: [round(v, 1) for v in to_utm.transform(p["Longitude"], p["Latitude"])]
        themes = sorted({THEMES[t["TagName"]["it"]] for t in it.get("LTSTags") or []
                         if (t.get("TagName") or {}).get("it") in THEMES})
        out.append(dict(id=it["Id"], title=(it.get("Detail.it.Title") or it.get("Detail.de.Title") or "").strip(),
                        assoc=it.get("LocationInfo.TvInfo.Name.it"), url=it.get("ContactInfos.it.Url"),
                        themes=themes, start=xy(start), end=xy(end) if end else None,
                        open=bool(it.get("IsOpen")), changed=(it.get("LastChange") or "")[:10]))
    return out


def fetch():
    sat = sat_trails()
    lts = suggested_hikes()
    (WORK / "official.json").write_text(json.dumps(dict(sat=sat, lts=lts), ensure_ascii=False))
    print(f"official.json: {len(sat)} SAT trails, {len(lts)} suggested hikes in South Tyrol "
          f"({sum(1 for h in lts if h['end'])} with an arrival point)")


def match():
    doc = json.loads((WORK / "official.json").read_text())
    sat, lts = doc["sat"], doc["lts"]
    rows = pq.read_table(WORK / "itineraries.parquet", columns=["id", "goal_poi", "goal_name", "geometry"]).to_pylist()
    pois = pq.read_table(WORK / "pois.parquet", columns=["geometry"]).column(0).to_pylist()
    lines = [shapely.from_wkb(r["geometry"]) for r in rows]
    goals = [shapely.point_on_surface(shapely.from_wkb(pois[r["goal_poi"]])) for r in rows]
    # SAT: share of each hike on each trail, from points every 50 m
    sat_lines = [shapely.LineString(t["coords"]) for t in sat]
    sat_tree = shapely.STRtree(sat_lines)
    out = defaultdict(dict)
    for r, line in zip(rows, lines):
        pts = shapely.points(shapely.get_coordinates(shapely.line_interpolate_point(line, np.arange(0, line.length, 50))))
        hit, near = sat_tree.query(pts, predicate="dwithin", distance=NEAR_SAT)
        if not len(hit):
            continue
        on = {}
        for p, t in zip(hit, near):
            on.setdefault(p, t)  # one trail per point is enough
        share = np.bincount(list(on.values()), minlength=len(sat)) / len(pts)
        used = []
        for t in np.argsort(-share)[:5]:
            if share[t] < 0.08:
                break
            # how much of the SAT trail the hike walks: its official times apply only to the whole trail
            tp = shapely.line_interpolate_point(sat_lines[t], np.arange(0, sat_lines[t].length, 50))
            whole = float(np.mean(shapely.dwithin(tp, line, NEAR_SAT)))
            used.append([int(t), round(float(share[t]), 2), round(whole, 2)])
        if used:
            out[r["id"]].update(sat=used, sat_share=round(len(on) / len(pts), 2))

    # South Tyrol: suggested hikes from about the same start, to about the same arrival point
    heads = np.array([shapely.get_coordinates(line)[0] for line in lines])
    head_tree = cKDTree(heads)
    goal_words = [words(r["goal_name"]) for r in rows]
    for k, h in enumerate(lts):
        title = words(h["title"])
        best = []
        for i in head_tree.query_ball_point(h["start"], START_RADIUS):
            if h["end"] and np.hypot(*np.subtract(h["end"], h["start"])) > 500:
                d = goals[i].distance(shapely.Point(h["end"]))
                if d <= END_RADIUS:
                    best.append((d, i))
            # a round walk has no arrival point: its title must name the hike's goal, away from the start
            elif title & goal_words[i] and goals[i].distance(shapely.Point(h["start"])) > 800:
                best.append((np.hypot(*(heads[i] - h["start"])), i))
        for d, i in sorted(best)[:2]:
            out[rows[i]["id"]].setdefault("lts", []).append(k)
    n_sat = sum(1 for v in out.values() if v.get("sat_share", 0) >= 0.7)
    n_lts = sum(1 for v in out.values() if v.get("lts"))
    (WORK / "official_match.json").write_text(json.dumps(out))
    print(f"official_match.json: {n_sat} hikes mostly on SAT trails, {n_lts} matching a suggested hike in South Tyrol")


if __name__ == "__main__":
    {"fetch": fetch, "match": match}[sys.argv[1]]()
