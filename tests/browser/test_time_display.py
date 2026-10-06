"""Start- og sluttid vises ens i alle visninger. Tænkte sluttider (endInferred) vises ikke, og en lektion uden sluttid varer 45 min."""
import datetime as dt
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Copenhagen")
TUE = dt.datetime(2026, 9, 29, 9, 40, tzinfo=TZ)        # demodata: ugen starter mandag 28/9; frikvarter 9.30–10.00
THU = dt.datetime(2026, 10, 1, 9, 40, tzinfo=TZ)


def texts(page, sel):
    return [page.locator(sel).nth(i).inner_text() for i in range(page.locator(sel).count())]


def test_day_timeline_shows_both_times_and_only_start_when_the_end_is_unknown(make_page):
    page, *_ = make_page(now=TUE, fixed=True)
    rows = texts(page, "#timeline li")
    assert any(r.startswith("15.00–15.30") and "Tandlæge Carla" in r for r in rows)
    frisor = next(r for r in rows if "Frisør Monica" in r)
    assert frisor.startswith("12.00") and "sluttid ukendt" in frisor and "13.00" not in frisor


def test_week_board_pills_show_ranges(make_page):
    page, *_ = make_page(now=TUE, fixed=True)
    page.click("#v-week")
    pills = texts(page, "#board .pill")
    assert any("16.30–17.45" in p and "Hugo fodbold" in p for p in pills)
    assert any(p.startswith("12.00") and "Frisør" in p and "13.00" not in p for p in pills)


def test_event_detail_says_the_end_is_unknown(make_page):
    page, *_ = make_page(now=TUE, fixed=True)
    page.evaluate("showEvent('g9')")
    assert "12.00 (sluttid ukendt)" in page.inner_text("#dBody")
    page.evaluate("detail.close(); showEvent('g2')")
    assert "15.00–15.30" in page.inner_text("#dBody")


def test_timetable_shows_start_and_end_of_every_lesson(make_page):
    page, *_ = make_page(now=THU, fixed=True)
    hugo = page.locator("#skema .scard", has_text="Hugo")
    assert texts(page, "#skema .scard:has-text('Hugo') .lessons time")[:3] == ["08.00–08.45", "08.45–09.30", "10.00–10.45"]
    assert "08.00–14.30" in hugo.locator(".shead").inner_text()


def test_a_lesson_without_end_lasts_45_minutes_and_ends_the_school_day_correctly(make_page):
    page, *_ = make_page(now=THU, fixed=True)
    page.evaluate("""(()=>{ const ev=state.data.events.find(e=>e.id==='h3'); const l=ev.lessons[ev.lessons.length-1];
                       l.start='13:00'; l.end=null; render(); })()""")
    hugo = page.locator("#skema .scard", has_text="Hugo")
    assert texts(page, "#skema .scard:has-text('Hugo') .lessons time")[-1] == "13.00–13.45"
    assert "08.00–13.45" in hugo.locator(".shead").inner_text()          # før: dagen sluttede ved sidste lektions start
    page.evaluate("state.data.settings={...(state.data.settings||{}),lessonMinutes:50}; render()")
    assert texts(page, "#skema .scard:has-text('Hugo') .lessons time")[-1] == "13.00–13.50"


def test_kiosk_shows_start_times_in_the_columns_and_ranges_in_the_details(make_page):
    page, *_ = make_page(now=THU, fixed=True, url="/index.html?kiosk=1")
    leo = page.locator(".k-person[data-person=leo]")
    assert "09.00 Bedsteforældredag" in " ".join(leo.inner_text().split())
    leo.locator(".k-row", has_text="Bedsteforældredag").click()
    assert "09.00–11.00" in page.inner_text("#dBody")
    page.keyboard.press("Escape")
    hugo = page.locator(".k-person[data-person=hugo]")
    math = hugo.locator(".k-row", has_text="Matematik").first
    assert "past" not in math.get_attribute("class") and "now" not in math.get_attribute("class")   # frikvarter kl. 9.40: næste lektion
    assert "past" in hugo.locator(".k-row").first.get_attribute("class")
    math.click()
    assert "10.00–10.45" in page.inner_text("#dBody")
