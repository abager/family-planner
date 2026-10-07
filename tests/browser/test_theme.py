"""Temaet gælder hele appen (ikke kun kiosken): himlen for fokusdagens vejr om dagen, mørkt kl. 21–06. Enhedens lyse/mørke
tilstand bruges ikke, og en uge uden prognose beholder dagens himmel. Torsdag 1. oktober 2026 er "i dag"."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

TZ = ZoneInfo("Europe/Copenhagen")
WX = """state.data.weather={kilde:'MET Norway',dage:[
    {dato:ymd(new Date()),min:8,max:11,ikon:'🌧️',tekst:'regn',raad:[],timer:[{kl:11,ikon:'🌧️',temp:9,regn:1.2,vind:4}]},
    {dato:ymd(addDays(new Date(),1)),min:6,max:12,ikon:'☀️',tekst:'sol',raad:[],timer:[]}]}; render()"""


def at(h, m=0):
    return dt.datetime(2026, 10, 1, h, m, tzinfo=TZ)


def themed(make_page, now, scheme="light", **kw):
    page, fake, _ = make_page(now=now, fixed=True, **kw)
    page.emulate_media(color_scheme=scheme)
    page.evaluate(WX)
    return page


def sky(page):
    return page.evaluate("document.body.dataset.sky || null")


def night(page):
    return page.evaluate("document.body.classList.contains('night')")


@pytest.mark.parametrize("view", ["v-today", "v-week", "v-mail", "v-aula"])
def test_every_view_has_the_sky_of_the_focus_day(make_page, view):
    page = themed(make_page, at(10))
    page.click("#" + view)
    assert sky(page) == "regn" and not night(page)
    assert "gradient" in page.evaluate("getComputedStyle(document.body).backgroundImage")


def test_after_the_evening_hour_the_sky_is_tomorrows(make_page):
    assert sky(themed(make_page, at(18, 30))) == "sol"


@pytest.mark.parametrize("h,dark", [(20, False), (21, True), (23, True), (5, True), (6, False)])
def test_from_21_to_6_the_app_is_dark(make_page, h, dark):
    page = themed(make_page, at(h, 30))
    assert night(page) is dark
    assert (page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(15, 20, 29)") is dark


def test_the_device_dark_mode_does_not_change_the_theme(make_page):
    page = themed(make_page, at(10), scheme="dark")
    assert not night(page) and sky(page) == "regn"
    assert page.evaluate("getComputedStyle(document.body).getPropertyValue('--sky1').trim()").upper() == "#AFC0CE"


def test_a_week_without_forecast_keeps_todays_sky(make_page):
    page = themed(make_page, at(10))
    page.click("#v-week")
    page.click("#next")
    page.click("#next")
    assert sky(page) == "regn"


def test_the_theme_switches_to_night_while_the_app_is_open(make_page):
    page, *_ = make_page(now=at(20, 59), fixed=False)
    page.evaluate(WX)
    assert not night(page)
    page.clock.run_for(2 * 60 * 1000)                                         # kl. 21.01
    assert night(page)


def test_the_status_bar_follows_the_top_of_the_screen(make_page):
    page = themed(make_page, at(10))
    assert page.eval_on_selector_all('meta[name="theme-color"]', "ms => ms.map(m => m.content.toUpperCase())") == ["#AFC0CE"]
    page = themed(make_page, at(22))
    assert page.eval_on_selector_all('meta[name="theme-color"]', "ms => ms.map(m => m.content.toUpperCase())") == ["#0F141D"]


def test_week_day_headers_show_the_forecast_and_past_days_are_dimmed(make_page):
    page = themed(make_page, at(10))
    page.click("#v-week")
    heads = page.locator("#board .dh")
    assert "8–11°" in heads.nth(4).inner_text()                              # kolonne 0 er "Uge 40", torsdag er nr. 4
    assert page.locator("#board .dh.past").count() == 3
    assert page.locator("#board .dh.is-today").count() == 1


def test_a_week_row_with_nothing_collapses_to_one_line(make_page):
    page, *_ = make_page(now=at(10), fixed=True)
    page.click("#v-week")
    page.click("#next")
    page.click("#next")                                                       # demodata har intet så langt ude
    assert page.locator("#board .lane.empty").count() == page.locator("#board .lane").count()
    assert "Intet denne uge" in page.inner_text("#board")
