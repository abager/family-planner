"""Ensartede termer og ingen dubletter: Praktisk info gentager ikke opgaver, og handlingerne ligger i menuen."""
import datetime as dt
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Copenhagen")
NOW = dt.datetime(2026, 10, 1, 10, 0, tzinfo=TZ)


def test_practical_info_skips_weekplan_items_that_became_tasks(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    before = page.locator('#planToday [data-plan="w6"]').count()
    # Backend laver opgaver med id "<ugeplanpunkt>:<n>" – så står punktet kun under "Husk og lektier"
    page.evaluate("""state.data.tasks.push({id:'w6:0',title:'Matematik: passer',due:ymd(focusDay()),person:'hugo',kind:'husk',source:'meebook'}); render()""")
    assert before == 1 and page.locator('#planToday [data-plan="w6"]').count() == 0
    assert "Matematik: passer" in page.inner_text("#taskList")
    assert page.inner_text("#infoTitle") == "Praktisk info i dag"


def test_actions_live_in_the_menu_and_the_status_line_only_shows_status(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    assert page.is_hidden("#actMenu") and page.get_attribute("#menuBtn", "aria-expanded") == "false"
    page.click("#menuBtn")
    assert page.is_visible("#kioskBtn") and page.get_attribute("#menuBtn", "aria-expanded") == "true"
    page.keyboard.press("Escape")
    assert page.is_hidden("#actMenu")
    page.click("#menuBtn")
    page.click("#kioskBtn")
    assert page.evaluate("state.kiosk") and page.is_hidden("#actMenu")


def test_feed_filter_says_alle_like_messages(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    assert page.locator('[data-ft="all"]').text_content() == "Alle"
    assert page.locator('[data-mf="all"]').text_content() == "Alle"


def test_week_picker_shows_the_shown_week_number_and_moves_with_the_arrows(make_page):
    page, *_ = make_page(now=NOW, fixed=True)                       # 1. oktober 2026 ligger i uge 40
    page.click("#v-week")
    assert page.inner_text("#weekLabel") == "Uge 40" and page.locator("#now").count() == 0
    page.click("#next")
    assert page.inner_text("#weekLabel") == "Uge 41"
    page.click("#prev")
    page.click("#prev")
    assert page.inner_text("#weekLabel") == "Uge 39"


def test_the_overview_has_no_read_aloud(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    assert page.locator("#briefDay .speak, #briefDay select.voice").count() == 0
    assert page.evaluate("typeof speak") == "undefined"


def test_settings_gear_holds_the_actions_and_has_no_refresh(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    gear = page.locator("#menuBtn")
    assert gear.get_attribute("aria-label") == "Indstillinger" and gear.get_attribute("title") == "Indstillinger"
    page.click("#menuBtn")
    assert page.locator("#refreshNow").count() == 0 and "Opdatér nu" not in page.inner_text("#actMenu")


def test_a_spinning_icon_replaces_the_fetching_text_while_the_server_fetches(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    page.evaluate("state.server={running:true,runs:1,aula:'ok'}; runWatch=true; renderChrome()")   # runWatch: ingen rigtig polling i testen
    assert page.locator("#status .spin").count() == 1 and "Henter …" not in page.inner_text("#status")
    assert page.locator("#status .spin .sronly").text_content() == "Henter data"
    page.evaluate("state.server={running:false,runs:2,aula:'ok'}; renderChrome()")
    assert page.locator("#status .spin").count() == 0


def _gear_box(page):
    return page.evaluate("""(()=>{const g=document.getElementById('menuBtn').getBoundingClientRect(), h=document.querySelector('header').getBoundingClientRect(),
        t=document.getElementById('greeting').getBoundingClientRect(); return {gr:g.right, gt:g.top, gb:g.bottom, hr:h.right, ht:h.top, tt:t.top, tb:t.bottom};})()""")


def test_the_gear_sits_in_the_top_right_corner_on_desktop_and_phone(make_page):
    for width, height in ((1280, 900), (390, 844)):
        page, *_ = make_page(now=NOW, fixed=True, width=width, height=height)
        b = _gear_box(page)
        assert abs(b["gr"] - b["hr"]) <= 2, (width, b)                  # helt ude til højre
        assert b["gt"] - b["ht"] <= 4 and b["gt"] < b["tb"], (width, b)  # øverst, på linje med hilsenen
        page.click("#menuBtn")
        m = page.evaluate("(()=>{const r=document.getElementById('actMenu').getBoundingClientRect(); return {l:r.left, r:r.right};})()")
        assert m["l"] >= 0 and m["r"] <= width, (width, m)               # menuen folder sig ud inden for skærmen
