"""⚠ ved titlen: aktuelle fejl i AI, Aula, Google, vejr og ntfy – og dialogen med hele fejlbeskeden."""
from __future__ import annotations

import datetime as dt

import pytest

from data import family
from fake_server import FakeServer

pytestmark = pytest.mark.browser

AULA = {"key": "aula.fetch", "area": "aula", "area_title": "Aula", "title": "Aula-login er udløbet",
        "detail": "Aula kræver et nyt MitID-login.", "hint": "Log ind med MitID igen.",
        "action": {"label": "Log ind", "href": "auth"}, "since": "2026-10-01T07:15:00+02:00",
        "last": "2026-10-01T09:45:00+02:00", "count": 4}
AI = {"key": "ai.briefing.day", "area": "ai", "area_title": "Familieassistent (AI)", "title": "Dagens overblik er lavet uden AI",
      "detail": "svaret holdt ikke appens tjek (klokkeslæt, der ikke står i data: 13:00)\n<script>alert(1)</script>",
      "hint": "Svaret ligger i secrets/ai_last_invalid.json.", "action": None,
      "since": "2026-10-01T09:30:00+02:00", "last": "2026-10-01T09:30:00+02:00", "count": 1}


def served(problems, now=None, **kw):
    fake = FakeServer()
    fake.family = family(generated=(now or dt.datetime.now().astimezone()).isoformat())
    fake.server = True
    fake.problems = problems
    return fake


def test_no_problems_means_no_symbol(make_page):
    page, *_ = make_page(served([]))
    assert page.locator("#probBtn").count() == 0


def test_the_symbol_sits_next_to_the_title_and_counts_the_problems(make_page):
    page, *_ = make_page(served([AULA, AI]))
    btn = page.locator(".htitle #probBtn")
    assert btn.count() == 1 and btn.inner_text().strip() == "2"
    assert btn.get_attribute("aria-label") == "2 fejl – vis detaljer"
    t, b = page.locator("#greeting").bounding_box(), btn.bounding_box()
    assert abs((t["y"] + t["height"] / 2) - (b["y"] + b["height"] / 2)) < t["height"] / 2     # på linje med titlen


def test_clicking_opens_a_dialog_with_the_full_error_and_what_to_do(make_page):
    page, *_ = make_page(served([AULA, AI]))
    page.click("#probBtn")
    dlg = page.locator("#probDlg")
    assert dlg.evaluate("d => d.open")
    text = dlg.inner_text()
    assert "Aula-login er udløbet" in text and "Aula kræver et nyt MitID-login." in text and "Log ind med MitID igen." in text
    assert "klokkeslæt, der ikke står i data: 13:00" in text and "ai_last_invalid.json" in text
    assert "4 gange" in text                                           # gentagne fejl tælles
    assert dlg.locator('a.probact[href="auth"]').inner_text() == "Log ind"
    assert page.locator("#probDlg script").count() == 0 and "<script>alert(1)</script>" in text   # vist som tekst, ikke kørt
    page.keyboard.press("Escape")
    assert not dlg.evaluate("d => d.open")


def test_the_dialog_closes_with_the_button(make_page):
    page, *_ = make_page(served([AI]))
    page.click("#probBtn")
    page.click("#probDlg form button")
    assert not page.locator("#probDlg").evaluate("d => d.open")


def test_the_status_line_no_longer_carries_warnings(make_page):
    old = dt.datetime.now().astimezone() - dt.timedelta(hours=3)
    page, *_ = make_page(served([AULA], now=old))
    s = page.inner_text("#status")
    assert "Opdateret" in s and "login" not in s.lower() and "over en time" not in s
    assert page.locator("#status .srvwarn").count() == 0


def test_old_data_is_listed_in_the_dialog(make_page):
    old = dt.datetime.now().astimezone() - dt.timedelta(hours=3)
    page, *_ = make_page(served([], now=old))
    page.click("#probBtn")
    assert "Data er over en time gamle" in page.inner_text("#probDlg")


def test_the_symbol_goes_away_when_the_server_reports_no_problems(make_page):
    fake = served([AULA])
    page, *_ = make_page(fake)
    assert page.locator("#probBtn").count() == 1
    fake.problems = []
    page.evaluate("loadServer().then(renderChrome)")
    page.wait_for_timeout(400)
    assert page.locator("#probBtn").count() == 0


def test_the_kiosk_never_shows_the_symbol(make_page):
    page, *_ = make_page(served([AULA, AI]), url="/index.html?kiosk=1")
    btn = page.locator("#probBtn")
    assert btn.count() == 0 or not btn.is_visible()


def test_the_symbol_is_reachable_by_keyboard(make_page):
    page, *_ = make_page(served([AI]))
    page.focus("#probBtn")
    page.keyboard.press("Enter")
    assert page.locator("#probDlg").evaluate("d => d.open")
