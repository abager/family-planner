"""Husk og lektier" i dagsoverblikket viser fokusdagen (i dag, efter kl. 18 i morgen) og de to næste dage – som kioskens
"Husk". En opgave står kun én gang, på den første dag den gælder: "hver dag", "snarest" og perioder (startdato → frist)
altså på fokusdagen. Ting længere ude står kun i "Ugen".
Torsdag 1. oktober 2026 er "i dag" i alle tests.
"""
import datetime as dt
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Copenhagen")


def at(h, m=0):
    return dt.datetime(2026, 10, 1, h, m, tzinfo=TZ)


def task(id, title, due, **kw):
    return {"id": id, "title": title, "due": due, "kind": "husk", "people": ["hugo"], "person": "hugo", "confidence": "høj", **kw}


TASKS = [
    task("t-today", "Gymnastiktøj", "2026-10-01"),
    task("t-tomorrow", "Madpakke til tur", "2026-10-02"),
    task("t-saturday", "Kage til fødselsdag", "2026-10-03"),
    task("t-period", "Projektopgave", "2026-10-05", **{"from": "2026-09-29", "kind": "lektie"}),
    task("t-open", "Aflever tilmelding", "2026-09-30", **{"from": "2026-09-30", "openEnded": True, "kind": "handling"}),
    task("t-daily", "Læs 20 minutter", "2026-09-30", **{"from": "2026-09-30", "recurring": "daily", "kind": "lektie"}),
]


def tasks_shown(page, day=None):
    """Opgaverne i én dags gruppe (standard: fokusdagen, den første gruppe)."""
    sel = f'#taskList .hgroup[data-day="{day}"]' if day else "#taskList .hgroup:first-child"
    return page.evaluate(f"[...document.querySelectorAll('{sel} [data-task]')].map(b=>b.dataset.task).sort()")


def all_shown(page):
    return page.evaluate("[...document.querySelectorAll('#taskList [data-task]')].map(b=>b.dataset.task)")


def load(make_page, hour, tasks):
    page, fake, _ = make_page(now=at(hour), fixed=True)
    fake.use_demo(page, tasks=tasks)
    page.reload()
    page.wait_for_timeout(500)
    return page


def test_during_the_day_only_today_and_the_exceptions_are_listed(make_page):
    page = load(make_page, 10, TASKS)
    assert tasks_shown(page) == sorted(["t-today", "t-period", "t-open", "t-daily"])


def test_after_the_evening_hour_tomorrow_is_listed_instead_of_today(make_page):
    page = load(make_page, 18, TASKS)
    assert tasks_shown(page) == sorted(["t-tomorrow", "t-period", "t-open", "t-daily"])
    assert page.inner_text("#taskList .hgroup:first-child .hday").lower() == "i morgen"


def test_the_next_two_days_have_their_own_groups_and_each_task_is_listed_once(make_page):
    page = load(make_page, 10, TASKS)
    assert page.evaluate("[...document.querySelectorAll('#taskList .hgroup')].map(g=>g.dataset.day)") == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert tasks_shown(page, "2026-10-02") == ["t-tomorrow"] and tasks_shown(page, "2026-10-03") == ["t-saturday"]
    assert len(all_shown(page)) == len(set(all_shown(page)))


def test_later_than_three_days_is_only_on_its_day_in_the_week_view(make_page):
    later = TASKS + [task("t-sunday", "Pakke taske", "2026-10-04")]
    page = load(make_page, 10, later)
    assert "t-sunday" not in all_shown(page)
    page.click("#v-week")
    page.wait_for_timeout(200)
    assert page.locator('#weekView [data-task="t-sunday"]').count() >= 1


def test_an_empty_list_names_the_focus_day(make_page):
    later = [task("t-saturday", "Kage til fødselsdag", "2026-10-03")]
    assert "Intet i dag" in load(make_page, 10, later).inner_text("#taskList .hgroup:first-child")
    assert "Intet i morgen" in load(make_page, 18, later).inner_text("#taskList .hgroup:first-child")
