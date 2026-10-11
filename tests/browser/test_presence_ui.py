"""Komme/Gå i appen: linjen i barnets bane (også telefon og kiosk), panelet, skrivekoden, sygemelding med bekræftelse,
ændring af en dag (også som gentagelse) og fejl fra Aula. Simuleret server (fake_server.py), appens egne demodata."""
import datetime as dt
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Copenhagen")
NOW = dt.datetime(2026, 10, 1, 10, 0, tzinfo=TZ)        # torsdag formiddag


def open_app(make_page, site, url="/index.html", width=1280, server=True, **over):
    page, fake, opened = make_page(now=NOW, fixed=True, goto=False, width=width)
    page.goto(site.url + url)
    if server:
        fake.use_demo(page)
        for k, v in over.items():
            setattr(fake, k, v)
        page.reload()
    page.wait_for_timeout(500)
    return page, fake, opened


def line(page, person, sel=".dpres"):
    i = ["hugo", "carla", "leo", "andreas", "monica"].index(person)
    return page.locator(f"#dayLanes .dpresrow .dpc").nth(i).inner_text().strip()


def open_panel(page, person="hugo"):
    i = ["hugo", "carla", "leo", "andreas", "monica"].index(person)
    page.locator("#dayLanes .dpresrow .dpc").nth(i).locator("button").click()
    page.wait_for_timeout(300)


def unlock(page, code="1111"):
    page.fill("#pCode", code)
    page.click(".punlock button[type=submit]")
    page.wait_for_timeout(300)


def test_each_child_with_komme_gaa_gets_a_line_under_the_name_and_leo_without_it_gets_none(make_page, site):
    page, *_ = open_app(make_page, site)
    assert "Kom 07.52" in line(page, "hugo") and "Sendes hjem 15.00" in line(page, "hugo")
    assert "Hentes 15.30 af Mor" in line(page, "carla")
    assert line(page, "leo") == "" and line(page, "andreas") == ""


def test_the_panel_asks_for_the_personal_code_before_any_change(make_page, site):
    page, fake, _ = open_app(make_page, site)
    open_panel(page)
    body = page.inner_text("#dBody")
    assert "Din kode til Komme/Gå" in body and "Meld syg" not in body
    unlock(page, "0000")
    assert "Forkert kode" in page.inner_text("#dBody")
    unlock(page)
    body = page.inner_text("#dBody")
    assert "Meld syg i dag" in body and "Låst op som Andreas" in body


def test_report_sick_needs_a_confirmation_names_both_targets_and_cannot_be_undone(make_page, site):
    page, fake, _ = open_app(make_page, site)
    open_panel(page)
    unlock(page)
    assert "Skolen: Intet fravær i dag" in page.inner_text("#dBody")
    page.click("[data-pa=sick]")
    body = page.inner_text("#dBody")
    assert "Meld Hugo syg i dag?" in body and "Komme/Gå og skolen" in body and "kan ikke fortrydes" in body
    assert not fake.posts("/api/presence/sick")
    page.click("[data-pa=do]")
    page.wait_for_timeout(400)
    assert fake.posts("/api/presence/sick") == [{"person": "hugo"}]
    body = page.inner_text("#dBody")
    assert "✓ Komme/Gå" in body and "✓ Skolen" in body and "Skolen: Meldt syg i dag" in body
    assert "Fortryd" not in body and page.locator("[data-pa=sick]").count() == 0
    assert "Meldt syg" in page.inner_text("#toast") and line(page, "hugo").endswith("Syg")


def test_when_the_school_fails_the_panel_says_so_and_offers_to_report_only_the_school(make_page, site):
    page, fake, _ = open_app(make_page, site)
    open_panel(page)
    unlock(page)
    fake.sfail = {"tabulex": "Tabulex svarede 500 (sygemelding)."}
    page.click("[data-pa=sick]")
    page.click("[data-pa=do]")
    page.wait_for_timeout(400)
    body = page.inner_text("#dBody")
    assert "✓ Komme/Gå" in body and "✗ Skolen" in body and "svarede 500" in body
    assert page.inner_text("[data-pa=sick]") == "Meld syg til skolen"
    assert "Syg · ikke meldt til skolen" in line(page, "hugo")
    page.click("[data-pa=sick]")
    assert "Sendes til: skolen" in page.inner_text("#dBody")
    page.click("[data-pa=do]")
    page.wait_for_timeout(400)
    assert "✓ Skolen" in page.inner_text("#dBody") and line(page, "hugo").endswith("Syg")


def test_on_a_day_without_school_the_button_is_disabled(make_page, site):
    page, fake, _ = open_app(make_page, site)
    fake.family["absence"][1]["schoolDay"] = False
    page.reload(); page.wait_for_timeout(500)
    open_panel(page)
    unlock(page)
    btn = page.get_by_role("button", name="Ikke skoledag i dag")
    assert btn.is_disabled() and "Skolen: Skolefridag" in page.inner_text("#dBody")
    assert page.locator("[data-pa=sick]").count() == 0


def test_after_the_evening_switch_there_is_no_sick_button(make_page, site):
    page, fake, opened = make_page(now=NOW.replace(hour=18, minute=5), fixed=True, goto=False, width=1280)
    page.goto(site.url + "/index.html")
    fake.use_demo(page)
    page.reload(); page.wait_for_timeout(500)
    page.locator("#dayLanes .dpresrow .dpc").nth(0).locator("button").click()
    page.wait_for_timeout(300)
    unlock(page)
    body = page.inner_text("#dBody")
    assert "Låst op som Andreas" in body and "Meld syg" not in body and "Ikke skoledag" not in body


def test_a_school_only_sick_report_is_visible_on_the_line_and_leo_gets_none(make_page, site):
    page, fake, _ = open_app(make_page, site)
    fake.family["absence"][1].update(sick=True, reason="Sygdom", text="Meldt syg")
    page.reload(); page.wait_for_timeout(500)
    assert "Syg hos skolen · ikke i Komme/Gå" in line(page, "hugo") and line(page, "leo") == ""


def test_changing_a_day_weekly_warns_that_the_week_schedule_changes_and_sends_the_repeat(make_page, site):
    page, fake, _ = open_app(make_page, site)
    open_panel(page, "carla")
    unlock(page)
    page.locator("[data-pa=edit]").first.click()
    page.fill("#pExit", "14:30")
    assert page.locator("#pRepWarn").is_hidden()
    page.check("input[name=pRep][value=weekly]")
    assert page.locator("#pRepWarn").is_visible()
    page.click(".pedit button[type=submit]")
    page.wait_for_timeout(200)
    confirm = page.inner_text("#dBody")
    assert "→" in confirm and "14.30" in confirm and "ugeskemaet" in confirm
    page.click("[data-pa=do]")
    page.wait_for_timeout(400)
    sent = fake.posts("/api/presence/day")[-1]
    assert sent["person"] == "carla" and sent["exit"] == "14:30" and sent["repeat"] == "weekly" and sent["exitWith"] == "Monica (Mor)"
    assert "Hentes 14.30 af Mor" in line(page, "carla")


def test_a_pickup_person_is_required_when_someone_collects_the_child(make_page, site):
    page, fake, _ = open_app(make_page, site)
    open_panel(page, "carla")
    unlock(page)
    page.locator("[data-pa=edit]").first.click()
    page.select_option("#pWho", "")
    page.click(".pedit button[type=submit]")
    page.wait_for_timeout(200)
    assert "Vælg, hvem der henter" in page.inner_text("#dBody") and not fake.posts("/api/presence/day")


def test_an_error_from_aula_is_shown_in_the_panel(make_page, site):
    page, fake, _ = open_app(make_page, site)
    open_panel(page)
    unlock(page)
    fake.pfail = (502, "Aula afviste ændringen: 400 Bad Request")
    page.click("[data-pa=sick]")
    page.click("[data-pa=do]")
    page.wait_for_timeout(400)
    assert "Aula afviste ændringen" in page.inner_text("#dBody") and "Meld syg i dag" in page.inner_text("#dBody")


def test_undo_is_offered_after_a_change_of_times_and_names_who_made_it(make_page, site):
    page, fake, _ = open_app(make_page, site)
    open_panel(page, "carla")
    unlock(page, "2222")
    page.locator("[data-pa=edit]").first.click()
    page.fill("#pExit", "14:30")
    page.click(".pedit button[type=submit]")
    page.wait_for_timeout(200)
    page.click("[data-pa=do]")
    page.wait_for_timeout(400)
    body = page.inner_text("#dBody")
    assert "Fortryd seneste ændring" in body and "Monica" in body
    page.click("[data-pa=undo]")
    page.click("[data-pa=do]")
    page.wait_for_timeout(400)
    assert fake.posts("/api/presence/undo") == [{"person": "carla"}]


def test_with_writing_switched_off_on_the_server_there_is_no_code_field(make_page, site):
    page, *_ = open_app(make_page, site, pw={"enabled": False, "configured": True})
    open_panel(page)
    body = page.inner_text("#dBody")
    assert "Planlagt" in body and "Din kode" not in body


def test_without_a_server_the_panel_only_shows_information(make_page, site):
    page, *_ = open_app(make_page, site, server=False)
    open_panel(page)
    assert "Planlagt" in page.inner_text("#dBody") and page.locator("#pCode").count() == 0


def test_on_a_phone_the_lines_sit_above_the_list(make_page, site):
    page, *_ = open_app(make_page, site, width=390)
    strip = page.locator(".dlist .lpresrow .lpres")
    assert strip.count() == 2 and "Syg" not in strip.first.inner_text()
    strip.nth(1).click()
    page.wait_for_timeout(300)
    assert "Carla: Komme/Gå" in page.inner_text("#dTitle")


def test_the_kiosk_shows_komme_gaa_read_only(make_page, site):
    page, fake, _ = open_app(make_page, site, url="/index.html?kiosk=1")
    k = page.locator('#kPeople .k-person[data-person="carla"] .k-pres')
    assert "Hentes 15.30 af Mor" in k.inner_text()
    k.click()
    page.wait_for_timeout(300)
    body = page.inner_text("#dBody")
    assert "Planlagt" in body and "Din kode" not in body and "Meld syg" not in body
    assert "Skolen: Intet fravær i dag" in body                         # skolens status, også kun til at læse
    assert page.locator('#kPeople .k-person[data-person="leo"] .k-pres').count() == 0


def test_goes_home_alone_shows_aulas_interval_on_the_line_and_in_the_panel(make_page, site):
    page, fake, _ = open_app(make_page, site)
    day = next(d for d in fake.family["presence"][1]["days"] if d["date"] == NOW.date().isoformat())
    day.update(activity=1, exit=None, exitWith=None, selfFrom="14:00", selfTo="14:30")   # Carlas institution har ikke 1 på listen
    page.reload(); page.wait_for_timeout(500)
    assert "Går selv 14.00–14.30" in line(page, "carla")
    open_panel(page, "carla")
    assert "08.00 · Går selv 14.00–14.30" in page.inner_text("#dBody")
    unlock(page)
    page.locator("[data-pa=edit]").first.click()
    assert page.input_value("#pExit") == "14:30"
    page.click(".pedit button[type=submit]")
    page.wait_for_timeout(200)
    assert "bliver til ét tidspunkt" in page.inner_text("#dBody")
