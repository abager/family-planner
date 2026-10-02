"""Overblikket: hvad der renses væk, før noget sendes til en sprogmodel. Opdigtede data."""
import pytest

import briefing as B


@pytest.mark.parametrize("text", ["CPR 120515-1234", "cpr: 1205151234", "Barnets nr. er 311299 4321", "(010101-0001)"])
def test_cpr_numbers_are_removed(text):
    out = B._scrub(text, 200)
    assert "[fjernet]" in out
    assert not any(ch.isdigit() for ch in out.replace("[fjernet]", "")), out


@pytest.mark.parametrize("text", ["ring 22 33 44 55", "+45 22334455", "skriv til lærer@skole.dk"])
def test_phone_numbers_and_mail_are_still_removed(text):
    assert "[fjernet]" in B._scrub(text, 200)


@pytest.mark.parametrize("text", ["Uge 41: side 12-34 i matematikbogen", "Mødet er 14.30-15.15 i lokale 112",
                                  "Afleveres 23/10 2026", "Ordre 4567 er på vej", "Lektion 3 af 12"])
def test_ordinary_numbers_are_left_alone(text):
    assert B._scrub(text, 200) == text


# ---------------------------------------------------------------- overblik via ai.py (simuleret Gemini)
import datetime as dt  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

import httpx  # noqa: E402

import ai  # noqa: E402

NOW = dt.datetime(2026, 10, 1, 9, 0, tzinfo=B.TZ)                     # torsdag formiddag → dagsoverblik for torsdag
KEY = "AIzaTEST-key_123"
PRIVATE = "HEMMELIG-PRIVAT-TRÅD"


def family_data(extra_task: str = "") -> dict:
    tasks = [{"id": "t1", "title": "Medbring drikkedunk og fodboldsko", "kind": "husk", "due": "2026-10-01", "person": "carla",
              "source": "besked", "subject": "Idrætsdag – ring 22 33 44 55, CPR 120515-1234"}]
    if extra_task:
        tasks.append({"id": "t2", "title": extra_task, "kind": "lektie", "due": "2026-10-01", "person": "hugo"})
    return {
        "people": [{"id": "hugo", "name": "Hugo", "role": "child"}, {"id": "carla", "name": "Carla", "role": "child"},
                   {"id": "andreas", "name": "Andreas", "role": "adult"}],
        "events": [], "weekplan": [], "posts": [], "tasks": tasks,
        "messages": [{"id": "m1", "private": True, "category": "samtale", "subject": PRIVATE, "text": PRIVATE,
                      "timestamp": "2026-10-01T08:00:00", "people": ["hugo"]}],
    }


class Fake:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, req):
        self.requests.append(req)
        status, body = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return httpx.Response(status, content=body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))


def ok(obj):
    return 200, {"candidates": [{"content": {"parts": [{"text": json.dumps(obj, ensure_ascii=False)}]}, "finishReason": "STOP"}]}


GOOD = {"afsnit": [{"titel": "Husk", "punkter": [{"tekst": "Carla: drikkedunk og fodboldsko", "hvem": ["Carla"], "refs": ["O1"]}]}]}
QUOTA = (429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota"}})


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t.astimezone(dt.timezone.utc)


def client(cfg, fake, clock=None, env=None, **settings):
    s = ai.Settings(**{"daily_cap": 20, "rpm": 0, **settings})
    return ai.Client(s, Path(cfg["output"]).parent, transport=httpx.MockTransport(fake), clock=clock or Clock(NOW),
                     sleep=lambda _s: None, env={s.api_key_env: KEY} if env is None else env)


@pytest.fixture
def acfg(cfg):
    cfg["assistant"] = {"mode": "ai", "min_minutes_between": 60}
    return cfg


def brief(cfg, fake, data=None, now=NOW, mode="day", **kw):
    return B.make_briefing(cfg, data or family_data(), mode, now=now, client=client(cfg, fake, Clock(now), **kw))


def saved(cfg, name="briefing.json"):
    return json.loads((Path(cfg["output"]).parent / name).read_text("utf-8"))


def test_ai_writes_the_briefing_with_readable_sources(acfg):
    fake = Fake(ok(GOOD))
    b = brief(acfg, fake)
    assert b["method"] == "ai" and b["provider"] == "gemini" and b["model"] == "gemini-3.5-flash-lite"
    assert b["afsnit"][0]["punkter"][0]["kilder"] == ["Medbring drikkedunk og fodboldsko"]
    assert "ai_fallback" not in b and "ai_stale" not in b
    assert saved(acfg)["method"] == "ai" and len(fake.requests) == 1


def test_private_threads_cpr_and_phone_numbers_never_reach_the_ai(acfg):
    fake = Fake(ok(GOOD))
    brief(acfg, fake)
    sent = fake.requests[0].content.decode("utf-8")
    assert PRIVATE not in sent
    assert "120515" not in sent and "1234" not in sent and "22 33 44 55" not in sent
    assert "drikkedunk" in sent                                        # det, der skal med, kommer med


@pytest.mark.parametrize("reply,reason", [
    ((200, {"candidates": [{"content": {"parts": [{"text": "Her er overblikket: Carla skal …"}]}, "finishReason": "STOP"}]}), "ugyldigt_svar"),
    (ok({"afsnit": [{"titel": "Sjove ting", "punkter": []}]}), "ugyldigt_svar"),
    (QUOTA, "kvote"),
    ((402, {"error": {"message": "payment required"}}), "betaling"),
    ((503, {"error": {"message": "unavailable"}}), "serverfejl"),
], ids=["ikke-json", "forkert-afsnit", "429", "402", "503"])
def test_any_ai_failure_falls_back_to_the_rules_and_says_so(acfg, reply, reason):
    b = brief(acfg, Fake(reply))
    assert b["method"] == "offline"
    assert b["ai_fallback"] == {"reason": reason, "since": NOW.isoformat(timespec="minutes")}
    assert any("drikkedunk" in p["tekst"] for s in b["afsnit"] for p in s["punkter"])   # reglerne fandt det samme
    assert saved(acfg)["ai_fallback"]["reason"] == reason


def test_an_exhausted_daily_budget_falls_back_without_asking(acfg):
    fake = Fake(ok(GOOD))
    for i in range(2):                                                 # brug budgettet op med to forskellige overblik
        B.make_briefing(acfg, family_data(f"lektie {i}"), "day", now=NOW, force=True, client=client(acfg, fake, daily_cap=2))
    b = B.make_briefing(acfg, family_data("ny lektie"), "day", now=NOW, force=True, client=client(acfg, fake, daily_cap=2))
    assert len(fake.requests) == 2
    assert b["ai_stale"]["reason"] == "dagsbudget"                     # der fandtes et AI-overblik for i dag: det beholdes


def test_a_missing_key_falls_back(acfg):
    fake = Fake(ok(GOOD))
    b = B.make_briefing(acfg, family_data(), "day", now=NOW, client=client(acfg, fake, env={}))
    assert b["method"] == "offline" and b["ai_fallback"]["reason"] == "mangler_noegle" and not fake.requests


def test_when_ai_fails_the_last_ai_briefing_for_the_same_day_is_kept_and_marked(acfg):
    first = brief(acfg, Fake(ok(GOOD)))
    later = NOW + dt.timedelta(hours=2)
    b = brief(acfg, Fake(QUOTA), data=family_data("Læs side 12-20"), now=later)
    assert b["method"] == "ai" and b["generated"] == first["generated"]
    assert b["ai_stale"] == {"reason": "kvote", "since": later.isoformat(timespec="minutes")}
    assert saved(acfg)["ai_stale"]["reason"] == "kvote"


def test_the_stale_mark_keeps_its_start_time_and_goes_away_when_ai_works_again(acfg):
    brief(acfg, Fake(ok(GOOD)))
    t1, t2, t3 = (NOW + dt.timedelta(hours=h) for h in (1, 2, 3))
    brief(acfg, Fake(QUOTA), data=family_data("a"), now=t1)
    b = brief(acfg, Fake(QUOTA), data=family_data("b"), now=t2, model="andet")   # anden model = ingen cache, ingen pause
    assert b["ai_stale"]["since"] == t1.isoformat(timespec="minutes")
    fixed = {"afsnit": [{"titel": "Husk", "punkter": [{"tekst": "Hugo: læs", "hvem": ["Hugo"], "refs": ["O2"]}]}]}
    b = brief(acfg, Fake(ok(fixed)), data=family_data("b"), now=t3, model="tredje")
    assert b["method"] == "ai" and "ai_stale" not in b and b["generated"] == t3.isoformat(timespec="minutes")


def test_a_stale_mark_is_cleared_without_a_request_when_the_data_is_back_to_what_the_ai_saw(acfg):
    brief(acfg, Fake(ok(GOOD)))
    brief(acfg, Fake(QUOTA), data=family_data("midlertidig"), now=NOW + dt.timedelta(hours=1))
    fake = Fake(ok(GOOD))
    b = brief(acfg, fake, now=NOW + dt.timedelta(hours=2))
    assert "ai_stale" not in b and not fake.requests


def test_an_ai_briefing_for_another_day_is_not_kept(acfg):
    brief(acfg, Fake(ok(GOOD)))
    tomorrow = NOW + dt.timedelta(days=1)
    b = brief(acfg, Fake(QUOTA), now=tomorrow)
    assert b["method"] == "offline" and b["ai_fallback"]["reason"] == "kvote"


def test_points_without_a_valid_source_are_dropped(acfg):
    reply = {"afsnit": [{"titel": "Husk", "punkter": [
        {"tekst": "Carla: drikkedunk", "hvem": ["Carla"], "refs": ["O1", "X9"]},
        {"tekst": "Leo: opdigtet tur til zoo", "hvem": ["Leo"], "refs": ["Z1"]},
        {"tekst": "Uden kilde", "hvem": []}]}]}
    b = brief(acfg, Fake(ok(reply)))
    pts = b["afsnit"][0]["punkter"]
    assert [p["tekst"] for p in pts] == ["Carla: drikkedunk"] and pts[0]["refs"] == ["O1"]


def test_unchanged_data_costs_no_new_request(acfg):
    fake = Fake(ok(GOOD))
    brief(acfg, fake)
    brief(acfg, fake, now=NOW + dt.timedelta(minutes=15))
    assert len(fake.requests) == 1


def test_offline_mode_never_uses_the_ai_or_shows_the_banner(cfg):
    fake = Fake(ok(GOOD))
    b = brief(cfg, fake)                                               # cfg-fixturen har mode = "offline"
    assert b["method"] == "offline" and "ai_fallback" not in b and not fake.requests


def test_the_week_briefing_goes_through_the_ai_too(acfg):
    fake = Fake(ok(GOOD))
    b = brief(acfg, fake, mode="week")
    assert b["method"] == "ai" and saved(acfg, "briefing_uge.json")["method"] == "ai"
    assert "ugens overblik" in json.loads(fake.requests[0].content)["contents"][0]["parts"][0]["text"]


def test_an_old_claude_config_goes_through_ai_py_to_claude(cfg):
    cfg["assistant"] = {"mode": "claude", "model": "claude-test"}
    fake = Fake((200, {"content": [{"type": "text", "text": json.dumps(GOOD, ensure_ascii=False)}], "stop_reason": "end_turn"}))
    c = ai.Client(ai.settings_from(cfg), Path(cfg["output"]).parent, transport=httpx.MockTransport(fake),
                  clock=Clock(NOW), env={"ANTHROPIC_API_KEY": KEY})
    b = B.make_briefing(cfg, family_data(), "day", now=NOW, client=c)
    assert b["method"] == "ai" and b["provider"] == "claude" and fake.requests[0].url.host == "api.anthropic.com"


def test_ai_status_reports_usage_but_no_key(acfg, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    brief(acfg, Fake(QUOTA))
    st = B.ai_status(acfg)
    assert st["provider"] == "gemini" and st["daily_cap"] == 100 and st["last_error"]["reason"] == "kvote"
    assert KEY not in json.dumps(st)


def test_ai_status_is_none_when_the_briefing_uses_no_language_model(cfg):
    assert B.ai_status(cfg) is None
