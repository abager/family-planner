"""Hele hentningen (run_once) mod simulerede kilder: Google-udfald, private tråde, aflysninger og aftenens overblik."""
import asyncio
import datetime as dt
import json
from pathlib import Path

import briefing as B
import fetch_family as F
import offline_briefing as OB
import private as P
import suggestions as S
from helpers import make_ics, msg

TODAY = dt.date.today()


def go(cfg, use_aula=False):
    return asyncio.run(F.run_once(cfg, use_aula))


def family(cfg):
    return json.loads(Path(cfg["output"]).read_text("utf-8"))


def gevents(d):
    return [e for e in d["events"] if e["source"] == "google"]


# ---------------------------------------------------------------- Google nede
def test_google_outage_keeps_the_last_known_events_and_says_so(cfg, ical):
    go(cfg)
    first = family(cfg)
    assert len(gevents(first)) == 2 and first["health"]["google"]["ok"] is True
    cfg["google"][0]["ical_url"] = ical.dead
    go(cfg)
    d = family(cfg)
    assert [e["id"] for e in gevents(d)] == [e["id"] for e in gevents(first)]               # kalenderen er ikke tom
    h = d["health"]["google"]
    assert h["ok"] is False and h["failed"] == ["Familiekalender"] and h["last_ok"] == first["health"]["google"]["last_ok"]
    cfg["google"][0]["ical_url"] = f"{ical.url}/family.ics"
    go(cfg)
    assert family(cfg)["health"]["google"]["ok"] is True


def test_events_deleted_in_google_do_disappear_when_google_works(cfg, ical):
    go(cfg)
    ical.path.write_text(make_ics([("a1@test", "Svømning", TODAY + dt.timedelta(days=2))]))
    go(cfg)
    assert [e["title"] for e in gevents(family(cfg))] == ["Svømning"]


def test_a_calendar_that_never_worked_shows_nothing_rather_than_something_invented(cfg, ical):
    cfg["google"][0]["ical_url"] = ical.dead
    go(cfg)
    d = family(cfg)
    assert gevents(d) == [] and d["health"]["google"]["ok"] is False and d["health"]["google"]["last_ok"] is None


def test_health_and_settings_are_published(cfg, fake_aula):
    cfg["display"] = {"evening_hour": 18}
    go(cfg, use_aula=True)
    d = family(cfg)
    assert d["health"]["aula"]["state"] == "ok" and d["settings"]["eveningHour"] == 18


# ---------------------------------------------------------------- private tråde
PRIVATE_TEXT = "Kære Andreas og Monica, vi er bekymrede for Hugos trivsel. Utryghed i 6B. Ring på 12345678."
PUBLIC = msg(1, "Husk gymnastiktøj", "Husk gymnastiktøj torsdag.", "2026-09-30T08:00:00+02:00", unread=True)
PRIVATE = msg(2, "Samtale om Hugo", PRIVATE_TEXT, "2026-09-30T09:00:00+02:00", unread=True, sender="Lærer Anders", thread_len=2)


def private_store(cfg):
    return P.load(P.store_path(cfg))


def test_private_threads_leave_family_json_and_live_behind_a_protected_file(cfg, fake_aula):
    fake_aula.messages = [PUBLIC, PRIVATE]
    go(cfg, use_aula=True)
    d = family(cfg)
    blob = json.dumps(d, ensure_ascii=False)
    assert not any(w in blob for w in ("Utryghed", "bekymrede", "12345678", "Samtale om Hugo"))       # intet fra samtalen i den fil, alle enheder henter
    shown = next(m for m in d["messages"] if m["id"] == "msg:2")
    assert shown["private"] and shown["redacted"] and shown["subject"] == "Privat samtale" and shown["text"] == "" and shown["thread"] == [] and shown["from"] == ""
    assert shown["unread"] and shown["timestamp"]                                                        # men man kan se, at der ligger en ulæst
    assert next(m for m in d["messages"] if m["id"] == "msg:1")["text"] == "Husk gymnastiktøj torsdag."   # almindelige beskeder er urørte
    path = P.store_path(cfg)
    assert path.parent.name == "secrets" and not str(path).startswith(str(Path(cfg["output"]).parent))     # aldrig i den mappe, der serveres
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert PRIVATE_TEXT in private_store(cfg)["msg:2"]["text"]


def test_private_threads_derive_no_tasks_events_or_suggestions(cfg, fake_aula):
    fake_aula.messages = [msg(2, "Samtale", "Kære Andreas og Monica, vi tager på tur til Zoo torsdag den 8. oktober kl. 8.30. Husk madpakke.", "2026-09-30T09:00:00+02:00", sender="Lærer Anders")]
    go(cfg, use_aula=True)
    d = family(cfg)
    assert [s for s in d["suggestions"] if "Zoo" in json.dumps(s, ensure_ascii=False)] == []
    assert "Zoo" not in json.dumps({k: d[k] for k in ("tasks", "events")}, ensure_ascii=False)


def test_the_protected_file_survives_an_aula_outage(cfg, fake_aula):
    fake_aula.messages = [PUBLIC, PRIVATE]
    go(cfg, use_aula=True)
    before = private_store(cfg)
    fake_aula.down = True
    go(cfg, use_aula=True)                                                  # Aula nede: de udtømte beskeder genbruges
    assert private_store(cfg) == before
    assert next(m for m in family(cfg)["messages"] if m["id"] == "msg:2")["redacted"] is True
    fake_aula.down = False
    go(cfg, use_aula=True)
    assert private_store(cfg)["msg:2"]["text"] == PRIVATE_TEXT


def test_private_threads_are_handed_back_to_the_fetch_as_cache(cfg, fake_aula):
    """Ellers ville de blive hentet forfra fra Aula ved hver eneste kørsel."""
    fake_aula.messages = [PUBLIC, PRIVATE]
    go(cfg, use_aula=True)
    go(cfg, use_aula=True)
    prev = {m["id"]: m for m in fake_aula.prev}
    assert prev["msg:2"]["text"] == PRIVATE_TEXT and len(prev["msg:2"]["thread"]) == 2 and not prev["msg:2"].get("redacted")


def test_protection_can_be_switched_off_for_static_use(cfg, fake_aula):
    cfg["private"] = {"protect": False}
    fake_aula.messages = [PRIVATE]
    go(cfg, use_aula=True)
    m = family(cfg)["messages"][0]
    assert m["private"] and m["text"] == PRIVATE_TEXT and not m.get("redacted") and not P.store_path(cfg).exists()


def test_media_names_come_from_private_threads_only():
    store = {"msg:2": {"images": ["media/aaa.jpg"], "thread": [{"images": ["media/bbb.png"]}, {"images": []}]}}
    assert P.media_names(store) == {"aaa.jpg", "bbb.png"}


# ---------------------------------------------------------------- aflysninger og flytninger mod rigtige aftaler
def day(n):
    d = TODAY + dt.timedelta(days=n)
    return d, f"{d.day}/{d.month}"


def created(cfg, key, title, d, event_id="fpabc123"):
    out = Path(cfg["output"]).parent
    ev = S.app_event({"title": title, "date": d.isoformat(), "end_date": d.isoformat(), "all_day": True}, event_id)
    S.Store(out / "suggestions_state.json").set(key, "created", event_id=event_id, event=ev, version=1)


def suggestions_of(cfg, kind):
    return [s for s in family(cfg)["suggestions"] if s.get("kind") == kind]


def test_a_cancellation_is_linked_to_the_event_the_app_created(cfg, fake_aula):
    d, txt = day(10)
    created(cfg, "sg_aaaaaaaaaaaa", "Hugo: Tur til Zoo", d)
    fake_aula.messages = [msg(5, "Tur", f"Turen til Zoo d. {txt} kl. 8.30 er desværre aflyst.", f"{TODAY}T08:00:00+02:00")]
    go(cfg, use_aula=True)
    (s,) = suggestions_of(cfg, "cancel")
    assert s["target"]["key"] == "sg_aaaaaaaaaaaa" and s["target"]["source"] == "app" and s["start"] == d.isoformat()
    assert [x for x in family(cfg)["suggestions"] if x.get("kind") is None and "Zoo" in x["title"]] == []     # og ingen ny "Tur til Zoo"


def test_new_activities_are_only_offered_manually_never_suggested(cfg, fake_aula):
    d, txt = day(10)
    fake_aula.messages = [msg(8, "Lejrskole", f"6.B tager på lejrskole d. {txt} kl. 8.00. Husk sovepose.", f"{TODAY}T08:00:00+02:00")]
    go(cfg, use_aula=True)
    data = family(cfg)
    assert data["suggestions"] == []                                                  # intet automatisk forslag
    (m,) = [x for x in data["messages"] if x["id"].endswith("8") or "Lejrskole" in (x.get("subject") or "")]
    (opt,) = m["cal"]
    assert opt["start"] == d.isoformat() and opt["start_time"] == "08:00" and "Lejrskole" in opt["title"]   # men "Føj til familiekalenderen" findes
    assert "learn" not in opt


def test_a_move_of_a_google_only_event_is_information_not_an_action(cfg, ical, fake_aula):
    old, old_txt = day(10)
    new, new_txt = day(17)
    ical.path.write_text(make_ics([("m1@test", "Forældremøde 6.B", old)]))
    fake_aula.messages = [msg(6, "Forældremøde", f"Forældremødet er flyttet fra {old_txt} til {new_txt} kl. 19.00.", f"{TODAY}T08:00:00+02:00")]
    go(cfg, use_aula=True)
    (s,) = suggestions_of(cfg, "move")
    assert s["target"]["source"] == "google" and s["target"]["key"] is None and s["start"] == new.isoformat() and s["old_start"] == old.isoformat()


def test_a_cancellation_with_nothing_to_cancel_produces_nothing(cfg, fake_aula):
    _, txt = day(10)
    fake_aula.messages = [msg(7, "Tur", f"Turen til Zoo d. {txt} kl. 8.30 er desværre aflyst.", f"{TODAY}T08:00:00+02:00")]
    go(cfg, use_aula=True)
    assert family(cfg)["suggestions"] == []


# ---------------------------------------------------------------- overblikket skifter til i morgen
def test_the_day_window_switches_to_tomorrow_after_the_evening_hour():
    tz = B.TZ
    assert B.target_window("day", dt.datetime(2026, 10, 1, 17, 59, tzinfo=tz))[2] == "i dag, torsdag 1/10"
    s, e, label = B.target_window("day", dt.datetime(2026, 10, 1, 18, 0, tzinfo=tz))
    assert (s, e, label) == (dt.date(2026, 10, 2), dt.date(2026, 10, 2), "i morgen, fredag 2/10")
    assert B.target_window("day", dt.datetime(2026, 10, 1, 15, tzinfo=tz), evening_hour=14)[2].startswith("i morgen")
    assert B.target_window("day", dt.datetime(2026, 10, 2, 19, tzinfo=tz))[2] == "i morgen, lørdag 3/10"        # fredag aften: lørdag


def test_make_briefing_follows_the_clock(cfg):
    go(cfg)
    data = family(cfg)
    b = B.make_briefing(cfg, data, "day", now=dt.datetime(2026, 10, 1, 18, 0, tzinfo=B.TZ))
    assert b["period"] == ["2026-10-02", "2026-10-02"] and b["headline_label"].startswith("i morgen")
    b = B.make_briefing(cfg, data, "day", now=dt.datetime(2026, 10, 1, 9, 0, tzinfo=B.TZ))
    assert b["period"] == ["2026-10-01", "2026-10-01"] and b["headline_label"].startswith("i dag")
    cfg["display"] = {"evening_hour": 20}
    assert B.make_briefing(cfg, data, "day", now=dt.datetime(2026, 10, 1, 18, 0, tzinfo=B.TZ))["headline_label"].startswith("i dag")


def test_deadlines_are_measured_against_the_real_today_not_the_day_shown():
    digest = {"opgaver": [{"id": "o1", "type": "skal gøres af forældre", "titel": "Underskriv blanketten", "frist": "2026-10-02", "hvem": ["Hugo"]},
                          {"id": "o2", "type": "lektie", "titel": "Læs side 12", "frist": "2026-10-02", "hvem": ["Carla"]}],
              "aftaler": [], "skema": [], "_refs": {}}
    out = OB.day_briefing(digest, dt.date(2026, 10, 2), real_today=dt.date(2026, 10, 1))        # torsdag aften, viser fredag
    secs = {s["titel"]: [p["tekst"] for p in s["punkter"]] for s in out["afsnit"]}
    assert any("Læs side 12" in t for t in secs["Husk"])                                      # forfalder på den viste dag
    assert any("senest i morgen" in t for t in secs["Skal gøres"])                            # ikke "senest i dag"
