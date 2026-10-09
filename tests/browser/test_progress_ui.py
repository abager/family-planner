"""Ring ved tandhjulet (altid til stede; under en hentning den samlede fremdrift) og bjælker pr. datatype med tid i et lille vindue ved tryk."""
from __future__ import annotations

import datetime as dt

import pytest

from data import family
from fake_server import FakeServer

pytestmark = pytest.mark.browser
NOW = dt.datetime(2026, 10, 1, 10, 0).astimezone()


def part(key, label, state="running", done=None, total=None, ms=None):
    return {"key": key, "label": label, "state": state, "done": done, "total": total, "ms": ms}


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
    before = page.evaluate("[...document.querySelectorAll('header > *:not(#progWrap), main, .tabs')].map(e=>JSON.stringify(e.getBoundingClientRect()))")
    assert "idle" in ring(page)["cls"] and ring(page)["label"].startswith("Ingen hentning endnu")
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
    fake.progress = {"running": False, "finished": NOW.isoformat(), "ms": 52300,
                     "parts": [dict(part("aula.messages", "Beskeder", "done", 10, 10), ms=4312)]}
    page.clock.run_for(1600)
    page.wait_for_timeout(300)
    assert "finished" in ring(page)["cls"] and bars(page)[0]["text"] == "færdig på 4,3 s"   # ✓ et par sekunder …
    page.clock.run_for(4500)
    page.wait_for_timeout(200)
    assert not page.locator("#progPop").is_visible()                                       # … så lukker vinduet
    r = ring(page)
    assert r is not None and "idle" in r["cls"] and r["pct"] == 100                         # og ringen bliver


def test_after_a_fetch_the_ring_stays_and_shows_the_last_fetch_with_times(make_page):
    fake = fetching()
    fake.running = False
    fake.progress = {"running": False, "finished": "2026-10-01T09:58:00+02:00", "ms": 52300, "parts": [
        dict(part("google", "Google", "done", 2, 2), ms=820), dict(part("aula.messages", "Beskeder", "done", 40, 40), ms=45210),
        dict(part("aula.albums", "Billeder", "failed"), ms=900000)]}
    page, *_ = make_page(fake, now=NOW)
    r = ring(page)
    assert "idle" in r["cls"] and "failed" in r["cls"] and "Seneste hentning kl. 09.58 (52,3 s)" in r["label"]
    open_pop(page)
    b = {x["label"]: x for x in bars(page)}
    times = page.eval_on_selector_all("#progPop .pg .ms", "els => els.map(e => e.textContent)")
    assert times == ["820 ms", "45,2 s", "900,0 s"]
    assert b["Google"]["text"] == "færdig på 820 ms" and b["Billeder"]["text"] == "fejlede på 900,0 s"
    assert "Seneste hentning kl. 09.58 · 52,3 s" in page.inner_text("#progPop .progtitle")


def test_times_are_only_shown_for_finished_or_failed_parts(make_page):
    page, *_ = make_page(fetching([dict(part("aula.messages", "Beskeder", "running", 5, 10), ms=None),
                                   dict(part("google", "Google", "done", 1, 1), ms=300)]), now=NOW)
    open_pop(page)
    assert page.eval_on_selector_all("#progPop .pg .ms", "els => els.map(e => e.textContent)") == ["300 ms"]


def test_without_any_fetch_yet_the_popup_says_so(make_page):
    fake = fetching()
    fake.running, fake.progress = False, {"running": False, "parts": []}
    page, *_ = make_page(fake, now=NOW)
    open_pop(page)
    assert "ikke hentet data" in page.inner_text("#progPop")


def test_the_full_fetch_button_starts_a_full_fetch_and_the_popup_follows_it(make_page):
    fake = fetching()
    fake.running = False
    fake.progress = {"running": False, "finished": NOW.isoformat(), "ms": 1000, "parts": [dict(part("google", "Google", "done", 1, 1), ms=500)]}

    def start(f):
        f.running = True
        f.progress = {"running": True, "parts": [part("google", "Google", "running", 0, 1), part("aula.messages", "Beskeder")]}
    fake.on_refresh = start
    page, *_ = make_page(fake, now=NOW)
    open_pop(page)
    btn = page.locator("#progFull")
    assert btn.inner_text() == "Tving fuld hentning" and btn.is_enabled()
    btn.click()
    page.clock.run_for(600)
    page.wait_for_timeout(300)
    assert fake.refreshes == [{"full": True, "csrf": True}]
    assert page.locator("#progPop").is_visible() and [b["label"] for b in bars(page)] == ["Google", "Beskeder"]
    assert not page.locator("#progFull").is_enabled() and page.locator("#progFull").inner_text() == "Henter …"


def test_the_full_fetch_button_is_disabled_while_fetching(make_page):
    page, *_ = make_page(fetching(), now=NOW)
    open_pop(page)
    assert not page.locator("#progFull").is_enabled()


def test_no_ring_without_a_server(make_page):
    fake = fetching()
    fake.server = False
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
