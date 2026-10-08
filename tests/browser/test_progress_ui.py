"""Bjælker pr. datatype i statuslinjen, mens serveren henter – i stedet for det snurrende ikon."""
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
    return page.eval_on_selector_all("#status .pg", """els => els.map(e => ({label: e.getAttribute('aria-label'),
        cls: e.className, now: e.getAttribute('aria-valuenow'), text: e.getAttribute('aria-valuetext'),
        width: e.querySelector('.fill').style.width, shown: e.querySelector('.pct').textContent}))""")


def test_one_bar_per_part_in_the_status_line_while_fetching(make_page):
    page, *_ = make_page(fetching(), now=NOW)
    b = {x["label"]: x for x in bars(page)}
    assert list(b) == ["Google", "Aula-kalender", "Beskeder", "Billeder", "Vejr", "Overblik"]
    assert page.locator("#status .spin").count() == 0


def test_a_known_total_shows_a_real_percentage(make_page):
    page, *_ = make_page(fetching(), now=NOW)
    m = {x["label"]: x for x in bars(page)}["Beskeder"]
    assert m["now"] == "30" and m["width"] == "30%" and m["shown"] == "30 %" and m["text"] == "12 af 40"


def test_an_unknown_total_shows_the_busy_animation(make_page):
    page, *_ = make_page(fetching(), now=NOW)
    k = {x["label"]: x for x in bars(page)}["Aula-kalender"]
    assert "ind" in k["cls"] and k["now"] is None and k["text"] == "henter …"


def test_done_waiting_and_failed_look_different(make_page):
    page, *_ = make_page(fetching(), now=NOW)
    b = {x["label"]: x for x in bars(page)}
    assert "done" in b["Google"]["cls"] and b["Google"]["now"] == "100" and b["Google"]["text"] == "færdig"
    assert "wait" in b["Vejr"]["cls"] and b["Vejr"]["text"] == "venter"
    assert "failed" in b["Billeder"]["cls"] and b["Billeder"]["shown"] == "fejlede"
    red = page.eval_on_selector('#status .pg.failed .fill', "e => getComputedStyle(e).backgroundColor")
    blue = page.eval_on_selector('#status .pg.done .fill', "e => getComputedStyle(e).backgroundColor")
    assert red != blue


def test_the_bars_follow_the_fetch_and_stay_a_few_seconds_as_done(make_page):
    fake = fetching([part("aula.messages", "Beskeder", "running", 5, 10)])
    page, *_ = make_page(fake, now=NOW)
    assert bars(page)[0]["shown"] == "50 %"
    fake.progress = {"running": True, "parts": [part("aula.messages", "Beskeder", "running", 8, 10)]}
    page.clock.run_for(1600)
    page.wait_for_timeout(200)
    assert bars(page)[0]["shown"] == "80 %"                            # opdateres uden at tegne hele appen forfra
    fake.running = False
    fake.progress = {"running": False, "parts": [part("aula.messages", "Beskeder", "done", 10, 10)]}
    page.clock.run_for(1600)
    page.wait_for_timeout(300)
    assert bars(page) and bars(page)[0]["text"] == "færdig"            # står et par sekunder som færdig …
    page.clock.run_for(4500)
    page.wait_for_timeout(200)
    assert bars(page) == []                                            # … og forsvinder så


def test_no_bars_when_nothing_is_being_fetched(make_page):
    fake = fetching()
    fake.running, fake.progress = False, {"running": False, "parts": PARTS}
    page, *_ = make_page(fake, now=NOW)
    assert bars(page) == []


def test_the_kiosk_never_shows_the_bars(make_page):
    page, *_ = make_page(fetching(), now=NOW, url="/index.html?kiosk=1")
    loc = page.locator("#status .pg")
    assert loc.count() == 0 or not loc.first.is_visible()


def test_the_bars_fit_on_a_phone(make_page):
    page, *_ = make_page(fetching(), now=NOW, width=390, height=844)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
