"""Vejret på kioskskærmen og knappen "Brug min placering som hjem". Simuleret server og placering."""
import pytest
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
    page, _ = kiosk(make_page, site, weather={"kilde": "MET Norway", "dage": [DAY]})
    t = page.inner_text("#kWeather")
    assert "8–11°" in t and "regn om eftermiddagen" in t and "regntøj og gummistøvler" in t and "🌧" in t
    assert page.evaluate("document.documentElement.scrollHeight<=innerHeight+1")


def test_no_weather_line_without_weather_or_for_another_day(make_page, site):
    page, _ = kiosk(make_page, site, weather=None)
    assert page.inner_text("#kWeather").strip() == ""
    page, _ = kiosk(make_page, site, weather={"kilde": "MET Norway", "dage": [{**DAY, "dato": "2026-10-05"}]})
    assert page.inner_text("#kWeather").strip() == ""


def test_the_weather_section_is_not_repeated_in_the_kiosk_overview(make_page, site):
    b = {"generated": "2026-10-01T07:00:00+02:00", "mode": "day", "method": "offline", "headline_label": "i dag",
         "period": ["2026-10-01", "2026-10-01"],
         "afsnit": [{"titel": "Vejr", "punkter": [{"tekst": "8–11°, regn – regntøj", "hvem": [], "kilder": ["Vejr (DMI)"]}]},
                    {"titel": "Husk", "punkter": [{"tekst": "Gymnastiktøj", "hvem": ["Hugo"], "kilder": []}]}]}
    page, _ = kiosk(make_page, site, weather={"kilde": "MET Norway", "dage": [DAY]}, briefing=b)
    overview = page.inner_text("#kBriefText")
    assert "Gymnastiktøj" in overview and "regntøj" not in overview


def open_home(make_page, site, geo=None):
    page, fake, _ = make_page(now=NOW, fixed=True, goto=False)
    if geo:
        page.context.grant_permissions(["geolocation"])
        page.context.set_geolocation({"latitude": geo[0], "longitude": geo[1]})
    page.goto(site.url + "/index.html")
    fake.use_demo(page)
    page.reload()
    page.wait_for_timeout(500)
    page.click("#menuBtn")
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
    page, _ = app(make_page, site, {"kilde": "MET Norway", "dage": [DAY_H]})
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
    page, _ = app(make_page, site, {"kilde": "MET Norway", "dage": [DAY_H]}, now=dt.datetime(2026, 10, 1, 6, 0), width=390)
    page.click("#wxHeadBtn")
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
    assert page.evaluate("(e=>e.scrollWidth>e.clientWidth)(document.querySelector('#wxHeadPanel .wxh'))")


def test_after_the_school_day_the_heading_shows_tomorrow(make_page, site):
    page, _ = app(make_page, site, {"kilde": "MET Norway", "dage": [TOMORROW]}, now=dt.datetime(2026, 10, 1, 20, 0))
    t = page.inner_text("#wxHeadBtn")
    assert "☀" in t and "6–13°" in t and "i morgen" in t


def test_no_weather_means_no_icon_in_the_heading(make_page, site):
    page, _ = app(make_page, site, None)
    assert page.locator("#wxHeadBtn").count() == 0 and page.is_hidden("#wxHeadPanel")


def test_the_kiosk_shows_the_temperature_now_and_a_strip_of_the_next_hours(make_page, site):
    page, _ = kiosk(make_page, site, weather={"kilde": "MET Norway", "dage": [DAY_H]})
    now = page.inner_text("#kWeather .k-wxnow")
    assert "8°" in now and "8–11° i dag" in now                        # kl. 9.30 i København: timen nu og dagens laveste–højeste
    hours = page.eval_on_selector_all("#kWeather .k-wxstrip .t", "ts => ts.map(t => t.textContent)")
    assert 1 <= len(hours) <= 8 and hours[0] == "10"
    assert page.evaluate("document.documentElement.scrollHeight<=innerHeight+1")


def test_a_tap_on_the_weather_shows_every_hour_in_the_details(make_page, site):
    page, _ = kiosk(make_page, site, weather={"kilde": "MET Norway", "dage": [DAY_H]})
    page.click("#kWxBtn")
    assert page.evaluate("detail.open") and page.inner_text("#dTitle") == "Vejret i dag"
    assert page.locator("#dBody .wxh li").count() == 14                # resten af dagen: kl. 9–22


def test_the_kiosk_without_hours_shows_the_day(make_page, site):
    page, _ = kiosk(make_page, site, weather={"kilde": "MET Norway", "dage": [DAY]})
    assert page.locator("#kWeather .k-wxstrip").count() == 0 and "8–11°" in page.inner_text("#kWeather")


def test_after_the_evening_hour_the_kiosk_shows_tomorrows_weather(make_page, site):
    page, fake, _ = make_page(now=dt.datetime(2026, 10, 1, 19, 0), fixed=True, goto=False)
    page.goto(site.url + "/index.html")
    fake.use_demo(page, weather={"kilde": "MET Norway", "dage": [DAY_H, TOMORROW]})
    page.goto(site.url + "/index.html?kiosk=1")
    page.wait_for_timeout(600)
    t = page.inner_text("#kWeather")
    assert "☀️" in t and "6–13°" in t
    assert page.eval_on_selector_all("#kWeather .k-wxstrip .t", "ts => ts.map(t => t.textContent)")[0] == "07"


def test_a_missing_home_for_the_weather_is_shown_under_the_warning_and_can_be_fixed_there(make_page, site):
    # Handlingen ligger altid i menuen; advarslen står under ⚠ ved titlen, når hjemmet mangler
    page, _ = app(make_page, site, None, status={"enabled": True, "home": False})
    assert "Hjem for vejret" not in page.inner_text("#status")
    page.click("#probBtn")
    assert "Hjem for vejret er ikke sat" in page.inner_text("#probDlg")
    page.click("#probHome")
    assert page.locator("#homeDlg").evaluate("d => d.open") and not page.locator("#probDlg").evaluate("d => d.open")
    assert page.locator("#homeBtn").text_content() == "Sæt hjem for vejret"
    page, _ = app(make_page, site, None, status={"enabled": True, "home": True})
    assert page.locator("#probBtn").count() == 0 and page.locator("#homeBtn").text_content() == "Sæt hjem for vejret"
    page, _ = app(make_page, site, None, status={"enabled": False, "home": False})
    assert page.locator("#probBtn").count() == 0


def test_the_details_close_by_themselves_after_10_seconds(make_page, site):
    page, fake, _ = make_page(now=NOW, goto=False)                      # uret kører, så ventetiden kan spoles frem
    page.goto(site.url + "/index.html")
    fake.use_demo(page, weather={"kilde": "MET Norway", "dage": [DAY_H]})
    page.goto(site.url + "/index.html?kiosk=1")
    page.clock.run_for(1000)
    page.click("#kWxBtn")
    page.clock.run_for(9000)
    assert page.evaluate("detail.open")
    page.clock.run_for(2000)
    assert not page.evaluate("detail.open")


def test_a_tap_outside_the_details_closes_them(make_page, site):
    page, _ = kiosk(make_page, site, weather={"kilde": "MET Norway", "dage": [DAY_H]})
    page.click("#kWxBtn")
    assert page.evaluate("detail.open")
    page.mouse.click(5, 5)
    page.wait_for_timeout(200)
    assert not page.evaluate("detail.open")



@pytest.mark.parametrize("view", ["v-today", "v-week", "v-mail", "v-aula"])
def test_the_weather_folds_out_the_same_way_on_every_page(make_page, site, view):
    page, _ = app(make_page, site, {"kilde": "MET Norway", "dage": [DAY_H]})
    page.click(f"#{view}")
    page.wait_for_timeout(200)
    page.click("#wxHeadBtn")
    assert page.locator("#wxHeadBtn").get_attribute("aria-expanded") == "true" and page.is_visible("#wxHeadPanel")
    assert page.locator("#wxHeadPanel .wxh li").count() > 0
    page.click("#wxHeadBtn")
    assert page.is_hidden("#wxHeadPanel")


def test_the_messages_view_still_fits_the_screen_with_the_weather_open(make_page, site):
    page, _ = app(make_page, site, {"kilde": "MET Norway", "dage": [DAY_H]})
    page.click("#v-mail")
    page.wait_for_timeout(200)
    page.click("#wxHeadBtn")
    page.wait_for_timeout(100)
    assert page.evaluate("document.getElementById('mail').getBoundingClientRect().bottom <= innerHeight + 1")
