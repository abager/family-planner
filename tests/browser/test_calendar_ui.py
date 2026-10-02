"""Kalenderforslag i appen: uden server (kan ikke oprette), med server (direkte oprettelse), lærte regler og ændringer (aflysning/flytning)."""
import datetime as dt


NOW = dt.datetime(2026, 10, 1, 10, 0)


def card(page, text):
    return page.locator(".suggwrap > .sg", has_text=text).first


def sections(page):
    all_ = [page.locator("details.sdone summary").nth(i).inner_text() for i in range(page.locator("details.sdone summary").count())]
    return [x for x in all_ if not x.startswith("Lærte regler")]


def to_server(page, fake, **over):
    fake.use_demo(page, **over)
    page.reload()
    page.wait_for_timeout(600)
    page.click("#v-sugg")
    page.wait_for_timeout(300)


# ---------------------------------------------------------------- uden server
def test_suggestions_show_up_as_a_badge_a_banner_and_cards(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    assert page.inner_text("#suggCount") == "3" and "3 nye forslag" in page.inner_text("#suggBanner")
    page.click("#v-sugg")
    titles = [page.locator(".suggwrap > .sg h3").nth(i).inner_text() for i in range(page.locator(".suggwrap > .sg").count())]
    assert len(titles) == 3 and any("Lejrskole" in t for t in titles)
    btn = card(page, "Lejrskole").locator('[data-sg="create"]')
    assert btn.inner_text() == "Opret i kalender" and btn.is_disabled()                             # uden server: kan ikke oprette
    assert "kører med serveren" in page.inner_text(".sintro .snote")


def test_the_dialog_is_prefilled_and_validated(make_page):
    page, fake, _ = make_page(now=NOW, fixed=True)
    to_server(page, fake)
    card(page, "Lejrskole").locator('[data-sg="edit"]').click()
    assert page.input_value("#evTitle") == "Hugo: Lejrskole" and page.input_value("#evDate") == "12/10/2026" and page.input_value("#evEnd") == "16/10/2026"
    assert page.is_checked("#evAll") and not page.locator("#evTimes").is_visible()
    page.fill("#evTitle", "")
    page.click("#evOk")
    assert page.inner_text("#evErr") == "Giv aftalen en titel."
    page.fill("#evTitle", "Hugo: Lejrskole (rettet)")
    page.uncheck("#evAll")
    page.click("#evOk")
    assert "starttid" in page.inner_text("#evErr").lower()
    page.fill("#evSt", "06:30")
    page.fill("#evEn", "05:00")
    page.fill("#evEnd", "")
    page.click("#evOk")
    assert "slut" in page.inner_text("#evErr").lower() and page.evaluate("evDlg.open")


def test_without_a_server_nothing_is_created_or_marked_as_created(make_page):
    page, _, opened = make_page(now=NOW, fixed=True)
    page.click("#v-sugg")
    card(page, "Lejrskole").locator('[data-sg="edit"]').click()
    assert page.locator("#evOk").is_disabled() and "kører med serveren" in page.inner_text("#evMode")
    assert page.locator("#evMode.warn").count() == 1 and not page.is_visible("#evTarget")
    page.keyboard.press("Escape")
    assert opened.links == [] and not any(s.startswith("Oprettet") for s in sections(page))      # intet åbnes, intet markeres som oprettet


def test_with_a_server_but_no_calendar_writing_the_reason_is_shown(make_page):
    page, fake, _ = make_page(now=NOW, fixed=True)
    fake.cal.update(enabled=False, problem="flere Google-kalendere – sæt write = true på den, aftaler skal oprettes i")
    to_server(page, fake)
    assert card(page, "Zoo").locator('[data-sg="create"]').is_disabled()
    assert "write = true" in page.inner_text(".sintro .snote") and "Sæt direkte oprettelse op" in page.inner_text(".sintro .snote")
    card(page, "Zoo").locator('[data-sg="edit"]').click()
    assert page.locator("#evOk").is_disabled() and "write = true" in page.inner_text("#evMode")


def test_all_day_and_timed_suggestions_are_sent_as_entered(make_page):
    page, fake, opened = make_page(now=NOW, fixed=True)
    to_server(page, fake)
    card(page, "Lejrskole").locator('[data-sg="edit"]').click()
    page.fill("#evTitle", "Hugo: Lejrskole (rettet)")
    page.check("#evAll")
    page.click("#evOk")
    page.wait_for_timeout(400)
    b = fake.posts("/api/calendar/events")[-1]
    assert (b["title"], b["date"], b["end_date"], b["all_day"]) == ("Hugo: Lejrskole (rettet)", "2026-10-12", "2026-10-16", True)
    card(page, "Zoo").locator('[data-sg="create"]').click()
    assert page.input_value("#evSt") == "08:30" and page.input_value("#evEn") == "14:00" and page.input_value("#evLoc") == "Zoo"
    page.click("#evOk")
    page.wait_for_timeout(400)
    b = fake.posts("/api/calendar/events")[-1]
    assert (b["start_time"], b["end_time"], b["location"]) == ("08:30", "14:00", "Zoo") and opened.links == []


def test_the_dialog_shows_which_calendar_and_who_the_event_goes_to(make_page):
    page, fake, _ = make_page(now=NOW, fixed=True)
    to_server(page, fake)
    card(page, "Zoo").locator('[data-sg="edit"]').click()
    t = page.inner_text("#evTarget")
    assert "Oprettes i Familiekalender" in t and "Tildeles: Carla, Leo" in t
    page.fill("#evTitle", "Tur til Zoo")
    assert "Hele familien (intet navn i titlen)" in page.inner_text("#evTarget")
    page.fill("#evTitle", "Hugo + Leopard-klubben")                                          # "Leo" i "Leopard" er ikke Leo
    assert page.inner_text("#evTarget").endswith("Tildeles: Hugo")


def test_dismiss_and_undo_and_the_choice_is_remembered_after_reload(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    page.click("#v-sugg")
    card(page, "Zoo").locator('[data-sg="dismiss"]').click()
    assert any(s.startswith("Afvist") for s in sections(page)) and page.locator(".suggwrap > .sg").count() == 2
    page.locator("details.sdone summary", has_text="Afvist").click()
    page.locator('[data-sg="restore"]').click()
    assert page.locator(".suggwrap > .sg").count() == 3 and page.inner_text("#suggCount") == "3"
    card(page, "Lejrskole").locator('[data-sg="dismiss"]').click()
    page.reload()
    page.wait_for_timeout(500)
    assert page.inner_text("#suggCount") == "2"                                    # huskes lokalt uden server


def test_a_manual_option_in_a_message_opens_the_same_dialog(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    page.click("#v-mail")
    page.wait_for_timeout(300)
    page.locator(".mrow").first.click()
    page.wait_for_timeout(300)
    assert page.locator(".calopt").count() >= 1
    page.locator("[data-addcal]").first.click()
    assert page.input_value("#evTitle") and page.input_value("#evDate") and page.evaluate("evDlg.open")


# ---------------------------------------------------------------- med server
def test_with_a_server_creation_is_direct_and_errors_stay_in_the_dialog(make_page):
    page, fake, _ = make_page(now=NOW, fixed=True)
    to_server(page, fake)
    c = card(page, "Zoo")
    assert c.locator('[data-sg="create"]').inner_text() == "Opret i kalender"
    fake.fail_event = True
    c.locator('[data-sg="create"]').click()
    page.click("#evOk")
    page.wait_for_timeout(400)
    assert "403" in page.inner_text("#evErr") and page.evaluate("evDlg.open") and not page.locator("#evOk").is_disabled()
    fake.fail_event = False
    page.click("#evOk")
    page.wait_for_timeout(500)
    body = fake.posts("/api/calendar/events")[-1]
    assert (body["title"], body["date"], body["start_time"], body["end_time"], body["location"]) == ("Carla + Leo: Tur til Zoo", "2026-10-08", "08:30", "14:00", "Zoo")
    assert not page.evaluate("evDlg.open") and sections(page)[0].startswith("Oprettet") and page.locator("details.sdone a.btn").count() >= 1
    page.locator("details.sdone summary").first.click()
    page.locator('[data-sg="remove"]').click()
    page.wait_for_timeout(500)
    assert page.locator(".suggwrap > .sg").count() == 3


# ---------------------------------------------------------------- lærte regler
def test_learning_from_a_manual_activity(make_page):
    page, fake, _ = make_page(now=NOW, fixed=True)
    page.click("#v-mail")
    page.locator(".mrow").first.click()
    page.locator("[data-addcal]").first.click()
    assert page.locator("#evLearn").evaluate("e=>e.classList.contains('hidden')")           # uden server: ingen læringssektion
    page.keyboard.press("Escape")
    fake.use_demo(page)
    page.reload()
    page.wait_for_timeout(600)
    page.click("#v-mail")
    page.wait_for_timeout(300)
    page.locator(".mrow").first.click()
    page.locator("[data-addcal]").first.click()
    page.wait_for_timeout(250)
    assert not page.locator("#evLearn").evaluate("e=>e.classList.contains('hidden')")
    chips = page.evaluate("[...document.querySelectorAll('#evLearnChips input')].map(i=>[i.dataset.text,i.checked])")
    assert ["naturcentret", True] in chips
    page.fill("#evLearnCustom", "mødes")                                                    # for almindeligt – skal afvises, men må ikke hindre oprettelsen
    page.click("#evOk")
    page.wait_for_timeout(600)
    ev, learn = fake.posts("/api/calendar/events")[-1], fake.posts("/api/learned")[-1]
    assert ev["source"]["id"] and ("keyword", "naturcentret") in [(r["kind"], r["text"]) for r in learn["rules"]]
    assert "mødes" in page.inner_text("#toast").lower() or "almindeligt" in page.inner_text("#toast").lower()
    page.click("#v-sugg")
    page.wait_for_timeout(400)
    page.locator("details.sdone summary", has_text="Lærte regler").click()
    assert "naturcentret" in page.locator(".rule").first.inner_text()
    page.locator('[data-rule="toggle"]').first.click()
    page.wait_for_timeout(300)
    assert page.locator(".rule.off").count() == 1
    page.locator('[data-rule="remove"]').first.click()
    page.wait_for_timeout(300)
    assert page.locator(".rule").count() == 0 and fake.posts("/api/learned/remove")


# ---------------------------------------------------------------- aflysning og flytning
def change(kind, source="app", **over):
    base = {"id": {"cancel": "sg_cccccccccccc", "move": "sg_dddddddddddd", "hold": "sg_eeeeeeeeeeee"}[kind], "type": "suggestion", "kind": kind, "category": "ændring",
            "label": {"cancel": "Aflyst", "move": "Flyttet", "hold": "Udsat"}[kind], "title": "Forældremøde", "calendar_title": "Hugo: Forældremøde", "start": "2026-10-21", "end": "2026-10-21",
            "old_start": "2026-10-21", "all_day": False, "start_time": "19:00", "end_time": None, "location": None, "people": ["hugo"], "description": "", "confidence": "høj",
            "reason": "nævner «flyttet» og en dato", "sources": [{"type": "message", "id": "msg:1", "title": "Forældremøde", "label": "besked", "date": "2026-10-01"}],
            "target": {"key": "sg_aaaaaaaaaaaa" if source == "app" else None, "event_id": "fp1" if source == "app" else None, "title": "Hugo: Forældremøde", "start": "2026-10-21", "end": "2026-10-21", "source": source}}
    if kind == "move":
        base.update(start="2026-11-04", end="2026-11-04")
    base.update(over)
    return base


def with_changes(make_page, *changes, server=True):
    page, fake, opened = make_page(now=NOW, fixed=True)
    if server:
        fake.use_demo(page)
    else:
        fake.family = None
    fake.use_demo(page, suggestions=list(changes)) if server else None
    page.reload()
    page.wait_for_timeout(600)
    page.click("#v-sugg")
    page.wait_for_timeout(300)
    return page, fake, opened


def test_a_cancellation_card_explains_what_it_concerns_and_offers_removal(make_page):
    page, fake, _ = with_changes(make_page, change("cancel", title="Tur til Zoo", calendar_title="Hugo: Tur til Zoo", reason="nævner «aflyst»"))
    c = page.locator(".sg.change")
    assert c.locator("h3").inner_text() == "Tur til Zoo er aflyst" and "Aflyst" in c.locator(".catchip").inner_text()
    assert "I kalenderen: «Hugo: Forældremøde»" in c.inner_text()
    assert [b.inner_text() for b in c.locator(".sgact .btn").all()] == ["Fjern fra kalender", "Behold aftalen"]
    assert page.inner_text("#suggCount") == "1"                                              # tæller med som et forslag


def test_removing_asks_first_and_then_calls_the_server(make_page):
    page, fake, _ = with_changes(make_page, change("cancel"))
    page.once("dialog", lambda d: d.dismiss())
    page.click('[data-ch="cancel"]')
    page.wait_for_timeout(300)
    assert fake.posts("/api/calendar/cancel") == []                                            # afbrudt = intet sker
    page.once("dialog", lambda d: (setattr(page, "_msg", d.message), d.accept()))
    page.click('[data-ch="cancel"]')
    page.wait_for_timeout(600)
    assert "Hugo: Forældremøde" in page._msg
    assert fake.posts("/api/calendar/cancel") == [{"key": "sg_cccccccccccc", "target_key": "sg_aaaaaaaaaaaa"}]
    assert sections(page) == ["Behandlet (1)"] and page.locator(".suggwrap > .sg").count() == 0
    page.locator("details.sdone summary", has_text="Behandlet").click()
    assert "fjernet fra kalenderen" in page.locator(".sg.change").inner_text()


def test_a_failed_removal_leaves_the_card_where_it_was(make_page):
    page, fake, _ = with_changes(make_page, change("cancel"))
    fake.fail_change = 403
    page.once("dialog", lambda d: d.accept())
    page.click('[data-ch="cancel"]')
    page.wait_for_timeout(600)
    assert page.locator(".suggwrap > .sg.change").count() == 1 and "403" in page.inner_text("#toast") and sections(page) == []


def test_keeping_the_event_dismisses_the_suggestion_without_touching_the_calendar(make_page):
    page, fake, _ = with_changes(make_page, change("cancel"))
    page.click('.sg.change [data-sg="dismiss"]')
    page.wait_for_timeout(300)
    assert any(s.startswith("Afvist") for s in sections(page)) and fake.posts("/api/calendar/cancel") == []


def test_moving_opens_the_dialog_with_the_new_time_and_sends_the_target(make_page):
    page, fake, _ = with_changes(make_page, change("move"))
    assert "Ny tid:" in page.locator(".sg.change").inner_text() and page.locator(".sg.change h3").inner_text() == "Forældremøde er flyttet"
    page.click('[data-ch="move"]')
    assert page.inner_text("#evHead") == "Flyt aftalen" and page.input_value("#evTitle") == "Hugo: Forældremøde"
    assert page.input_value("#evDate") == "04/11/2026" and page.input_value("#evSt") == "19:00" and page.inner_text("#evOk") == "Flyt aftalen"
    assert page.locator("#evLearn").evaluate("e=>e.classList.contains('hidden')")
    page.fill("#evSt", "19:30")
    page.click("#evOk")
    page.wait_for_timeout(600)
    body = fake.posts("/api/calendar/move")[-1]
    assert (body["key"], body["target_key"], body["date"], body["start_time"], body["title"]) == ("sg_dddddddddddd", "sg_aaaaaaaaaaaa", "2026-11-04", "19:30", "Hugo: Forældremøde")
    assert not page.evaluate("evDlg.open") and sections(page) == ["Behandlet (1)"]


def test_a_failed_move_keeps_the_dialog_open_with_the_reason(make_page):
    page, fake, _ = with_changes(make_page, change("move"))
    fake.fail_change = 403
    page.click('[data-ch="move"]')
    page.click("#evOk")
    page.wait_for_timeout(500)
    assert "403" in page.inner_text("#evErr") and page.evaluate("evDlg.open") and not page.locator("#evOk").is_disabled()


def test_events_not_created_by_the_app_are_information_only(make_page):
    page, fake, _ = with_changes(make_page, change("move", source="google"), change("cancel", source="google"))
    for c in page.locator(".sg.change").all():
        assert [b.inner_text() for b in c.locator(".sgact .btn").all()] == ["Åbn Google Kalender", "Forstået"]
        assert "ikke oprettet via appen" in c.inner_text()
    assert page.locator('[data-ch]').count() == 0


def test_without_calendar_writing_changes_are_also_information_only(make_page):
    page, fake, _ = make_page(now=NOW, fixed=True)
    fake.cal["enabled"] = False
    fake.use_demo(page, suggestions=[change("cancel")])
    page.reload()
    page.wait_for_timeout(600)
    page.click("#v-sugg")
    assert page.locator('[data-ch]').count() == 0 and "Åbn Google Kalender" in page.locator(".sg.change").inner_text()
