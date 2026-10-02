"""Drift: planlægger, aftenpush, tilsyn og push-beskeder. Tiden gives med som argument, så intet skal vente på et ur."""
import asyncio
import json
from datetime import datetime

import pytest

import ops
import server

TZ = ops.TZ


def mk(cfg):
    st = server.State()
    s = server.Settings(cfg)
    return server.Runner(cfg, s, st, server.WebAuthHooks(st)), s, st


def at(h, m=0, s=0, day=1):
    return datetime(2026, 10, day, h, m, s, tzinfo=TZ)


# ---------------------------------------------------------------- push og tilstand
def test_notifier_sends_json_with_danish_letters_and_click_link(ntfy):
    n = ops.Notifier(ntfy.url, "https://familieplan.example.dk/")
    assert asyncio.run(n.send("I morgen, lørdag", "Åbn Familieplan – æøå", path="/auth", tags=("warning",)))
    m = ntfy.msgs[0]
    assert (m["topic"], m["title"], m["message"], m["click"], m["tags"]) == ("familieplan-test", "I morgen, lørdag", "Åbn Familieplan – æøå", "https://familieplan.example.dk/auth", ["warning"])


def test_notifier_is_silent_when_not_configured_or_unreachable():
    assert asyncio.run(ops.Notifier("").send("x", "y")) is False
    assert asyncio.run(ops.Notifier("http://127.0.0.1:1/emne").send("x", "y")) is False


def test_statefile_survives_restart_and_removes_none(tmp_path):
    f = ops.StateFile(tmp_path / "state.json")
    f.set(a="1", b="2")
    f.set(b=None)
    assert ops.StateFile(tmp_path / "state.json").get("a") == "1" and ops.StateFile(tmp_path / "state.json").get("b") is None


SECTIONS = [{"titel": "Husk", "punkter": [{"tekst": "Hugo: gymnastiktøj"}, {"tekst": "Carla: biblioteksbog"}]},
            {"titel": "Skal gøres", "punkter": [{"tekst": "Underskriv blanketten"}]},
            {"titel": "Særligt", "punkter": [{"tekst": "Hugo har vikar i Idræt"}]}]


def briefing(day="2026-10-02", sections=SECTIONS):
    return {"generated": "x", "mode": "day", "period": [day, day], "afsnit": sections}


def test_push_text_summary_contains_numbers_only_never_names():
    title, text, has = ops.evening_push_text(briefing())
    assert title == "I morgen, fredag" and has
    assert text == "2 at huske · 1 skal gøres · 1 særligt. Åbn Familieplan for detaljer."
    assert not any(w in text for w in ("Hugo", "Carla", "gymnastik", "vikar"))


def test_push_text_full_lists_the_content():
    _, text, _ = ops.evening_push_text(briefing(), "full")
    assert "Hugo: gymnastiktøj" in text and "Hugo har vikar i Idræt" in text


def test_push_has_nothing_to_say_when_only_deadlines():
    assert ops.evening_push_text(briefing(sections=[{"titel": "Kommende frister", "punkter": [{"tekst": "x"}]}]))[2] is False


# ---------------------------------------------------------------- planlæggeren
@pytest.mark.parametrize("now,expected", [(at(10), 900), (at(16, 59), 65), (at(17, 0, 3), 5), (at(17, 20), 900), (at(23, 59, 50), 15), (at(3), 7200)])
def test_scheduler_wakes_exactly_when_the_view_switches_and_at_midnight(cfg, now, expected):
    runner, *_ = mk(cfg)
    assert runner.wait_seconds(now) == expected


def test_an_unexpected_error_never_kills_the_scheduler(cfg):
    runner, s, st = mk(cfg)
    runner.error_backoff = 0.01
    calls = []

    async def boom(interactive=False):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("uventet")
        st.runs += 1

    runner.run = boom
    runner.wait_seconds = lambda now=None: 0.01

    async def go():
        t = asyncio.create_task(runner.loop())
        await asyncio.sleep(0.5)
        alive = not t.done()
        t.cancel()
        return alive

    assert asyncio.run(go())
    assert st.internal_errors == 2 and st.runs >= 1          # fejlene er talt og vist, og senere kørsler lykkedes


def test_a_strange_answer_from_the_fetch_is_handled(cfg, monkeypatch):
    import fetch_family
    runner, s, st = mk(cfg)

    async def odd(cfg, use_aula):
        return {}

    monkeypatch.setattr(fetch_family, "run_once", odd)
    asyncio.run(runner.run())
    assert st.aula == "error" and st.runs == 1


def test_supervise_restarts_a_crashing_task():
    n = {"calls": 0}

    async def flaky():
        n["calls"] += 1
        if n["calls"] < 3:
            raise RuntimeError("nede")
        await asyncio.sleep(10)

    async def go():
        t = asyncio.create_task(ops.supervise("test", flaky, backoff=0.01))
        await asyncio.sleep(0.3)
        t.cancel()

    asyncio.run(go())
    assert n["calls"] == 3


# ---------------------------------------------------------------- aftenpush
def write_briefing(cfg, day, sections=SECTIONS):
    from pathlib import Path
    (Path(cfg["output"]).parent / "briefing.json").write_text(json.dumps(briefing(day, sections), ensure_ascii=False), "utf-8")


def test_evening_push_is_sent_once_a_day_after_the_hour_and_survives_a_restart(cfg, ntfy):
    runner, *_ = mk(cfg)
    write_briefing(cfg, "2026-10-02")
    assert asyncio.run(runner.maybe_evening_push(at(16, 59))) == "ikke endnu"
    assert asyncio.run(runner.maybe_evening_push(at(17, 5))) == "sendt"
    assert asyncio.run(runner.maybe_evening_push(at(17, 20))) == "allerede sendt"
    assert asyncio.run(mk(cfg)[0].maybe_evening_push(at(17, 30))) == "allerede sendt"        # ny Runner = genstart
    assert len(ntfy.msgs) == 1
    m = ntfy.msgs[0]
    assert m["title"] == "I morgen, fredag" and m["click"] == "https://familieplan.example.dk/" and "Hugo" not in m["message"]
    assert asyncio.run(runner.maybe_evening_push(at(17, 5, day=2))) == "overblikket handler ikke om i morgen endnu"      # næste dag, overblikket er fra i går


def test_evening_push_skips_a_briefing_about_the_wrong_day(cfg, ntfy):
    runner, *_ = mk(cfg)
    write_briefing(cfg, "2026-10-01")                           # handler om i dag – ikke om i morgen
    assert asyncio.run(runner.maybe_evening_push(at(18))) == "overblikket handler ikke om i morgen endnu"
    assert ntfy.msgs == []


def test_evening_push_stays_quiet_when_there_is_nothing_special(cfg, ntfy):
    runner, *_ = mk(cfg)
    write_briefing(cfg, "2026-10-02", [{"titel": "Kommende frister", "punkter": [{"tekst": "x"}]}])
    assert asyncio.run(runner.maybe_evening_push(at(18))) == "intet særligt"
    assert ntfy.msgs == []
    assert asyncio.run(runner.maybe_evening_push(at(19))) == "allerede sendt"               # og spørger ikke igen samme dag


def test_evening_push_can_be_switched_off_or_show_details(cfg, ntfy):
    cfg["server"]["evening_push"] = False
    runner, *_ = mk(cfg)
    write_briefing(cfg, "2026-10-02")
    assert asyncio.run(runner.maybe_evening_push(at(18))) == "fra"
    cfg["server"]["evening_push"], cfg["server"]["push_details"] = True, "full"
    runner, *_ = mk(cfg)
    assert asyncio.run(runner.maybe_evening_push(at(18))) == "sendt"
    assert "Hugo: gymnastiktøj" in ntfy.msgs[0]["message"]


def test_a_failed_push_is_retried_next_run(cfg, ntfy):
    cfg["server"]["notify_ntfy"] = "http://127.0.0.1:1/emne"
    runner, *_ = mk(cfg)
    write_briefing(cfg, "2026-10-02")
    assert asyncio.run(runner.maybe_evening_push(at(18))) == "fejlede"
    assert runner.sf.get("evening_push") is None


# ---------------------------------------------------------------- tilsyn med gamle data
def watchdog(cfg, **state):
    runner, s, st = mk(cfg)
    st.started = at(6).isoformat()
    for k, v in state.items():
        setattr(st, k, v)
    return ops.Watchdog(s, st, runner.notifier, runner.sf), st


def test_watchdog_alarms_once_stays_quiet_at_night_and_reports_recovery(cfg, ntfy):
    wd, st = watchdog(cfg, last_success=at(8).isoformat(), aula="ok", aula_last_ok=at(8).isoformat())
    assert asyncio.run(wd.check(at(10))) == []                                        # 2 timer: fint
    assert asyncio.run(wd.check(at(13))) == ["data:alarm", "aula:alarm"]              # 5 timer
    assert asyncio.run(wd.check(at(13, 30))) == []                                    # ikke igen
    assert "siden 08.00" in ntfy.msgs[0]["message"]
    st.last_success = st.aula_last_ok = at(13, 40).isoformat()
    assert asyncio.run(wd.check(at(14))) == ["data:tilbage", "aula:tilbage"]
    assert asyncio.run(wd.check(at(14, 5))) == []


def test_watchdog_is_silent_at_night(cfg, ntfy):
    wd, _ = watchdog(cfg, last_success=at(8).isoformat(), aula="ok", aula_last_ok=at(8).isoformat())
    assert asyncio.run(wd.check(at(1, day=2))) == [] and ntfy.msgs == []


def test_watchdog_leaves_an_expired_login_to_its_own_message(cfg, ntfy):
    wd, _ = watchdog(cfg, last_success=at(13, 50).isoformat(), aula="login_required", aula_last_ok=None)
    assert asyncio.run(wd.check(at(14))) == []


def test_watchdog_reminds_after_a_day_and_can_be_switched_off(cfg, ntfy):
    wd, _ = watchdog(cfg, last_success=at(8).isoformat(), aula="ok", aula_last_ok=at(8).isoformat())
    asyncio.run(wd.check(at(13)))
    assert asyncio.run(wd.check(at(13, 5, day=2))) == ["data:alarm", "aula:alarm"]        # døgnet efter: en påmindelse
    cfg["server"]["stale_alert_hours"] = 0
    wd2, _ = watchdog(cfg, last_success=at(1).isoformat())
    assert asyncio.run(wd2.check(at(20))) == []
