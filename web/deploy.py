"""Publish the demo as a standalone website on the gh-pages branch.

The branch holds the page and its data at the root, ready for GitHub Pages,
Cloudflare Pages or Netlify. The data (about 90 MB) changes every week: keeping
its history would grow the repository by that much each time, so the branch is
a single commit, replaced on every run.

    .venv/bin/python web/deploy.py                # build and push
    .venv/bin/python web/deploy.py --out FOLDER   # only build the site, into FOLDER
"""
import argparse
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WEB = Path(__file__).resolve().parent
REPO = WEB.parent
BRANCH = "gh-pages"
DATA = ["hikes.json", "pois.json", "details", "tiles", "photos"]

# the artifact host adds the document skeleton; a plain web server does not
HEAD = """<!doctype html>
<html lang="it">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Escursioni in Trentino-Alto Adige scelte in base a foliage, neve, meteo e panorami di questa settimana.">
"""
ATTRIBUTION = ("Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
               "Claude-Session: https://claude.ai/code/session_015D7zvfCuQK1J7wScVChrLe")


def git(*args, env=None):
    return subprocess.run(["git", *args], cwd=REPO, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


def link(src, dst):
    try:
        os.link(src, dst)
    except OSError:  # another disk
        shutil.copy2(src, dst)


def build_site(site):
    subprocess.run([sys.executable, str(WEB / "build.py")], check=True)
    site.mkdir()
    (site / "index.html").write_text(HEAD + (WEB / "index.html").read_text())
    (site / ".nojekyll").write_text("")  # serve files as they are, no Jekyll build
    for name in DATA:
        src, dst = WEB / "data" / name, site / "data" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, copy_function=link)
        else:
            link(src, dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, help="build the site into this folder and do not push")
    args = ap.parse_args()
    if args.out:
        if args.out.exists():
            # only ever replace a previous build, never another folder
            if not (args.out / ".nojekyll").exists() and any(args.out.iterdir()):
                sys.exit(f"{args.out} is not empty")
            shutil.rmtree(args.out)
        build_site(args.out)
        return
    # inside the ignored data folder: same disk as web/data, so files are linked, not copied
    (REPO / "data").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=REPO / "data") as tmp:
        site = Path(tmp) / "site"
        build_site(site)
        files = [p for p in site.rglob("*") if p.is_file()]
        print(f"site: {len(files)} files, {sum(p.stat().st_size for p in files) / 1e6:.0f} MB")
        # commit the folder without touching the checkout: a separate index, then plumbing
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git("--work-tree", str(site), "add", "-A", env=env)
        tree = git("write-tree", env=env)
        msg = f"Demo of {datetime.date.today():%Y-%m-%d}\n\nBuilt from {git('rev-parse', '--short', 'HEAD')}.\n\n{ATTRIBUTION}"
        commit = git("commit-tree", tree, "-m", msg)
        git("push", "--force", "origin", f"{commit}:refs/heads/{BRANCH}")
        print(f"pushed {commit[:7]} to {BRANCH}")


if __name__ == "__main__":
    main()
