"""Tilgængelighed med axe-core. Valgfri: kør `npm install axe-core` og sæt AXE_JS=sti/til/node_modules/axe-core/axe.min.js.

Ingen kendte fund er tilladt længere (KNOWN er tom). Testen kører i lyst og mørkt tema.
"""
import datetime as dt
import os
from pathlib import Path

import pytest

AXE = os.environ.get("AXE_JS", "")
pytestmark = pytest.mark.skipif(not (AXE and Path(AXE).exists()), reason="sæt AXE_JS til axe.min.js for at køre tilgængelighedstesten")
KNOWN: set[str] = set()      # tilføj kun midlertidigt og med en forklaring
VIEWS = [("v-today", "I dag"), ("v-week", "Ugen"), ("v-mail", "Beskeder"), ("v-aula", "Feed")]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_no_new_accessibility_violations(make_page, site, scheme):
    page, fake, _ = make_page(now=dt.datetime(2026, 10, 1, 10, 0), fixed=True, goto=False)
    # Med et overblik lavet af reserven, så banneret "Familieassistenten er ikke tilgængelig" også kontrastprøves
    fake.briefing = {"generated": "2026-10-01T08:00:00+02:00", "mode": "day", "method": "offline", "headline_label": "i dag",
                     "period": ["2026-10-01", "2026-10-01"], "ai_fallback": {"reason": "kvote", "since": "2026-10-01T08:00"},
                     "afsnit": [{"titel": "Husk", "punkter": [{"tekst": "Gymnastiktøj", "hvem": ["Hugo"], "kilder": []}]}]}
    page.emulate_media(color_scheme=scheme)
    page.goto(site.url + "/index.html")
    page.wait_for_timeout(500)
    assert page.evaluate("!document.getElementById('aiBanner').classList.contains('hidden')")
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
