"""Bjælker pr. datatype: progress.py og at hentningen melder fremdrift for hver del."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path

import fetch_family as F
import progress
from test_aula_fetch import NOW, Aula, People, Media, thread, run, fetch


def parts():
    return {p["key"]: p for p in progress.snapshot()["parts"]}


def test_a_new_fetch_lists_only_its_parts_in_display_order():
    progress.start(["overblik", "google", "aula.messages"])
    s = progress.snapshot()
    assert s["running"] and [p["key"] for p in s["parts"]] == ["google", "aula.messages", "overblik"]
    assert {p["state"] for p in s["parts"]} == {"waiting"}
    assert [p["label"] for p in s["parts"]] == ["Google", "Beskeder", "Overblik"]


def test_known_totals_give_a_count_and_done_fills_the_bar():
    progress.start(["aula.messages"])
    progress.begin("aula.messages")
    assert parts()["aula.messages"]["total"] is None                      # ukendt endnu: "i gang"
    progress.step("aula.messages", 3, 12)
    assert (parts()["aula.messages"]["done"], parts()["aula.messages"]["total"]) == (3, 12)
    progress.step("aula.messages", 99)
    assert parts()["aula.messages"]["done"] == 12                         # aldrig over total
    progress.finish("aula.messages")
    assert parts()["aula.messages"]["state"] == "done"


def test_a_failed_part_stays_failed():
    progress.start(["aula.albums"])
    progress.finish("aula.albums", ok=False)
    progress.begin("aula.albums")
    progress.step("aula.albums", 1, 2)
    assert parts()["aula.albums"]["state"] == "failed"


def test_parts_that_never_finished_count_as_failed_when_the_fetch_ends():
    progress.start(["google", "aula.posts"])
    progress.finish("google")
    progress.begin("aula.posts")
    progress.end()
    s = progress.snapshot()
    assert not s["running"] and s["finished"] and {p["key"]: p["state"] for p in s["parts"]} == {"google": "done", "aula.posts": "failed"}


def test_the_parts_follow_the_config():
    cfg = {"google": [{"name": "x"}], "aula": {"fetch_gallery": False, "fetch_tasks": False}, "assistant": {"mode": "off"},
           "weather": {"enabled": False}, "suggestions": {"enabled": False}}
    assert F._progress_parts(cfg, True) == ["google", "aula.kalender", "aula.weekplan", "aula.posts", "aula.messages"]
    assert F._progress_parts(cfg, False) == ["google"]


def test_messages_report_threads_fetched_of_those_to_fetch():
    seen = []
    real = progress.step
    progress.start(["aula.messages"])
    progress.begin("aula.messages")

    def spy(key, done, total=None):
        seen.append((done, total))
        real(key, done, total)
    progress.step = spy
    try:
        fetch(Aula([thread(i) for i in range(1, 5)]), full_sweep=True)
    finally:
        progress.step = real
    assert seen[0] == (0, 4) and seen[-1] == (4, 4)


def test_a_full_pipeline_run_ends_with_every_part_done(cfg, ical):
    from test_pipeline import go
    go(cfg)
    s = progress.snapshot()
    assert not s["running"]
    states = {p["key"]: p["state"] for p in s["parts"]}
    assert states.get("google") == "done" and "aula.kalender" not in states      # Aula slået fra i testen
    assert all(v == "done" for v in states.values()), states


def test_a_google_outage_turns_its_bar_red(cfg, ical):
    from test_pipeline import go
    cfg["google"][0]["ical_url"] = ical.dead
    go(cfg)
    assert {p["key"]: p["state"] for p in progress.snapshot()["parts"]}["google"] == "failed"



def test_each_part_and_the_whole_fetch_record_how_long_they_took(monkeypatch):
    t = [100.0]
    monkeypatch.setattr(progress.time, "perf_counter", lambda: t[0])
    progress.start(["google", "aula.posts", "vejr"])
    progress.begin("google")
    t[0] += 0.82
    progress.finish("google")
    progress.begin("aula.posts")
    t[0] += 45.21
    progress.end()                                                    # opslag nåede ikke i mål; vejr gik aldrig i gang
    s = progress.snapshot()
    ms = {p["key"]: (p["state"], p["ms"]) for p in s["parts"]}
    assert ms == {"google": ("done", 820), "aula.posts": ("failed", 45210), "vejr": ("failed", None)}
    assert s["ms"] == 46030 and s["finished"]


def test_the_last_fetch_stays_until_the_next_one_starts():
    progress.start(["google"])
    progress.finish("google")
    progress.end()
    progress.end()                                                    # serveren kalder også end(): ændrer intet
    first = progress.snapshot()
    assert not first["running"] and first["parts"][0]["state"] == "done"
    progress.start(["vejr"])
    assert [p["key"] for p in progress.snapshot()["parts"]] == ["vejr"]


# ---------------------------------------------------------------- tider pr. del
def test_each_part_and_the_whole_fetch_get_a_duration_in_ms(monkeypatch):
    t = {"now": 100.0}
    monkeypatch.setattr(progress.time, "perf_counter", lambda: t["now"])
    progress.start(["google", "aula.posts", "vejr"])
    progress.begin("google")
    t["now"] += 0.82
    progress.finish("google")
    progress.begin("aula.posts")
    t["now"] += 45.21
    progress.finish("aula.posts", ok=False)
    t["now"] += 1
    progress.end()
    s = progress.snapshot()
    ms = {p["key"]: p["ms"] for p in s["parts"]}
    assert ms == {"google": 820, "aula.posts": 45210, "vejr": None}       # vejret gik aldrig i gang
    assert s["ms"] == 47030 and s["finished"]


def test_the_last_fetch_stays_until_the_next_one_starts():
    progress.start(["google"])
    progress.finish("google")
    progress.end()
    progress.end()                                                      # serveren kalder også end() – må ikke ændre tiden
    first = progress.snapshot()
    assert not first["running"] and first["parts"][0]["state"] == "done"
    progress.start(["vejr"])
    assert [p["key"] for p in progress.snapshot()["parts"]] == ["vejr"]


# ---------------------------------------------------------------- "Tving fuld hentning"
def test_a_full_fetch_forces_the_deep_check_and_a_fresh_ai_overview(cfg, monkeypatch):
    seen = {}

    async def fake_aula(cfg, people, start, end, dump_path=None, previous_messages=None, full_sweep=False):
        seen["full_sweep"] = full_sweep
        return {"events": [], "tasks": [], "weekplan": [], "posts": [], "messages": [], "albums": []}

    def fake_briefing(cfg, data, mode="day", force=False, **kw):
        seen.setdefault("force", []).append(force)
    import briefing
    monkeypatch.setattr(F, "fetch_aula", fake_aula)
    monkeypatch.setattr(briefing, "make_briefing", fake_briefing)
    cfg["assistant"] = {"mode": "ai"}
    prev = {"health": {"aula": {"last_full_sweep": dt.datetime.now(F.TZ).isoformat()}}}   # dyb kontrol lige lavet
    Path(cfg["output"]).write_text(json.dumps(prev))
    asyncio.run(F.run_once(cfg, True))
    assert seen == {"full_sweep": False, "force": [False, False]}
    seen.clear()
    asyncio.run(F.run_once(cfg, True, full=True))
    assert seen == {"full_sweep": True, "force": [True, True]}


def test_fresh_skips_the_ai_cache_but_still_stores_the_answer(tmp_path):
    import httpx
    import ai
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        text = json.dumps({"svar": n["calls"]})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]})
    s = ai.Settings(rpm=0)
    c = ai.Client(s, tmp_path, transport=httpx.MockTransport(handler), env={s.api_key_env: "AIzaX"})
    assert c.generate_json("s", "p") == {"svar": 1}
    assert c.generate_json("s", "p") == {"svar": 1} and n["calls"] == 1          # fra cachen
    assert c.generate_json("s", "p", fresh=True) == {"svar": 2} and n["calls"] == 2
    assert c.generate_json("s", "p") == {"svar": 2}                               # det nye svar er gemt


def test_the_refresh_endpoint_passes_full_on_to_the_runner(cfg, monkeypatch):
    import server
    from test_server import Env, CODE
    monkeypatch.setenv("FAMILIEPLAN_PRIVATE_CODE", CODE)
    Path(cfg["output"]).write_text(json.dumps({"events": [], "messages": []}))
    e = Env(cfg, server.Settings(cfg))
    runner = e.app.state.runner
    h = {"X-Requested-With": "familieplan"}
    assert e.c.post("/api/refresh").status_code == 403                            # uden CSRF-header: afvist
    assert e.c.post("/api/refresh", headers=h).json() == {"ok": True, "full": False} and runner.full_next is False
    assert e.c.post("/api/refresh?full=true", headers=h).json() == {"ok": True, "full": True} and runner.full_next is True


def test_the_runner_hands_full_on_to_the_fetch_once(cfg, monkeypatch):
    import server
    from test_server import Env, CODE
    monkeypatch.setenv("FAMILIEPLAN_PRIVATE_CODE", CODE)
    Path(cfg["output"]).write_text(json.dumps({"events": [], "messages": []}))
    runner = Env(cfg, server.Settings(cfg)).app.state.runner
    calls = []

    async def fake(cfg, use_aula, **kw):
        calls.append(kw)
        return {"aula": "skipped", "error": None, "counts": {}}
    monkeypatch.setattr(server.fetch_family, "run_once", fake)
    asyncio.run(runner.safe_run(False, True))
    asyncio.run(runner.safe_run(False))
    assert calls == [{"full": True}, {}]


def test_the_weather_is_fetched_alongside_the_rest(cfg, monkeypatch):
    import time as _time
    seen = {}

    def slow_weather(cfg, now):
        seen["weather_start"] = _time.perf_counter()
        _time.sleep(0.4)
        return {"kilde": "MET Norway", "dage": []}

    async def slow_google(cfg, people, start, end, failed=None):
        seen["google_start"] = _time.perf_counter()
        await asyncio.sleep(0.4)
        return []
    monkeypatch.setattr(F, "_fetch_weather", slow_weather)
    monkeypatch.setattr(F, "fetch_google", slow_google)
    t = _time.perf_counter()
    asyncio.run(F.run_once(cfg, False))
    took = _time.perf_counter() - t
    assert abs(seen["weather_start"] - seen["google_start"]) < 0.2      # startet samtidigt …
    assert took < 0.75                                                   # … ikke efter hinanden (0,8 s)
    assert json.loads(Path(cfg["output"]).read_text())["weather"] == {"kilde": "MET Norway", "dage": []}
