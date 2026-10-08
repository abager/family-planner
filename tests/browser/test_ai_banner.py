"""Noten "Familieassistenten er ikke tilgængelig" lige under overblikkets tekst – én pr. overblik, i appen og på
kioskskærmen – når overblikket ikke kom fra sprogmodellen eller måske er forældet."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

TZ = ZoneInfo("Europe/Copenhagen")
NOW = dt.datetime(2026, 10, 1, 10, 0, tzinfo=TZ)
FALLBACK = "Familieassistenten er ikke tilgængelig – overblikket er lavet ud fra faste regler."
STALE = "Familieassistenten er ikke tilgængelig – overblikket er fra kl. 08.15 og er måske ikke opdateret."


def brief(**extra):
    return {"generated": "2026-10-01T08:15:00+02:00", "mode": "day", "method": "ai", "provider": "gemini",
            "headline_label": "i dag", "period": ["2026-10-01", "2026-10-01"],
            "afsnit": [{"titel": "Husk", "punkter": [{"tekst": "Gymnastiktøj", "hvem": ["Hugo"], "kilder": []}]}], **extra}


def week(**extra):
    return brief(mode="week", headline_label="denne uge", period=["2026-09-28", "2026-10-04"], **extra)


def note(page, panel):
    return page.evaluate(f"""(()=>{{const n=document.querySelector('#{panel} .ainote');
        if(!n) return null; const body=document.querySelector('#{panel} .story,#{panel} .bsecs,#{panel} .empty'),
        foot=document.querySelector('#{panel} .bfoot');
        return {{text:n.textContent, belowText:body.getBoundingClientRect().bottom<=n.getBoundingClientRect().top+1,
                 aboveFoot:n.getBoundingClientRect().bottom<=foot.getBoundingClientRect().top+1,
                 small:parseFloat(getComputedStyle(n).fontSize)<parseFloat(getComputedStyle(body).fontSize||16)+0.01}};}})()""")


@pytest.fixture
def open_with(make_page, site):
    """Åbn appen kl. 10 med de givne overblik (dag/uge), evt. direkte i kioskvisning."""
    def go(day=None, week=None, kiosk=False, width=1280, height=900):
        page, fake, _ = make_page(now=NOW, fixed=True, goto=False, width=width, height=height)
        fake.briefing, fake.briefing_week = day, week
        page.goto(site.url + ("/index.html?kiosk=1" if kiosk else "/index.html"))
        page.wait_for_timeout(600)
        return page
    return go


def test_there_is_no_banner_at_the_top_any_more(open_with):
    page = open_with(day=brief(method="offline", ai_fallback={"reason": "kvote", "since": "x"}))
    assert page.locator("#aiBanner, .aibanner").count() == 0


def test_no_note_when_the_ai_wrote_the_overview(open_with):
    assert note(open_with(day=brief()), "briefDay") is None


def test_no_note_in_plain_offline_mode(open_with):
    assert note(open_with(day=brief(method="offline")), "briefDay") is None


def test_a_subtle_note_below_the_text_when_the_rules_made_the_overview(open_with):
    page = open_with(day=brief(method="offline", ai_fallback={"reason": "kvote", "since": "2026-10-01T08:00"}))
    n = note(page, "briefDay")
    assert n["text"] == FALLBACK and n["belowText"] and n["aboveFoot"] and n["small"]
    assert "kvote" not in n["text"]                                     # den tekniske grund står under ⚠ ved titlen


def test_the_note_says_the_kept_ai_overview_may_be_out_of_date(open_with):
    page = open_with(day=brief(ai_stale={"reason": "pause", "since": "2026-10-01T09:30"}))
    assert note(page, "briefDay")["text"] == STALE


def test_each_overview_gets_its_own_note(open_with):
    fb = {"reason": "netvaerk", "since": "x"}
    page = open_with(day=brief(), week=week(method="offline", ai_fallback=fb))
    assert note(page, "briefDay") is None                               # dagens overblik er skrevet af AI
    page.click("#v-week")
    page.wait_for_timeout(300)
    assert note(page, "briefWeek")["text"] == FALLBACK


def test_the_day_note_and_the_week_note_can_differ(open_with):
    page = open_with(day=brief(ai_stale={"reason": "pause", "since": "x"}), week=week(method="offline", ai_fallback={"reason": "kvote", "since": "x"}))
    assert note(page, "briefDay")["text"] == STALE
    page.click("#v-week")
    page.wait_for_timeout(300)
    assert note(page, "briefWeek")["text"] == FALLBACK


@pytest.mark.parametrize("w,h", [(1180, 820), (1024, 768), (1366, 1024), (1920, 1080), (820, 1180)])
def test_on_the_kiosk_the_note_sits_below_the_overview_and_the_screen_still_fits(open_with, w, h):
    long = ["Torsdag er en lang dag med mange aftaler for hele familien, og der er meget at huske. " * 6]
    page = open_with(day=brief(method="offline", fortaelling=long, ai_fallback={"reason": "kvote", "since": "x"}),
                     kiosk=True, width=w, height=h)
    assert page.evaluate("state.kiosk")
    n = page.locator("#kAiNote")
    assert n.is_visible() and n.inner_text() == FALLBACK
    t, b = page.locator("#kBriefText").bounding_box(), n.bounding_box()
    assert b["y"] >= t["y"] + t["height"] - 1                            # under teksten
    assert page.evaluate("document.documentElement.scrollHeight<=innerHeight+1")    # kiosken er stadig på én skærm


def test_no_note_on_the_kiosk_when_the_ai_wrote_it(open_with):
    page = open_with(day=brief(), kiosk=True)
    assert not page.locator("#kAiNote").is_visible()
