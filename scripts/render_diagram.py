#!/usr/bin/env python3
"""Render docs/architecture.mmd to docs/architecture.svg and .png, offline, with the Mermaid copy that ships in the app.

    python scripts/render_diagram.py          # needs: pip install playwright && playwright install chromium
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
SRC = (REPO / "docs" / "architecture.mmd").read_text()
MERMAID = REPO / "stella80" / "static" / "vendor" / "mermaid.min.js"

PAGE = """<!doctype html><meta charset="utf-8"><body style="margin:0;background:#fff"><div id="out"></div></body>"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1000, "height": 1400}, device_scale_factor=2)
    pg.set_content(PAGE)
    pg.add_script_tag(path=str(MERMAID))
    svg = pg.evaluate("""async (src) => {
        mermaid.initialize({startOnLoad: false, securityLevel: 'loose', theme: 'base', flowchart: {htmlLabels: true, curve: 'basis'},
            themeVariables: {fontFamily: 'DejaVu Sans, Arial, sans-serif', fontSize: '15px', lineColor: '#5b4a33', primaryTextColor: '#2b2016'}});
        const {svg} = await mermaid.render('arch', src);
        document.getElementById('out').innerHTML = svg;
        return svg;
    }""", SRC)
    (REPO / "docs" / "architecture.svg").write_text(svg)
    el = pg.query_selector("#out svg")
    box = el.bounding_box()
    pg.set_viewport_size({"width": int(box["width"]) + 40, "height": int(box["height"]) + 40})
    pg.query_selector("#out").screenshot(path=str(REPO / "docs" / "architecture.png"))
    b.close()
print("wrote docs/architecture.svg and docs/architecture.png")
