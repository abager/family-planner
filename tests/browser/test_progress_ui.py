"""Ring ved tandhjulet, mens serveren henter (samlet fremdrift), og bjælker pr. datatype i et lille vindue ved tryk."""
from __future__ import annotations

import datetime as dt

import pytest

from data import family
from fake_server import FakeServer

pytestmark = pytest.mark.browser
NOW = dt.datetime(2026, 10, 1, 10, 0).astimezone()


def part(key, label, state="running", done=None, total=None):
    return {"key": key, "label": label, "state": state, "done": done, "total": total}


PARTS = [part("google", "Google", "done", 2, 2), part("aula.kalender", "Aula-kalender"),
         part("aula.messages", "Beskeder", "running", 12, 40), part("aula.albums", "Billeder", "failed"),
         part("vejr", "Vejr", "waiting"), part("overblik", "Overblik", "running", 1, 2)]


def fetching(parts=PARTS):
    fake = FakeServer()
    fake.family = family(generated=NOW.isoformat())
    fake.server, fake.running = True, True
    fake.progress = {"running": True, "parts": parts}
    return fake


def bars(page):
    return page.eval_on_selector_all("#progPop .pg", """els => els.map(e => ({label: e.getAttribute('aria-label'),
        cls: e.className, now: e.getAttribute('aria-valuenow'), text: e.getAttribute('aria-valuetext'),
        width: e.querySelector('.fill').style.width, shown: e.querySelector('.pct').textContent}))""")


def ring(page):
    return page.evaluate("""(()=>{const b=document.getElementById('progBtn'); if(!b) return null;
        const v=b.querySelector('.val'), c=parseFloat(v.getAttribute('stroke-dasharray'));
        return {label:b.getAttribute('aria-label'), cls:b.className, expanded:b.getAttribute('aria-expanded'),
                pct:Math.round(100*(1-parseFloat(v.getAttribute('stroke-dashoffset'))/c)), visible:b.checkVisibility()};})()""")


def open_pop(page):
    page.click("#progBtn")
    assert page.locator("#progPop").is_visible()


def test_a_ring_appears_next_to_the_gear_while_fetching_and_nothing_else_moves(make_page):
    fake = fetching()
    fake.running, fake.progress = False, {"running": False, "parts": []}
    page, *_ = make_page(fake, now=NOW)
    before = page.evaluate("[...document.querySelectorAll('header > *, main, .tabs')].map(e=>JSON.stringify(e.getBoundingClientRect()))")
    assert ring(page) is None
    page.evaluate("state.server.running=true; state.server.progress={running:true,parts:[{key:'google',label:'Google',state:'running',done:1,total:2}]}; renderProgress()")
    r, g = page.locator("#progBtn").bounding_box(), page.locator("#menuBtn").bounding_box()
    assert r["x"] + r["width"] <= g["x"] + 1 and abs((r["y"] + r["height"] / 2) - (g["y"] + g["height"] / 2)) < 8
    after = page.evaluate("[...document.querySelectorAll('header > *:not(#progWrap), main, .tabs')].map(e=>JSON.stringify(e.getBoundingClientRect()))")
    assert [b for b in before if b in after] == after                  # ingen andre elementer har flyttet sig
    assert page.locator("#status .pg, #status .spin").count() == 0     # intet i statuslinjen


def test_the_ring_shows_the_weighted_overall_progress(make_page):
    page, *_ = make_page(fetching(), now=NOW)
    # done 1 + kalender 0.5 + beskeder 0.3 + billeder (fejlet) 1 + vejr 0 + overblik 0.5 = 3.3 / 6 = 55 %
    assert ring(page)["pct"] == 55 and "55 %" in ring(page)["label"]


def test_a_failed_part_turns_the_ring_red(make_page):
    page, *_ = make_page(fetching(), now=NOW)
    assert "failed" in ring(page)["cls"] and "noget fejlede" in ring(page)["label"]
    ok, *_ = make_page(fetching([part("google", "Google", "running", 1, 2)]), now=NOW)
    assert "failed" not in ring(ok)["cls"]


def test_tapping_the_ring_shows_one_bar_per_part(make_page):
    page, *_ = make_page(fetching(), now=NOW)
    assert not page.locator("#progPop").is_visible()
    open_pop(page)
    assert ring(page)["expanded"] == "true"
    assert [b["label"] for b in bars(page)] == ["Google", "Aula-kalender", "Beskeder", "Billeder", "Vejr", "Overblik"]


def test_a_known_total_shows_a_real_percentage(make_page):
    page, *_ = make_page(fetching(), now=NOW)
    open_pop(page)
    m = {x["label"]: x for x in bars(page)}["Beskeder"]
    assert m["now"] == "30" and m["width"] == "30%" and m["shown"] == "30 %" and m["text"] == "12 af 40"


def test_an_unknown_total_shows_the_busy_animation(make_page):
    page, *_ = make_page(fetching(), now=NOW)
    open_pop(page)
    k = {x["label"]: x for x in bars(page)}["Aula-kalender"]
    assert "ind" in k["cls"] and k["now"] is None and k["text"] == "henter …"


def test_done_waiting_and_failed_look_different(make_page):
    page, *_ = make_page(fetching(), now=NOW)
    open_pop(page)
    b = {x["label"]: x for x in bars(page)}
    assert "done" in b["Google"]["cls"] and b["Google"]["text"] == "færdig"
    assert "wait" in b["Vejr"]["cls"] and "failed" in b["Billeder"]["cls"] and b["Billeder"]["shown"] == "fejlede"


def test_the_popup_closes_with_escape_and_outside_taps(make_page):
    page, *_ = make_page(fetching(), now=NOW)
    open_pop(page)
    page.keyboard.press("Escape")
    assert not page.locator("#progPop").is_visible()
    open_pop(page)
    page.mouse.click(20, 400)
    assert not page.locator("#progPop").is_visible()


def test_the_open_popup_follows_the_fetch_and_closes_a_few_seconds_after_it_ends(make_page):
    fake = fetching([part("aula.messages", "Beskeder", "running", 5, 10)])
    page, *_ = make_page(fake, now=NOW)
    open_pop(page)
    assert bars(page)[0]["shown"] == "50 %" and ring(page)["pct"] == 50
    fake.progress = {"running": True, "parts": [part("aula.messages", "Beskeder", "running", 8, 10)]}
    page.clock.run_for(1600)
    page.wait_for_timeout(200)
    assert bars(page)[0]["shown"] == "80 %" and page.locator("#progPop").is_visible()     # forbliver åben
    fake.running = False
    fake.progress = {"running": False, "parts": [part("aula.messages", "Beskeder", "done", 10, 10)]}
    page.clock.run_for(1600)
    page.wait_for_timeout(300)
    assert "finished" in ring(page)["cls"] and bars(page)[0]["text"] == "færdig"           # ✓ et par sekunder …
    page.clock.run_for(4500)
    page.wait_for_timeout(200)
    assert ring(page) is None and page.locator("#progPop").count() == 0                    # … så er begge væk


def test_no_ring_when_nothing_is_being_fetched(make_page):
    fake = fetching()
    fake.running, fake.progress = False, {"running": False, "parts": PARTS}
    page, *_ = make_page(fake, now=NOW)
    assert ring(page) is None


def test_the_kiosk_never_shows_the_ring(make_page):
    page, *_ = make_page(fetching(), now=NOW, url="/index.html?kiosk=1")
    assert ring(page) is None or not ring(page)["visible"]


@pytest.mark.parametrize("w,h", [(390, 844), (820, 1180), (1280, 900)])
def test_the_ring_never_covers_the_title_or_the_warning(make_page, w, h):
    from test_problems_ui import AULA
    fake = fetching()
    fake.problems = [AULA]
    page, *_ = make_page(fake, now=NOW, width=w, height=h)
    r = page.locator("#progBtn").bounding_box()
    for sel in ("#greeting", "#probBtn"):
        b = page.locator(sel).bounding_box()
        assert b["x"] + b["width"] <= r["x"] or b["y"] + b["height"] <= r["y"] or r["y"] + r["height"] <= b["y"], sel
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
