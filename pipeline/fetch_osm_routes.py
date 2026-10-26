"""Step 2 - extract the signed hiking routes (OSM route relations) for the region.

Overture keeps only road route relations, so the numbered trails (SAT, CAI,
AVS...) come from the OpenStreetMap planet in ORC format on AWS. Relations
sit in the last stripes of the file, so only those are read (~1% of it).
A relation is kept when at least one of its member ways is in the region
network extracted by fetch_overture.py.

Writes data/raw/osm_routes.parquet.
"""
import json
from concurrent.futures import ThreadPoolExecutor

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.orc as orc
import pyarrow.parquet as pq

from common import OSM_PLANET_ORC, RAW, s3

HIKING = pa.array(["hiking", "foot", "walking"])


def open_planet():
    return orc.ORCFile(s3("us-east-1").open_input_file(OSM_PLANET_ORC))


def first_relation_stripe(o: orc.ORCFile) -> int:
    """Rows are ordered nodes, ways, relations: bisect on the last row's type."""
    cache = RAW / "osm_planet_first_relation_stripe.json"
    key = f"{o.nstripes}:{o.nrows}"
    if cache.exists():
        saved = json.loads(cache.read_text())
        if saved.get("key") == key:
            return saved["stripe"]

    def last_type(i):
        col = o.read_stripe(i, columns=["type"]).column(0)
        return col[len(col) - 1].as_py()

    lo, hi = 0, o.nstripes - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if last_type(mid) == "relation":
            hi = mid
        else:
            lo = mid + 1
    cache.write_text(json.dumps({"key": key, "stripe": lo}))
    return lo


def hiking_relations(i: int) -> pa.Table:
    o = open_planet()
    tb = pa.Table.from_batches([o.read_stripe(i, columns=["id", "type", "tags", "members"])])
    tb = tb.filter(pc.equal(tb.column("type"), "relation"))
    route = pc.map_lookup(tb.column("tags"), "route", "first")
    return tb.filter(pc.fill_null(pc.is_in(route, value_set=HIKING), False)).drop_columns(["type"])


def main():
    o = open_planet()
    first = first_relation_stripe(o)
    print(f"relations in stripes {first}..{o.nstripes - 1}")
    with ThreadPoolExecutor(6) as ex:
        rel = pa.concat_tables(ex.map(hiking_relations, range(first, o.nstripes)))
    print(f"hiking relations in the planet: {rel.num_rows}")

    seg_ways = pq.read_table(RAW / "segments.parquet", columns=["osm_ways"]).column(0)
    ways = set(pc.struct_field(pc.list_flatten(seg_ways), "way").to_pylist())
    keep = []
    for i, members in enumerate(rel.column("members").to_pylist()):
        if any(m["type"] == "way" and m["ref"] in ways for m in members or []):
            keep.append(i)
    out = rel.take(keep)
    pq.write_table(out, RAW / "osm_routes.parquet", compression="zstd")
    print(f"osm_routes.parquet: {out.num_rows} relations touching the region")


if __name__ == "__main__":
    main()
