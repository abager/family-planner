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
