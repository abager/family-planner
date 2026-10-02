"""Kalenderen fra appen: kun manuelt via "Føj til kalender" på beskeder, opslag og ugeplan. Ingen automatiske forslag.
Aflysninger og flytninger af aftaler, appen har oprettet, vises på beskeden selv."""
import datetime as dt


NOW = dt.datetime(2026, 10, 1, 10, 0)
DEMO_OPT = "ev_demo0000001"                       # demobeskeden m1: "Tur til Naturcentret" torsdag 8.30–13


def to_server(page, fake, **over):
    fake.use_demo(page, **over)
    page.reload()
    page.wait_for_timeout(600)


def open_message(page, mid="m1"):
    page.click("#v-mail")
    page.wait_for_timeout(250)
    page.locator(f'.mrow[data-mid="{mid}"]').click()
    page.wait_for_timeout(300)


def open_add(page):
    open_message(page)
    page.locator(f'[data-addcal="{DEMO_OPT}"]').click()


# ---------------------------------------------------------------- ingen automatiske forslag
def test_there_is_no_suggestions_tab_badge_or_banner(make_page):
    page, fake, _ = make_page(now=NOW, fixed=True)
    to_server(page, fake)
    assert page.locator("#v-sugg").count() == 0 and page.locator("#suggCount").count() == 0 and page.locator("#suggBanner").count() == 0
    assert page.locator("#evLearn").count() == 0                                              # ingen "lær af aktiviteten" i dialogen


# ---------------------------------------------------------------- manuelt: "Føj til kalender"
def test_the_manual_option_opens_a_prefilled_dialog_that_is_validated(make_page):
    page, fake, _ = make_page(now=NOW, fixed=True)
    to_server(page, fake)
    open_add(page)
    assert page.inner_text("#evHead") == "Føj til kalender"
    assert page.input_value("#evTitle") == "Carla: Tur til Naturcentret" and page.input_value("#evDate") == "01/10/2026"
    assert page.input_value("#evSt") == "08:30" and page.input_value("#evEn") == "13:00"
    page.fill("#evTitle", "")
    page.click("#evOk")
    assert page.inner_text("#evErr") == "Giv aftalen en titel."
    page.fill("#evTitle", "Carla: Tur")
    page.fill("#evEn", "07:00")
    page.click("#evOk")
    assert "slut" in page.inner_text("#evErr").lower() and page.evaluate("evDlg.open")


def test_without_a_server_nothing_can_be_created(make_page):
    page, _, opened = make_page(now=NOW, fixed=True)
    open_add(page)
    assert page.locator("#evOk").is_disabled() and "kører med serveren" in page.inner_text("#evMode")
    assert page.locator("#evMode.warn").count() == 1 and not page.is_visible("#evTarget")
    page.keyboard.press("Escape")
    assert opened.links == [] and page.locator(".calopt .done").count() == 0               # intet åbnes, intet markeres som oprettet


def test_with_a_server_but_no_calendar_writing_the_reason_is_shown(make_page):
    page, fake, _ = make_page(now=NOW, fixed=True)
    fake.cal.update(enabled=False, problem="flere Google-kalendere – sæt write = true på den, aftaler skal oprettes i")
    to_server(page, fake)
    open_add(page)
    assert page.locator("#evOk").is_disabled() and "write = true" in page.inner_text("#evMode") and "Sæt direkte oprettelse op" in page.inner_text("#evMode")


def test_creating_is_direct_errors_stay_in_the_dialog_and_the_message_shows_it_is_done(make_page):
    page, fake, opened = make_page(now=NOW, fixed=True)
    to_server(page, fake)
    open_add(page)
    fake.fail_event = True
    page.click("#evOk")
    page.wait_for_timeout(400)
    assert "403" in page.inner_text("#evErr") and page.evaluate("evDlg.open") and not page.locator("#evOk").is_disabled()
    fake.fail_event = False
    page.click("#evOk")
    page.wait_for_timeout(500)
    b = fake.posts("/api/calendar/events")[-1]
    assert (b["key"], b["title"], b["date"], b["start_time"], b["end_time"]) == (DEMO_OPT, "Carla: Tur til Naturcentret", "2026-10-01", "08:30", "13:00")
    assert not page.evaluate("evDlg.open") and "I kalenderen" in page.locator(".calopt").first.inner_text() and opened.links == []


def test_the_dialog_shows_which_calendar_and_who_the_event_goes_to(make_page):
    page, fake, _ = make_page(now=NOW, fixed=True)
    to_server(page, fake)
    open_add(page)
    t = page.inner_text("#evTarget")
    assert "Oprettes i Familiekalender" in t and "Tildeles: Carla" in t
    page.fill("#evTitle", "Tur til Naturcentret")
    assert "Hele familien (intet navn i titlen)" in page.inner_text("#evTarget")
    page.fill("#evTitle", "Hugo + Leopard-klubben")                                          # "Leo" i "Leopard" er ikke Leo
    assert page.inner_text("#evTarget").endswith("Tildeles: Hugo")


def test_a_new_event_can_be_undone_from_the_confirmation(make_page):
    page, fake, _ = make_page(now=NOW, fixed=True)
    to_server(page, fake)
    open_add(page)
    page.click("#evOk")
    page.wait_for_timeout(400)
    assert "Oprettet i Familiekalender" in page.inner_text("#toast")
    page.click("#toast .tbtn")
    page.wait_for_timeout(400)
    assert fake.posts("/api/calendar/remove")[-1]["key"] == DEMO_OPT and "fjernet igen" in page.inner_text("#toast")
    assert page.locator(f'[data-addcal="{DEMO_OPT}"]').count() == 1                          # kan tilføjes igen


# ---------------------------------------------------------------- aflysninger og flytninger, vist på beskeden
def change(kind, source="app", **over):
    base = {"id": {"cancel": "sg_cccccccccccc", "move": "sg_dddddddddddd", "hold": "sg_eeeeeeeeeeee"}[kind], "type": "suggestion", "kind": kind, "category": "ændring",
            "label": {"cancel": "Aflyst", "move": "Flyttet", "hold": "Udsat"}[kind], "title": "Forældremøde", "calendar_title": "Hugo: Forældremøde", "start": "2026-10-21", "end": "2026-10-21",
            "old_start": "2026-10-21", "all_day": False, "start_time": "19:00", "end_time": None, "location": None, "people": ["hugo"], "description": "", "confidence": "høj",
            "reason": "nævner «flyttet» og en dato", "sources": [{"type": "message", "id": "m1", "title": "Program for lejrskole", "label": "besked", "date": "2026-10-01"}],
            "target": {"key": "sg_aaaaaaaaaaaa" if source == "app" else None, "event_id": "fp1" if source == "app" else None, "title": "Hugo: Forældremøde", "start": "2026-10-21", "end": "2026-10-21", "source": source}}
    if kind == "move":
        base.update(start="2026-11-04", end="2026-11-04")
    base.update(over)
    return base


def with_changes(make_page, *changes, enabled=True):
    page, fake, opened = make_page(now=NOW, fixed=True)
    fake.cal["enabled"] = enabled
    to_server(page, fake, suggestions=list(changes))
    open_message(page)
    return page, fake, opened


def block(page):
    return page.locator("#reader .calopts.chg")


def test_a_cancellation_is_shown_on_the_message_with_what_it_concerns(make_page):
    page, *_ = with_changes(make_page, change("cancel"))
    t = block(page).inner_text()
    assert "Ændring i kalenderen" in t and "«Hugo: Forældremøde» er aflyst" in t and "21. oktober" in t
    assert [b.inner_text() for b in block(page).locator(".btn").all()] == ["Fjern fra kalender", "Behold"]


def test_removing_asks_first_and_then_calls_the_server(make_page):
    page, fake, _ = with_changes(make_page, change("cancel"))
    page.once("dialog", lambda d: d.dismiss())
    page.click('[data-chg="cancel"]')
    page.wait_for_timeout(300)
    assert fake.posts("/api/calendar/cancel") == []                                          # afbrudt = intet sker
    page.once("dialog", lambda d: (setattr(page, "_msg", d.message), d.accept()))
    page.click('[data-chg="cancel"]')
    page.wait_for_timeout(600)
    assert "Hugo: Forældremøde" in page._msg
    assert fake.posts("/api/calendar/cancel") == [{"key": "sg_cccccccccccc", "target_key": "sg_aaaaaaaaaaaa"}]
    assert "fjernet fra kalenderen" in block(page).inner_text() and block(page).locator(".btn").count() == 0


def test_a_failed_removal_keeps_the_buttons(make_page):
    page, fake, _ = with_changes(make_page, change("cancel"))
    fake.fail_change = 403
    page.once("dialog", lambda d: d.accept())
    page.click('[data-chg="cancel"]')
    page.wait_for_timeout(600)
    assert "403" in page.inner_text("#toast") and page.locator('[data-chg="cancel"]').count() == 1


def test_keeping_the_event_can_be_undone_and_never_touches_the_calendar(make_page):
    page, fake, _ = with_changes(make_page, change("cancel"))
    page.click('[data-chg="dismiss"]')
    page.wait_for_timeout(300)
    assert "Aftalen er beholdt" in block(page).inner_text() and fake.posts("/api/calendar/cancel") == []
    page.click('[data-chg="restore"]')
    page.wait_for_timeout(300)
    assert page.locator('[data-chg="cancel"]').count() == 1


def test_moving_opens_the_dialog_with_the_new_time_and_sends_the_target(make_page):
    page, fake, _ = with_changes(make_page, change("move"))
    assert "er flyttet" in block(page).inner_text() and "ny tid:" in block(page).inner_text()
    page.click('[data-chg="move"]')
    assert page.inner_text("#evHead") == "Flyt aftalen" and page.input_value("#evTitle") == "Hugo: Forældremøde"
    assert page.input_value("#evDate") == "04/11/2026" and page.input_value("#evSt") == "19:00" and page.inner_text("#evOk") == "Flyt aftalen"
    assert "Flyttes i Familiekalender" in page.inner_text("#evTarget")
    page.fill("#evSt", "19:30")
    page.click("#evOk")
    page.wait_for_timeout(600)
    b = fake.posts("/api/calendar/move")[-1]
    assert (b["key"], b["target_key"], b["date"], b["start_time"], b["title"]) == ("sg_dddddddddddd", "sg_aaaaaaaaaaaa", "2026-11-04", "19:30", "Hugo: Forældremøde")
    assert not page.evaluate("evDlg.open") and "Aftalen er flyttet" in block(page).inner_text()


def test_a_failed_move_keeps_the_dialog_open_with_the_reason(make_page):
    page, fake, _ = with_changes(make_page, change("move"))
    fake.fail_change = 403
    page.click('[data-chg="move"]')
    page.click("#evOk")
    page.wait_for_timeout(500)
    assert "403" in page.inner_text("#evErr") and page.evaluate("evDlg.open") and not page.locator("#evOk").is_disabled()


def test_events_not_created_by_the_app_are_information_only(make_page):
    page, *_ = with_changes(make_page, change("move", source="google"), change("cancel", source="google"))
    assert page.locator("[data-chg]").count() == 0
    assert block(page).inner_text().count("ikke oprettet via appen") == 2 and block(page).locator('a.btn[href="https://calendar.google.com/"]').count() == 2


def test_without_calendar_writing_changes_are_also_information_only(make_page):
    page, *_ = with_changes(make_page, change("cancel"), enabled=False)
    assert page.locator("[data-chg]").count() == 0 and "ret den selv i Google Kalender" in block(page).inner_text()


def test_a_message_without_changes_shows_no_change_block(make_page):
    page, fake, _ = make_page(now=NOW, fixed=True)
    to_server(page, fake, suggestions=[change("cancel")])
    open_message(page, "m2")
    assert block(page).count() == 0
