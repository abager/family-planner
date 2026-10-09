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


def test_report_sick_needs_a_confirmation_and_then_the_line_says_sick(make_page, site):
    page, fake, _ = open_app(make_page, site)
    open_panel(page)
    unlock(page)
    page.click("[data-pa=sick]")
    assert "Meld Hugo syg i dag?" in page.inner_text("#dBody") and not fake.posts("/api/presence/sick")
    page.click("[data-pa=do]")
    page.wait_for_timeout(400)
    assert fake.posts("/api/presence/sick") == [{"person": "hugo", "sick": True}]
    assert "Fortryd sygemelding" in page.inner_text("#dBody") and "Gemt i Aula" in page.inner_text("#toast")
    assert line(page, "hugo").endswith("Syg")


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


def test_undo_is_offered_after_a_change_and_names_who_made_it(make_page, site):
    page, fake, _ = open_app(make_page, site)
    open_panel(page)
    unlock(page, "2222")
    page.click("[data-pa=sick]")
    page.click("[data-pa=do]")
    page.wait_for_timeout(400)
    body = page.inner_text("#dBody")
    assert "Fortryd seneste ændring" in body and "Monica" in body
    page.click("[data-pa=undo]")
    page.click("[data-pa=do]")
    page.wait_for_timeout(400)
    assert fake.posts("/api/presence/undo") == [{"person": "hugo"}]


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
    assert page.locator('#kPeople .k-person[data-person="leo"] .k-pres').count() == 0
