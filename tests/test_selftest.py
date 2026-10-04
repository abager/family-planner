"""Selvtesten: finder den rigtige fejl og forklarer, hvad man skal gøre. Aula er simuleret (pakken kræver Python 3.14)."""
import types
from pathlib import Path

import pytest

import fetch_family as F
import selftest as T
import server


class FakeClient:
    api_url = "x"
    _request_with_version_retry = None

    async def get_profile(self):
        return types.SimpleNamespace(children=[types.SimpleNamespace(name="Hugo"), types.SimpleNamespace(name="Carla"), types.SimpleNamespace(name="Leo")])

    async def mark_thread_read(self, tid):
        return True


class Ctx:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def healthy(cfg, monkeypatch):
    monkeypatch.setenv("FAMILIEPLAN_PASSWORD", "hemmelig-kode")
    monkeypatch.setenv("FAMILIEPLAN_PRIVATE_CODE", "4711")
    monkeypatch.setattr(T, "aula_version", lambda: "1.12.0")
    monkeypatch.setattr(T, "python_ok", lambda: True)

    async def opener(cfg):
        return Ctx(FakeClient())

    async def threads(client, mx, pages):
        return [{"id": i} for i in range(42)]

    monkeypatch.setattr(F, "open_aula_client", opener)
    monkeypatch.setattr(F, "_all_threads", threads)
    cfg["aula"]["mitid_username"] = "andreas"
    return cfg


def run(cfg, notify=True):
    return T.asyncio.run(T.run_checks(cfg, server.Settings(cfg), notify))


def by(results, title):
    return next(r for r in results if title in r.title)


def test_a_healthy_setup_has_no_failures_and_the_test_leaves_no_trace(healthy, google, ntfy):
    res = run(healthy)
    assert T.exit_code(res) == 0, T.render(res)
    assert by(res, "Skrivning til Google").status == T.OK and "42 tråde" in by(res, "Beskeder").detail
    assert google.live() == {} and any(e["status"] == "cancelled" for e in google.events.values())    # prøveaftalen er oprettet og slettet
    assert ntfy.msgs[0]["title"] == "Familieplan selvtest"


def test_the_test_event_is_far_in_the_future_so_a_left_over_one_never_bothers_anyone(healthy, google):
    run(healthy, notify=False)
    ev = next(l[2] for l in google.log if l[0] == "insert")
    import datetime as dt
    assert dt.date.fromisoformat(ev["start"]["date"]) > dt.date.today() + dt.timedelta(days=365)


def test_no_notify_skips_the_push(healthy, ntfy):
    res = run(healthy, notify=False)
    assert by(res, "ntfy").status == T.SKIP and ntfy.msgs == []


def test_a_dead_calendar_address_is_explained(healthy, ical):
    healthy["google"][0]["ical_url"] = ical.dead
    r = by(run(healthy), "Google-kalender")
    assert r.status == T.FAIL and "internettet" in r.hint


def test_the_placeholder_address_from_the_example_config_is_caught(healthy):
    healthy["google"][0]["ical_url"] = "https://calendar.google.com/calendar/ical/.../basic.ics"
    assert by(run(healthy), "Google-kalender").detail == "adressen er ikke udfyldt"


def test_unshared_calendar_points_at_the_sharing_step(healthy, google):
    google.fail = {"family123": 403}
    r = by(run(healthy), "Skrivning til Google")
    assert r.status == T.FAIL and "Del kalenderen" in r.hint


def test_expired_aula_login_says_what_to_do(healthy, monkeypatch):
    async def expired(cfg):
        raise F.LoginRequired("udløbet")

    monkeypatch.setattr(F, "open_aula_client", expired)
    r = by(run(healthy), "Aula-login")
    assert r.status == T.FAIL and "/auth" in r.hint


def test_missing_aula_package_and_old_python_are_reported(healthy, monkeypatch):
    monkeypatch.setattr(T, "aula_version", lambda: None)
    monkeypatch.setattr(T, "python_ok", lambda: False)
    res = run(healthy)
    assert by(res, "Aula-pakken").status == T.FAIL and by(res, "Python").status == T.FAIL


def test_old_aula_package_without_paging_or_mark_read_gives_warnings(healthy, monkeypatch):
    class Old(FakeClient):
        mark_thread_read = None

    del Old.mark_thread_read
    Old.api_url = None
    monkeypatch.setattr(Old, "_request_with_version_retry", None, raising=False)
    monkeypatch.setattr(F, "open_aula_client", lambda cfg: _async(Ctx(type("O", (), {"get_profile": FakeClient.get_profile})())))
    res = run(healthy)
    assert by(res, "Beskeder").status == T.WARN and by(res, "Markér som læst").status == T.WARN


async def _async(v):
    return v


def test_missing_passwords_and_placeholder_username_are_reported(healthy, monkeypatch):
    monkeypatch.delenv("FAMILIEPLAN_PASSWORD")
    monkeypatch.delenv("FAMILIEPLAN_PRIVATE_CODE")
    healthy["aula"]["mitid_username"] = "DIT_MITID_BRUGERNAVN"
    res = run(healthy)
    assert by(res, "Adgangskode").status == T.FAIL and by(res, "private samtaler").status == T.WARN and by(res, "MitID").status == T.FAIL


def test_a_dead_ntfy_address_is_a_failure(healthy):
    healthy["server"]["notify_ntfy"] = "http://127.0.0.1:1/emne"
    assert by(run(healthy), "ntfy").status == T.FAIL


def test_loose_permissions_on_key_files_warn(healthy):
    tok = Path(healthy["aula"]["token_file"])
    tok.parent.mkdir(parents=True, exist_ok=True)
    tok.write_text("{}")
    tok.chmod(0o644)
    r = by(run(healthy), "Rettigheder")
    assert r.status == T.WARN and "chmod 600" in r.hint
    tok.chmod(0o600)
    Path(healthy["calendar_write"]["service_account_file"]).chmod(0o600)            # også servicekontoens nøgle tæller med
    assert by(run(healthy), "Rettigheder").status == T.OK


def test_private_text_lying_in_family_json_is_flagged(healthy):
    import json
    out = Path(healthy["output"])
    out.write_text(json.dumps({"generated": __import__("datetime").datetime.now(F.TZ).isoformat(), "messages": [{"id": "msg:1", "private": True, "text": "fortroligt"}]}))
    r = by(run(healthy), "Private samtaler i family.json")
    assert r.status == T.FAIL
    out.write_text(json.dumps({"generated": __import__("datetime").datetime.now(F.TZ).isoformat(), "messages": [{"id": "msg:1", "private": True, "redacted": True, "text": ""}]}))
    assert by(run(healthy), "Private samtaler i family.json").status == T.OK


def test_report_is_readable_and_the_exit_code_follows_the_failures(healthy):
    res = run(healthy, notify=False)
    text = T.render(res)
    assert "Familieplan – selvtest" in text and "✔" in text and "i orden" in text
    healthy["google"][0]["ical_url"] = "http://127.0.0.1:1/dead.ics"
    bad = run(healthy, notify=False)
    assert T.exit_code(bad) == 1 and "✖" in T.render(bad) and "→" in T.render(bad)


def test_the_command_line_entry_point_runs_without_the_server(healthy, capsys, monkeypatch):
    code = T.main(healthy, server.Settings(healthy), notify=False)
    assert code == 0 and "Familieplan – selvtest" in capsys.readouterr().out


def test_reading_via_the_api_is_checked_with_the_test_event(healthy, google):
    healthy["calendar_write"]["read_via_api"] = True
    res = run(healthy, notify=False)
    assert by(res, "Læsning via Google API").status == T.OK and any(l[0] == "list" for l in google.log)


def test_obsolete_reminder_setting_is_flagged(healthy):
    healthy["calendar_write"]["reminder_minutes"] = [1440]
    assert by(run(healthy, notify=False), "Påmindelser").status == T.WARN


def test_writing_to_a_calendar_the_app_does_not_show_is_flagged(healthy):
    healthy["calendar_write"]["calendar_id"] = "other@group.calendar.google.com"
    assert by(run(healthy, notify=False), "Kalender til nye aftaler").status == T.WARN


# ---------------------------------------------------------------- sprogmodel (simuleret Gemini)
def _ai_client(cfg, status, body, env=None, backoff=False):
    import json as _json

    import httpx

    import ai
    sent = []

    def handler(req):
        sent.append(req)
        return httpx.Response(status, content=_json.dumps(body))
    out = Path(cfg["output"]).parent
    if backoff:
        (out / "ai_usage.json").write_text(_json.dumps({"backoff_until": "2999-01-01T00:00:00+00:00"}))
    c = ai.Client(ai.settings_from(cfg), out, transport=httpx.MockTransport(handler),
                  env={"GEMINI_API_KEY": "AIzaTEST"} if env is None else env)
    return c, sent


OK_BODY = {"candidates": [{"content": {"parts": [{"text": '{"svar": "ok"}'}]}, "finishReason": "STOP"}]}


def test_selftest_makes_one_real_ai_request_and_reports_ok(cfg):
    cfg["assistant"] = {"mode": "ai"}
    c, sent = _ai_client(cfg, 200, OK_BODY)
    r = T.check_assistant(cfg, c)
    assert r.status == T.OK and "gemini" in r.title and "1/100" in r.detail and len(sent) == 1
    assert "Hugo" not in sent[0].content.decode() and "Carla" not in sent[0].content.decode()     # ingen familiedata


def test_selftest_tries_even_during_a_pause_so_a_fixed_key_can_be_checked(cfg):
    cfg["assistant"] = {"mode": "ai"}
    c, sent = _ai_client(cfg, 200, OK_BODY, backoff=True)
    assert T.check_assistant(cfg, c).status == T.OK and len(sent) == 1


@pytest.mark.parametrize("status,body,expect,hint", [
    (403, {"error": {"message": "API key not valid"}}, T.FAIL, "Generative Language API"),
    (402, {"error": {"message": "payment"}}, T.FAIL, "fakturering forblive slået FRA"),
    (429, {"error": {"message": "quota"}}, T.WARN, "Stillehavstid"),
])
def test_selftest_explains_ai_failures(cfg, status, body, expect, hint):
    cfg["assistant"] = {"mode": "ai"}
    c, _ = _ai_client(cfg, status, body)
    r = T.check_assistant(cfg, c)
    assert r.status == expect and hint in r.hint


def test_selftest_without_key_says_where_to_put_it(cfg):
    cfg["assistant"] = {"mode": "ai"}
    c, sent = _ai_client(cfg, 200, OK_BODY, env={})
    r = T.check_assistant(cfg, c)
    assert r.status == T.FAIL and "GEMINI_API_KEY=" in r.hint and "anførselstegn" in r.hint and not sent


def test_selftest_offline_mode_sends_nothing(cfg):
    assert T.check_assistant(cfg).status == T.OK


# ---------------------------------------------------------------- vejr (simuleret MET Norway)
def test_selftest_weather_without_home_explains_the_button(cfg):
    r = T.check_weather(cfg)
    assert r[0].status == T.WARN and "Sæt hjem for vejret" in r[0].hint and "localhost" in r[0].hint


def test_selftest_weather_asks_met_and_reports_days(cfg):
    import datetime as dt

    import httpx

    import weather
    from tests.test_weather import met
    weather.save_home(Path(cfg["output"]).parent, 55.68, 12.57, dt.datetime.now(weather.TZ))
    start = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    r = T.check_weather(cfg, httpx.MockTransport(lambda req: httpx.Response(200, json=met(start=start, hours=60))))
    assert [x.status for x in r] == [T.OK, T.OK] and "svarer" in r[1].detail and "dag" in r[1].detail


def test_selftest_weather_met_down_is_a_warning_not_an_error(cfg):
    import datetime as dt

    import httpx

    import weather
    weather.save_home(Path(cfg["output"]).parent, 55.68, 12.57, dt.datetime.now(weather.TZ))
    r = T.check_weather(cfg, httpx.MockTransport(lambda req: httpx.Response(503)))
    assert r[1].status == T.WARN and "503" in r[1].detail


def test_selftest_weather_can_be_turned_off(cfg):
    cfg["weather"] = {"enabled": False}
    assert T.check_weather(cfg)[0].status == T.SKIP
