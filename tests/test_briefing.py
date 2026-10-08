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


STORY = ["Torsdag starter med, at I skal huske drikkedunk og fodboldsko til Carla, inden I går ud ad døren.",
         "Resten af dagen er rolig, så der er tid til at lande, når alle er hjemme igen."]
GOOD = {"fortaelling": STORY, "kilder": ["O1"]}
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
    assert b["fortaelling"] == STORY and b["kilde_ids"] == ["O1"] and "afsnit" not in b
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
    fixed = {"fortaelling": ["Torsdag skal Hugo læse, og Carla skal have drikkedunk og fodboldsko med i skole."], "kilder": ["O1", "O2"]}
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


def test_unknown_source_ids_are_dropped_but_one_valid_is_needed(acfg):
    b = brief(acfg, Fake(ok({"fortaelling": STORY, "kilder": ["O1", "X9"]})))
    assert b["method"] == "ai" and b["kilde_ids"] == ["O1"]
    b = B.make_briefing(acfg, family_data("ny lektie"), "day", now=NOW, force=True,
                        client=client(acfg, Fake(ok({"fortaelling": STORY, "kilder": ["X9"]})), model="anden"))
    assert b["ai_stale"]["reason"] == "ugyldigt_svar"                  # det gyldige AI-overblik beholdes


@pytest.mark.parametrize("story", [
    ["Carla har fodbold kl. 16.30, så husk drikkedunk og fodboldsko."],                 # tid findes ikke i data
    ["- Carla: drikkedunk og fodboldsko", "- Hugo: lektier"],                            # punktopstilling
    ["**Torsdag** skal Carla have drikkedunk og fodboldsko med."],                       # fed skrift
    ["Kort."],                                                                            # for kort
    [" ".join(["ord"] * 300)],                                                            # for lang
    [],
], ids=["opdigtet-tid", "punkter", "fed", "for-kort", "for-lang", "tom"])
def test_a_narrative_that_breaks_the_rules_falls_back(acfg, story):
    b = brief(acfg, Fake(ok({"fortaelling": story, "kilder": ["O1"]})))
    assert b["method"] == "offline" and b["ai_fallback"]["reason"] == "ugyldigt_svar"


def test_times_that_are_in_the_data_are_allowed(acfg):
    d = family_data()
    d["events"] = [{"id": "e1", "title": "Fodbold", "start": "2026-10-01T16:30:00+02:00", "end": "2026-10-01T18:00:00+02:00",
                    "people": ["carla"], "source": "google", "location": "Kunstgræsbanen"}]
    story = ["Torsdag skal Carla have drikkedunk og fodboldsko med, for hun har fodbold kl. 16.30 til 18 på Kunstgræsbanen."]
    b = brief(acfg, Fake(ok({"fortaelling": story, "kilder": ["A1", "O1"]})), data=d)
    assert b["method"] == "ai" and b["fortaelling"] == story


def test_a_single_string_is_split_into_paragraphs(acfg):
    b = brief(acfg, Fake(ok({"fortaelling": STORY[0] + "\n\n" + STORY[1], "kilder": ["O1"]})))
    assert b["fortaelling"] == STORY


def test_the_ai_gets_calendar_messages_and_posts_but_not_the_normal_timetable(acfg):
    d = family_data()
    d["events"] = [
        {"id": "e1", "title": "Tandlæge", "start": "2026-10-01T14:00:00+02:00", "end": "2026-10-01T14:30:00+02:00",
         "people": ["hugo"], "source": "google", "location": "Torvet 3", "notes": "Husk sundhedskort"},
        {"id": "s1", "title": "Skole", "start": "2026-10-01T08:00:00+02:00", "end": "2026-10-01T14:00:00+02:00", "people": ["hugo"],
         "lessons": [{"start": "08:00", "end": "08:45", "title": "Dansk"},
                     {"start": "10:00", "end": "10:45", "title": "Matematik", "substitute": True}]},
        {"id": "s2", "title": "Skole", "start": "2026-10-01T08:00:00+02:00", "end": "2026-10-01T12:00:00+02:00", "people": ["carla"],
         "lessons": [{"start": "08:00", "end": "12:00", "title": "Engelsk"}]}]
    d["messages"].append({"id": "m2", "subject": "Forældremøde", "text": "Forældremøde tirsdag kl. 19", "from": "Lærer",
                          "timestamp": "2026-09-30T10:00:00", "people": ["carla"], "category": "info"})
    d["posts"] = [{"id": "p1", "title": "Motionsdag", "text": "Motionsdag på fredag", "timestamp": "2026-09-30T09:00:00", "people": ["leo"]}]
    fake = Fake(ok(GOOD))
    brief(acfg, fake, data=d)
    payload = json.loads(json.loads(fake.requests[0].content)["contents"][0]["parts"][0]["text"].split("Data:\n", 1)[1])
    assert [a["titel"] for a in payload["aftaler"]] == ["Tandlæge"]
    assert payload["aftaler"][0]["sted"] == "Torvet 3" and payload["aftaler"][0]["note"] == "Husk sundhedskort"
    assert payload["skema"] == [{"dato": "2026-10-01", "hvem": ["Hugo"], "vikar": [{"fag": "Matematik", "tid": "10:00"}]}]
    assert "Dansk" not in json.dumps(payload) and "Engelsk" not in json.dumps(payload)          # normalt skema sendes ikke
    assert payload["nye_beskeder"][0]["emne"] == "Forældremøde" and payload["nye_opslag"][0]["titel"] == "Motionsdag"
    assert PRIVATE not in json.dumps(payload)


def test_the_prompt_asks_for_a_warm_chronological_narrative_without_the_timetable(acfg):
    fake = Fake(ok(GOOD))
    brief(acfg, fake)
    system = json.loads(fake.requests[0].content)["systemInstruction"]["parts"][0]["text"]
    for phrase in ("Kronologisk", "Tal direkte til forældrene", "Nævn IKKE det normale skoleskema", "ca. 150 ord", '"fortaelling"'):
        assert phrase in system



def test_the_prompt_has_no_read_aloud_and_a_stray_one_is_dropped(acfg):
    # Oplæsning er fjernet: prompten beder ikke om den, og svarer modellen alligevel med den, gemmes den ikke
    fake = Fake(ok({**GOOD, "oplaesning": "I dag skal Carla til fodbold."}))
    b = brief(acfg, fake)
    system = json.loads(fake.requests[0].content)["systemInstruction"]["parts"][0]["text"]
    assert "oplaesning" not in system and "LÆST HØJT" not in system
    assert b["method"] == "ai" and "oplaesning" not in b


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
    assert "ugens fortælling" in json.loads(fake.requests[0].content)["contents"][0]["parts"][0]["text"]


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


# ---------------------------------------------------------------- vejret i overblikket
def wx(dato="2026-10-01", ugedag="torsdag", regn="regn", raad=("regntøj og gummistøvler",), **kw):
    d = {"dato": dato, "ugedag": ugedag, "min": 8, "max": 11, "regn": regn, "himmel": "overskyet", "vind": None,
         "frost": False, "raad": list(raad), **kw}
    if regn != "ingen":
        d.setdefault("regn_hvornaar", "om eftermiddagen")
    return d


def with_weather(*days):
    d = family_data()
    d["weather"] = {"kilde": "MET Norway", "hentet": "2026-10-01T06:00:00+02:00", "dage": list(days)}
    return d


def test_the_ai_gets_coarse_weather_for_the_day_with_a_source_id(acfg):
    fake = Fake(ok({"fortaelling": ["Torsdag bliver grå med regn om eftermiddagen og 8 til 11 grader, så pak regntøj og gummistøvler."],
                    "kilder": ["V1"]}))
    b = brief(acfg, fake, data=with_weather(wx(), wx("2026-10-02", "fredag")))
    sent = json.loads(fake.requests[0].content)["contents"][0]["parts"][0]["text"]
    payload = json.loads(sent.split("Data:\n", 1)[1])
    assert [v["dato"] for v in payload["vejr"]] == ["2026-10-01"]          # kun dagen overblikket handler om
    assert payload["vejr"][0]["id"] == "V1" and "lat" not in sent and "tekst" not in payload["vejr"][0]
    assert b["method"] == "ai" and b["kilde_ids"] == ["V1"]
    assert "Væv vejret ind" in json.loads(fake.requests[0].content)["systemInstruction"]["parts"][0]["text"]


def test_the_week_gets_only_the_days_the_forecast_covers(acfg):
    fake = Fake(ok(GOOD))
    brief(acfg, fake, mode="week", data=with_weather(wx(), wx("2026-10-02", "fredag")))
    payload = json.loads(json.loads(fake.requests[0].content)["contents"][0]["parts"][0]["text"].split("Data:\n", 1)[1])
    assert [v["ugedag"] for v in payload["vejr"]] == ["torsdag", "fredag"]


def test_without_ai_the_rules_write_the_weather_with_advice(cfg):
    b = B.make_briefing(cfg, with_weather(wx()), "day", now=NOW)
    sec = b["afsnit"][0]
    assert sec["titel"] == "Vejr"
    assert sec["punkter"][0]["tekst"] == "8–11°, regn om eftermiddagen – regntøj og gummistøvler"
    assert sec["punkter"][0]["kilder"] == ["Vejr (MET Norway)"]


def test_the_week_fallback_has_one_weather_line_per_covered_day(cfg):
    b = B.make_briefing(cfg, with_weather(wx(), wx("2026-10-02", "fredag", regn="ingen", raad=())), "week", now=NOW)
    vejr = next(s for s in b["afsnit"] if s["titel"] == "Vejr")
    assert [p["tekst"] for p in vejr["punkter"]] == ["Torsdag: 8–11°, regn om eftermiddagen – regntøj og gummistøvler",
                                                     "Fredag: 8–11°, overskyet"]


def test_no_weather_means_no_weather_section_and_no_weather_in_the_prompt(acfg, cfg):
    b = B.make_briefing(cfg, family_data(), "day", now=NOW)
    assert all(s["titel"] != "Vejr" for s in b["afsnit"])
    fake = Fake(ok(GOOD))
    brief(acfg, fake)
    payload = json.loads(json.loads(fake.requests[0].content)["contents"][0]["parts"][0]["text"].split("Data:\n", 1)[1])
    assert payload["vejr"] == []


def test_unchanged_coarse_weather_costs_no_new_ai_request(acfg):
    fake = Fake(ok(GOOD))
    brief(acfg, fake, data=with_weather(wx()))
    later = with_weather(wx())
    later["weather"]["hentet"] = "2026-10-01T07:00:00+02:00"           # ny hentning, samme grove vejr
    brief(acfg, fake, data=later, now=NOW + dt.timedelta(hours=1, minutes=5))
    assert len(fake.requests) == 1


# ---------------------------------------------------------------- svarformat og log
def test_the_briefing_asks_gemini_for_a_fixed_answer_format(acfg):
    fake = Fake(ok(GOOD))
    brief(acfg, fake)
    gc = json.loads(fake.requests[0].content)["generationConfig"]
    assert gc["responseSchema"] == B.SCHEMA
    assert set(B.SCHEMA["required"]) == {"fortaelling", "kilder"}


def test_the_format_example_in_the_prompt_is_valid_json():
    example = B.SYSTEM.split("i dette format", 1)[1].split("\n", 1)[1].rsplit("}", 1)[0] + "}"
    assert set(json.loads(example)) == {"fortaelling", "kilder"}            # ingen "…" modellen kan efterligne


def test_the_ai_client_saves_invalid_answers_next_to_the_private_threads(acfg):
    import private
    c = B.ai_client(acfg)
    assert c.invalid_path == private.store_path(acfg).parent / ai.INVALID_FILE
    assert Path(acfg["output"]).parent not in c.invalid_path.parents       # aldrig i den mappe, der serveres


def test_the_fallback_log_line_says_why(acfg, caplog):
    with caplog.at_level("INFO", logger="familieplanner.briefing"):
        B.make_briefing(acfg, family_data(), "day", now=NOW, client=client(acfg, Fake(ok(GOOD)), env={}))
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Skrev"))
    assert "uden sprogmodel – AI ikke tilgængelig: API-nøglen mangler i .env (GEMINI_API_KEY)" in line


def test_during_a_pause_the_log_line_names_the_last_error(acfg, caplog):
    out = Path(acfg["output"]).parent
    (out / "ai_usage.json").write_text(json.dumps({
        "day": "2026-10-01", "failures": 2, "backoff_until": "2026-10-01T09:30:00+00:00", "pause_reason": "serverfejl",
        "last_error": {"reason": "serverfejl", "detail": "HTTP 503", "at": "2026-10-01T06:45:00+00:00"}}))
    with caplog.at_level("INFO", logger="familieplanner.briefing"):
        b = brief(acfg, Fake(ok(GOOD)))
    assert b["ai_fallback"]["reason"] == "pause"
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Skrev"))
    assert "venter efter en tidligere fejl til kl. 11.30" in line
    assert "seneste fejl 01.10. kl. 08.45: udbyderen har problemer (5xx) (HTTP 503)" in line


def test_the_case_from_the_log_invalid_json_twice_is_saved_and_only_that_data_waits(acfg, tmp_path):
    broken = {"candidates": [{"content": {"parts": [{"text": '{\n  "fortaelling": [\n    Torsdag skal Carla …\n  ]\n}'}]},
                              "finishReason": "STOP"}]}
    fake = Fake((200, broken))
    secret = tmp_path / "secrets" / ai.INVALID_FILE
    c = client(acfg, fake)
    c.invalid_path = secret
    b = B.make_briefing(acfg, family_data(), "day", now=NOW, client=c)
    assert b["method"] == "offline" and b["ai_fallback"]["reason"] == "ugyldigt_svar" and len(fake.requests) == 2
    assert "line 3 column 5" in json.loads(secret.read_text("utf-8"))["error"]
    B.make_briefing(acfg, family_data(), "day", now=NOW + dt.timedelta(minutes=5), client=c)
    assert len(fake.requests) == 2                                         # samme data: venter
    good = Fake(ok(GOOD))
    b = B.make_briefing(acfg, family_data("Læs side 12-20"), "day", now=NOW + dt.timedelta(minutes=10),
                        client=client(acfg, good))
    assert b["method"] == "ai" and len(good.requests) == 1                 # nye data: spørger med det samme
