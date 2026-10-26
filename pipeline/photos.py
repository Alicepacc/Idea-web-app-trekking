"""Step 11 - openly licensed photos of the goals of the hikes.

Wikidata links many peaks, lakes and mountain huts to a picture on Wikimedia
Commons (property P18). Goals are matched to Wikidata items by name and
distance; for each picture the author and licence come from Commons, and a
small thumbnail is shipped with the page, which cannot load images from other
sites. Photos without a known licence are skipped.

Writes web/data/photos/<pack>.webp (thumbnails end to end) and data/work/photos.json:
  {"<poi index>": {"pack", "offset", "length", "title", "author", "license", "page"}}
"""
import hashlib
import html
import io
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pyarrow.parquet as pq
import shapely
from PIL import Image
from pyproj import Transformer
from scipy.spatial import cKDTree

from common import BBOX, RAW, UTM, WEB_DATA, WORK

UA = {"User-Agent": "SentieriAutunno/0.1 (hiking prototype; github.com/Alicepacc/Idea-web-app-trekking)"}
SPARQL = "https://query.wikidata.org/sparql"
THUMB = "https://upload.wikimedia.org/wikipedia/commons/thumb/{a}/{ab}/{name}/960px-{name}"
ORIGINAL = "https://upload.wikimedia.org/wikipedia/commons/{a}/{ab}/{name}"
OUT_W = 440            # stored size
MATCH_M = {"peak": 400, "lake": 500, "rifugio": 250, "malga": 250, "bivacco": 250, "viewpoint": 250}
PACK_BYTES = 6_000_000
OUT = WEB_DATA / "photos"

to_utm = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)

QUERY = """
SELECT ?item ?it ?de ?image ?coord WHERE {
  SERVICE wikibase:box {
    ?item wdt:P625 ?coord .
    bd:serviceParam wikibase:cornerSouthWest "Point(%f %f)"^^geo:wktLiteral .
    bd:serviceParam wikibase:cornerNorthEast "Point(%f %f)"^^geo:wktLiteral .
  }
  ?item wdt:P18 ?image .
  OPTIONAL { ?item rdfs:label ?it . FILTER(LANG(?it) = "it") }
  OPTIONAL { ?item rdfs:label ?de . FILTER(LANG(?de) = "de") }
}"""


def get(url, data=None, attempts=6):
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, data=data, headers={**UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503) or attempt == attempts - 1:
                raise
            time.sleep(int(e.headers.get("Retry-After") or 0) or 20 * (attempt + 1))
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(rifugio|rif|hutte|huette|schutzhutte|malga|alm|bivacco|lago|see|monte|cima|punta|piz|spitze)\b", " ", s)
    return set(re.findall(r"[a-z0-9]{3,}", s))


def wikidata_items():
    cache = RAW / "wikidata_images.json"
    if not cache.exists():
        q = QUERY % (BBOX[0], BBOX[1], BBOX[2], BBOX[3])
        cache.write_bytes(get(SPARQL, data=urllib.parse.urlencode({"query": q, "format": "json"}).encode()))
    rows = json.loads(cache.read_text())["results"]["bindings"]
    items = {}
    for b in rows:
        lon, lat = map(float, re.findall(r"[-\d.]+", b["coord"]["value"])[:2])
        file = urllib.parse.unquote(b["image"]["value"].rsplit("/", 1)[-1])
        names = {b[k]["value"] for k in ("it", "de") if k in b}
        it = items.setdefault(b["item"]["value"], dict(lon=lon, lat=lat, file=file, names=set()))
        it["names"] |= names
    return list(items.values())


def match(goals, items):
    xy = np.array([to_utm.transform(i["lon"], i["lat"]) for i in items])
    tree = cKDTree(xy)
    out = {}
    for poi, p in goals.items():
        g = p["geometry"]
        c = (g.x, g.y) if p["kind"] != "lake" else (g.centroid.x, g.centroid.y)
        words = norm(p["name"])
        best = None
        for j in tree.query_ball_point(c, r=MATCH_M[p["kind"]] + (np.sqrt(p["area"] or 0) if p["kind"] == "lake" else 0)):
            cand = items[j]
            common = max((len(words & norm(n)) / max(len(words), 1) for n in cand["names"]), default=0)
            if words and common < 0.5:
                continue
            d = np.hypot(*(xy[j] - c))
            score = common - d / 5000
            if best is None or score > best[0]:
                best = (score, cand)
        if best:
            out[poi] = best[1]["file"]
    return out


def page_meta(name):
    """Licence and author from the machine-readable markup of a Commons file page."""
    page = get(f"https://commons.wikimedia.org/wiki/File:{urllib.parse.quote(name)}").decode("utf-8", "replace")
    text = lambda m: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).strip() if m else ""
    lic = text(re.search(r'class="licensetpl&#95;short"[^>]*>(.*?)</span>', page, re.S))
    author = (text(re.search(r'class="licensetpl&#95;attr"[^>]*>(.*?)</span>', page, re.S))
              or text(re.search(r'fileinfotpl&#95;aut[^>]*>.*?</td>\s*<td[^>]*>(.*?)</td>', page, re.S)))
    return lic, author


def api_meta(names):
    """Licence and author of up to 50 files in one Commons API call."""
    q = {"action": "query", "prop": "imageinfo", "iiprop": "extmetadata", "format": "json", "formatversion": 2,
         "iiextmetadatafilter": "LicenseShortName|Artist|Attribution", "titles": "|".join("File:" + n for n in names)}
    data = json.loads(get(f"https://commons.wikimedia.org/w/api.php?{urllib.parse.urlencode(q)}", attempts=10))
    out = {}
    for pg in data["query"]["pages"]:
        info = (pg.get("imageinfo") or [None])[0]
        if not info:
            continue
        em = info.get("extmetadata", {})
        clean = lambda v: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", v or ""))).strip()
        lic = clean(em.get("LicenseShortName", {}).get("value"))
        author = clean(em.get("Attribution", {}).get("value")) or clean(em.get("Artist", {}).get("value"))
        out[pg["title"][5:].replace(" ", "_")] = (lic, author)
    return out


def licences(files):
    """Author, licence and thumbnail URL of Commons files.

    Commons is often rate-limited from shared networks: a few large API calls go
    first, then file pages for what is left, slowly. Thumbnails need no lookup:
    their URL comes from the file name (Commons stores files by MD5 prefix).
    """
    names = sorted({f.replace(" ", "_") for f in files if re.search(r"\.(jpe?g|png)$", f, re.I)})
    found = {}
    for i in range(0, len(names), 50):
        try:
            found.update(api_meta(names[i:i + 50]))
        except urllib.error.HTTPError:
            pass
        time.sleep(5)
    for n in names:
        if found.get(n, ("", ""))[0]:
            continue
        try:
            found[n] = page_meta(n)
        except urllib.error.HTTPError:
            continue
        time.sleep(3)
    meta = {}
    for n in names:
        lic, author = found.get(n, ("", ""))
        if not lic:
            continue
        h = hashlib.md5(n.encode()).hexdigest()
        q = urllib.parse.quote(n)
        meta[n.replace("_", " ")] = dict(thumb=THUMB.format(a=h[0], ab=h[:2], name=q),
                                        original=ORIGINAL.format(a=h[0], ab=h[:2], name=q),
                                        license=lic, author=(author or "autore sconosciuto")[:80],
                                        page=f"https://commons.wikimedia.org/wiki/File:{q}")
    print(f"licences: {len(meta)} of {len(names)} files")
    return meta


def thumbnail(meta):
    data = None
    # pictures narrower than a thumbnail size have no thumbnail at that size: try smaller ones,
    # and the original (the most rate-limited request) only as a last resort
    for width in (960, 500, 330):
        try:
            data = get(meta["thumb"].replace("/960px-", f"/{width}px-"), attempts=2)
            break
        except urllib.error.HTTPError:
            continue
    if data is None:
        data = get(meta["original"])
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if img.width > OUT_W:
        img = img.resize((OUT_W, round(img.height * OUT_W / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=72, method=5)
    return buf.getvalue()


def main():
    itin = pq.read_table(WORK / "itineraries.parquet", columns=["goal_poi"]).column(0).to_pylist()
    pois = pq.read_table(WORK / "pois.parquet").to_pylist()
    goals = {}
    for i in set(itin):
        p = pois[i]
        if p["name"] and p["kind"] in MATCH_M:
            p["geometry"] = shapely.from_wkb(p["geometry"])
            goals[i] = p
    items = wikidata_items()
    files = match(goals, items)
    print(f"{len(goals)} named goals, {len(files)} with a Wikidata photo")
    meta = licences(files.values())
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.webp"):
        old.unlink()
    todo = [(poi, f) for poi, f in files.items() if f in meta]
    with ThreadPoolExecutor(4) as ex:
        thumbs = list(ex.map(lambda t: thumbnail(meta[t[1]]), todo))
    index, pack, buf = {}, 0, bytearray()
    for (poi, f), data in zip(todo, thumbs):
        if len(buf) + len(data) > PACK_BYTES:
            (OUT / f"photos-{pack}.webp").write_bytes(buf)
            pack, buf = pack + 1, bytearray()
        m = meta[f]
        index[str(poi)] = dict(pack=f"photos-{pack}", offset=len(buf), length=len(data), title=f,
                               author=m["author"], license=m["license"], page=m["page"])
        buf += data
    (OUT / f"photos-{pack}.webp").write_bytes(buf)
    (WORK / "photos.json").write_text(json.dumps(index, ensure_ascii=False))
    print(f"photos.json: {len(index)} photos in {pack + 1} packs")


if __name__ == "__main__":
    main()
