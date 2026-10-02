"""Familiekalenderen læst via Google Calendar API (servicekontoen) i stedet for iCal – mod den simulerede Google."""
import asyncio
import datetime as dt
import json
from pathlib import Path

import fetch_family as F
import suggestions as S

TODAY = dt.date.today()
D2 = (TODAY + dt.timedelta(days=2)).isoformat()


def go(cfg):
    return asyncio.run(F.run_once(cfg, False))


def gevents(cfg):
    return [e for e in json.loads(Path(cfg["output"]).read_text("utf-8"))["events"] if e["source"] == "google"]


def api_on(cfg):
    cfg["calendar_write"]["read_via_api"] = True
    return cfg


def test_the_write_calendar_is_read_via_the_api_not_ical(cfg, google):
    google.add("e1", "Hugo fodbold", {"dateTime": f"{D2}T16:00:00+02:00"}, {"dateTime": f"{D2}T17:30:00+02:00"}, location="Banen")
    go(api_on(cfg))
    evs = gevents(cfg)
    assert [e["title"] for e in evs] == ["Hugo fodbold"]                    # iCal-aftalerne (Svømning …) er ikke med
    e = evs[0]
    assert e["id"].startswith("g:e1@google.com:") and e["people"] == ["hugo"] and e["location"] == "Banen"
    assert e["start"].endswith("16:00+02:00") and e["end"].endswith("17:30+02:00") and "endInferred" not in e
    assert any(l[0] == "list" and l[2]["singleEvents"] == "true" for l in google.log)


def test_all_day_events_get_an_inclusive_end_like_ical(cfg, google):
    d3 = (TODAY + dt.timedelta(days=3)).isoformat()
    google.add("e2", "Lejrskole", {"date": D2}, {"date": (TODAY + dt.timedelta(days=4)).isoformat()})
    go(api_on(cfg))
    e = gevents(cfg)[0]
    assert e["allDay"] and e["start"][:10] == D2 and e["end"][:10] == d3 and e["end"][11:16] == "23:59"


def test_all_pages_are_fetched(cfg, google):
    google.page_size = 2
    for i in range(5):
        google.add(f"p{i}", f"Aftale {i}", {"dateTime": f"{D2}T1{i}:00:00+02:00"}, {"dateTime": f"{D2}T1{i}:30:00+02:00"})
    go(api_on(cfg))
    assert len(gevents(cfg)) == 5 and sum(1 for l in google.log if l[0] == "list") == 3


def test_events_created_by_the_app_are_recognised_and_keep_an_unknown_end(cfg, google):
    g = S.GoogleCalendar(cfg)
    asyncio.run(g.create("ev_aaaaaaaaaaaa", {"title": "Tandlæge", "date": D2, "start_time": "14:00"}))
    asyncio.run(g.create("ev_bbbbbbbbbbbb", {"title": "Møde", "date": D2, "start_time": "09:00", "end_time": "10:00"}))
    go(api_on(cfg))
    by = {e["title"]: e for e in gevents(cfg)}
    assert by["Tandlæge"]["appCreated"] and by["Tandlæge"]["endInferred"]       # ingen sluttid angivet – vises kun med start
    assert by["Møde"]["appCreated"] and "endInferred" not in by["Møde"]


def test_api_failure_falls_back_to_ical(cfg, google):
    google.fail["family123"] = 403
    go(api_on(cfg))
    assert sorted(e["title"] for e in gevents(cfg)) == ["Bedsteforældre på besøg", "Svømning"]


def test_api_failure_without_ical_keeps_the_previous_events(cfg, google):
    google.add("e1", "Hugo fodbold", {"dateTime": f"{D2}T16:00:00+02:00"}, {"dateTime": f"{D2}T17:00:00+02:00"})
    del cfg["google"][0]["ical_url"]
    go(api_on(cfg))
    google.fail["family123"] = 500
    go(cfg)
    assert [e["title"] for e in gevents(cfg)] == ["Hugo fodbold"]


def test_other_calendars_are_still_read_via_ical(cfg, google, ical):
    cfg["google"].append({"name": "Arbejde", "ical_url": f"{ical.url}/family.ics", "default_people": ["andreas"]})
    google.add("e1", "Hugo fodbold", {"dateTime": f"{D2}T16:00:00+02:00"}, {"dateTime": f"{D2}T17:00:00+02:00"})
    go(api_on(cfg))
    cals = sorted({(e["calendar"], e["title"]) for e in gevents(cfg)})
    assert ("Familiekalender", "Hugo fodbold") in cals and ("Arbejde", "Svømning") in cals
