"""Step 1 - download the Overture Maps layers for the region.

Writes to data/raw/:
  segments.parquet    walkable network (paths, tracks, roads) with OSM way ids
  peaks.parquet       peaks and saddles with elevation
  viewpoints.parquet  mapped viewpoints
  access.parquet      parking, cable-car stations, bus stops (trailheads)
  lakes.parquet       lakes, ponds and reservoirs (polygons)
  orchards.parquet    orchards and vineyards (excluded from the foliage index)
  huts.parquet        mountain huts, bivouacs and alpine pastures (by name)
  boundary.parquet    Trentino-Alto Adige/Suedtirol region polygon
  localities.parquet  towns and villages, for "start from" searches
"""
import re

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from common import RAW, osm_ways, primary_name, read_overture


def save(tb: pa.Table, name: str) -> None:
    pq.write_table(tb, RAW / name, compression="zstd")
    print(f"{name}: {tb.num_rows} rows")


def segments():
    tb = read_overture("transportation", "segment",
                       ["id", "subtype", "class", "subclass", "names", "connectors", "sources", "geometry"],
                       row_filter=lambda t: pc.equal(t.column("subtype"), "road"))
    way_type = pa.list_(pa.struct([("way", pa.int64()), ("start", pa.float64()), ("end", pa.float64())]))
    ways = pa.array([osm_ways(s) for s in tb.column("sources").to_pylist()], way_type)
    out = pa.table({
        "id": tb.column("id"),
        "class": tb.column("class"),
        "subclass": tb.column("subclass"),
        "name": primary_name(tb),
        "osm_ways": ways,
        "connectors": tb.column("connectors"),
        "geometry": tb.column("geometry"),
    })
    save(out, "segments.parquet")


def peaks():
    tb = read_overture("base", "land", ["id", "names", "class", "elevation", "wikidata", "geometry"],
                       row_filter=lambda t: pc.is_in(t.column("class"), pa.array(["peak", "saddle", "volcano"])))
    save(tb.select(["id", "class", "elevation", "wikidata", "geometry"]).append_column("name", primary_name(tb)),
         "peaks.parquet")


def infrastructure():
    tb = read_overture("base", "infrastructure", ["id", "names", "subtype", "class", "geometry"],
                       row_filter=lambda t: pc.is_in(t.column("class"), pa.array(
                           ["viewpoint", "parking", "aerialway_station", "bus_stop", "bus_station",
                            "railway_station", "train_station"])))
    tb = tb.select(["id", "class", "geometry"]).append_column("name", primary_name(tb))
    is_view = pc.equal(tb.column("class"), "viewpoint")
    save(tb.filter(is_view), "viewpoints.parquet")
    save(tb.filter(pc.invert(is_view)), "access.parquet")


def farmland_trees():
    """Orchards and vineyards: WorldCover counts them as trees, but they are not woods to walk in."""
    tb = read_overture("base", "land_use", ["id", "class", "geometry"],
                       row_filter=lambda t: pc.is_in(t.column("class"), pa.array(["orchard", "vineyard", "plant_nursery"])))
    save(tb.select(["id", "class", "geometry"]), "orchards.parquet")


def lakes():
    tb = read_overture("base", "water", ["id", "names", "subtype", "class", "wikidata", "geometry"],
                       row_filter=lambda t: pc.is_in(t.column("class"), pa.array(["lake", "reservoir", "pond", "water"])))
    save(tb.select(["id", "class", "wikidata", "geometry"]).append_column("name", primary_name(tb)), "lakes.parquet")


HUT_KIND = [
    ("bivacco", re.compile(r"\b(bivacco|biwak|bivouac)\b", re.I)),
    ("rifugio", re.compile(r"\b(rifugio|rif\.|schutzh(ü|ue)tte|schutzhaus|h(ü|ue)tte|capanna|refuge|rifugi)\b"
                           r"|(h(ü|ue)tte)$", re.I)),
    ("malga", re.compile(r"\b(malga|alm|almh(ü|ue)tte|casera)\b", re.I)),
]
# Places whose name matches but that are clearly not huts.
NOT_HUT = re.compile(r"pilates|benessere|massag|spa\b|wellness|chiesa|taxi|amazon|parcheggio|scout|bottega|"
                     r"museo|apart|appartement|single room|festa|beauty|fioreria|real estate|immobil|"
                     r"prodotti|vendita|speck|kneipp|pub\b|sport|club|camping|campeggio|scuola|school", re.I)


def hut_kind(name: str | None) -> str | None:
    if not name or NOT_HUT.search(name):
        return None
    for kind, rx in HUT_KIND:
        if rx.search(name):
            return kind
    return None


def huts():
    rows = []
    # OSM buildings carry the hut names reliably (e.g. "Rifugio Pedrotti").
    tb = read_overture("buildings", "building", ["id", "names", "geometry"])
    names = primary_name(tb).to_pylist()
    keep = [i for i, n in enumerate(names) if hut_kind(n)]
    sub = tb.take(keep)
    for n, g in zip(primary_name(sub).to_pylist(), sub.column("geometry").to_pylist()):
        rows.append(dict(name=n, kind=hut_kind(n), source="osm_building", geometry=g))
    # Overture places (mostly from commercial sources) add huts mapped only as points.
    tb = read_overture("places", "place", ["id", "names", "confidence", "basic_category", "geometry"])
    names = primary_name(tb).to_pylist()
    conf = tb.column("confidence").to_pylist()
    keep = [i for i, n in enumerate(names) if hut_kind(n) and (conf[i] or 0) >= 0.5]
    sub = tb.take(keep)
    for n, g in zip(primary_name(sub).to_pylist(), sub.column("geometry").to_pylist()):
        rows.append(dict(name=n, kind=hut_kind(n), source="overture_place", geometry=g))
    save(pa.Table.from_pylist(rows), "huts.parquet")


def boundary_and_localities():
    tb = read_overture("divisions", "division_area", ["id", "names", "subtype", "country", "region", "geometry"],
                       row_filter=lambda t: pc.and_(pc.equal(t.column("subtype"), "region"),
                                                    pc.equal(t.column("country"), "IT")))
    tb = tb.append_column("name", primary_name(tb))
    region = tb.filter(pc.equal(tb.column("region"), "IT-32"))
    if region.num_rows == 0:
        region = tb.filter(pc.match_substring(tb.column("name"), "Trentino"))
    save(region.select(["id", "name", "region", "geometry"]), "boundary.parquet")

    tb = read_overture("divisions", "division", ["id", "names", "subtype", "country", "population", "geometry"],
                       row_filter=lambda t: pc.is_in(t.column("subtype"), pa.array(["locality", "localadmin"])))
    save(tb.select(["id", "subtype", "population", "geometry"]).append_column("name", primary_name(tb)),
         "localities.parquet")


if __name__ == "__main__":
    segments()
    peaks()
    infrastructure()
    lakes()
    farmland_trees()
    huts()
    boundary_and_localities()
