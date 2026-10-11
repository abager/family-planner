"""Skolefravær fra Tabulex og den samlede sygemelding (Komme/Gå + skolen). Kører mod en simuleret Aula
(presence_fake.py) og en simuleret Tabulex (tabulex_fake.py) – aldrig de rigtige."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import briefing
import fetch_family as F
import presence as P
import server
import tabulex as T
from presence_fake import CARLA, HUGO, SICK, FakePresenceAula
from tabulex_fake import CARLA_CPR, HUGO_CPR, FakeTabulex, Widgets

TZ = ZoneInfo("Europe/Copenhagen")
H = {"X-Requested-With": "familieplan"}
PASSWORD = "hemmelig-kode"
ANDREAS = "1111"
CPRS = (HUGO_CPR, CARLA_CPR, HUGO_CPR[:6] + "-" + HUGO_CPR[6:])


def today() -> dt.date:
    return dt.datetime.now(TZ).date()


def no_cpr(text: str) -> bool:
    return not any(c in text for c in CPRS)


@pytest.fixture
def tab(monkeypatch):
    fake = FakeTabulex(today())
    monkeypatch.setattr(T, "TRANSPORT", fake.transport)
    return fake


def people():
    return F.People([{"id": i, "name": i.title(), "aula_name": i.title()} for i in ("hugo", "carla", "leo")])


def aula(fail=None):
    a = FakePresenceAula(today())
    a.widgets = Widgets(fail)
    return a


CFG = {"tabulex": {"enabled": True}}


# ---------------------------------------------------------------- tolkning
@pytest.mark.parametrize("d, sick", [
    ({"HarHalvdagsFravaerSetting": True, "Trin": 3, "FoersteLektionAarsag": "Sygdom"}, True),
    ({"HarHalvdagsFravaerSetting": True, "Trin": 3, "FoersteLektionAarsag": "Lovligt"}, False),
    ({"HarHalvdagsFravaerSetting": True, "Trin": 8, "FoersteLektionAarsag": "Sygdom"}, False),     # fra 7. klasse: begge
    ({"HarHalvdagsFravaerSetting": True, "Trin": 8, "FoersteLektionAarsag": "Sygdom", "SidsteLektionAarsag": "Sygdom"}, True),
    ({"HarHalvdagsFravaerSetting": False, "Dag": True, "Aarsag": "Sygdom"}, True),
    ({"HarHalvdagsFravaerSetting": False, "Dag": None, "Aarsag": "Sygdom"}, False),
    ({}, False), (None, False),
])
def test_sick_follows_tabulex_own_rules(d, sick):
    assert T.is_sick(d) is sick


def test_reason_text_is_shown_for_other_absence():
    e = T.entry("hugo", {"Klasse": "6B", "SkoleNavn": "X", "Cpr": HUGO_CPR},
                {"HarHalvdagsFravaerSetting": True, "Trin": 6, "FoersteLektionAarsag": "Lovligt", "Dato": "2026-10-13T00:00:00"},
                [{"Dato": "2026-10-13T00:00:00", "SkoleDag": True}], dt.date(2026, 10, 13))
    assert e == {"person": "hugo", "school": "X", "class": "6B", "date": "2026-10-13", "schoolDay": True, "sick": False,
                 "reason": "Lovligt", "text": "Lovligt fravær"}


# ---------------------------------------------------------------- login og læsning
def test_login_through_aula_and_fetch_gives_each_child_without_cpr(tab):
    a = aula()
    out = asyncio.run(T.fetch(a, people(), today(), CFG))
    assert a.widgets.asked == ["0047"] and tab.logins == 1
    assert [x["person"] for x in out] == ["carla", "hugo"]
    assert out[1] == {"person": "hugo", "school": "Testskolen", "class": "6B", "date": today().isoformat(), "schoolDay": True,
                      "sick": False, "reason": None, "text": None}
    assert no_cpr(json.dumps(out))


def test_a_refused_aula_token_is_a_clear_login_error(tab):
    tab.accept_token = False
    with pytest.raises(T.TabulexError, match="Login i Tabulex via Aula mislykkedes"):
        asyncio.run(T.fetch(aula(), people(), today(), CFG))


def test_tabulex_down_gives_a_danish_error_without_address_or_cpr(tab):
    tab.down = True
    with pytest.raises(T.TabulexError) as e:
        asyncio.run(T.fetch(aula(), people(), today(), CFG))
    assert "503" in e.value.message and "http" not in e.value.message and no_cpr(e.value.message)


def test_a_failed_sick_report_never_puts_the_cpr_in_the_error(tab):
    tab.fail_put = 500

    async def go():
        async with await T.open_session(aula(), CFG) as s:
            return await T.report_sick_today(s, "hugo", people(), today())
    with pytest.raises(T.TabulexError) as e:
        asyncio.run(go())
    assert "500" in e.value.message and no_cpr(e.value.message)


def test_tabulex_is_off_by_default_and_gets_a_progress_bar_when_on(cfg):
    assert T.settings({})["enabled"] is False
    assert "aula.absence" not in F._progress_parts(cfg, True)
    cfg["tabulex"] = {"enabled": True}
    assert "aula.absence" in F._progress_parts(cfg, True)


def test_school_absence_is_never_part_of_what_is_sent_to_the_language_model(tab):
    tab.sick[HUGO_CPR] = True
    data = {"people": [{"id": "hugo", "name": "Hugo"}], "events": [], "tasks": [], "messages": [], "posts": [], "weekplan": [],
            "absence": asyncio.run(T.fetch(aula(), people(), today(), CFG))}
    digest = json.dumps(briefing.build_digest(data, today(), today() + dt.timedelta(days=1), dt.datetime.now(TZ)), ensure_ascii=False)
    assert "absence" not in digest and "Testskolen" not in digest and "Meldt syg" not in digest


# ---------------------------------------------------------------- den samlede sygemelding (serveren)
class Env:
    def __init__(self, cfg, aula_fake, tab_fake):
        self.cfg, self.aula, self.tab = cfg, aula_fake, tab_fake
        self.app = server.create_app(cfg, server.Settings(cfg), PASSWORD, b"s" * 32, no_auth=False)
        self.c = TestClient(self.app, base_url="http://testserver", follow_redirects=False)
        self.c.post("/login", data={"password": PASSWORD})
        self.c.post("/api/presence/write/unlock", json={"code": ANDREAS}, headers=H)
        self.family = Path(cfg["output"])

    def sick(self, person="hugo", **extra):
        return self.c.post("/api/presence/sick", json={"person": person, **extra}, headers=H)

    def data(self):
        return json.loads(self.family.read_text("utf-8"))

    def absence(self, person="hugo"):
        return next(a for a in self.data()["absence"] if a["person"] == person)

    def presence(self, person="hugo"):
        return next(c for c in self.data()["presence"] if c["person"] == person)

    def log(self):
        p = P.log_path(self.cfg)
        return json.loads(p.read_text("utf-8")) if p.exists() else []

    def aula_sick_calls(self):
        return [c for c in self.aula.calls if c[0] == "status"]


def _at(hour):
    return lambda: dt.datetime.combine(today(), dt.time(hour, 5), TZ)


@pytest.fixture
def env(cfg, monkeypatch, tab):
    monkeypatch.setenv("FAMILIEPLAN_WRITE_CODE_ANDREAS", ANDREAS)
    cfg["presence"] = {"write_enabled": True}
    cfg["tabulex"] = {"enabled": True}
    a = aula()

    async def opener(_cfg):
        return a
    monkeypatch.setattr(F, "open_aula_client", opener)
    monkeypatch.setattr(server, "_now", _at(9))
    presence = asyncio.run(P.fetch(a, asyncio.run(a.get_profile()), {HUGO: "hugo", CARLA: "carla", 103: "leo"}, today(), 7))
    absence = asyncio.run(T.fetch(a, F.People(cfg["people"]), today(), cfg))
    Path(cfg["output"]).write_text(json.dumps({"events": [], "messages": [], "presence": presence, "absence": absence}))
    return Env(cfg, a, tab)


def test_one_sick_report_reaches_both_komme_gaa_and_the_school(env):
    r = env.sick()
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True and [(x["target"], x["ok"]) for x in j["results"]] == [("aula", True), ("tabulex", True)]
    assert env.aula.status[HUGO] == SICK and env.tab.sick[HUGO_CPR] is True and env.tab.sick[CARLA_CPR] is False
    assert env.presence()["today"]["status"] == "sick" and env.absence()["sick"] is True      # alle enheder ser det
    rows = env.log()
    assert [(x["action"], x["by"]) for x in rows] == [("sick", "andreas"), ("sick_school", "andreas")]


def test_the_school_failing_does_not_stop_komme_gaa_and_a_retry_only_sends_what_is_missing(env):
    env.tab.fail_put = 500
    j = env.sick().json()
    assert [(x["target"], x["ok"]) for x in j["results"]] == [("aula", True), ("tabulex", False)]
    assert j["ok"] is False and "500" in j["results"][1]["error"]
    env.tab.fail_put = None
    j = env.sick().json()                                                  # "Prøv igen"
    assert j["ok"] is True and j["results"][0]["already"] is True and j["results"][1]["already"] is False
    assert len(env.aula_sick_calls()) == 1 and env.tab.sick[HUGO_CPR] is True


def test_a_failed_tabulex_login_still_reports_sick_in_komme_gaa(env):
    env.tab.accept_token = False
    j = env.sick().json()
    assert [(x["target"], x["ok"]) for x in j["results"]] == [("aula", True), ("tabulex", False)]
    assert "Login i Tabulex" in j["results"][1]["error"] and env.aula.status[HUGO] == SICK


def test_komme_gaa_failing_does_not_stop_the_school(env):
    env.aula.ignore_writes = True
    r = env.sick()
    j = r.json()
    assert r.status_code == 200 and [(x["target"], x["ok"]) for x in j["results"]] == [("aula", False), ("tabulex", True)]
    assert "viser den ikke endnu" in j["results"][0]["error"] and env.tab.sick[HUGO_CPR] is True


def test_when_nothing_succeeds_the_answer_is_an_error_with_the_details(env):
    env.aula.ignore_writes = True
    env.tab.fail_put = 503
    r = env.sick()
    assert r.status_code == 502 and "Komme/Gå" in r.json()["error"] and "Skolen" in r.json()["error"]
    assert len(r.json()["results"]) == 2


def test_not_a_school_day_refuses_the_whole_report_before_anything_is_sent(env):
    env.tab.school_day = False
    r = env.sick()
    assert r.status_code == 409 and r.json()["error"] == "Ikke skoledag i dag."
    assert env.aula_sick_calls() == [] and env.tab.puts == []
    assert env.absence()["schoolDay"] is False                             # appen viser det med det samme


def test_the_saved_school_day_flag_refuses_without_asking_anyone(env):
    data = env.data()
    data["absence"][1]["schoolDay"] = False
    env.family.write_text(json.dumps(data))
    logins = env.tab.logins
    assert env.sick().status_code == 409 and env.tab.logins == logins and env.aula_sick_calls() == []


def test_no_sick_report_after_the_evening_switch(env, monkeypatch):
    monkeypatch.setattr(server, "_now", _at(18))
    r = env.sick()
    assert r.status_code == 409 and "kl. 18" in r.json()["error"]
    assert env.aula_sick_calls() == [] and env.tab.puts == []


def test_a_sick_report_cannot_be_taken_back_or_undone(env):
    assert env.sick(sick=False).status_code == 400
    env.sick()
    r = env.c.post("/api/presence/undo", json={"person": "hugo"}, headers=H)
    assert r.status_code == 404                                            # intet at fortryde
    assert env.aula.status[HUGO] == SICK and env.c.get("/api/presence/log?person=hugo").json()["undo"] is None


def test_a_day_change_before_the_sick_report_can_still_be_undone(env):
    day = {"person": "hugo", "date": today().isoformat(), "entry": "08:00", "exit": "14:00", "activity": 0,
           "exitWith": "Far Test (Far)", "comment": "", "repeat": "never"}
    assert env.c.post("/api/presence/day", json=day, headers=H).status_code == 200
    env.sick()
    assert env.c.get("/api/presence/log?person=hugo").json()["undo"]["text"].startswith("08:00–14:00")
    assert env.c.post("/api/presence/undo", json={"person": "hugo"}, headers=H).status_code == 200
    assert env.aula.status[HUGO] == SICK                                   # sygemeldingen står urørt


def test_a_child_only_in_tabulex_is_reported_to_the_school_only(env):
    data = env.data()
    data["presence"] = [c for c in data["presence"] if c["person"] != "hugo"]
    env.family.write_text(json.dumps(data))
    j = env.sick().json()
    assert [(x["target"], x["ok"]) for x in j["results"]] == [("tabulex", True)] and env.aula_sick_calls() == []


def test_with_tabulex_off_only_komme_gaa_is_reported(env, cfg):
    cfg["tabulex"] = {"enabled": False}
    e = Env(cfg, env.aula, env.tab)
    j = e.sick().json()
    assert [(x["target"], x["ok"]) for x in j["results"]] == [("aula", True)] and env.tab.puts == []


def test_a_child_with_neither_is_refused(env):
    assert env.sick("leo").status_code == 404


def test_the_cpr_is_never_in_family_json_the_log_the_answers_or_the_server_log(env, caplog):
    caplog.set_level(logging.DEBUG)
    env.tab.fail_put = 500
    answers = [env.sick().text]
    env.tab.fail_put = None
    answers.append(env.sick("hugo").text)
    answers.append(env.sick("carla").text)
    assert env.tab.sick[CARLA_CPR] and env.tab.sick[HUGO_CPR]
    for text in (env.family.read_text("utf-8"), json.dumps(env.log()), *answers, caplog.text):
        assert no_cpr(text)
