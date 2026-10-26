"""Build web/index.html from web/src/index.html, inlining the MapLibre stylesheet.

The published page may load scripts from a few CDNs but no external stylesheets,
so the MapLibre CSS ships inside the page.
"""
from pathlib import Path

WEB = Path(__file__).resolve().parent
css = (WEB / "vendor" / "maplibre-gl-4.7.1.css").read_text().strip()
src = (WEB / "src" / "index.html").read_text()
assert "/*MAPLIBRE_CSS*/" in src
(WEB / "index.html").write_text(src.replace("/*MAPLIBRE_CSS*/", "/* maplibre-gl 4.7.1, BSD-3-Clause */\n" + css))
print("web/index.html built")
