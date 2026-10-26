"""Weekly update: refresh satellite images, foliage, weather and everything built on them.

Steps whose inputs rarely change (trail network, terrain) run only when their
outputs are missing, e.g. in a fresh environment. Run from the pipeline folder:

    ../.venv/bin/python update.py            # weekly refresh
    ../.venv/bin/python update.py --all      # rebuild everything from scratch
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

from common import RAW, WORK

PY = sys.executable
HERE = Path(__file__).resolve().parent

# (script, args, outputs that make it skippable when --all is not given)
STABLE = [
    ("fetch_overture.py", [], [RAW / "segments.parquet", RAW / "orchards.parquet", RAW / "boundary.parquet"]),
    ("fetch_osm_routes.py", [], [RAW / "osm_routes.parquet"]),
    ("build_routes.py", [], [WORK / "routes.parquet"]),
    ("dem.py", [], [WORK / "dem20.tif", WORK / "dem100.tif", WORK / "horizon.tif"]),
]
WEEKLY = [
    ("sentinel.py", ["--only", "now"], None),
    ("sentinel.py", ["--only", "summer"], [WORK / "s2_summer.tif"]),
    ("foliage.py", [], None),
    ("score.py", [], None),
    ("itineraries.py", [], None),
    ("weather.py", [], None),
    ("photos.py", [], None),
    ("webcams.py", [], None),
    ("panorama.py", [], None),
    ("tiles.py", [], None),
    ("export_web.py", [], None),
]


def run(script, args):
    t = time.time()
    print(f"--> {script} {' '.join(args)}", flush=True)
    subprocess.run([PY, script, *args], cwd=HERE, check=True)
    print(f"    done in {(time.time() - t) / 60:.1f} min", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="rebuild every step")
    args = ap.parse_args()
    for script, extra, outputs in STABLE + WEEKLY:
        if not args.all and outputs and all(p.exists() for p in outputs):
            print(f"--- {script} {' '.join(extra)}: up to date")
            continue
        run(script, extra)
    subprocess.run([PY, str(HERE.parent / "web" / "build.py")], check=True)


if __name__ == "__main__":
    main()
