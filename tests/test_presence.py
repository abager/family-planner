"""Komme/Gå: læsning, skrivekoder pr. voksen, sygemelding, ændring af tider (også gentaget), fortryd og ændringslog.
Kører mod en simuleret Aula (presence_fake.py) – aldrig den rigtige."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import briefing
import fetch_family as F
import presence as P
import server
from presence_fake import CARLA, HUGO, SICK, FakePresenceAula

TZ = ZoneInfo("Europe/Copenhagen")
H = {"X-Requested-With": "familieplan"}
PASSWORD = "hemmelig-kode"
ANDREAS, MONICA = "1111", "2222"


def today() -> dt.date:
    return dt.datetime.now(TZ).date()


def owner():
    return {HUGO: "hugo", CARLA: "carla", 103: "leo"}


def fetch_all(fake) -> list[dict]:
    async def go():
        return await P.fetch(fake, await fake.get_profile(), owner(), today(), 7)
    return asyncio.run(go())


# ---------------------------------------------------------------- læsning
def test_only_children_with_komme_gaa_are_included_and_leo_appears_when_his_institution_has_it():
    fake = FakePresenceAula(today())
    rows = fetch_all(fake)
    assert [r["person"] for r in rows] == ["hugo", "carla"]          # Leo har ikke modulet endnu: springes stille over
    fake.with_presence.add(103)
    fake.status[103] = 0
    fake.permissions[103] = {"report_sick": "editable"}
    assert [r["person"] for r in fetch_all(fake)] == ["hugo", "carla", "leo"]


def test_a_child_entry_has_status_planned_days_pickup_list_vacations_and_permissions():
    hugo = fetch_all(FakePresenceAula(today()))[0]
    assert hugo["today"]["status"] == "present" and hugo["today"]["statusText"] == "Til stede"
    assert hugo["today"]["checkIn"] == "08:02"
    assert [d["date"] for d in hugo["days"]] == [(today() + dt.timedelta(days=i)).isoformat() for i in range(8)]
    assert hugo["days"][0]["entry"] == "08:00" and hugo["days"][0]["exit"] == "15:30" and hugo["days"][0]["activity"] == 0
    assert hugo["pickup"] == ["Mor Test (Mor)", "Far Test (Far)", "Mormor"]
    assert hugo["vacations"][0]["title"] == "Juleferie" and hugo["vacations"][0]["missingAnswer"] is True
    assert hugo["can"] == {"sick": True, "times": True, "pickupType": True}
    assert hugo["institution"] == "SFO Testhuset"
    assert 1 not in hugo["activities"]                                  # institutionen tillader ikke "går selv"


def test_a_module_the_institution_only_lets_parents_read_is_not_offered():
    fake = FakePresenceAula(today())
    fake.permissions[HUGO]["report_sick"] = "readable"
    fake.permissions[HUGO]["pickup_times"] = "deactivated"
    hugo = fetch_all(fake)[0]
    assert hugo["can"]["sick"] is False and hugo["can"]["times"] is False


@pytest.mark.parametrize("raw,want", [("08:00", "08:00"), ("08:00:00", "08:00"), ("2026-10-09T14:52:00+02:00", "14:52"),
                                      (None, None), ("", None), ("kl. 8", None)])
def test_times_from_aula_are_normalised(raw, want):
    assert P.hhmm(raw) == want


def test_komme_gaa_is_never_part_of_what_is_sent_to_the_language_model():
    data = {"people": [{"id": "hugo", "name": "Hugo"}], "events": [], "tasks": [], "messages": [], "posts": [], "weekplan": [],
            "presence": fetch_all(FakePresenceAula(today()))}
    digest = json.dumps(briefing.build_digest(data, today(), today() + dt.timedelta(days=1), dt.datetime.now(TZ)), ensure_ascii=False)
    assert "Mor Test" not in digest and "presence" not in digest and "SFO Testhuset" not in digest


def test_the_fetch_gets_a_progress_bar_for_komme_gaa_unless_it_is_switched_off(cfg):
    assert "aula.presence" in F._progress_parts(cfg, True)
    cfg["presence"] = {"enabled": False}
    assert "aula.presence" not in F._progress_parts(cfg, True)


# ---------------------------------------------------------------- validering
def _child():
    return fetch_all(FakePresenceAula(today()))[0]


@pytest.mark.parametrize("change,message", [
    ({"date": "2001-01-01"}, "Dagen er gået"),
    ({"date": "ikke-en-dato"}, "Ugyldig dato"),
    ({"entry": "8"}, "tt:mm"),
    ({"entry": "16:00", "exit": "15:00"}, "efter kommetiden"),
    ({"exitWith": "Fremmed Person"}, "listen over dem"),
    ({"exitWith": ""}, "hvem der henter"),
    ({"activity": 1}, "tilbyder ikke"),
    ({"comment": "x" * 251}, "højst være 250"),
    ({"repeat": "monthly"}, "Ukendt gentagelse"),
])
def test_a_change_is_checked_before_it_is_sent_to_aula(change, message):
    body = {"date": today().isoformat(), "entry": "08:00", "exit": "14:00", "activity": 0, "exitWith": "Mor Test (Mor)", **change}
    with pytest.raises(P.PresenceError, match=message):
        P.validate_day(body, _child(), today(), 60)


def test_too_far_ahead_is_refused_and_send_home_needs_no_name():
    c = _child()
    with pytest.raises(P.PresenceError, match="60 dage"):
        P.validate_day({"date": (today() + dt.timedelta(days=61)).isoformat(), "entry": "08:00", "exit": "14:00", "activity": 2}, c, today(), 60)
    v = P.validate_day({"date": today().isoformat(), "entry": "08:00", "exit": "14:00", "activity": 2, "exitWith": "Mor Test (Mor)"}, c, today(), 60)
    assert v["exitWith"] is None                                        # "sendes hjem" har ingen afhenter


# ---------------------------------------------------------------- serveren
class Env:
    def __init__(self, cfg, fake):
        self.cfg, self.fake = cfg, fake
        self.app = server.create_app(cfg, server.Settings(cfg), PASSWORD, b"s" * 32, no_auth=False)
        self.c = TestClient(self.app, base_url="http://testserver", follow_redirects=False)
        self.c.post("/login", data={"password": PASSWORD})
        self.family = Path(cfg["output"])

    def post(self, url, body=None):
        return self.c.post(url, json=body or {}, headers=H)

    def unlock(self, code):
        return self.post("/api/presence/write/unlock", {"code": code})

    def child(self, person="hugo"):
        return next(c for c in json.loads(self.family.read_text("utf-8"))["presence"] if c["person"] == person)

    def log(self):
        p = P.log_path(self.cfg)
        return json.loads(p.read_text("utf-8")) if p.exists() else []


@pytest.fixture
def penv(cfg, monkeypatch):
    monkeypatch.setenv("FAMILIEPLAN_WRITE_CODE_ANDREAS", ANDREAS)
    monkeypatch.setenv("FAMILIEPLAN_WRITE_CODE_MONICA", MONICA)
    cfg["presence"] = {"write_enabled": True}
    fake = FakePresenceAula(today())

    async def opener(_cfg):
        return fake
    monkeypatch.setattr(F, "open_aula_client", opener)
    monkeypatch.setattr(server, "_now", lambda: dt.datetime.combine(today(), dt.time(9, 0), TZ))   # sygemelding kun før kl. 18
    Path(cfg["output"]).write_text(json.dumps({"events": [], "messages": [], "presence": fetch_all(fake)}))
    return Env(cfg, fake)


def _day(**kw):
    return {"person": "hugo", "date": today().isoformat(), "entry": "08:00", "exit": "14:00", "activity": 0,
            "exitWith": "Far Test (Far)", "comment": "Til tandlæge", "repeat": "never", **kw}


def test_nothing_can_be_written_without_a_personal_code(penv):
    r = penv.post("/api/presence/sick", {"person": "hugo"})
    assert r.status_code == 403 and r.json()["locked"] is True
    assert penv.fake.calls == [] or all(c[0] not in ("status", "template") for c in penv.fake.calls)
    assert penv.unlock("9999").status_code == 403
    assert penv.unlock(PASSWORD).status_code == 403                    # familiens adgangskode er ikke en skrivekode


def test_wrong_codes_are_throttled(penv):
    assert [penv.unlock("0000").status_code for _ in range(5)] == [403] * 5
    assert penv.unlock(ANDREAS).status_code == 429


def test_writing_is_off_until_write_enabled_is_set(cfg, monkeypatch):
    monkeypatch.setenv("FAMILIEPLAN_WRITE_CODE_ANDREAS", ANDREAS)
    Path(cfg["output"]).write_text(json.dumps({"presence": []}))
    e = Env(cfg, None)
    assert e.c.get("/api/presence/write/status").json()["enabled"] is False
    e.unlock(ANDREAS)
    r = e.post("/api/presence/sick", {"person": "hugo"})
    assert r.status_code == 403 and "write_enabled" in r.json()["error"]


def test_the_write_cookie_is_httponly_strict_and_names_who_unlocked(penv):
    r = penv.unlock(MONICA)
    assert r.status_code == 200 and r.json()["who"] == "monica" and r.json()["whoName"] == "Monica"
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc
    st = penv.c.get("/api/presence/write/status").json()
    assert st["unlocked"] is True and st["who"] == "monica" and st["expires_in"] > 0
    penv.post("/api/presence/write/lock")
    assert penv.c.get("/api/presence/write/status").json()["unlocked"] is False


def test_report_sick_reaches_aula_is_read_back_patched_into_family_json_and_logged_with_who(penv):
    penv.unlock(ANDREAS)
    r = penv.post("/api/presence/sick", {"person": "hugo", "sick": True})
    assert r.status_code == 200, r.text
    assert penv.fake.status[HUGO] == SICK
    assert penv.child()["today"]["status"] == "sick"                  # alle enheder ser det med det samme
    row = penv.log()[-1]
    assert row["by"] == "andreas" and row["person"] == "hugo" and row["action"] == "sick" and row["before"] == {"status": "present"}
    assert oct(P.log_path(penv.cfg).stat().st_mode)[-3:] == "600"


def test_monica_is_logged_as_monica(penv):
    penv.unlock(MONICA)
    penv.post("/api/presence/sick", {"person": "carla", "sick": True})
    assert penv.log()[-1]["by"] == "monica"


def test_change_one_day_sends_the_existing_template_id_and_only_that_day_changes(penv):
    penv.unlock(ANDREAS)
    r = penv.post("/api/presence/day", _day())
    assert r.status_code == 200, r.text
    call = [c for c in penv.fake.calls if c[0] == "template"][-1]
    assert call[2:] == (today().isoformat(), "08:00", "14:00", 0, "Far Test (Far)", "Til tandlæge", HUGO * 1000, "Never")
    days = {d["date"]: d for d in penv.child()["days"]}
    assert days[today().isoformat()]["exit"] == "14:00"
    assert days[(today() + dt.timedelta(days=7)).isoformat()]["exit"] == "15:30"
    assert penv.log()[-1]["before"]["exit"] == "15:30" and "14:00" in r.json()["text"]


def test_a_weekly_change_is_sent_as_a_repeat_and_shows_on_the_following_weeks(penv):
    penv.unlock(ANDREAS)
    r = penv.post("/api/presence/day", _day(repeat="weekly", exit="13:00"))
    assert r.status_code == 200, r.text
    assert [c for c in penv.fake.calls if c[0] == "template"][-1][-1] == "Weekly"
    days = {d["date"]: d for d in penv.child()["days"]}
    assert days[(today() + dt.timedelta(days=7)).isoformat()]["exit"] == "13:00"
    assert days[(today() + dt.timedelta(days=1)).isoformat()]["exit"] == "15:30"
    assert "hver uge" in r.json()["text"]


def test_undo_writes_the_previous_values_back_and_only_the_latest_change_can_be_undone(penv):
    penv.unlock(ANDREAS)
    penv.post("/api/presence/day", _day(exit="12:00"))
    log = penv.c.get("/api/presence/log", params={"person": "hugo"}).json()
    assert log["undo"]["byName"] == "Andreas"
    r = penv.post("/api/presence/undo", {"person": "hugo"})
    assert r.status_code == 200, r.text
    today_row = next(d for d in penv.child()["days"] if d["date"] == today().isoformat())
    assert today_row["exit"] == "15:30" and today_row["exitWith"] == "Mor Test (Mor)"
    assert penv.post("/api/presence/undo", {"person": "hugo"}).status_code == 404
    rows = penv.log()
    assert rows[0]["undone"]["by"] == "andreas" and rows[-1]["action"] == "undo"


def test_a_sick_report_cannot_be_undone(penv):
    penv.unlock(MONICA)
    penv.post("/api/presence/sick", {"person": "hugo", "sick": True})
    assert penv.post("/api/presence/undo", {"person": "hugo"}).status_code == 404       # brugerens beslutning: ingen fortryd
    assert penv.post("/api/presence/sick", {"person": "hugo", "sick": False}).status_code == 400
    assert penv.fake.status[HUGO] == SICK and penv.child()["today"]["status"] == "sick"


def test_aula_saying_ok_without_saving_is_not_reported_as_saved(penv):
    penv.unlock(ANDREAS)
    penv.fake.ignore_writes = True
    r = penv.post("/api/presence/sick", {"person": "hugo"})
    assert r.status_code == 502 and "viser den ikke endnu" in r.json()["error"]
    assert penv.log() == []                                           # intet logges som gemt


def test_an_aula_error_is_shown_and_the_aula_login_is_free_again_afterwards(penv):
    penv.unlock(ANDREAS)
    penv.fake.fail_write = RuntimeError("400 Bad Request: ugyldig tid")
    r = penv.post("/api/presence/day", _day())
    assert r.status_code == 502 and "ugyldig tid" in r.json()["error"]
    penv.fake.fail_write = None
    assert penv.post("/api/presence/day", _day()).status_code == 200    # låsen blev frigivet


def test_an_expired_aula_login_says_so(penv, monkeypatch):
    async def expired(_cfg):
        raise F.LoginRequired("udløbet")
    monkeypatch.setattr(F, "open_aula_client", expired)
    penv.unlock(ANDREAS)
    r = penv.post("/api/presence/sick", {"person": "hugo"})
    assert r.status_code == 409 and r.json()["action"] == "auth"


def test_what_the_institution_does_not_allow_is_refused_before_aula_is_asked(penv):
    data = json.loads(penv.family.read_text("utf-8"))
    data["presence"][0]["can"]["sick"] = False
    data["presence"][0]["can"]["times"] = False
    penv.family.write_text(json.dumps(data))
    penv.unlock(ANDREAS)
    assert penv.post("/api/presence/sick", {"person": "hugo"}).status_code == 403
    assert penv.post("/api/presence/day", _day()).status_code == 403
    assert not [c for c in penv.fake.calls if c[0] in ("status", "template")]


def test_a_child_without_komme_gaa_cannot_be_written(penv):
    penv.unlock(ANDREAS)
    assert penv.post("/api/presence/sick", {"person": "leo"}).status_code == 404


def test_a_write_during_a_running_fetch_waits_and_then_gives_up_politely(cfg, monkeypatch):
    monkeypatch.setenv("FAMILIEPLAN_WRITE_CODE_ANDREAS", ANDREAS)
    cfg["presence"] = {"write_enabled": True, "lock_wait_seconds": 0.1}
    fake = FakePresenceAula(today())
    Path(cfg["output"]).write_text(json.dumps({"presence": fetch_all(fake)}))
    e = Env(cfg, fake)

    class Busy:                                            # hentningen holder Aula-loginet
        async def acquire(self):
            await asyncio.sleep(5)

        def release(self):
            raise AssertionError("må ikke frigive en lås, den ikke fik")
    e.app.state.runner.lock = Busy()
    e.unlock(ANDREAS)
    r = e.post("/api/presence/sick", {"person": "hugo"})
    assert r.status_code == 503 and "henter fra Aula" in r.json()["error"]


def test_the_change_log_is_never_served(penv):
    (Path(penv.cfg["output"]).parent / "presence_log.json").write_text("[]")
    assert penv.c.get("/presence_log.json").status_code == 404


def test_the_log_needs_the_write_code(penv):
    assert penv.c.get("/api/presence/log").status_code == 403


# ---------------------------------------------------------------- selvtest
def test_the_selftest_checks_the_write_codes_only_when_writing_is_on(cfg, monkeypatch):
    import selftest
    cfg["presence"] = {"write_enabled": False}
    assert selftest.check_write_codes(server.Settings(cfg))[0].status == selftest.OK
    cfg["presence"] = {"write_enabled": True}
    assert selftest.check_write_codes(server.Settings(cfg))[0].status == selftest.FAIL      # ingen koder sat
    monkeypatch.setenv("FAMILIEPLAN_WRITE_CODE_ANDREAS", ANDREAS)
    r = selftest.check_write_codes(server.Settings(cfg))
    assert r[0].status == selftest.WARN and "monica" in r[0].detail
    monkeypatch.setenv("FAMILIEPLAN_WRITE_CODE_MONICA", ANDREAS)                             # samme kode som Andreas
    assert [x.status for x in selftest.check_write_codes(server.Settings(cfg))] == [selftest.OK, selftest.FAIL]
    monkeypatch.setenv("FAMILIEPLAN_WRITE_CODE_MONICA", MONICA)
    assert [x.status for x in selftest.check_write_codes(server.Settings(cfg), PASSWORD, "4711")] == [selftest.OK]


def test_the_fetch_puts_komme_gaa_in_its_own_part_through_the_aula_gate(cfg, monkeypatch):
    fake = FakePresenceAula(today())

    async def opener(_cfg):
        return fake

    async def nothing(*a, **k):
        return []
    monkeypatch.setattr(F, "open_aula_client", opener)
    for name in ("_lesson_notes", "_enrich_aula_events", "_fetch_mu_tasks", "_fetch_meebook", "_fetch_posts", "_fetch_messages", "_fetch_gallery"):
        monkeypatch.setattr(F, name, nothing)
    now = dt.datetime.now(TZ)
    res = asyncio.run(F.fetch_aula(cfg, F.People(cfg["people"]), now, now + dt.timedelta(days=1)))
    assert [c["person"] for c in res["presence"]] == ["hugo", "carla"]
    import problems
    assert not [p for p in problems.snapshot() if p["key"] == "aula.part:presence"]


def test_undo_refuses_when_the_day_was_changed_in_aula_afterwards(penv):
    penv.unlock(ANDREAS)
    penv.post("/api/presence/day", _day(exit="12:00"))
    penv.fake.days[(HUGO, today().isoformat())]["exitTime"] = "13:15"      # nogen retter dagen direkte i Aula
    r = penv.post("/api/presence/undo", {"person": "hugo"})
    assert r.status_code == 409 and "ændret siden" in r.json()["error"]
    assert penv.fake.days[(HUGO, today().isoformat())]["exitTime"] == "13:15"     # deres ændring står urørt


