"""Tilgængelighed med axe-core. Valgfri: kør `npm install axe-core` og sæt AXE_JS=sti/til/node_modules/axe-core/axe.min.js.

Ingen kendte fund er tilladt længere (KNOWN er tom). Testen kører i dagtemaet (med vejr) og i nattemaet (kl. 22).
"""
import datetime as dt
import os
from pathlib import Path

import pytest

AXE = os.environ.get("AXE_JS", "")
pytestmark = pytest.mark.skipif(not (AXE and Path(AXE).exists()), reason="sæt AXE_JS til axe.min.js for at køre tilgængelighedstesten")
KNOWN: set[str] = set()      # tilføj kun midlertidigt og med en forklaring
VIEWS = [("v-today", "I dag"), ("v-week", "Ugen"), ("v-mail", "Beskeder"), ("v-aula", "Feed")]


@pytest.mark.parametrize("hour", [10, 22])
def test_no_new_accessibility_violations(make_page, site, hour):
    page, fake, _ = make_page(now=dt.datetime(2026, 10, 1, hour, 0), fixed=True, goto=False)
    day = "2026-10-02" if hour >= 18 else "2026-10-01"                   # efter kl. 18 handler I dag om i morgen
    # Med et overblik lavet af reserven, så noten "Familieassistenten er ikke tilgængelig" også kontrastprøves
    fake.briefing = {"generated": "2026-10-01T08:00:00+02:00", "mode": "day", "method": "offline", "headline_label": "i dag",
                     "period": [day, day], "ai_fallback": {"reason": "kvote", "since": "2026-10-01T08:00"},
                     "afsnit": [{"titel": "Husk", "punkter": [{"tekst": "Gymnastiktøj", "hvem": ["Hugo"], "kilder": []}]}]}
    page.goto(site.url + "/index.html")
    page.wait_for_timeout(500)
    # Med vejr, så himlen (regn: den mørkeste) også kontrastprøves
    page.evaluate("state.data.weather={kilde:'MET Norway',dage:[0,1].map(i=>({dato:ymd(addDays(new Date(),i)),min:8,max:11,ikon:'🌧️',tekst:'regn',raad:[],"
                  "timer:[10,11,12].map(kl=>({kl,ikon:'🌧️',temp:9,regn:1.2,vind:4}))}))}; render()")
    assert page.locator("#briefDay .ainote").count() == 1                 # noten under overblikket kontrastprøves også
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


@pytest.mark.parametrize("hour", [10, 22])
def test_the_warning_symbol_and_its_dialog_are_accessible(make_page, site, hour):
    page, fake, _ = make_page(now=dt.datetime(2026, 10, 1, hour, 0), fixed=True, goto=False)
    page.goto(site.url + "/index.html")
    fake.use_demo(page)
    fake.problems = [{"key": "aula.fetch", "area": "aula", "area_title": "Aula", "title": "Aula-login er udløbet",
                      "detail": "Aula kræver et nyt MitID-login.", "hint": "Log ind med MitID igen.",
                      "action": {"label": "Log ind", "href": "auth"}, "since": "2026-10-01T07:15:00+02:00",
                      "last": "2026-10-01T09:45:00+02:00", "count": 3}]
    page.reload()
    page.wait_for_timeout(500)
    page.add_script_tag(content=Path(AXE).read_text())
    rules = "{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa','best-practice']}}"
    closed = page.evaluate(f"axe.run(document.querySelector('header'),{rules}).then(r=>r.violations.map(v=>v.id))")
    page.click("#probBtn")
    opened = page.evaluate(f"axe.run(document.getElementById('probDlg'),{rules}).then(r=>r.violations.map(v=>v.id))")
    assert closed == [] and opened == [], (closed, opened)
