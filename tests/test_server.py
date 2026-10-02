"""Serveren: adgang, private tråde, kalenderhandlinger (opret, aflys, flyt). Kører i hukommelsen mod simuleret Google."""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import private as P
import server
import suggestions as S

H = {"X-Requested-With": "familieplan"}
PASSWORD = "hemmelig-kode"
CODE = "4711"


class Env:
    def __init__(self, cfg, settings):
        self.cfg, self.settings = cfg, settings
        self.app = server.create_app(cfg, settings, PASSWORD, b"s" * 32, no_auth=False)
        self.c = TestClient(self.app, base_url="http://testserver", follow_redirects=False)
        self.out = Path(cfg["output"]).parent
        self.c.post("/login", data={"password": PASSWORD})

    def post(self, url, body=None):
        return self.c.post(url, json=body or {}, headers=H)


@pytest.fixture
def env(cfg, monkeypatch):
    monkeypatch.setenv("FAMILIEPLAN_PRIVATE_CODE", CODE)
    (Path(cfg["output"]).parent / "family.json").write_text(json.dumps({"events": [], "messages": []}))
    return Env(cfg, server.Settings(cfg))


# ---------------------------------------------------------------- adgang
def test_everything_but_health_and_login_needs_a_session(cfg, monkeypatch):
    monkeypatch.setenv("FAMILIEPLAN_PRIVATE_CODE", CODE)
    app = server.create_app(cfg, server.Settings(cfg), PASSWORD, b"s" * 32, no_auth=False)
    c = TestClient(app, follow_redirects=False)
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/private").status_code == 401
    r = c.get("/", headers={"accept": "text/html"})
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert c.post("/login", data={"password": "forkert"}).status_code == 401


def test_a_session_cookie_is_httponly_and_state_changing_calls_need_the_csrf_header(env):
    assert env.c.get("/api/status").status_code == 200
    assert env.c.post("/api/refresh", json={}).status_code == 403                  # uden X-Requested-With
    assert env.post("/api/refresh").status_code == 200
    r = env.c.post("/login", data={"password": PASSWORD})
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()


def test_login_is_throttled_after_five_wrong_passwords(cfg, monkeypatch):
    monkeypatch.setenv("FAMILIEPLAN_PRIVATE_CODE", CODE)
    c = TestClient(server.create_app(cfg, server.Settings(cfg), PASSWORD, b"s" * 32, no_auth=False), follow_redirects=False)
    assert [c.post("/login", data={"password": "x"}).status_code for _ in range(5)] == [401] * 5
    assert c.post("/login", data={"password": PASSWORD}).status_code == 429       # selv den rigtige afvises i en periode


def test_security_headers_are_set(env):
    h = env.c.get("/api/status").headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and "default-src" in h["content-security-policy"]


@pytest.mark.parametrize("name", ["private_messages.json", "suggestions_state.json", "learned_rules.json", "server_state.json", "config.toml", "aula_tokens.json", "session.key", ".env",
                                  "ai_cache.json", "ai_usage.json"])
def test_files_with_personal_data_or_keys_are_never_served(env, name):
    (env.out / name).write_text("hemmeligt")
    assert env.c.get("/" + name).status_code == 404
    assert env.c.get("/media/../" + name).status_code in (404, 400)


def test_the_private_store_is_not_in_the_served_folder_even_with_default_paths(cfg):
    cfg.pop("aula", None)                                        # standardstien, når intet er sat
    assert P.store_path({}).parts[0] == "secrets"


# ---------------------------------------------------------------- private tråde
def seed_private(env, with_image=False):
    msgs = {"msg:2": {"id": "msg:2", "subject": "Samtale om Hugo", "text": "Utryghed i 6B", "private": True, "timestamp": "2026-09-30T09:00:00+02:00",
                      "images": ["media/hemmelig.jpg"] if with_image else [], "thread": []}}
    P.save(P.store_path(env.cfg), msgs)


def test_private_endpoints_without_a_configured_code_stay_locked(cfg, monkeypatch):
    monkeypatch.delenv("FAMILIEPLAN_PRIVATE_CODE", raising=False)
    e = Env(cfg, server.Settings(cfg))
    assert e.c.get("/api/private/status").json()["configured"] is False
    r = e.post("/api/private/unlock", {"code": ""})
    assert r.status_code == 400 and "FAMILIEPLAN_PRIVATE_CODE" in r.json()["error"]
    assert e.c.get("/api/private").status_code == 403


def test_private_content_needs_the_extra_code(env):
    seed_private(env)
    assert env.c.get("/api/private/status").json() == {"configured": True, "unlocked": False, "expires_in": 0, "protect": True}
    assert env.c.get("/api/private").status_code == 403                                 # logget ind er ikke nok
    bad = env.post("/api/private/unlock", {"code": "0000"})
    assert bad.status_code == 403 and bad.json()["error"] == "Forkert kode."             # 403, ikke 401: forveksles ikke med "log ind"
    assert env.c.get("/api/private").status_code == 403
    ok = env.post("/api/private/unlock", {"code": CODE})
    assert ok.status_code == 200 and ok.json()["unlocked"] is True and "fp_private" in ok.headers["set-cookie"] and "httponly" in ok.headers["set-cookie"].lower()
    got = env.c.get("/api/private")
    assert got.status_code == 200 and got.json()["messages"][0]["text"] == "Utryghed i 6B"
    assert env.c.get("/api/private/status").json()["unlocked"] is True
    env.post("/api/private/lock")
    assert env.c.get("/api/private").status_code == 403 and env.c.get("/api/private/status").json()["unlocked"] is False


def test_the_unlock_expires_by_itself(cfg, monkeypatch):
    monkeypatch.setenv("FAMILIEPLAN_PRIVATE_CODE", CODE)
    cfg["server"]["private_unlock_minutes"] = 0.02                                       # ≈ 1,2 sekund
    e = Env(cfg, server.Settings(cfg))
    seed_private(e)
    e.post("/api/private/unlock", {"code": CODE})
    assert e.c.get("/api/private").status_code == 200
    time.sleep(1.6)
    assert e.c.get("/api/private").status_code == 403


def test_the_private_code_is_throttled_and_a_forged_cookie_does_not_work(env):
    seed_private(env)
    assert [env.post("/api/private/unlock", {"code": "x"}).status_code for _ in range(5)] == [403] * 5
    assert env.post("/api/private/unlock", {"code": CODE}).status_code == 429
    env.c.cookies.set("fp_private", f"{int(time.time()) + 999}.deadbeef")
    assert env.c.get("/api/private").status_code == 403


def test_a_session_alone_cannot_open_private_images(env):
    seed_private(env, with_image=True)
    (env.out / "media" / "hemmelig.jpg").write_bytes(b"\xff\xd8secret")
    (env.out / "media" / "offentlig.jpg").write_bytes(b"\xff\xd8public")
    assert env.c.get("/media/hemmelig.jpg").status_code == 404
    assert env.c.get("/media/offentlig.jpg").status_code == 200                           # andre billeder er uberørte
    env.post("/api/private/unlock", {"code": CODE})
    assert env.c.get("/media/hemmelig.jpg").status_code == 200
    env.post("/api/private/lock")
    assert env.c.get("/media/hemmelig.jpg").status_code == 404


# ---------------------------------------------------------------- kalender: opret, aflys, flyt
EVENT = {"key": "sg_aaaaaaaaaaaa", "title": "Hugo: Forældremøde", "date": "2026-10-21", "end_date": "2026-10-21", "all_day": False, "start_time": "19:00", "end_time": "20:00", "location": "Skolen"}


def family_events(env):
    return json.loads((env.out / "family.json").read_text("utf-8"))["events"]


def test_create_cancel_removes_the_event_everywhere(env, google):
    assert env.post("/api/calendar/events", EVENT).json()["status"] == "created"
    (ev,) = google.live().values()
    assert len(family_events(env)) == 1
    r = env.post("/api/calendar/cancel", {"key": "sg_bbbbbbbbbbbb", "target_key": EVENT["key"]})
    assert r.status_code == 200 and r.json()["status"] == "applied"
    assert google.live() == {} and family_events(env) == []                                 # væk hos Google og i appen med det samme
    cal = env.c.get("/api/calendar").json()
    assert "sg_bbbbbbbbbbbb" in cal["applied"] and EVENT["key"] not in cal["created"]


def test_move_updates_google_and_the_app_and_keeps_the_title(env, google):
    env.post("/api/calendar/events", EVENT)
    r = env.post("/api/calendar/move", {"key": "sg_bbbbbbbbbbbb", "target_key": EVENT["key"], "date": "2026-11-04", "end_date": "2026-11-04",
                                         "all_day": False, "start_time": "19:30", "end_time": "20:30"})
    assert r.status_code == 200
    (ev,) = google.live().values()
    assert ev["summary"] == "Hugo: Forældremøde" and ev["start"]["dateTime"].startswith("2026-11-04T19:30") and ev["location"] == "Skolen"
    (shown,) = family_events(env)
    assert shown["start"].startswith("2026-11-04T19:30") and shown["title"] == "Hugo: Forældremøde"
    cal = env.c.get("/api/calendar").json()
    assert "sg_bbbbbbbbbbbb" in cal["applied"] and cal["created"][EVENT["key"]]["html_link"]
    # kan flyttes igen, og derefter aflyses
    assert env.post("/api/calendar/move", {"key": "sg_cccccccccccc", "target_key": EVENT["key"], "date": "2026-11-11", "all_day": True}).status_code == 200
    assert env.post("/api/calendar/cancel", {"key": "sg_dddddddddddd", "target_key": EVENT["key"]}).status_code == 200 and google.live() == {}


@pytest.mark.parametrize("url", ["/api/calendar/cancel", "/api/calendar/move"])
def test_changes_refuse_unknown_or_malformed_targets(env, url):
    assert env.post(url, {"key": "sg_bbbbbbbbbbbb", "target_key": "sg_999999999999"}).status_code == 404       # findes ikke (mere)
    assert env.post(url, {"key": "sg_bbbbbbbbbbbb", "target_key": "../../etc"}).status_code == 400
    assert env.post(url, {"key": "ikke-en-nøgle", "target_key": "sg_999999999999"}).status_code == 400
    assert env.post(url, {"key": "sg_bbbbbbbbbbbb"}).status_code == 400


def test_a_google_error_during_cancel_leaves_everything_as_it_was(env, google):
    env.post("/api/calendar/events", EVENT)
    google.fail = {"family123": 403}
    r = env.post("/api/calendar/cancel", {"key": "sg_bbbbbbbbbbbb", "target_key": EVENT["key"]})
    assert r.status_code == 403 and "delt" in r.json()["error"]
    google.fail = {}
    cal = env.c.get("/api/calendar").json()
    assert EVENT["key"] in cal["created"] and "sg_bbbbbbbbbbbb" not in cal["applied"] and len(google.live()) == 1 and len(family_events(env)) == 1


def test_changes_need_calendar_writing_to_be_set_up(cfg, monkeypatch):
    monkeypatch.setenv("FAMILIEPLAN_PRIVATE_CODE", CODE)
    cfg["calendar_write"]["enabled"] = False
    e = Env(cfg, server.Settings(cfg))
    S.Store(e.out / "suggestions_state.json").set(EVENT["key"], "created", event_id="fp1", event={"title": "x"}, version=1)
    assert e.post("/api/calendar/cancel", {"key": "sg_bbbbbbbbbbbb", "target_key": EVENT["key"]}).status_code == 400


# ---------------------------------------------------------------- status og læst-markering
def test_status_exposes_health_for_the_ui(env):
    j = env.c.get("/api/status").json()
    assert {"aula", "running", "runs", "last_success", "aula_last_ok", "internal_errors", "pending_reads"} <= set(j)


def test_mark_read_validates_ids(env):
    (env.out / "family.json").write_text(json.dumps({"events": [], "messages": [{"id": "msg:12", "unread": True}, {"id": "msg:abc-9", "unread": True}, {"id": "msg:7", "unread": False}]}))
    assert env.post("/api/messages/read", {"ids": ["msg:12", "msg:abc-9"]}).json() == {"enabled": True, "queued": 2}
    for bad in ({"ids": []}, {"ids": ["../x"]}, {"ids": ["msg:1"] * 51}, {"ids": "msg:1"}, {}):
        assert env.post("/api/messages/read", bad).status_code == 400


def test_calendar_state_names_the_target_calendar_and_its_default_people(env):
    cal = env.c.get("/api/calendar").json()
    assert cal["enabled"] and cal["calendar_name"] == "Familiekalender" and cal["default_people"] == ["family"]


def test_created_events_without_end_time_are_marked_in_the_app(env, google):
    env.post("/api/calendar/events", {**EVENT, "start_time": "14:00", "end_time": "", "all_day": False})
    (ev,) = family_events(env)
    assert ev["appCreated"] and ev["endInferred"]
    assert next(iter(google.live().values()))["extendedProperties"]["private"]["endInferred"] == "1"


# ---------------------------------------------------------------- sprogmodellens tilstand
def test_ai_status_is_shown_after_login_but_never_the_key(cfg, monkeypatch):
    monkeypatch.setenv("FAMILIEPLAN_PRIVATE_CODE", CODE)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaHEMMELIG")
    cfg["assistant"] = {"mode": "ai"}
    (Path(cfg["output"]).parent / "family.json").write_text(json.dumps({"events": [], "messages": []}))
    env = Env(cfg, server.Settings(cfg))
    st = env.c.get("/api/status").json()["ai"]
    assert st["provider"] == "gemini" and st["daily_cap"] == 100
    assert "AIzaHEMMELIG" not in env.c.get("/api/status").text
    assert "ai" not in env.c.get("/api/health").json()


def test_ai_status_is_empty_in_offline_mode(env):
    assert env.c.get("/api/status").json()["ai"] is None


# ---------------------------------------------------------------- hjemmets placering
def test_home_location_is_saved_rounded_and_shown_without_extra_decimals(env, monkeypatch):
    triggered = []
    monkeypatch.setattr(server.Runner, "trigger", lambda self, interactive=False: triggered.append(1))
    assert env.c.get("/api/home-location").json() == {"set": False}
    r = env.post("/api/home-location", {"lat": 55.676098, "lon": 12.568337})
    assert r.status_code == 200
    j = r.json()
    assert (j["set"], j["lat"], j["lon"]) == (True, 55.68, 12.57) and "openstreetmap.org" in j["map"]
    assert "55.676" not in (env.out / "home_location.json").read_text("utf-8")
    assert triggered                                                   # vejret hentes med det samme


@pytest.mark.parametrize("body", [{"lat": 48.85, "lon": 2.35}, {"lat": "x", "lon": 1}, {"lon": 12.5}, {}])
def test_a_bad_or_foreign_home_location_is_refused(env, body):
    r = env.post("/api/home-location", body)
    assert r.status_code == 400 and not (env.out / "home_location.json").exists()


def test_home_location_needs_login_and_the_file_is_never_served(cfg, env):
    env.post("/api/home-location", {"lat": 55.68, "lon": 12.57})
    anon = TestClient(env.app, base_url="http://testserver", follow_redirects=False)
    assert anon.get("/api/home-location").status_code in (302, 303, 401, 403)
    assert anon.post("/api/home-location", json={"lat": 55.7, "lon": 12.6}, headers=H).status_code in (302, 303, 401, 403)
    assert env.c.get("/home_location.json").status_code == 404
