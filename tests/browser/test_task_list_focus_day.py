"""Husk og lektier" i dagsoverblikket viser kun fokusdagen (i dag, efter kl. 17 i morgen).

Undtagelser: "hver dag", "snarest" og perioder (startdato → frist). Ting med frist senere på ugen står kun i "Ugen".
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


def tasks_shown(page):
    return page.evaluate("[...document.querySelectorAll('#taskList [data-task]')].map(b=>b.dataset.task).sort()")


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
    assert "i morgen" in page.inner_text("#taskList")


def test_later_in_the_week_is_on_its_day_in_the_week_view(make_page):
    page = load(make_page, 10, TASKS)
    assert "t-saturday" not in tasks_shown(page)
    page.click("#v-week")
    page.wait_for_timeout(200)
    assert page.locator('#weekView [data-task="t-saturday"]').count() >= 1


def test_an_empty_list_names_the_focus_day(make_page):
    later = [task("t-saturday", "Kage til fødselsdag", "2026-10-03")]
    assert "Intet i dag" in load(make_page, 10, later).inner_text("#taskList")
    assert "Intet i morgen" in load(make_page, 18, later).inner_text("#taskList")
