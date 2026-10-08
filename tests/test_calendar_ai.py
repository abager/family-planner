"""calendar_ai: AI finder aftaler i Aula-punkter, kontrolleret mod teksten, med reglerne som reserve. Simuleret Gemini."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

import ai
import calendar_ai as C

TODAY = dt.date(2026, 9, 30)                                         # onsdag
NOW = dt.datetime(2026, 9, 30, 9, 0, tzinfo=dt.timezone.utc)
PEOPLE = {"hugo": "Hugo", "carla": "Carla", "leo": "Leo"}
KEY = "AIzaTEST-key_123"


def msg(mid, text, subject="Info", when="2026-09-30T08:00:00", people=("carla",), **kw):
    return {"id": mid, "subject": subject, "text": text, "timestamp": when, "people": list(people), "category": "info", **kw}


def data(*messages, posts=(), weekplan=()):
    return {"messages": list(messages), "posts": list(posts), "weekplan": list(weekplan), "events": []}


class Gemini:
    """Simuleret Gemini: `answer(items)` får punkterne fra forespørgslen og returnerer svaret pr. id."""

    def __init__(self, answer=None, status=200):
        self.answer, self.status, self.requests = answer or (lambda items: {}), status, []

    def items(self, i=-1):
        text = json.loads(self.requests[i].content)["contents"][0]["parts"][0]["text"]
        return json.loads(text.split("Punkter:\n", 1)[1])

    def __call__(self, req):
        self.requests.append(req)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "nope"}})
        items = self.items()
        per = self.answer(items)
        body = {"punkter": [{"id": it["id"], **per.get(it["emne"], {"nye": [], "aendringer": []})} for it in items]}
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(body, ensure_ascii=False)}]},
                                                         "finishReason": "STOP"}]})


@pytest.fixture
def acfg(cfg):
    cfg["assistant"] = {"mode": "ai"}
    return cfg


def run(cfg, d, fake, targets=None, today=TODAY, **settings):
    s = ai.Settings(**{"daily_cap": 100, "rpm": 0, **settings})
    client = ai.Client(s, Path(cfg["output"]).parent, transport=httpx.MockTransport(fake),
                       clock=lambda: NOW, sleep=lambda _s: None, env={s.api_key_env: KEY})
    return C.find_all(d, cfg, today, PEOPLE, targets, client=client)


def new(**kw):
    return {"nye": [{"titel": "Tur til skoven", "dato": "2026-10-08", "slut_dato": None, "start": "08:15", "slut": None,
                     "hele_dagen": False, "sted": "den røde port", "hvem": ["Carla"], "citat": "Vi tager i skoven", **kw}],
            "aendringer": []}


TRIP = "Vi tager i skoven næste torsdag, mødetid 8.15 ved den røde port. Husk madpakke."


# ---------------------------------------------------------------- nye aftaler
def test_the_ai_prefills_the_add_to_calendar_form(acfg):
    fake = Gemini(lambda items: {"Skovtur": new()})
    _, opts = run(acfg, data(msg("m1", TRIP, "Skovtur")), fake)
    o = opts["m1"][0]
    assert (o["title"], o["calendar_title"], o["start"], o["start_time"], o["all_day"]) == \
        ("Tur til skoven", "Carla: Tur til skoven", "2026-10-08", "08:15", False)
    assert o["location"] == "den røde port" and o["people"] == ["carla"] and o["by"] == "ai"
    assert o["source"] == {"type": "message", "id": "m1", "title": "Skovtur", "label": "Aula-besked"}
    assert "Vi tager i skoven" in o["description"]


def test_an_item_the_ai_does_not_flag_gets_no_button_even_if_the_rules_would(acfg):
    text = "Forældremøde d. 23/10 kl. 19-21 i klassen."
    _, rule_opts = C.A.find_all(data(msg("m1", text, "Møde")), acfg, TODAY, PEOPLE)
    assert rule_opts.get("m1")                                          # reglerne ville vise en knap
    _, opts = run(acfg, data(msg("m1", text, "Møde")), Gemini())
    assert "m1" not in opts


@pytest.mark.parametrize("change,ok", [
    ({"dato": "2026-10-01"}, True),                                     # "næste torsdag" = i morgen: også gyldig læsning
    ({"dato": "2026-10-09"}, False),                                    # fredag står ikke i teksten
    ({"dato": "2026-12-24"}, False),                                    # ingen sådan dato i teksten
    ({"start": "09:00"}, False),                                        # opdigtet klokkeslæt
    ({"dato": "2026-09-01"}, False),                                    # i fortiden
    ({"titel": ""}, False),
], ids=["torsdag-1-10", "fredag", "jul", "opdigtet-tid", "fortid", "uden-titel"])
def test_dates_and_times_must_be_in_the_text(acfg, change, ok):
    _, opts = run(acfg, data(msg("m1", TRIP, "Skovtur")), Gemini(lambda items: {"Skovtur": new(**change)}))
    assert bool(opts.get("m1")) is ok


def test_a_place_or_child_not_in_the_item_is_left_out_but_the_event_kept(acfg):
    fake = Gemini(lambda items: {"Skovtur": new(sted="Zoologisk Have", hvem=["Leo"])})
    _, opts = run(acfg, data(msg("m1", TRIP, "Skovtur")), fake)
    assert opts["m1"][0]["location"] is None and opts["m1"][0]["people"] == ["carla"]


def test_no_time_means_all_day(acfg):
    text = "Motionsdag fredag den 9. oktober. Husk gode sko."
    fake = Gemini(lambda items: {"Motionsdag": new(titel="Motionsdag", dato="2026-10-09", start=None, sted=None, hele_dagen=True)})
    o = run(acfg, data(msg("m1", text, "Motionsdag")), fake)[1]["m1"][0]
    assert o["all_day"] and o["start_time"] is None


def test_weekplan_items_use_their_own_day(acfg):
    w = {"id": "w1", "person": "hugo", "date": "2026-10-02", "subject": "Natur/teknologi", "category": "husk",
         "text": "Vi tager ud til åen kl. 9.00 og er tilbage kl. 12.00."}
    fake = Gemini(lambda items: {"Natur/teknologi": new(titel="Tur til åen", dato="2026-10-02", start="09:00", slut="12:00",
                                                         sted=None, hvem=["Hugo"])})
    o = run(acfg, data(weekplan=[w]), fake)[1]["w1"][0]
    assert (o["start"], o["start_time"], o["end_time"], o["people"]) == ("2026-10-02", "09:00", "12:00", ["hugo"])


# ---------------------------------------------------------------- privatliv
def test_private_threads_and_cpr_never_reach_the_ai(acfg):
    fake = Gemini()
    d = data(msg("m1", "Tur den 8. oktober. Spørg på 22334455 eller CPR 120515-1234", "Tur"),
             msg("m2", "HEMMELIG samtale om Hugo den 8. oktober", "Privat", private=True, category="samtale"))
    run(acfg, d, fake)
    sent = fake.requests[0].content.decode()
    assert "HEMMELIG" not in sent and "22334455" not in sent and "120515" not in sent
    assert [it["id"] for it in fake.items()] == ["S1"]


# ---------------------------------------------------------------- kvote, cache og reserve
def test_each_item_is_checked_only_once(acfg):
    fake = Gemini(lambda items: {"Skovtur": new()})
    d = data(msg("m1", TRIP, "Skovtur"))
    run(acfg, d, fake)
    _, opts = run(acfg, d, fake)
    assert len(fake.requests) == 1 and opts["m1"]


def test_a_changed_item_is_checked_again(acfg):
    fake = Gemini(lambda items: {"Skovtur": new()})
    run(acfg, data(msg("m1", TRIP, "Skovtur")), fake)
    run(acfg, data(msg("m1", TRIP + " Rettelse: husk regntøj.", "Skovtur")), fake)
    assert len(fake.requests) == 2


def test_items_are_sent_in_batches(acfg):
    fake = Gemini()
    d = data(*[msg(f"m{i}", f"Info nummer {i} om den 8. oktober", f"Emne {i}") for i in range(10)])
    run(acfg, d, fake)
    assert len(fake.requests) == 2 and [len(fake.items(i)) for i in range(2)] == [8, 2]


def test_at_most_a_few_requests_per_fetch(acfg):
    fake = Gemini()
    d = data(*[msg(f"m{i}", f"Info {i} den 8. oktober", f"Emne {i}") for i in range(40)])
    run(acfg, d, fake)
    assert len(fake.requests) == C.MAX_REQUESTS
    run(acfg, d, fake)                                                  # næste hentning tager resten: 16 punkter = 2 forespørgsler
    assert len(fake.requests) == C.MAX_REQUESTS + 2


def test_only_the_last_two_weeks_are_sent_the_first_time(acfg):
    fake = Gemini()
    old = msg("m0", "Tur den 8. oktober kl. 8.15", "Gammel", when="2026-09-01T08:00:00")
    run(acfg, data(old, msg("m1", TRIP, "Skovtur")), fake)
    assert [it["emne"] for it in fake.items()] == ["Skovtur"]


def test_the_summary_keeps_a_reserve_of_the_daily_budget(acfg):
    fake = Gemini(lambda items: {"Skovtur": new()})
    _, opts = run(acfg, data(msg("m1", TRIP, "Skovtur")), fake, daily_cap=C.RESERVE)
    assert not fake.requests and opts["m1"][0].get("by") != "ai"       # reglerne bruges


@pytest.mark.parametrize("status", [429, 503])
def test_ai_unavailable_means_the_rules_decide(acfg, status):
    _, opts = run(acfg, data(msg("m1", TRIP, "Skovtur")), Gemini(status=status))
    assert opts["m1"] and all(o.get("by") != "ai" for o in opts["m1"])


def test_offline_mode_never_asks_the_ai(cfg):
    fake = Gemini()
    _, opts = run(cfg, data(msg("m1", TRIP, "Skovtur")), fake)
    assert not fake.requests and opts["m1"]


def test_an_invalid_answer_means_the_rules_decide(acfg):
    def bad(req):
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"forkert": 1}'}]}, "finishReason": "STOP"}]})
    _, opts = run(acfg, data(msg("m1", TRIP, "Skovtur")), bad)
    assert opts["m1"] and opts["m1"][0].get("by") != "ai"


# ---------------------------------------------------------------- aflyst / udsat / flyttet
TARGET = {"key": "k1", "event_id": "ev1", "title": "Carla: Tur til skoven", "start": "2026-10-08", "end": "2026-10-08", "source": "app"}


def change(**kw):
    return {"nye": [], "aendringer": [{"type": "cancel", "gammel_titel": "Tur til skoven", "gammel_dato": "2026-10-08",
                                       "ny_dato": None, "start": None, "slut": None, "citat": "Turen er aflyst", **kw}]}


def test_the_ai_finds_a_cancellation_of_an_event_the_app_created(acfg):
    fake = Gemini(lambda items: {"Aflysning": change()})
    sugg, _ = run(acfg, data(msg("m2", "Skovturen torsdag den 8. oktober er desværre aflyst.", "Aflysning")), fake, [TARGET])
    s = sugg[0]
    assert (s["kind"], s["label"], s["target"]["event_id"], s["by"]) == ("cancel", "Aflyst", "ev1", "ai")
    assert s["sources"][0]["id"] == "m2"


def test_a_move_needs_the_new_date_in_the_text(acfg):
    text = "Skovturen den 8. oktober er flyttet til tirsdag den 13. oktober kl. 9.00."
    ok = Gemini(lambda items: {"Flytning": change(type="move", ny_dato="2026-10-13", start="09:00")})
    s = run(acfg, data(msg("m2", text, "Flytning")), ok, [TARGET])[0][0]
    assert (s["kind"], s["start"], s["start_time"]) == ("move", "2026-10-13", "09:00")
    bad = Gemini(lambda items: {"Flytning": change(type="move", ny_dato="2026-10-20")})
    assert run(acfg, data(msg("m2", text, "Flytning")), bad, [TARGET], model="anden")[0] == []   # anden model: ingen cache


@pytest.mark.parametrize("targets", [[], [{**TARGET, "source": "google", "key": None, "event_id": None}],
                                     [TARGET, {**TARGET, "key": "k2", "event_id": "ev2"}]],
                         ids=["ingen-aftale", "ikke-oprettet-af-appen", "to-mulige"])
def test_a_change_without_exactly_one_app_event_is_dropped(acfg, targets):
    fake = Gemini(lambda items: {"Aflysning": change()})
    assert run(acfg, data(msg("m2", "Skovturen den 8. oktober er aflyst.", "Aflysning")), fake, targets)[0] == []


def test_the_calendar_check_asks_gemini_for_a_fixed_answer_format(acfg):
    fake = Gemini()
    run(acfg, data(msg("m1", "Fotografering torsdag den 8. oktober.")), fake)
    assert json.loads(fake.requests[0].content)["generationConfig"]["responseSchema"] == C.SCHEMA
