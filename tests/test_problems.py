"""⚠ ved titlen: fejllisten i problems.py og at hver integration melder fejl – og rydder dem igen, når det lykkes."""
from __future__ import annotations

import asyncio
import datetime as dt
import json

import httpx
import pytest

import ai
import briefing as B
import ops
import problems
import suggestions as S
import weather as W
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Copenhagen")
NOW = dt.datetime(2026, 10, 1, 7, 30, tzinfo=TZ)


def keys():
    return [p["key"] for p in problems.snapshot()]


# ---------------------------------------------------------------- selve listen
def test_report_keeps_the_first_time_and_counts_repeats():
    problems.report("aula.fetch", "aula", "Aula-hentningen fejlede", "boom")
    first = problems.snapshot()[0]
    problems.report("aula.fetch", "aula", "Aula-hentningen fejlede", "boom igen")
    p = problems.snapshot()[0]
    assert p["since"] == first["since"] and p["count"] == 2 and p["detail"] == "boom igen" and p["area_title"] == "Aula"


def test_clear_removes_only_that_problem():
    problems.report("a", "ai", "x")
    problems.report("b", "weather", "y")
    problems.clear("a")
    assert keys() == ["b"]


def test_the_list_is_ordered_by_area():
    problems.report("w", "weather", "vejr")
    problems.report("g", "google", "google")
    problems.report("a", "ai", "ai")
    assert keys() == ["a", "g", "w"]


@pytest.mark.parametrize("raw,leak", [
    ("GET https://x/y?key=AIzaSyA1234567890abcdefghijklmnop&alt=json", "AIzaSy"),
    ("Authorization: Bearer ya29.a0AfH6SMBabcdefgh", "ya29"),
    ("refresh_token=1//0gabcdef123", "0gabcdef"),
    ("https://api.met.no/x?lat=55.67&lon=12.57", "55.67"),
    ("x-api-key: sk-ant-api03-abcdefghijk", "sk-ant-api03"),
])
def test_keys_tokens_and_coordinates_are_scrubbed(raw, leak):
    problems.report("x", "ai", "t", raw)
    assert leak not in problems.snapshot()[0]["detail"]


def test_http_status_codes_survive_scrubbing():
    problems.report("x", "google", "t", "Google svarede 403: status code: 403")
    assert "403" in problems.snapshot()[0]["detail"].split("code")[-1]


def test_external_links_are_never_offered_as_actions():
    problems.report("x", "aula", "t", action={"label": "Klik", "href": "https://evil.example"})
    problems.report("y", "aula", "t", action={"label": "Log ind", "href": "auth"})
    acts = {p["key"]: p["action"] for p in problems.snapshot()}
    assert acts == {"x": None, "y": {"label": "Log ind", "href": "auth"}}


# ---------------------------------------------------------------- AI
def gemini(obj):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj, ensure_ascii=False)}]}, "finishReason": "STOP"}]}


GOOD = {"fortaelling": ["Torsdag skal Carla have drikkedunk og fodboldsko med, så pak tasken i aften, så morgenen bliver rolig."],
        "kilder": ["O1"]}


def family():
    return {"people": [{"id": "carla", "name": "Carla", "role": "child"}], "events": [], "weekplan": [], "posts": [],
            "messages": [], "tasks": [{"id": "t1", "title": "Medbring drikkedunk og fodboldsko", "kind": "husk",
                                       "due": "2026-10-01", "person": "carla"}]}


def client(cfg, handler, env=None):
    s = ai.settings_from(cfg)
    return ai.Client(s, tmp_state(cfg), transport=httpx.MockTransport(handler), clock=lambda: NOW.astimezone(dt.UTC),
                     sleep=lambda _: None, env={s.api_key_env: "AIzaTEST"} if env is None else env)


def tmp_state(cfg):
    from pathlib import Path
    return Path(cfg["output"]).parent


@pytest.fixture
def acfg(tmp_path):
    return {"output": str(tmp_path / "family.json"), "assistant": {"mode": "ai"}, "ai": {"provider": "gemini", "rpm": 0}}


def test_a_briefing_without_ai_shows_a_problem_with_the_reason_and_what_to_do(acfg):
    B.make_briefing(acfg, family(), "day", now=NOW, client=client(acfg, lambda r: httpx.Response(200), env={}))
    p = problems.snapshot()[0]
    assert p["key"] == "ai.briefing.day" and p["area"] == "ai" and p["title"] == "Dagens overblik er lavet uden AI"
    assert "API-nøglen mangler" in p["detail"] and "GEMINI_API_KEY" in p["hint"]


def test_the_next_successful_ai_briefing_clears_it(acfg):
    B.make_briefing(acfg, family(), "day", now=NOW, client=client(acfg, lambda r: httpx.Response(200), env={}))
    B.make_briefing(acfg, family(), "day", now=NOW, client=client(acfg, lambda r: httpx.Response(200, json=gemini(GOOD))))
    assert keys() == []


def test_day_and_week_are_separate_problems(acfg):
    c = client(acfg, lambda r: httpx.Response(200), env={})
    B.make_briefing(acfg, family(), "day", now=NOW, client=c)
    B.make_briefing(acfg, family(), "week", now=NOW, client=c)
    assert sorted(keys()) == ["ai.briefing.day", "ai.briefing.week"]
    assert {p["title"] for p in problems.snapshot()} == {"Dagens overblik er lavet uden AI", "Ugens overblik er lavet uden AI"}


def test_offline_mode_in_config_is_not_an_error(acfg):
    problems.report("ai.briefing.day", "ai", "gammel")
    B.make_briefing({**acfg, "assistant": {"mode": "offline"}}, family(), "day", now=NOW)
    assert keys() == []


def test_the_raw_invalid_answer_never_reaches_the_problem_text(acfg):
    bad = {"candidates": [{"content": {"parts": [{"text": '{\n  "fortaelling": [\n    Carla drikkedunk\n  ]\n}'}]}, "finishReason": "STOP"}]}
    B.make_briefing(acfg, family(), "day", now=NOW, client=client(acfg, lambda r: httpx.Response(200, json=bad)))
    p = problems.snapshot()[0]
    assert "drikkedunk" not in json.dumps(p, ensure_ascii=False) and "ai_last_invalid.json" in p["hint"]


# ---------------------------------------------------------------- Google: skrivning
class FakeCal(S.GoogleCalendar):
    def __init__(self, status):
        self.api, self.calendar_id, self.status = "https://g", "fam@group", status

    async def _call(self, method, url, **kw):
        return httpx.Response(self.status, json={"error": {"message": "nope"}} if self.status >= 300 else {"id": "e1"})


def test_a_failed_write_to_google_is_a_problem_until_the_next_write_works():
    with pytest.raises(S.CalendarError):
        asyncio.run(FakeCal(403).delete("e1"))
    p = problems.snapshot()[0]
    assert p["key"] == "google.write" and p["title"] == "Kunne ikke slette aftalen i Google Kalender" and "403" in p["detail"]
    asyncio.run(FakeCal(204).delete("e1"))
    assert keys() == []


# ---------------------------------------------------------------- vejr
def test_weather_that_cannot_be_fetched_is_a_problem_and_clears_on_the_next_fetch(tmp_path):
    W.save_home(tmp_path, 55.67, 12.57, NOW)
    W.hours(tmp_path, NOW, httpx.MockTransport(lambda r: httpx.Response(500, text="nede")))
    p = problems.snapshot()[0]
    assert p["key"] == "weather" and "500" in p["detail"] and "55.67" not in json.dumps(p)
    later = NOW + dt.timedelta(hours=3)
    W.hours(tmp_path, later, httpx.MockTransport(lambda r: httpx.Response(304)))
    assert keys() == []


# ---------------------------------------------------------------- ntfy
def test_a_failed_ntfy_message_is_a_problem_and_clears_when_one_gets_through(monkeypatch):
    n = ops.Notifier("https://ntfy.example/familie", "")
    real = httpx.AsyncClient

    def with_status(code):
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(code)), **kw))

    with_status(500)
    assert asyncio.run(n.send("t", "m")) is False
    assert keys() == ["ntfy"] and problems.snapshot()[0]["area_title"] == "Beskeder (ntfy)"
    with_status(200)
    assert asyncio.run(n.send("t", "m")) is True
    assert keys() == []
