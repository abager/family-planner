"""Layout: feedet er én kolonne uanset skærm, Vigtig info har ingen skjul/vis-knap, intet løber ud over siden."""
import datetime as dt

import pytest

NOW = dt.datetime(2026, 10, 1, 10, 0)
VIEWS = [("v-today", "I dag"), ("v-week", "Ugen"), ("v-mail", "Beskeder"), ("v-aula", "Feed")]


@pytest.mark.parametrize("w,h,expected", [(390, 844, 358), (810, 1080, 560), (1080, 810, 560), (1440, 900, 720), (1920, 1080, 860)])
def test_feed_is_one_column_and_wider_on_big_screens(make_page, w, h, expected):
    page, *_ = make_page(now=NOW, fixed=True, width=w, height=h)
    page.click("#v-aula")
    page.wait_for_timeout(300)
    m = page.evaluate("""(()=>{const cs=[...document.querySelectorAll('#feed .card')];
        return {n:cs.length,fw:Math.round(document.querySelector('.feedwrap').getBoundingClientRect().width),
                lefts:[...new Set(cs.map(c=>Math.round(c.getBoundingClientRect().left)))],ph:Math.round(document.querySelector('#feed .photos button').getBoundingClientRect().height),vh:innerHeight}})()""")
    assert m["n"] >= 3 and len(m["lefts"]) == 1                                  # alle kort står under hinanden
    assert m["fw"] == expected
    assert m["ph"] <= max(760, 0.7 * m["vh"]) + 2                                # et billede fylder aldrig mere end ca. 70 % af skærmhøjden


def test_important_info_has_no_show_hide_button_for_regular_lessons(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    assert page.locator("#planMore").count() == 0 and page.locator("#planToday .more").count() == 0
    assert "almindelig undervisning" not in page.inner_text("#planToday").lower()
    assert page.evaluate("typeof state.showAllPlan") == "undefined"


@pytest.mark.parametrize("w,h", [(390, 844), (820, 1180), (1280, 800), (1920, 1080)])
def test_no_view_scrolls_sideways(make_page, w, h):
    page, *_ = make_page(now=NOW, fixed=True, width=w, height=h)
    for vid, name in VIEWS:
        page.click("#" + vid)
        page.wait_for_timeout(200)
        assert page.evaluate("document.documentElement.scrollWidth-document.documentElement.clientWidth") == 0, f"{name} ved {w} px"


def test_message_list_uses_plain_list_semantics(make_page):
    """Axe fandt en gang listbox/option med <li> imellem. En almindelig liste af knapper med aria-current er korrekt."""
    page, *_ = make_page(now=NOW, fixed=True)
    page.click("#v-mail")
    page.wait_for_timeout(300)
    assert page.locator('#mitems[role="listbox"], #mitems [role="option"]').count() == 0
    assert page.locator('#mitems .mrow[aria-current="true"]').count() == 1


def test_google_outage_is_shown_in_the_header(make_page):
    from data import family
    from fake_server import FakeServer
    fake = FakeServer()
    fake.family = family(health={"google": {"ok": False, "failed": ["Familiekalender"], "last_ok": "2026-10-01T08:15:00+02:00"}, "aula": {"state": "ok"}})
    page, *_ = make_page(fake)
    assert "Google Kalender kunne ikke hentes" in page.inner_text("#status")
