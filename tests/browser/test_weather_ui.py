"""Vejret på kioskskærmen og knappen "Brug min placering som hjem". Simuleret server og placering."""
import datetime as dt

NOW = dt.datetime(2026, 10, 1, 7, 30)
DAY = {"dato": "2026-10-01", "ugedag": "torsdag", "min": 8, "max": 11, "regn": "regn", "regn_hvornaar": "om eftermiddagen",
       "himmel": "overskyet", "vind": None, "frost": False, "ikon": "🌧",
       "tekst": "8–11°, regn om eftermiddagen", "raad": ["regntøj og gummistøvler"]}


def kiosk(make_page, site, weather=None, briefing=None):
    page, fake, _ = make_page(now=NOW, fixed=True, goto=False)
    page.goto(site.url + "/index.html")
    fake.use_demo(page, weather=weather)
    fake.briefing = briefing
    page.goto(site.url + "/index.html?kiosk=1")
    page.wait_for_timeout(600)
    return page, fake


def test_the_kiosk_shows_todays_weather_with_advice(make_page, site):
    page, _ = kiosk(make_page, site, weather={"kilde": "DMI", "dage": [DAY]})
    t = page.inner_text("#kWeather")
    assert "8–11°, regn om eftermiddagen" in t and "regntøj og gummistøvler" in t and "🌧" in t
    assert page.evaluate("document.documentElement.scrollHeight<=innerHeight+1")


def test_no_weather_line_without_weather_or_for_another_day(make_page, site):
    page, _ = kiosk(make_page, site, weather=None)
    assert page.inner_text("#kWeather").strip() == ""
    page, _ = kiosk(make_page, site, weather={"kilde": "DMI", "dage": [{**DAY, "dato": "2026-10-05"}]})
    assert page.inner_text("#kWeather").strip() == ""


def test_the_weather_section_is_not_repeated_in_the_kiosk_remember_list(make_page, site):
    b = {"generated": "2026-10-01T07:00:00+02:00", "mode": "day", "method": "offline", "headline_label": "i dag",
         "period": ["2026-10-01", "2026-10-01"],
         "afsnit": [{"titel": "Vejr", "punkter": [{"tekst": "8–11°, regn – regntøj", "hvem": [], "kilder": ["Vejr (DMI)"]}]},
                    {"titel": "Husk", "punkter": [{"tekst": "Gymnastiktøj", "hvem": ["Hugo"], "kilder": []}]}]}
    page, _ = kiosk(make_page, site, weather={"kilde": "DMI", "dage": [DAY]}, briefing=b)
    remember = page.inner_text("#kRemember")
    assert "Gymnastiktøj" in remember and "regntøj" not in remember


def open_home(make_page, site, geo=None):
    page, fake, _ = make_page(now=NOW, fixed=True, goto=False)
    if geo:
        page.context.grant_permissions(["geolocation"])
        page.context.set_geolocation({"latitude": geo[0], "longitude": geo[1]})
    page.goto(site.url + "/index.html")
    fake.use_demo(page)
    page.reload()
    page.wait_for_timeout(500)
    page.click("#homeBtn")
    page.wait_for_function("!document.getElementById('homeInfo').textContent.includes('Henter')")
    return page, fake


def test_setting_home_from_the_browser_sends_the_position_and_shows_it_rounded(make_page, site):
    page, fake = open_home(make_page, site, geo=(55.676098, 12.568337))
    page.wait_for_function("document.getElementById('homeInfo').textContent.includes('ikke sat')")
    page.click("#homeUse")
    page.wait_for_function("document.getElementById('homeInfo').textContent.includes('55,68')")
    info = page.inner_text("#homeInfo")
    assert "55,68 N" in info and "12,57 Ø" in info and "Vejret hentes nu" in info
    assert page.get_attribute("#homeInfo a", "href").startswith("https://www.openstreetmap.org/")
    assert fake.posts("/api/home-location")[0]["lat"] == 55.676098      # serveren afrunder


def test_a_position_outside_denmark_shows_the_servers_reason(make_page, site):
    page, _ = open_home(make_page, site, geo=(48.85, 2.35))
    page.click("#homeUse")
    page.wait_for_function("document.getElementById('homeInfo').textContent.includes('Danmark')")


def test_a_refused_permission_explains_where_to_turn_it_on(make_page, site):
    page, _ = open_home(make_page, site)                                # ingen tilladelse givet
    page.click("#homeUse")
    page.wait_for_function("document.getElementById('homeInfo').textContent.length > 20 && !document.getElementById('homeInfo').textContent.includes('Finder')", timeout=20000)
    assert "Privatliv" in page.inner_text("#homeInfo")
