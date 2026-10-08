"""ai.py mod en simuleret Gemini/Claude (httpx.MockTransport). Ingen rigtig netværkstrafik."""
from __future__ import annotations

import datetime as dt
import json
import os
import stat

import httpx
import pytest

import ai

UTC = dt.timezone.utc
KEY = "AIzaTEST-key_123"


class Fake:
    """Simuleret udbyder. `replies` er en kø af (status, body, headers); den sidste gentages."""

    def __init__(self, *replies):
        self.replies = list(replies) or [(200, gemini_ok({"ok": True}), {})]
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body, headers = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(body, Exception):
            raise body
        content = body if isinstance(body, (bytes, str)) else json.dumps(body)
        return httpx.Response(status, content=content, headers=headers)

    def body(self, i=-1) -> dict:
        return json.loads(self.requests[i].content)


class Clock:
    def __init__(self, t: dt.datetime):
        self.t = t
        self.slept: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += dt.timedelta(seconds=s)

    def advance(self, **kw):
        self.t += dt.timedelta(**kw)


def gemini_ok(obj, finish="STOP"):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj, ensure_ascii=False)}], "role": "model"},
                            "finishReason": finish}]}


def gemini_text(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}


def google_error(code, status, message="fejl", details=None):
    return {"error": {"code": code, "status": status, "message": message, "details": details or []}}


@pytest.fixture
def clock():
    return Clock(dt.datetime(2026, 10, 2, 8, 0, tzinfo=UTC))           # kl. 10 dansk tid, 01.00 i Californien


def make(tmp_path, clock, fake, **kw):
    s = ai.Settings(**{"daily_cap": 10, "rpm": 3, **kw})
    return ai.Client(s, tmp_path, transport=httpx.MockTransport(fake), clock=clock, sleep=clock.sleep,
                     env={s.api_key_env: KEY})


def usage(tmp_path):
    return json.loads((tmp_path / "ai_usage.json").read_text("utf-8"))


# ---------------------------------------------------------------- normalt svar
def test_a_normal_gemini_reply_is_returned_as_a_dict(tmp_path, clock):
    fake = Fake((200, gemini_ok({"afsnit": [{"titel": "Husk", "punkter": []}]}), {}))
    out = make(tmp_path, clock, fake).generate_json("system", "prompt")
    assert out == {"afsnit": [{"titel": "Husk", "punkter": []}]}
    req = fake.requests[0]
    assert req.url.path == "/v1beta/models/gemini-3.5-flash-lite:generateContent"
    assert req.headers["x-goog-api-key"] == KEY
    assert KEY not in str(req.url)                                      # nøglen aldrig i adressen
    b = fake.body()
    assert b["generationConfig"]["responseMimeType"] == "application/json"
    assert b["systemInstruction"]["parts"][0]["text"] == "system"
    assert b["contents"][0]["parts"][0]["text"] == "prompt"
    assert usage(tmp_path)["requests"] == 1


def test_a_reply_wrapped_in_a_json_fence_is_accepted(tmp_path, clock):
    fake = Fake((200, gemini_text('```json\n{"a": 1}\n```'), {}))
    assert make(tmp_path, clock, fake).generate_json("s", "p") == {"a": 1}


def test_thought_parts_are_ignored(tmp_path, clock):
    body = {"candidates": [{"content": {"parts": [{"text": "tænker…", "thought": True}, {"text": '{"a": 2}'}]}, "finishReason": "STOP"}]}
    assert make(tmp_path, clock, Fake((200, body, {}))).generate_json("s", "p") == {"a": 2}


def test_the_model_is_configurable_and_pinned(tmp_path, clock):
    fake = Fake()
    make(tmp_path, clock, fake, model="gemini-9-test").generate_json("s", "p")
    assert fake.requests[0].url.path.endswith("/models/gemini-9-test:generateContent")


# ---------------------------------------------------------------- cache
def test_the_same_content_costs_only_one_request(tmp_path, clock):
    fake = Fake()
    c = make(tmp_path, clock, fake)
    c.generate_json("s", "p")
    c.generate_json("s", "p")
    assert len(fake.requests) == 1 and usage(tmp_path)["requests"] == 1


def test_an_explicit_cache_key_ignores_volatile_parts_of_the_prompt(tmp_path, clock):
    fake = Fake()
    c = make(tmp_path, clock, fake)
    c.generate_json("s", "klokken er 10.00 …", cache_key="samme-indhold")
    c.generate_json("s", "klokken er 10.15 …", cache_key="samme-indhold")
    assert len(fake.requests) == 1


def test_new_content_or_another_model_is_not_served_from_cache(tmp_path, clock):
    fake = Fake()
    make(tmp_path, clock, fake).generate_json("s", "p1")
    make(tmp_path, clock, fake).generate_json("s", "p2")
    make(tmp_path, clock, fake, model="andet").generate_json("s", "p1")
    assert len(fake.requests) == 3


def test_old_cache_entries_expire(tmp_path, clock):
    fake = Fake()
    make(tmp_path, clock, fake).generate_json("s", "p")
    clock.advance(days=ai.CACHE_MAX_DAYS + 1)
    make(tmp_path, clock, fake).generate_json("s", "p")
    assert len(fake.requests) == 2


def test_a_cached_reply_is_a_copy(tmp_path, clock):
    c = make(tmp_path, clock, Fake((200, gemini_ok({"l": [1]}), {})))
    c.generate_json("s", "p")["l"].append(2)
    assert c.generate_json("s", "p") == {"l": [1]}


# ---------------------------------------------------------------- ugyldigt svar
@pytest.mark.parametrize("body", [gemini_text("Her er dit overblik: …"), gemini_text("[1, 2]"), {"candidates": []},
                                  gemini_ok({"a": 1}, finish="MAX_TOKENS"), {"promptFeedback": {"blockReason": "SAFETY"}}],
                         ids=["ikke-json", "liste", "tomt", "afbrudt", "blokeret"])
def test_invalid_output_is_retried_once_then_pauses_only_that_content(tmp_path, clock, body):
    fake = Fake((200, body, {}))
    c = make(tmp_path, clock, fake)
    with pytest.raises(ai.AIUnavailable) as e:
        c.generate_json("s", "p")
    assert e.value.reason == "ugyldigt_svar" and len(fake.requests) == 2      # prøvet straks én gang til
    u = usage(tmp_path)
    assert not u.get("backoff_until")                                       # ingen pause for al AI
    assert len(u["invalid"]) == 1 and u["last_error"]["reason"] == "ugyldigt_svar"


def test_the_same_content_waits_but_other_content_may_still_ask(tmp_path, clock):
    fake = Fake((200, gemini_text('{\n  "fortaelling": [\n    …\n  ]\n}'), {}), (200, gemini_text("nej"), {}),
                (200, gemini_ok({"andet": 1}), {}))
    c = make(tmp_path, clock, fake)
    with pytest.raises(ai.AIUnavailable):
        c.generate_json("s", "p")
    with pytest.raises(ai.AIUnavailable) as e:                              # samme data: intet sendes
        c.generate_json("s", "p")
    assert e.value.reason == "ugyldigt_svar" and "samme data" in e.value.detail and len(fake.requests) == 2
    assert c.generate_json("s", "nye data") == {"andet": 1}                 # nyt indhold (fx kalenderforslag) må gerne
    assert len(usage(tmp_path)["invalid"]) == 1                             # mærket for det første indhold består


def test_the_pause_for_one_content_grows_and_a_valid_answer_removes_it(tmp_path, clock):
    fake = Fake((200, gemini_text("nej"), {}), (200, gemini_text("nej"), {}), (200, gemini_text("nej"), {}),
                (200, gemini_text("nej"), {}), (200, gemini_ok({"ok": 1}), {}))
    c = make(tmp_path, clock, fake, rpm=0)
    pauses = []
    for _ in range(2):
        with pytest.raises(ai.AIUnavailable):
            c.generate_json("s", "p")
        until = dt.datetime.fromisoformat(next(iter(usage(tmp_path)["invalid"].values()))["until"])
        pauses.append((until - clock.t).total_seconds())
        clock.t = until
    assert pauses == [15 * 60, 30 * 60]
    assert c.generate_json("s", "p") == {"ok": 1}
    assert not usage(tmp_path)["invalid"]


def test_a_retry_that_succeeds_is_returned_and_cached(tmp_path, clock):
    fake = Fake((200, gemini_text("Her er dit overblik"), {}), (200, gemini_ok({"ok": 1}), {}))
    c = make(tmp_path, clock, fake)
    assert c.generate_json("s", "p") == {"ok": 1} and len(fake.requests) == 2
    assert c.generate_json("s", "p") == {"ok": 1} and len(fake.requests) == 2      # fra cachen
    assert not usage(tmp_path).get("invalid") and usage(tmp_path)["last_ok"]


def test_the_raw_invalid_answer_is_saved_privately_and_never_logged(tmp_path, clock, caplog):
    raw = '{\n  "fortaelling": [\n    Carla skal have drikkedunk med\n  ]\n}'
    secret = tmp_path / "secrets" / ai.INVALID_FILE
    s = ai.Settings(daily_cap=10, rpm=3)
    c = ai.Client(s, tmp_path, transport=httpx.MockTransport(Fake((200, gemini_text(raw), {}))), clock=clock,
                  sleep=clock.sleep, env={s.api_key_env: KEY}, invalid_path=secret)
    with caplog.at_level("INFO", logger="familieplanner.ai"), pytest.raises(ai.AIUnavailable):
        c.generate_json("s", "p")
    saved = json.loads(secret.read_text("utf-8"))
    assert saved["text"] == raw and saved["finish"] == "STOP" and saved["model"] == s.model
    assert "Expecting value: line 3 column 5" in saved["error"]
    assert "drikkedunk" not in caplog.text and str(secret) in caplog.text
    if os.name == "posix":
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600


def test_a_blocked_answer_without_text_saves_the_providers_reply(tmp_path, clock):
    secret = tmp_path / "bad.json"
    s = ai.Settings(daily_cap=10, rpm=3)
    c = ai.Client(s, tmp_path, transport=httpx.MockTransport(Fake((200, {"promptFeedback": {"blockReason": "SAFETY"}}, {}))),
                  clock=clock, sleep=clock.sleep, env={s.api_key_env: KEY}, invalid_path=secret)
    with pytest.raises(ai.AIUnavailable):
        c.generate_json("s", "p")
    saved = json.loads(secret.read_text("utf-8"))
    assert saved["text"] is None and saved["response"]["promptFeedback"]["blockReason"] == "SAFETY"


def test_without_a_path_nothing_is_saved(tmp_path, clock):
    with pytest.raises(ai.AIUnavailable):
        make(tmp_path, clock, Fake((200, gemini_text("nej"), {}))).generate_json("s", "p")
    assert not list(tmp_path.rglob(ai.INVALID_FILE))


def test_an_old_pause_from_an_invalid_answer_no_longer_blocks_everything(tmp_path, clock):
    (tmp_path / "ai_usage.json").write_text(json.dumps({                    # sådan så filen ud før denne version
        "day": "2026-10-02", "requests": 3, "failures": 5, "backoff_until": "2026-10-02T12:00:00+00:00",
        "last_error": {"reason": "ugyldigt_svar", "detail": "Expecting value", "at": "2026-10-02T07:00:00+00:00"}}))
    fake = Fake((200, gemini_ok({"ok": 1}), {}))
    assert make(tmp_path, clock, fake).generate_json("s", "p") == {"ok": 1}
    assert usage(tmp_path)["failures"] == 0


def test_a_pause_from_a_real_outage_still_blocks(tmp_path, clock):
    (tmp_path / "ai_usage.json").write_text(json.dumps({
        "day": "2026-10-02", "failures": 1, "backoff_until": "2026-10-02T12:00:00+00:00", "pause_reason": "serverfejl",
        "last_error": {"reason": "ugyldigt_svar", "detail": "x", "at": "2026-10-02T07:00:00+00:00"}}))
    fake = Fake()
    with pytest.raises(ai.AIUnavailable) as e:
        make(tmp_path, clock, fake).generate_json("s", "p")
    assert e.value.reason == "pause" and not fake.requests


# ---------------------------------------------------------------- svarformat (schema)
SCHEMA = {"type": "OBJECT", "properties": {"svar": {"type": "STRING"}}, "required": ["svar"]}


def test_a_schema_is_sent_to_gemini(tmp_path, clock):
    fake = Fake((200, gemini_ok({"svar": "ok"}), {}))
    make(tmp_path, clock, fake).generate_json("s", "p", schema=SCHEMA)
    gc = fake.body()["generationConfig"]
    assert gc["responseSchema"] == SCHEMA and gc["responseMimeType"] == "application/json"


def test_without_a_schema_gemini_gets_none(tmp_path, clock):
    fake = Fake((200, gemini_ok({"svar": "ok"}), {}))
    make(tmp_path, clock, fake).generate_json("s", "p")
    assert "responseSchema" not in fake.body()["generationConfig"]


def test_claude_ignores_the_schema(tmp_path, clock):
    fake = Fake((200, {"content": [{"type": "text", "text": '{"svar": "ok"}'}], "stop_reason": "end_turn"}, {}))
    c = make(tmp_path, clock, fake, provider="claude", model="claude-sonnet-5-5", api_key_env="ANTHROPIC_API_KEY")
    assert c.generate_json("s", "p", schema=SCHEMA) == {"svar": "ok"}
    assert "schema" not in json.dumps(fake.body()).lower()


def test_if_gemini_rejects_the_schema_the_request_is_repeated_without(tmp_path, clock):
    fake = Fake((400, google_error(400, "INVALID_ARGUMENT", "Invalid JSON payload: responseSchema"), {}),
                (200, gemini_ok({"svar": "ok"}), {}))
    assert make(tmp_path, clock, fake).generate_json("s", "p", schema=SCHEMA) == {"svar": "ok"}
    assert "responseSchema" in fake.body(0)["generationConfig"] and "responseSchema" not in fake.body(1)["generationConfig"]
    assert not usage(tmp_path).get("backoff_until")


def test_a_400_without_and_with_schema_is_still_afvist(tmp_path, clock):
    fake = Fake((400, google_error(400, "INVALID_ARGUMENT", "nope"), {}))
    with pytest.raises(ai.AIUnavailable) as e:
        make(tmp_path, clock, fake).generate_json("s", "p", schema=SCHEMA)
    assert e.value.reason == "afvist" and len(fake.requests) == 2


# ---------------------------------------------------------------- logning
def test_reasons_that_send_nothing_are_logged_once(tmp_path, clock, caplog):
    s = ai.Settings()
    c = ai.Client(s, tmp_path, clock=clock, env={})
    with caplog.at_level("WARNING", logger="familieplanner.ai"):
        for _ in range(3):
            with pytest.raises(ai.AIUnavailable):
                c.generate_json("s", "p")
    assert caplog.text.count("API-nøglen mangler") == 1


def test_a_pause_is_logged_once_and_again_after_the_next_failure(tmp_path, clock, caplog):
    fake = Fake((503, google_error(503, "UNAVAILABLE"), {}))
    c = make(tmp_path, clock, fake)
    with pytest.raises(ai.AIUnavailable):
        c.generate_json("s", "p")
    with caplog.at_level("WARNING", logger="familieplanner.ai"):
        for _ in range(3):
            with pytest.raises(ai.AIUnavailable):
                c.generate_json("s", "p")
        assert caplog.text.count("venter efter en tidligere fejl") == 1
        clock.t = dt.datetime.fromisoformat(usage(tmp_path)["backoff_until"])
        with pytest.raises(ai.AIUnavailable):                              # ny fejl, ny pause
            c.generate_json("s", "p")
        with pytest.raises(ai.AIUnavailable):
            c.generate_json("s", "p")
    assert caplog.text.count("venter efter en tidligere fejl") == 2


def test_output_failing_the_callers_validation_is_invalid_and_not_cached(tmp_path, clock):
    def need_afsnit(d):
        if "afsnit" not in d:
            raise ValueError("mangler afsnit")
    fake = Fake((200, gemini_ok({"forkert": 1}), {}))
    c = make(tmp_path, clock, fake)
    with pytest.raises(ai.AIUnavailable) as e:
        c.generate_json("s", "p", validate=need_afsnit)
    assert e.value.reason == "ugyldigt_svar"
    assert not (tmp_path / "ai_cache.json").exists()


# ---------------------------------------------------------------- 429, 402, 403, 5xx, netværk
def test_429_pauses_for_the_time_google_asks(tmp_path, clock):
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "300s"}]
    fake = Fake((429, google_error(429, "RESOURCE_EXHAUSTED", details=details), {}))
    c = make(tmp_path, clock, fake)
    with pytest.raises(ai.AIUnavailable) as e:
        c.generate_json("s", "p")
    assert e.value.reason == "kvote"
    until = dt.datetime.fromisoformat(usage(tmp_path)["backoff_until"])
    assert until == clock.t + dt.timedelta(seconds=300)
    with pytest.raises(ai.AIUnavailable) as e:                         # under pausen sendes intet
        c.generate_json("s", "p2")
    assert e.value.reason == "pause" and len(fake.requests) == 1


def test_429_on_the_daily_quota_waits_until_pacific_midnight(tmp_path, clock):
    details = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]
    with pytest.raises(ai.AIUnavailable):
        make(tmp_path, clock, Fake((429, google_error(429, "RESOURCE_EXHAUSTED", details=details), {}))).generate_json("s", "p")
    until = dt.datetime.fromisoformat(usage(tmp_path)["backoff_until"])
    assert until == dt.datetime(2026, 10, 3, 7, 0, tzinfo=UTC)          # midnat i Californien (PDT, UTC−7)


def test_402_means_payment_required_and_a_long_pause(tmp_path, clock):
    with pytest.raises(ai.AIUnavailable) as e:
        make(tmp_path, clock, Fake((402, {"error": {"message": "betaling"}}, {}))).generate_json("s", "p")
    assert e.value.reason == "betaling"
    assert dt.datetime.fromisoformat(usage(tmp_path)["backoff_until"]) - clock.t >= dt.timedelta(hours=6)


@pytest.mark.parametrize("code,status", [(400, "FAILED_PRECONDITION"), (401, "UNAUTHENTICATED"), (403, "PERMISSION_DENIED"), (404, "NOT_FOUND")])
def test_a_rejected_key_or_request_is_reported_as_afvist(tmp_path, clock, code, status):
    with pytest.raises(ai.AIUnavailable) as e:
        make(tmp_path, clock, Fake((code, google_error(code, status, "nope"), {}))).generate_json("s", "p")
    assert e.value.reason == "afvist" and f"HTTP {code}" in e.value.detail


@pytest.mark.parametrize("code", [500, 503, 529])
def test_server_errors_pause_and_grow(tmp_path, clock, code):
    fake = Fake((code, google_error(code, "UNAVAILABLE"), {}))
    c = make(tmp_path, clock, fake)
    pauses = []
    for _ in range(3):
        with pytest.raises(ai.AIUnavailable) as e:
            c.generate_json("s", f"p{len(pauses)}")
        assert e.value.reason == "serverfejl"
        until = dt.datetime.fromisoformat(usage(tmp_path)["backoff_until"])
        pauses.append((until - clock.t).total_seconds())
        clock.t = until
    assert pauses == [120, 240, 480]


def test_a_timeout_or_dead_network_is_netvaerk(tmp_path, clock):
    for exc in (httpx.ConnectTimeout("t"), httpx.ConnectError("c")):
        with pytest.raises(ai.AIUnavailable) as e:
            make(tmp_path, clock, Fake((0, exc, {}))).generate_json("s", "p")
        assert e.value.reason == "netvaerk"
        clock.advance(hours=2)


def test_a_success_after_errors_clears_the_pause(tmp_path, clock):
    fake = Fake((503, google_error(503, "UNAVAILABLE"), {}), (200, gemini_ok({"a": 1}), {}))
    c = make(tmp_path, clock, fake)
    with pytest.raises(ai.AIUnavailable):
        c.generate_json("s", "p")
    clock.advance(minutes=5)
    assert c.generate_json("s", "p") == {"a": 1}
    u = usage(tmp_path)
    assert u["failures"] == 0 and u.get("backoff_until") is None


# ---------------------------------------------------------------- budget og minutgrænse
def test_the_daily_budget_stops_requests_before_google_does(tmp_path, clock):
    fake = Fake()
    c = make(tmp_path, clock, fake, daily_cap=2, rpm=0)
    c.generate_json("s", "1")
    c.generate_json("s", "2")
    with pytest.raises(ai.AIUnavailable) as e:
        c.generate_json("s", "3")
    assert e.value.reason == "dagsbudget" and len(fake.requests) == 2


def test_the_budget_resets_at_pacific_midnight_not_danish(tmp_path, clock):
    fake = Fake()
    c = make(tmp_path, clock, fake, daily_cap=1, rpm=0)
    c.generate_json("s", "1")
    clock.t = dt.datetime(2026, 10, 2, 22, 30, tzinfo=UTC)             # 00.30 dansk tid – stadig samme dag i Californien
    with pytest.raises(ai.AIUnavailable):
        c.generate_json("s", "2")
    clock.t = dt.datetime(2026, 10, 3, 7, 1, tzinfo=UTC)               # lige efter midnat i Californien
    c.generate_json("s", "3")
    assert len(fake.requests) == 2 and usage(tmp_path)["day"] == "2026-10-03"


def test_the_per_minute_limit_waits_a_little(tmp_path, clock):
    fake = Fake()
    c = make(tmp_path, clock, fake, rpm=2)
    c.generate_json("s", "1")
    clock.advance(seconds=40)
    c.generate_json("s", "2")
    c.generate_json("s", "3")                                           # venter ~20 s, til den første er over et minut gammel
    assert len(fake.requests) == 3 and clock.slept and 19 <= clock.slept[0] <= 22


def test_the_per_minute_limit_gives_up_rather_than_waiting_long(tmp_path, clock):
    fake = Fake()
    c = make(tmp_path, clock, fake, rpm=1, max_wait_seconds=5)
    c.generate_json("s", "1")
    with pytest.raises(ai.AIUnavailable) as e:
        c.generate_json("s", "2")
    assert e.value.reason == "minutgraense" and not clock.slept and len(fake.requests) == 1


# ---------------------------------------------------------------- opsætning
def test_a_missing_key_never_touches_the_network(tmp_path, clock):
    fake = Fake()
    c = ai.Client(ai.Settings(), tmp_path, transport=httpx.MockTransport(fake), clock=clock, env={})
    with pytest.raises(ai.AIUnavailable) as e:
        c.generate_json("s", "p")
    assert e.value.reason == "mangler_noegle" and not fake.requests


def test_a_key_with_stray_quotes_or_spaces_still_works(tmp_path, clock):
    fake = Fake()
    c = ai.Client(ai.Settings(), tmp_path, transport=httpx.MockTransport(fake), clock=clock, env={"GEMINI_API_KEY": f' "{KEY}" '})
    c.generate_json("s", "p")
    assert fake.requests[0].headers["x-goog-api-key"] == KEY


def test_a_key_with_invalid_characters_is_reported_not_crashed(tmp_path, clock):
    fake = Fake()
    c = ai.Client(ai.Settings(), tmp_path, transport=httpx.MockTransport(fake), clock=clock, env={"GEMINI_API_KEY": "nøgle med mellemrum"})
    with pytest.raises(ai.AIUnavailable) as e:
        c.generate_json("s", "p")
    assert e.value.reason == "mangler_noegle" and "ugyldige tegn" in e.value.detail and not fake.requests


def test_an_unknown_provider_is_reported(tmp_path, clock):
    c = ai.Client(ai.Settings(provider="nope"), tmp_path, env={"GEMINI_API_KEY": KEY})
    with pytest.raises(ai.AIUnavailable) as e:
        c.generate_json("s", "p")
    assert e.value.reason == "ukendt_udbyder"


def test_settings_default_to_gemini_and_read_the_ai_section():
    s = ai.settings_from({"ai": {"model": "gemini-x", "daily_cap": 40, "rpm": 7}})
    assert (s.provider, s.model, s.api_key_env, s.daily_cap, s.rpm) == ("gemini", "gemini-x", "GEMINI_API_KEY", 40, 7)


def test_an_old_claude_assistant_config_still_means_claude():
    s = ai.settings_from({"assistant": {"mode": "claude", "model": "claude-test", "api_key_env": "MIN_NØGLE", "max_tokens": 900}})
    assert (s.provider, s.model, s.api_key_env, s.max_output_tokens) == ("claude", "claude-test", "MIN_NØGLE", 900)


def test_mode_claude_wins_over_an_ai_section_for_gemini():
    cfg = {"assistant": {"mode": "claude"}, "ai": {"provider": "gemini", "model": "gemini-3.5-flash-lite", "daily_cap": 30}}
    s = ai.settings_from(cfg)
    assert (s.provider, s.model, s.api_key_env, s.daily_cap) == ("claude", "claude-sonnet-5-5", "ANTHROPIC_API_KEY", 30)


def test_state_files_are_only_readable_by_the_owner(tmp_path, clock):
    import os
    import stat
    make(tmp_path, clock, Fake()).generate_json("s", "p")
    for f in ("ai_cache.json", "ai_usage.json"):
        mode = stat.S_IMODE(os.stat(tmp_path / f).st_mode)
        if os.name == "posix":
            assert mode == 0o600, (f, oct(mode))


# ---------------------------------------------------------------- Claude over samme grænseflade
def test_claude_is_called_over_plain_https_with_its_own_headers(tmp_path, clock):
    fake = Fake((200, {"content": [{"type": "text", "text": '{"a": 3}'}], "stop_reason": "end_turn"}, {}))
    c = ai.Client(ai.Settings(provider="claude", model="claude-test", api_key_env="ANTHROPIC_API_KEY"), tmp_path,
                  transport=httpx.MockTransport(fake), clock=clock, env={"ANTHROPIC_API_KEY": KEY})
    assert c.generate_json("sys", "p") == {"a": 3}
    r = fake.requests[0]
    assert r.url.host == "api.anthropic.com" and r.url.path == "/v1/messages"
    assert r.headers["x-api-key"] == KEY and r.headers["anthropic-version"]
    assert json.loads(r.content)["system"] == "sys" and json.loads(r.content)["model"] == "claude-test"


def test_claude_429_uses_the_retry_after_header(tmp_path, clock):
    fake = Fake((429, {"type": "error", "error": {"type": "rate_limit_error"}}, {"retry-after": "90"}))
    c = ai.Client(ai.Settings(provider="claude", api_key_env="K"), tmp_path, transport=httpx.MockTransport(fake),
                  clock=clock, env={"K": KEY})
    with pytest.raises(ai.AIUnavailable) as e:
        c.generate_json("s", "p")
    assert e.value.reason == "kvote"
    assert dt.datetime.fromisoformat(usage(tmp_path)["backoff_until"]) == clock.t + dt.timedelta(seconds=90)
