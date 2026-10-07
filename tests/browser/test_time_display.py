"""Start- og sluttid vises ens i alle visninger. Tænkte sluttider (endInferred) vises ikke, og en lektion uden sluttid varer 45 min."""
import datetime as dt
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Copenhagen")
TUE = dt.datetime(2026, 9, 29, 9, 40, tzinfo=TZ)        # demodata: ugen starter mandag 28/9; frikvarter 9.30–10.00
THU = dt.datetime(2026, 10, 1, 9, 40, tzinfo=TZ)


def texts(page, sel):
    return [page.locator(sel).nth(i).inner_text() for i in range(page.locator(sel).count())]


def labels(page, sel):
    """Knappens fulde navn: den synlige tekst plus det, der kun er til skærmlæsere."""
    return page.eval_on_selector_all(sel, "els => els.map(e => e.textContent.replace(/\\s+/g, ' ').trim())")


def test_day_lanes_show_both_times_and_only_start_when_the_end_is_unknown(make_page):
    page, *_ = make_page(now=TUE, fixed=True)
    carla = labels(page, '#dayLanes .dlane[data-person="carla"] .blk')
    assert any("Tandlæge Carla" in r and "15.00–15.30" in r for r in carla)
    frisor = next(r for r in labels(page, '#dayLanes .dlane[data-person="monica"] .blk') if "Frisør Monica" in r)
    assert "12.00" in frisor and "sluttid ukendt" in frisor and "13.00" not in frisor
    lst = page.eval_on_selector("#dayLanes .dlist", "e => e.textContent")       # smal skærm: samme regel i listen
    assert "sluttid ukendt" in lst


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


def hugo_lessons(page):
    return labels(page, '#dayLanes .dlane[data-person="hugo"] .blk.lesson')


def hugo_school_row(page):
    return page.eval_on_selector_all("#dayLanes .dlist .lrow", "rs => rs.map(r => r.textContent).filter(t => t.includes('Skole'))")


def test_the_lanes_show_start_and_end_of_every_lesson(make_page):
    page, *_ = make_page(now=THU, fixed=True)
    ls = hugo_lessons(page)
    assert [l.split(" ")[-1] for l in ls[:3]] == ["08.00–08.45", "08.45–09.30", "10.00–10.45"]
    assert any("til 14.30" in r for r in hugo_school_row(page))


def test_a_lesson_without_end_lasts_45_minutes_and_ends_the_school_day_correctly(make_page):
    page, *_ = make_page(now=THU, fixed=True)
    page.evaluate("""(()=>{ const ev=state.data.events.find(e=>e.id==='h3'); const l=ev.lessons[ev.lessons.length-1];
                       l.start='13:00'; l.end=null; render(); })()""")
    assert hugo_lessons(page)[-1].endswith("13.00–13.45")
    assert any("til 13.45" in r for r in hugo_school_row(page))         # før: dagen sluttede ved sidste lektions start
    page.evaluate("state.data.settings={...(state.data.settings||{}),lessonMinutes:50}; render()")
    assert hugo_lessons(page)[-1].endswith("13.00–13.50")


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
