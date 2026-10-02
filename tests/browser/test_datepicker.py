"""Datovælgeren: skriv selv i mange former, eller vælg i kalenderen."""
import datetime as dt

import pytest

NOW = dt.datetime(2026, 10, 1, 10, 0)          # torsdag

PARSE = [("12/10", "2026-10-12"), ("12.10.2026", "2026-10-12"), ("12-10-26", "2026-10-12"), ("12 10", "2026-10-12"), ("12 okt", "2026-10-12"), ("12. oktober 2026", "2026-10-12"),
         ("12okt", "2026-10-12"), ("1210", "2026-10-12"), ("121026", "2026-10-12"), ("12102026", "2026-10-12"), ("512", "2026-12-05"), ("i morgen", "2026-10-02"),
         ("imorgen", "2026-10-02"), ("idag", "2026-10-01"), ("i overmorgen", "2026-10-03"), ("mandag", "2026-10-05"), ("torsdag", "2026-10-01"), ("man", "2026-10-05"),
         ("5/1", "2027-01-05"), ("30/9", "2026-09-30"), ("2026-10-12", "2026-10-12"), ("29/2/2028", "2028-02-29"), ("", ""),
         ("31/2", None), ("32/1", None), ("0/5", None), ("12/13", None), ("abc", None), ("12/10/1999", None), ("29/2/2027", None), ("12345", None), ("1.", None)]


def open_dialog(page, fake=None):
    if fake:                                    # med server: kun her kan dialogen gemme
        fake.use_demo(page)
        page.reload()
        page.wait_for_timeout(600)
    page.click("#v-sugg")
    page.wait_for_timeout(250)
    page.locator(".suggwrap > .sg").first.locator('[data-sg="edit"]').click()
    page.wait_for_timeout(200)


def test_every_way_of_writing_a_date_is_understood(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    wrong = [(t, got, exp) for t, exp in PARSE if (got := page.evaluate("t=>DP.parse(t)", t)) != exp]
    assert wrong == []


def test_typing_shows_the_weekday_and_normalises_on_leaving_the_field(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    open_dialog(page)
    page.fill("#evDate", "13.10")
    assert page.inner_text("#evDateHint") == "tirsdag 13. oktober 2026" and page.evaluate("evDate.dataset.iso") == "2026-10-13"
    page.press("#evDate", "Tab")
    assert page.input_value("#evDate") == "13/10/2026"


def test_an_unreadable_date_is_marked_and_blocks_saving(make_page):
    page, fake, _ = make_page(now=NOW, fixed=True)
    open_dialog(page, fake)
    page.fill("#evDate", "31/2")
    page.press("#evDate", "Tab")
    assert page.get_attribute("#evDate", "aria-invalid") == "true" and page.locator("#evDateHint.bad").count() == 1
    page.click("#evOk")
    assert "kan ikke læses" in page.inner_text("#evErr") and page.evaluate("evDlg.open")


def test_calendar_opens_on_the_chosen_month_and_picking_a_day_fills_the_field(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    open_dialog(page)
    page.fill("#evDate", "13/10/2026")
    page.press("#evDate", "Tab")
    page.click("#evDateBtn")
    assert page.is_visible("#dpCal") and page.inner_text(".dptitle") == "Oktober 2026" and page.get_attribute("#evDateBtn", "aria-expanded") == "true"
    assert page.locator(".dpday.sel").inner_text() == "13" and page.locator(".dpday.today").get_attribute("data-iso") == "2026-10-01"
    assert page.locator(".dpgrid th").first.inner_text() == "MA" and page.locator(".dpgrid tbody tr").count() == 6        # ugen starter mandag
    page.locator('.dpday[data-iso="2026-10-20"]').click()
    assert page.input_value("#evDate") == "20/10/2026" and not page.is_visible("#dpCal") and page.evaluate("document.activeElement.id") == "evDate"


def test_picking_a_start_after_the_end_clears_the_end(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    open_dialog(page)
    assert page.input_value("#evEnd") != ""
    page.fill("#evDate", "13/10/2026")
    page.click("#evDateBtn")
    page.locator('.dpday[data-iso="2026-10-20"]').click()
    assert page.input_value("#evEnd") == ""


def test_the_end_calendar_disables_days_before_the_start_and_marks_the_range(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    open_dialog(page)
    page.fill("#evDate", "20/10/2026")
    page.press("#evDate", "Tab")
    page.click("#evEndBtn")
    assert page.locator('.dpday[data-iso="2026-10-19"]').is_disabled() and not page.locator('.dpday[data-iso="2026-10-20"]').is_disabled()
    page.locator('.dpday[data-iso="2026-10-25"]').click()
    page.click("#evEndBtn")
    assert page.locator(".dpday.range").count() == 4                                    # 21.–24.


def test_keyboard_navigation_and_escape(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    open_dialog(page)
    page.fill("#evDate", "20/10/2026")
    page.press("#evDate", "Tab")
    page.focus("#evDate")
    page.keyboard.press("ArrowDown")
    focus = lambda: page.evaluate("document.activeElement.dataset.iso")
    assert focus() == "2026-10-20"
    page.keyboard.press("ArrowRight")
    assert focus() == "2026-10-21"
    page.keyboard.press("ArrowDown")
    assert focus() == "2026-10-28"
    page.keyboard.press("PageDown")
    assert page.inner_text(".dptitle") == "November 2026" and focus() == "2026-11-28"
    page.keyboard.press("Home")
    assert focus() == "2026-11-23"
    page.keyboard.press("End")
    assert focus() == "2026-11-29"
    page.keyboard.press("Enter")
    assert page.input_value("#evDate") == "29/11/2026" and page.evaluate("document.activeElement.id") == "evDate"
    page.click("#evDateBtn")
    page.keyboard.press("Escape")                                                       # Esc fra kalenderen lukker kun kalenderen
    assert not page.is_visible("#dpCal") and page.evaluate("evDlg.open")


def test_escape_in_the_field_closes_the_calendar_not_the_dialog(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    open_dialog(page)
    page.click("#evDateBtn")
    page.fill("#evDate", "3/3/2027")
    assert page.inner_text(".dptitle") == "Marts 2027"                                  # kalenderen følger med, mens man skriver
    page.keyboard.press("Escape")
    assert not page.is_visible("#dpCal") and page.evaluate("evDlg.open") and page.input_value("#evDate") == "3/3/2027"


def test_enter_in_a_date_field_does_not_submit_the_form(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    open_dialog(page)
    page.fill("#evEnd", "")
    page.fill("#evDate", "15/11")
    page.press("#evDate", "Enter")
    assert page.evaluate("evDlg.open") and page.input_value("#evDate") == "15/11/2026"


def test_a_typed_date_ends_up_in_the_created_event(make_page):
    page, fake, opened = make_page(now=NOW, fixed=True)
    open_dialog(page, fake)
    page.fill("#evDate", "14/10/2026")
    page.fill("#evEnd", "16 okt")
    page.check("#evAll")
    page.click("#evOk")
    page.wait_for_timeout(400)
    b = fake.posts("/api/calendar/events")[-1]
    assert (b["date"], b["end_date"], b["all_day"]) == ("2026-10-14", "2026-10-16", True) and opened.errors == []


@pytest.mark.parametrize("device", ["iPhone 13"])
def test_calendar_fits_a_phone_and_has_big_enough_touch_targets(make_page, device):
    page, *_ = make_page(now=NOW, fixed=True, device=device)
    page.locator("#v-sugg").tap()
    page.wait_for_timeout(250)
    page.locator(".suggwrap > .sg").first.locator('[data-sg="edit"]').tap()
    page.wait_for_timeout(250)
    page.locator("#evDateBtn").tap()
    page.wait_for_timeout(300)
    m = page.evaluate("""(()=>{const c=document.getElementById('dpCal').getBoundingClientRect(),d=document.querySelector('.dpday').getBoundingClientRect();
        return {left:c.left,right:c.right,vw:innerWidth,w:d.width,h:d.height,overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth}})()""")
    assert m["left"] >= 0 and m["right"] <= m["vw"] and m["overflow"] == 0
    assert m["w"] >= 40 and m["h"] >= 44
    page.locator('.dpday[data-iso="2026-10-22"]').tap()
    assert page.input_value("#evDate") == "22/10/2026"
