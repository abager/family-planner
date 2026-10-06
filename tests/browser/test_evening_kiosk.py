"""Efter kl. 18 handler I dag-fanen om i morgen. Kioskvisningen er en stor vægvisning, der følger samme regel."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from data import family, message
from fake_server import FakeServer

TZ = ZoneInfo("Europe/Copenhagen")


def at(h, m=0, day=1):
    return dt.datetime(2026, 10, day, h, m, tzinfo=TZ)


def state(page):
    return page.evaluate("""({off:dayOffset(),focus:ymd(focusDay()),skema:skemaTitle.textContent,apt:dayTitle.textContent,info:infoTitle.textContent,
        brief:(document.querySelector('#briefDay h2')||{}).textContent||null,toggle:!!document.getElementById('dayToggle')})""")


def brief_for(day):
    return {"generated": "2026-10-01T09:00:00+02:00", "mode": "day", "method": "offline", "headline_label": "x", "period": [day, day],
            "afsnit": [{"titel": "Husk", "punkter": [{"tekst": "Gymnastiktøj", "hvem": ["Hugo"], "kilder": []}]}]}


# ---------------------------------------------------------------- I dag / I morgen
def test_before_the_evening_hour_the_view_is_today(make_page):
    page, *_ = make_page(now=at(10), fixed=True)
    s = state(page)
    assert (s["off"], s["focus"], s["skema"], s["apt"], s["info"]) == (0, "2026-10-01", "Skema i dag", "Dagens aftaler", "Praktisk info i dag")
    assert s["toggle"] is False                                         # ingen I dag/I morgen-vælger – skiftet sker kun automatisk


def test_after_the_evening_hour_the_view_is_tomorrow(make_page):
    page, *_ = make_page(now=at(18, 30), fixed=True)
    s = state(page)
    assert (s["off"], s["focus"], s["skema"], s["apt"], s["info"]) == (1, "2026-10-02", "Skema i morgen", "Aftaler i morgen", "Praktisk info i morgen")


def test_the_switch_happens_by_itself_while_the_page_is_open_and_back_at_midnight(make_page):
    page, *_ = make_page(now=at(17, 50))
    assert state(page)["off"] == 0
    page.clock.run_for(11 * 60 * 1000)                                  # → 18.01
    page.wait_for_timeout(300)
    s = state(page)
    assert (s["off"], s["focus"], s["skema"]) == (1, "2026-10-02", "Skema i morgen")
    page.clock.run_for(7 * 3600 * 1000)                                 # → efter midnat
    page.wait_for_timeout(300)
    s = state(page)
    assert (s["off"], s["focus"]) == (0, "2026-10-02")                  # efter midnat: i dag igen


def test_the_evening_hour_comes_from_the_data(make_page):
    page, fake, _ = make_page(now=at(19), fixed=True)
    fake.use_demo(page, settings={"hidePrivate": True, "eveningHour": 20})
    page.reload()
    page.wait_for_timeout(500)
    assert state(page)["off"] == 0                                      # kl. 19 er stadig før kl. 20


def test_the_overview_follows_the_day_and_is_hidden_when_it_is_about_another_day(make_page):
    page, fake, _ = make_page(now=at(18), fixed=True)
    fake.briefing = brief_for("2026-10-02")
    page.reload()
    page.wait_for_timeout(500)
    assert state(page)["brief"] == "Overblik i morgen"


def test_an_overview_about_another_day_is_not_shown(make_page):
    page, fake, _ = make_page(now=at(10), fixed=True)
    fake.briefing = brief_for("2026-10-02")                             # om i morgen, men klokken er 10: "I dag" handler om i dag
    page.reload()
    page.wait_for_timeout(500)
    assert page.evaluate("document.getElementById('briefDay').classList.contains('hidden')")


def test_deadlines_in_the_task_list_say_tomorrow_when_it_is(make_page):
    page, fake, _ = make_page(now=at(18), fixed=True)
    fake.use_demo(page, tasks=[{"id": "t1", "title": "Aflever blanket", "due": "2026-10-02", "kind": "handling", "people": ["hugo"], "person": "hugo", "confidence": "høj"}])
    page.reload()
    page.wait_for_timeout(500)
    assert "i morgen" in page.inner_text("#taskList") and "Aflever blanket" in page.inner_text("#taskList")


# ---------------------------------------------------------------- kioskvisning
def kiosk(page):
    return page.evaluate("""({on:state.kiosk,url:location.search,hdr:getComputedStyle(document.querySelector('header')).display,shown:!document.getElementById('kioskView').classList.contains('hidden'),
        clock:kClock.textContent,day:kDay.textContent,date:kDate.textContent,apt:kAptTitle.textContent,kids:document.querySelectorAll('.k-kid').length,
        ox:document.documentElement.scrollWidth-document.documentElement.clientWidth,oy:document.documentElement.scrollHeight-innerHeight,exit:getComputedStyle(kExit).opacity,fs:parseFloat(getComputedStyle(kioskView).fontSize)})""")


def test_kiosk_button_starts_a_clean_full_screen_view(make_page):
    page, *_ = make_page(now=at(10, 20), width=1180, height=820)
    page.click("#menuBtn")
    page.click("#kioskBtn")
    page.wait_for_timeout(300)
    k = kiosk(page)
    assert k["on"] and k["url"] == "?kiosk=1" and k["hdr"] == "none" and k["shown"] and k["kids"] == 3
    assert k["clock"] == "10:20" and k["day"] == "I dag" and k["date"] == "torsdag 1. oktober" and k["apt"] == "Dagens aftaler"
    assert k["ox"] == 0 and k["oy"] == 0 and k["fs"] >= 18
    assert page.locator("#v-today:visible").count() == 0                         # ingen menu, ingen knapper at komme til at trykke på


def test_kiosk_text_is_correctly_capitalised_in_danish(make_page):
    page, *_ = make_page(now=at(10, 20))
    page.click("#menuBtn")
    page.click("#kioskBtn")
    assert page.inner_text("#kDay") == "I dag" and page.inner_text("#kDate")[0].islower()


def test_the_exit_button_shows_on_tap_and_hides_again(make_page):
    page, *_ = make_page(now=at(10))
    page.click("#menuBtn")
    page.click("#kioskBtn")
    assert kiosk(page)["exit"] == "0"
    page.click("#kClock")
    page.wait_for_timeout(400)
    assert kiosk(page)["exit"] == "1"
    page.clock.run_for(6500)
    page.wait_for_timeout(400)
    assert kiosk(page)["exit"] == "0"
    page.click("#kClock")
    page.wait_for_timeout(300)
    page.click("#kExit")
    page.wait_for_timeout(300)
    k = kiosk(page)
    assert not k["on"] and k["url"] == "" and k["hdr"] != "none"


def test_escape_leaves_the_kiosk(make_page):
    page, *_ = make_page(now=at(10))
    page.click("#menuBtn")
    page.click("#kioskBtn")
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    assert not kiosk(page)["on"]


def test_a_link_with_kiosk_in_it_starts_in_kiosk(make_page):
    page, *_ = make_page(now=at(10), url="/index.html?kiosk=1")
    assert kiosk(page)["on"] and kiosk(page)["kids"] == 3


def test_the_kiosk_switches_to_tomorrow_by_itself_in_the_evening(make_page):
    page, *_ = make_page(now=at(17, 59), url="/index.html?kiosk=1")
    assert kiosk(page)["day"] == "I dag"
    page.clock.run_for(2 * 60 * 1000)
    page.wait_for_timeout(300)
    k = kiosk(page)
    assert (k["day"], k["date"], k["apt"], k["clock"]) == ("I morgen", "fredag 2. oktober", "Aftaler i morgen", "18:01")


def test_the_kiosk_never_shows_messages_and_refreshes_its_data(make_page):
    fake = FakeServer()
    fake.family, fake.server = family([message(1, subject="Hemmeligt emne", text="Fortroligt indhold")]), True
    page, fake, _ = make_page(fake, now=at(10), url="/index.html?kiosk=1")
    assert "Fortroligt" not in page.inner_text("body") and "Hemmeligt" not in page.inner_text("body")
    before = fake.family_gets
    page.clock.run_for(2 * 60 * 1000 + 5000)
    page.wait_for_timeout(500)
    assert fake.family_gets > before                                                   # frisk data hvert andet minut


def test_the_kiosk_asks_the_screen_to_stay_on(make_page, site):
    page, *_ = make_page(now=at(10), goto=False)
    page.add_init_script("window.__wake=0;Object.defineProperty(navigator,'wakeLock',{configurable:true,value:{request:async()=>{window.__wake++;return {addEventListener(){},release(){window.__wake=-1}}}}})")
    page.goto(site.url + "/index.html?kiosk=1")
    page.wait_for_timeout(500)
    assert page.evaluate("window.__wake") == 1
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    assert page.evaluate("window.__wake") == -1                           # slipper igen ved afslut


def test_the_kiosk_shows_a_warning_when_data_is_old_or_google_is_down(make_page):
    fake = FakeServer()
    fake.family = family(generated="2026-10-01T06:00:00+02:00", health={"google": {"ok": False, "failed": ["x"], "last_ok": None}, "aula": {"state": "ok"}})
    fake.server = True
    page, *_ = make_page(fake, now=at(10), url="/index.html?kiosk=1")
    s = page.inner_text("#kStatus")
    assert "over en time gamle" in s and "Familiekalenderen ikke hentet" in s


@pytest.mark.parametrize("w,h", [(1180, 820), (820, 1180), (1920, 1080)])
def test_the_kiosk_fits_tablets_in_both_orientations(make_page, w, h):
    page, *_ = make_page(now=at(10, 20), width=w, height=h, url="/index.html?kiosk=1")
    k = kiosk(page)
    assert k["kids"] == 3 and k["ox"] == 0 and k["oy"] == 0
