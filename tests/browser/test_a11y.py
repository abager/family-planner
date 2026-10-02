"""Tilgængelighed med axe-core. Valgfri: kør `npm install axe-core` og sæt AXE_JS=sti/til/node_modules/axe-core/axe.min.js.

Kendte, endnu ikke rettede fund (se README): lav kontrast i de nedtonede, overståede lektioner og den blå accentfarve i mørk tilstand,
samt manglende <main>/<nav>-områder. Testen sikrer, at der ikke kommer NYE kritiske fejl, og at de kendte ikke bliver værre.
"""
import datetime as dt
import os
from pathlib import Path

import pytest

AXE = os.environ.get("AXE_JS", "")
pytestmark = pytest.mark.skipif(not (AXE and Path(AXE).exists()), reason="sæt AXE_JS til axe.min.js for at køre tilgængelighedstesten")
KNOWN = {"color-contrast", "landmark-one-main", "region"}
VIEWS = [("v-today", "I dag"), ("v-week", "Ugen"), ("v-mail", "Beskeder"), ("v-sugg", "Forslag"), ("v-aula", "Feed")]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_no_new_accessibility_violations(make_page, scheme):
    page, *_ = make_page(now=dt.datetime(2026, 10, 1, 10, 0), fixed=True)
    page.emulate_media(color_scheme=scheme)
    page.add_script_tag(content=Path(AXE).read_text())
    found = {}
    for vid, name in VIEWS:
        page.click("#" + vid)
        page.wait_for_timeout(250)
        for v in page.evaluate("axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa','best-practice']}}).then(r=>r.violations.map(v=>({id:v.id,impact:v.impact,n:v.nodes.length})))"):
            found.setdefault(v["id"], []).append((name, v["impact"], v["n"]))
    new = {k: v for k, v in found.items() if k not in KNOWN}
    assert new == {}, new
    assert all(i != "critical" for vs in found.values() for (_, i, _) in vs)
