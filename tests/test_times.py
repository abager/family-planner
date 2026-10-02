"""Start- og sluttider: rigtige sluttider vises, tænkte markeres (endInferred), og en lektion varer 45 minutter, når intet andet står."""
import asyncio
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import activities as A
import fetch_family as F
import schedule
import suggestions as S

TZ = ZoneInfo("Europe/Copenhagen")
D2 = dt.date.today() + dt.timedelta(days=2)


def ics(*vevents):
    body = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//da//"]
    for uid, extra in vevents:
        body += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{D2:%Y%m%d}T080000Z", f"SUMMARY:{uid}", *extra, "END:VEVENT"]
    return "\r\n".join(body + ["END:VCALENDAR"]) + "\r\n"


def google_events(cfg, ical, *vevents):
    ical.path.write_text(ics(*vevents))
    asyncio.run(F.run_once(cfg, False))
    return {e["title"]: e for e in json.loads(Path(cfg["output"]).read_text("utf-8"))["events"] if e["source"] == "google"}


# ---------------------------------------------------------------- iCal
def test_ical_end_time_is_kept_and_missing_end_is_marked_not_invented(cfg, ical):
    by = google_events(cfg, ical,
                       ("med-slut", [f"DTSTART;TZID=Europe/Copenhagen:{D2:%Y%m%d}T160000", f"DTEND;TZID=Europe/Copenhagen:{D2:%Y%m%d}T173000"]),
                       ("uden-slut", [f"DTSTART;TZID=Europe/Copenhagen:{D2:%Y%m%d}T140000"]),
                       ("varighed", [f"DTSTART;TZID=Europe/Copenhagen:{D2:%Y%m%d}T090000", "DURATION:PT45M"]))
    assert by["med-slut"]["end"][11:16] == "17:30" and "endInferred" not in by["med-slut"]
    assert by["uden-slut"]["endInferred"] is True                         # vises som "kl. 14.00", ikke "14.00–15.00"
    assert by["varighed"]["end"][11:16] == "09:45" and "endInferred" not in by["varighed"]


def test_app_events_without_end_time_are_marked(cfg):
    ev = S.app_event({"title": "Tandlæge", "date": D2.isoformat(), "start_time": "14:00"}, "fpx")
    assert ev["endInferred"] is True
    assert "endInferred" not in S.app_event({"title": "Møde", "date": D2.isoformat(), "start_time": "14:00", "end_time": "15:00"}, "fpx")
    assert "endInferred" not in S.app_event({"title": "Lejrskole", "date": D2.isoformat(), "all_day": True}, "fpx")


# ---------------------------------------------------------------- lektioner
def lesson(title, start, end):
    s = dt.datetime.combine(D2, dt.time.fromisoformat(start), TZ)
    e = dt.datetime.combine(D2, dt.time.fromisoformat(end), TZ)
    return NS(title=title, start_datetime=s, end_datetime=e, substitute_names=None, substitute_name=None, has_substitute=False,
              teacher_names=None, teacher_name=None, id=title, _raw={}, location=None)


def test_lessons_keep_their_end_and_a_missing_one_gets_45_minutes():
    ls = schedule.build_lessons([lesson("DAN", "08:00", "08:45"), lesson("MAT", "09:00", "09:00")], TZ)
    assert [(l["start"], l["end"], l.get("endInferred", False)) for l in ls] == [("08:00", "08:45", False), ("09:00", "09:45", True)]


def test_lesson_length_can_be_configured():
    ls = schedule.build_lessons([lesson("MAT", "09:00", "09:00")], TZ, lesson_minutes=50)
    assert ls[0]["end"] == "09:50"
    assert F.lesson_minutes({}) == 45 and F.lesson_minutes({"aula": {"lesson_minutes": 50}}) == 50


def test_schedule_summary_shows_both_times():
    assert schedule.schedule_summary([{"start": "08:00", "end": "08:45", "title": "Dansk", "substitute": "Jeppe L."}]) == "08.00–08.45 Dansk (vikar: Jeppe L.)"


def test_lesson_minutes_reach_the_app(cfg):
    cfg["aula"]["lesson_minutes"] = 50
    asyncio.run(F.run_once(cfg, False))
    assert json.loads(Path(cfg["output"]).read_text("utf-8"))["settings"]["lessonMinutes"] == 50


# ---------------------------------------------------------------- forslag i en lektion
def test_a_test_with_a_start_time_lasts_one_lesson():
    assert A.lesson_end_time("10:00", None, "Matematiktest tirsdag kl. 10", "test") == "10:45"
    assert A.lesson_end_time("10:00", None, "Vi har fremlæggelse i 3. lektion kl. 10") == "10:45"


def test_other_activities_and_given_end_times_are_left_alone():
    assert A.lesson_end_time("10:00", None, "Tur til Zoo kl. 10", "tur") is None
    assert A.lesson_end_time("10:00", "12:00", "Test kl. 10-12", "test") == "12:00"
    assert A.lesson_end_time(None, None, "Test i 3. lektion", "test") is None
