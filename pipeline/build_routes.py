"""Step 3 - turn OSM hiking relations into route geometries.

Each relation lists its member ways; their geometry comes from the Overture
segments (which keep the OSM way id). Routes mostly outside the region,
fragments under 1 km and multi-day trails over 40 km are dropped.

Writes data/work/routes.parquet (geometry as WKB in UTM 32N).
"""
from collections import defaultdict

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
import shapely.ops
from pyproj import Transformer

from common import RAW, UTM, WORK

MIN_KM, MAX_KM = 1.0, 40.0
TAGS = ["ref", "name", "name:it", "name:de", "from", "to", "network", "operator", "cai_scale", "osmc:symbol",
        "roundtrip", "website", "description", "description:it", "symbol", "duration:forward", "via"]

to_utm = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)


def project(geom):
    return shapely.transform(geom, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])))


def main():
    seg = pq.read_table(RAW / "segments.parquet", columns=["osm_ways", "geometry"]).to_pydict()
    # OSM way id -> [(segment index, start, end)] for the part of each segment it covers
    way_parts = defaultdict(list)
    for i, ways in enumerate(seg["osm_ways"]):
        for w in ways or []:
            way_parts[w["way"]].append((i, w["start"], w["end"]))
    seg_utm = {}

    def way_line(w):
        lines = []
        for i, start, end in way_parts[w]:
            if i not in seg_utm:
                seg_utm[i] = project(shapely.from_wkb(seg["geometry"][i]))
            line = seg_utm[i]
            if start > 0 or end < 1:
                line = shapely.ops.substring(line, start, end, normalized=True)
            if line.length > 0:
                lines.append(line)
        return lines

    region = project(shapely.from_wkb(pq.read_table(RAW / "boundary.parquet").column("geometry")[0].as_py()))
    shapely.prepare(region)

    rel = pq.read_table(RAW / "osm_routes.parquet").to_pylist()
    rows = []
    for r in rel:
        tags = dict(r["tags"])
        ways = [m["ref"] for m in r["members"] or [] if m["type"] == "way"]
        found = [w for w in ways if w in way_parts]
        if not found:
            continue
        lines = [ln for w in dict.fromkeys(found) for ln in way_line(w)]
        if not lines:
            continue
        # Snap to 0.5 m so that ways meeting at the same node merge cleanly.
        geom = shapely.line_merge(shapely.set_precision(shapely.union_all(lines), 0.5))
        length = geom.length
        if not MIN_KM * 1000 <= length <= MAX_KM * 1000:
            continue
        inside = shapely.intersection(geom, region).length
        if inside < 0.5 * length:
            continue
        parts = shapely.get_num_geometries(geom) if geom.geom_type == "MultiLineString" else 1
        row = {
            "osm_id": r["id"],
            "length_m": round(length),
            "inside_share": round(inside / length, 3),
            "complete": round(len(found) / len(ways), 3),
            "parts": int(parts),
            "geometry": shapely.to_wkb(geom),
        }
        row.update({k.replace(":", "_"): tags.get(k) for k in TAGS})
        rows.append(row)

    tb = pa.Table.from_pylist(rows)
    pq.write_table(tb, WORK / "routes.parquet", compression="zstd")
    lengths = np.array(tb.column("length_m").to_pylist()) / 1000
    print(f"routes.parquet: {tb.num_rows} routes, median {np.median(lengths):.1f} km, "
          f"{sum(p > 1 for p in tb.column('parts').to_pylist())} with gaps")


if __name__ == "__main__":
    main()
