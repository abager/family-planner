"""Bjælker pr. datatype: progress.py og at hentningen melder fremdrift for hver del."""
from __future__ import annotations

import asyncio

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
