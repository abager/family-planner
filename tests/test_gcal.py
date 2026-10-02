"""Skrivning til Google Kalender mod den simulerede Google (som kontrollerer servicekontoens JWT-signatur)."""
import asyncio

import pytest

import suggestions as S

LEJRSKOLE = {"title": "Hugo: Lejrskole", "date": "2026-10-12", "end_date": "2026-10-16", "all_day": True, "location": "Jylland", "description": "Fra besked"}
ZOO = {"title": "Tur til Zoo", "date": "2026-10-08", "all_day": False, "start_time": "08:30", "end_time": "14:00", "location": "Zoo"}


def run(c):
    return asyncio.run(c)


def test_service_account_token_is_valid_and_scoped(cfg, google):
    g = S.GoogleCalendar(cfg)
    assert g.enabled and g.calendar_id == "family123@group.calendar.google.com"
    run(g.create("sg_aaaaaaaaaaaa", LEJRSKOLE))
    _, ok, alg, iss, scope, aud = next(l for l in google.log if l[0] == "token")
    assert ok and alg == "RS256" and iss.startswith("familieplan@") and scope == S.GoogleCalendar.SCOPE and aud.endswith("/token")


def test_all_day_range_uses_an_exclusive_end_and_a_legal_id(cfg, google):
    r = run(S.GoogleCalendar(cfg).create("sg_aaaaaaaaaaaa", LEJRSKOLE))
    sent = next(l[2] for l in google.log if l[0] == "insert")
    assert sent["start"] == {"date": "2026-10-12"} and sent["end"] == {"date": "2026-10-17"}
    assert set(sent["id"]) <= set("0123456789abcdefghijklmnopqrstuv") and sent["reminders"]["overrides"][0]["minutes"] == 1440
    assert r["already"] is False and r["html_link"].startswith("https://www.google.com/calendar/event")


def test_creating_the_same_suggestion_twice_makes_one_event(cfg, google):
    g = S.GoogleCalendar(cfg)
    a, b = run(g.create("sg_aaaaaaaaaaaa", LEJRSKOLE)), run(g.create("sg_aaaaaaaaaaaa", LEJRSKOLE))
    assert b["already"] is True and a["id"] == b["id"] and len(google.live()) == 1


def test_timed_event_and_default_one_hour_over_midnight(cfg, google):
    g = S.GoogleCalendar(cfg)
    e = google.events[run(g.create("ev_bbbbbbbbbbbb", ZOO))["id"]]
    assert e["start"]["dateTime"].startswith("2026-10-08T08:30") and e["end"]["dateTime"].startswith("2026-10-08T14:00")
    e2 = google.events[run(g.create("ev_cccccccccccc", {"title": "X", "date": "2026-10-08", "start_time": "23:30"}))["id"]]
    assert e2["end"]["dateTime"].startswith("2026-10-09T00:30")


def test_delete_then_recreate_gives_a_new_event(cfg, google):
    g = S.GoogleCalendar(cfg)
    r1 = run(g.create("ev_bbbbbbbbbbbb", ZOO))
    run(g.delete(r1["id"]))
    assert google.events[r1["id"]]["status"] == "cancelled"
    r2 = run(g.create("ev_bbbbbbbbbbbb", ZOO, version=1))
    assert r2["id"] != r1["id"] and len(google.live()) == 1


@pytest.mark.parametrize("bad", [{"title": " ", "date": "2026-10-08"}, {"title": "a", "date": "i morgen"}, {"title": "a", "date": "2026-10-08", "end_date": "2026-10-07"},
                                 {"title": "a", "date": "2026-10-08"}, {"title": "a", "date": "2026-10-08", "start_time": "25:99"}],
                         ids=["tom titel", "ugyldig dato", "slut før start", "mangler starttid", "ugyldigt klokkeslæt"])
def test_invalid_events_are_refused_before_google_is_called(cfg, google, bad):
    with pytest.raises(S.CalendarError):
        run(S.GoogleCalendar(cfg).create("ev_dddddddddddd", bad))
    assert [l for l in google.log if l[0] == "insert"] == []


def test_google_errors_are_explained_in_plain_danish(cfg, google):
    google.fail = {"forbidden": 403, "missing": 404}
    cfg["calendar_write"]["calendar_id"] = "forbidden1@group.calendar.google.com"
    with pytest.raises(S.CalendarError) as e:
        run(S.GoogleCalendar(cfg).create("sg_eeeeeeeeeeee", LEJRSKOLE))
    assert e.value.status == 403 and "delt" in str(e.value)                         # kalenderen er ikke delt med servicekontoen
    cfg["calendar_write"]["calendar_id"] = "missing1@group.calendar.google.com"
    with pytest.raises(S.CalendarError) as e:
        run(S.GoogleCalendar(cfg).create("sg_ffffffffffff", LEJRSKOLE))
    assert e.value.status in (404, 502) and str(e.value)


def test_missing_key_file_disables_writing_with_a_reason(cfg):
    cfg["calendar_write"]["service_account_file"] = "/findes/ikke.json"
    g = S.GoogleCalendar(cfg)
    assert not g.enabled and g.problem


def test_patch_moves_an_event_and_keeps_its_title(cfg, google):
    g = S.GoogleCalendar(cfg)
    r = run(g.create("sg_aaaaaaaaaaaa", {**ZOO, "title": "Hugo: Forældremøde"}))
    run(g.patch(r["id"], {"title": "Hugo: Forældremøde", "date": "2026-11-04", "all_day": False, "start_time": "19:00", "end_time": "20:00"}))
    e = google.events[r["id"]]
    assert e["summary"] == "Hugo: Forældremøde" and e["start"]["dateTime"].startswith("2026-11-04T19:00") and e["end"]["dateTime"].startswith("2026-11-04T20:00")
    assert len(google.live()) == 1                                                  # flyttet, ikke duplikeret


def test_patching_a_deleted_event_says_so(cfg, google):
    g = S.GoogleCalendar(cfg)
    r = run(g.create("sg_aaaaaaaaaaaa", ZOO))
    run(g.delete(r["id"]))
    with pytest.raises(S.CalendarError) as e:
        run(g.patch(r["id"], {**ZOO, "date": "2026-11-04"}))
    assert e.value.status == 404 and "findes ikke" in str(e.value)
