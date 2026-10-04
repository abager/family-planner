"""Banneret "Familieassistenten er ikke tilgængelig" øverst – i appen og på kioskskærmen – når overblikket ikke kom fra sprogmodellen."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

TZ = ZoneInfo("Europe/Copenhagen")
NOW = dt.datetime(2026, 10, 1, 10, 0, tzinfo=TZ)


def brief(**extra):
    return {"generated": "2026-10-01T08:15:00+02:00", "mode": "day", "method": "ai", "provider": "gemini",
            "headline_label": "i dag", "period": ["2026-10-01", "2026-10-01"],
            "afsnit": [{"titel": "Husk", "punkter": [{"tekst": "Gymnastiktøj", "hvem": ["Hugo"], "kilder": []}]}], **extra}


def banner(page):
    return page.evaluate("""(()=>{const b=document.getElementById('aiBanner'), r=b.getBoundingClientRect();
        return {shown:!b.classList.contains('hidden') && r.height>0, text:b.textContent, top:r.top,
                role:b.getAttribute('role'), first:b===document.querySelector('.wrap').firstElementChild};})()""")


@pytest.fixture
def open_with(make_page, site):
    """Åbn appen kl. 10 med de givne overblik (dag/uge), evt. direkte i kioskvisning."""
    def go(day=None, week=None, kiosk=False):
        page, fake, _ = make_page(now=NOW, fixed=True, goto=False)
        fake.briefing, fake.briefing_week = day, week
        page.goto(site.url + ("/index.html?kiosk=1" if kiosk else "/index.html"))
        page.wait_for_timeout(600)
        return page
    return go


def test_no_banner_when_the_ai_wrote_the_overview(open_with):
    b = banner(open_with(day=brief()))
    assert not b["shown"]


def test_no_banner_in_plain_offline_mode(open_with):
    b = banner(open_with(day=brief(method="offline")))
    assert not b["shown"]


def test_banner_when_the_rules_made_the_overview(open_with):
    page = open_with(day=brief(method="offline", ai_fallback={"reason": "kvote", "since": "2026-10-01T08:00"}))
    b = banner(page)
    assert b["shown"] and b["first"] and b["role"] == "status"
    assert b["text"] == "Familieassistenten er ikke tilgængelig – overblikket er lavet ud fra faste regler."
    assert "kvote" not in b["text"]                                    # den tekniske grund vises ikke
    assert page.evaluate("document.querySelector('#briefDay .bfoot').textContent").startswith("Samlet af familieassistenten ud fra faste regler")


def test_banner_says_the_kept_ai_overview_may_be_out_of_date(open_with):
    page = open_with(day=brief(ai_stale={"reason": "pause", "since": "2026-10-01T09:30"}))
    b = banner(page)
    assert b["shown"] and b["text"] == "Familieassistenten er ikke tilgængelig – overblikket er fra kl. 08.15 og er måske ikke opdateret."
    assert page.evaluate("document.querySelector('#briefDay .bfoot').textContent").startswith("Skrevet af familieassistenten")


def test_a_fallback_for_another_day_shows_no_banner(open_with):
    other = brief(method="offline", period=["2026-09-30", "2026-09-30"], ai_fallback={"reason": "kvote", "since": "x"})
    assert not banner(open_with(day=other))["shown"]


def test_a_fallback_in_the_week_overview_also_shows_the_banner(open_with):
    week = brief(mode="week", method="offline", period=["2026-09-28", "2026-10-04"], ai_fallback={"reason": "netvaerk", "since": "x"})
    assert banner(open_with(day=brief(), week=week))["shown"]


def test_the_banner_is_at_the_top_of_the_kiosk_screen_too(open_with):
    page = open_with(day=brief(method="offline", ai_fallback={"reason": "kvote", "since": "x"}), kiosk=True)
    assert page.evaluate("state.kiosk")
    b = banner(page)
    assert b["shown"] and b["top"] < 60
    k = page.evaluate("document.getElementById('kioskView').getBoundingClientRect().top")
    assert k > b["top"]                                                # over kioskens indhold
    assert page.evaluate("document.documentElement.scrollHeight<=innerHeight+1")    # kiosken kan stadig være på én skærm
