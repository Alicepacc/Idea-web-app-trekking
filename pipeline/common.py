"""Shared settings and data-access helpers for the pipeline.

Every source used here is open data published on AWS S3 (Overture Maps,
the OpenStreetMap planet in ORC format, Sentinel-2 L2A COGs, Copernicus DEM,
ESA WorldCover), so the pipeline needs no API keys.
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.fs as pafs
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
WORK = ROOT / "data" / "work"
WEB_DATA = ROOT / "web" / "data"
for _d in (RAW, WORK, WEB_DATA):
    _d.mkdir(parents=True, exist_ok=True)

# Trentino-Alto Adige plus a small margin: lon_min, lat_min, lon_max, lat_max.
BBOX = (10.35, 45.65, 12.50, 47.10)
# Every raster is resampled onto one metric grid: UTM 32N, 20 m pixels.
UTM = "EPSG:32632"
GRID_RES = 20.0

OVERTURE_RELEASE = "2026-10-21.0"
OSM_PLANET_ORC = "osm-pds/planet/planet-latest.orc"

# The session proxy re-terminates TLS with its own CA.
CA_BUNDLE = "/root/.ccr/ca-bundle.crt"


def _proxy() -> str | None:
    return os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")


def s3(region: str) -> pafs.S3FileSystem:
    if os.path.exists(CA_BUNDLE):
        os.environ.setdefault("AWS_CA_BUNDLE", CA_BUNDLE)
    kwargs = dict(anonymous=True, region=region)
    if _proxy():
        kwargs["proxy_options"] = _proxy()
    return pafs.S3FileSystem(**kwargs)


def gdal_env() -> dict:
    """GDAL options for reading cloud-optimized GeoTIFFs over HTTPS."""
    env = dict(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF",
        GDAL_HTTP_MULTIRANGE="YES",
        GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
        GDAL_HTTP_MAX_RETRY=5,
        GDAL_HTTP_RETRY_DELAY=2,
    )
    if _proxy():
        env["GDAL_HTTP_PROXY"] = _proxy().split("://", 1)[-1]
    if os.path.exists(CA_BUNDLE):
        env["CURL_CA_BUNDLE"] = CA_BUNDLE
    return env


def grid():
    """The shared UTM grid covering BBOX: (transform, width, height)."""
    from pyproj import Transformer
    from rasterio.transform import from_origin

    tr = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)
    lon0, lat0, lon1, lat1 = BBOX
    xs, ys = [], []
    for lon in (lon0, lon1):
        for lat in (lat0, lat1):
            x, y = tr.transform(lon, lat)
            xs.append(x)
            ys.append(y)
    xmin = GRID_RES * (min(xs) // GRID_RES)
    ymax = GRID_RES * -(-max(ys) // GRID_RES)
    width = int(-(-(max(xs) - xmin) // GRID_RES))
    height = int(-(-(ymax - min(ys)) // GRID_RES))
    return from_origin(xmin, ymax, GRID_RES, GRID_RES), width, height


def display_name(name):
    """Pick the Italian form of bilingual South Tyrolean names ("Meran - Merano" -> "Merano")."""
    if not name or " - " not in name:
        return name
    parts = [p.strip() for p in name.split(" - ")]
    plain = [p for p in parts if not re.search(r"[äöüßëÄÖÜ]", p)] or parts
    vowel = [p for p in plain if p[-1:].lower() in "aeiou"]
    return (vowel or plain)[-1]


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


# --- Overture Maps -----------------------------------------------------------

def _bbox_mask(tb: pa.Table, bbox) -> pa.ChunkedArray:
    b = tb.column("bbox")
    return pc.and_(
        pc.and_(pc.greater_equal(pc.struct_field(b, "xmax"), bbox[0]),
                pc.less_equal(pc.struct_field(b, "xmin"), bbox[2])),
        pc.and_(pc.greater_equal(pc.struct_field(b, "ymax"), bbox[1]),
                pc.less_equal(pc.struct_field(b, "ymin"), bbox[3])),
    )


def overture_row_groups(theme: str, type_: str, bbox=BBOX) -> dict[str, list[int]]:
    """Find the Parquet row groups whose bbox statistics intersect `bbox`.

    Overture files are spatially sorted, so a region touches only a few
    row groups; reading their footers costs a few MB instead of the full
    multi-GB theme.
    """
    cache = RAW / f"overture_{OVERTURE_RELEASE}_{theme}_{type_}_rowgroups.json"
    if cache.exists():
        return json.loads(cache.read_text())
    fs = s3("us-west-2")
    base = f"overturemaps-us-west-2/release/{OVERTURE_RELEASE}/theme={theme}/type={type_}/"
    files = sorted(f.path for f in fs.get_file_info(pafs.FileSelector(base)) if f.path.endswith(".parquet"))

    def scan(path):
        md = pq.ParquetFile(path, filesystem=fs).metadata
        names = [md.row_group(0).column(i).path_in_schema for i in range(md.row_group(0).num_columns)]
        ix = {n: i for i, n in enumerate(names)}
        hits = []
        for r in range(md.num_row_groups):
            rg = md.row_group(r)
            xmin = rg.column(ix["bbox.xmin"]).statistics.min
            xmax = rg.column(ix["bbox.xmax"]).statistics.max
            ymin = rg.column(ix["bbox.ymin"]).statistics.min
            ymax = rg.column(ix["bbox.ymax"]).statistics.max
            if xmax >= bbox[0] and xmin <= bbox[2] and ymax >= bbox[1] and ymin <= bbox[3]:
                hits.append(r)
        return path, hits

    with ThreadPoolExecutor(16) as ex:
        out = {path: hits for path, hits in ex.map(scan, files) if hits}
    cache.write_text(json.dumps(out))
    return out


def read_overture(theme: str, type_: str, columns: list[str], bbox=BBOX, row_filter=None) -> pa.Table:
    """Read the rows of an Overture theme/type that intersect `bbox`."""
    fs = s3("us-west-2")
    cols = list(dict.fromkeys(columns + ["bbox"]))
    tables = []
    for path, rgs in overture_row_groups(theme, type_, bbox).items():
        tb = pq.ParquetFile(path, filesystem=fs).read_row_groups(rgs, columns=cols)
        tb = tb.filter(_bbox_mask(tb, bbox))
        if row_filter is not None:
            tb = tb.filter(row_filter(tb))
        tables.append(tb)
    return pa.concat_tables(tables)


def primary_name(tb: pa.Table) -> pa.ChunkedArray:
    return pc.struct_field(tb.column("names"), "primary")


_OSM_WAY = re.compile(r"^w(\d+)@")


def osm_ways(sources) -> list[dict]:
    """OSM ways behind an Overture segment, with the part of the segment each covers.

    One segment can merge several consecutive OSM ways; `between` gives each
    way's [start, end] as fractions of the segment length.
    """
    out = []
    for s in sources or []:
        m = _OSM_WAY.match(s.get("record_id") or "")
        if m and not s.get("property"):
            start, end = s.get("between") or (0.0, 1.0)
            out.append({"way": int(m.group(1)), "start": float(start), "end": float(end)})
    return out
