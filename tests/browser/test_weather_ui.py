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


# ---------------------------------------------------------------- time for time: ét ikon, der folder sig ud
HOURS = [{"kl": h, "ikon": "🌧️" if 14 <= h <= 17 else "⛅", "temp": 8 + (h - 6) // 4, "regn": 1.2 if 14 <= h <= 17 else 0.0, "vind": 4}
         for h in range(6, 23)]
DAY_H = {**DAY, "timer": HOURS}
TOMORROW = {**DAY_H, "dato": "2026-10-02", "ugedag": "fredag", "ikon": "☀️", "min": 6, "max": 13,
            "tekst": "6–13°, sol", "raad": [], "regn": "ingen"}


def app(make_page, site, weather, now=NOW, status=None, width=1280):
    page, fake, _ = make_page(now=now, fixed=True, goto=False, width=width)
    page.goto(site.url + "/index.html")
    fake.use_demo(page, weather=weather)
    if status is not None:
        fake.weather_status = status
    page.reload()
    page.wait_for_timeout(500)
    return page, fake


def test_the_date_heading_shows_one_icon_and_folds_out_the_rest_of_the_day(make_page, site):
    page, _ = app(make_page, site, {"kilde": "DMI", "dage": [DAY_H]})
    chip = page.locator("#wxHeadBtn")
    assert "🌧" in chip.inner_text() and "8–11°" in chip.inner_text()
    assert chip.get_attribute("aria-expanded") == "false" and page.is_hidden("#wxHeadPanel")
    chip.click()
    assert page.locator("#wxHeadBtn").get_attribute("aria-expanded") == "true" and page.is_visible("#wxHeadPanel")
    hours = page.locator("#wxHeadPanel .wxh li .t").all_inner_texts()
    assert hours[0] == "09" and hours[-1] == "22"                      # testuret står på 9.30 dansk tid: timerne før er væk
    assert "1,2 mm" in page.inner_text("#wxHeadPanel") and "regntøj og gummistøvler" in page.inner_text("#wxHeadPanel")
    assert "millimeter regn" in page.inner_text("#wxHeadPanel .wxh")    # skærmlæsere får hele sætningen
    page.click("#wxHeadBtn")
    assert page.is_hidden("#wxHeadPanel")


def test_the_strip_scrolls_sideways_instead_of_widening_the_page_on_a_phone(make_page, site):
    page, _ = app(make_page, site, {"kilde": "DMI", "dage": [DAY_H]}, now=dt.datetime(2026, 10, 1, 6, 0), width=390)
    page.click("#wxHeadBtn")
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
    assert page.evaluate("(e=>e.scrollWidth>e.clientWidth)(document.querySelector('#wxHeadPanel .wxh'))")


def test_after_the_school_day_the_heading_shows_tomorrow(make_page, site):
    page, _ = app(make_page, site, {"kilde": "DMI", "dage": [TOMORROW]}, now=dt.datetime(2026, 10, 1, 20, 0))
    t = page.inner_text("#wxHeadBtn")
    assert "☀" in t and "6–13°" in t and "i morgen" in t


def test_no_weather_means_no_icon_in_the_heading(make_page, site):
    page, _ = app(make_page, site, None)
    assert page.locator("#wxHeadBtn").count() == 0 and page.is_hidden("#wxHeadPanel")


def test_the_kiosk_folds_out_the_hours_on_touch(make_page, site):
    page, _ = kiosk(make_page, site, weather={"kilde": "DMI", "dage": [DAY_H]})
    assert page.is_hidden("#kHours") and "time for time" in page.inner_text("#kWeather")
    page.click("#kWxBtn")
    assert page.is_visible("#kHours") and page.locator("#kHours .wxh li").count() == 14
    assert page.evaluate("document.documentElement.scrollHeight<=innerHeight+1")     # stadig ingen rulning
    page.click("#kWxBtn")
    assert page.is_hidden("#kHours")


def test_the_kiosk_without_hours_is_plain_text_as_before(make_page, site):
    page, _ = kiosk(make_page, site, weather={"kilde": "DMI", "dage": [DAY]})
    assert page.locator("#kWxBtn").count() == 0 and "8–11°" in page.inner_text("#kWeather")


def test_the_home_button_warns_when_weather_is_on_but_home_is_missing(make_page, site):
    page, _ = app(make_page, site, None, status={"enabled": True, "home": False})
    b = page.locator("#homeBtn")
    assert "ikke sat" in b.inner_text() and "srvwarn" in b.get_attribute("class")
    page, _ = app(make_page, site, None, status={"enabled": True, "home": True})
    assert page.inner_text("#homeBtn") == "Vejr: hjem"
    page, _ = app(make_page, site, None, status={"enabled": False, "home": False})
    assert "srvwarn" not in page.locator("#homeBtn").get_attribute("class")
