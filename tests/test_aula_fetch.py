"""Optimeret Aula-hentning: AulaGate (loft, timeout, genforsøg), _once, dyb kontrol hver time, beskeder der
genbruges eller hentes igen – og at en del, der fejler, står under ⚠ ved titlen."""
from __future__ import annotations

import asyncio
import datetime as dt

import httpx
import pytest

import fetch_family as F
import problems

TZ = F.TZ
NOW = dt.datetime(2026, 10, 1, 14, 0, tzinfo=TZ)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(F.AulaGate, "backoff", 0.001)


def run(coro):
    return asyncio.run(coro)


def http_error(code, headers=None):
    req = httpx.Request("GET", "https://aula/api?token=HEMMELIG")
    return httpx.HTTPStatusError("fejl", request=req, response=httpx.Response(code, headers=headers or {}, request=req))


class Target:
    """Et falsk Aula-bibliotek: hvert kald svarer med det næste i køen (en undtagelse rejses)."""
    def __init__(self, *answers):
        self.answers, self.calls, self.active, self.peak = list(answers), 0, 0, 0
        self.widgets = None

    async def get_profile(self):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.01)
            a = self.answers.pop(0) if self.answers else "ok"
            if isinstance(a, BaseException):
                raise a
            return a
        finally:
            self.active -= 1

    def not_async(self):
        return "direkte"


# ---------------------------------------------------------------- AulaGate
@pytest.mark.parametrize("err", [httpx.ReadTimeout("t"), httpx.ConnectError("n"), http_error(503), http_error(429), TimeoutError()],
                         ids=["timeout", "netværk", "503", "429", "asyncio-timeout"])
def test_temporary_errors_are_retried(err):
    t = Target(err, "svar")
    g = F.AulaGate(t, retries=2)
    assert run(g.get_profile()) == "svar" and t.calls == 2 and g.stats["genforsøg"] == 1


@pytest.mark.parametrize("err", [F.LoginRequired("udløbet"), http_error(401), http_error(403), http_error(404), ValueError("bug")],
                         ids=["login", "401", "403", "404", "programfejl"])
def test_login_and_program_errors_are_never_retried(err):
    t = Target(err, "svar")
    with pytest.raises(type(err)):
        run(F.AulaGate(t, retries=2).get_profile())
    assert t.calls == 1


def test_it_gives_up_after_the_retries_and_logs_without_query_strings(caplog):
    t = Target(http_error(503), http_error(503), http_error(503), "for sent")
    g = F.AulaGate(t, retries=2)
    with caplog.at_level("WARNING", logger=F.log.name), pytest.raises(httpx.HTTPStatusError):
        run(g.get_profile())
    assert t.calls == 3 and g.stats["opgivet"] == 1
    assert "HEMMELIG" not in caplog.text


def test_a_response_with_a_retry_status_is_retried_and_finally_returned():
    t = Target(httpx.Response(503), httpx.Response(503), httpx.Response(503))
    r = run(F.AulaGate(t, retries=2).get_profile())
    assert r.status_code == 503 and t.calls == 3                       # kalderen afgør selv, hvad svaret betyder


def test_retry_after_is_respected_but_capped(monkeypatch):
    waits = []
    real = asyncio.sleep

    async def sleep(s):
        waits.append(s)
        await real(0)
    monkeypatch.setattr(F.asyncio, "sleep", sleep)
    t = Target(http_error(429, {"Retry-After": "7"}), http_error(429, {"Retry-After": "999"}), "ok")
    assert run(F.AulaGate(t, retries=2).get_profile()) == "ok"
    assert 7 in waits and 30 in waits


def test_the_cap_on_simultaneous_calls_holds():
    t = Target()
    g = F.AulaGate(t, max_concurrent=3)

    async def many():
        await asyncio.gather(*(g.get_profile() for _ in range(12)))
    run(many())
    assert t.calls == 12 and t.peak <= 3


def test_one_hanging_call_times_out_instead_of_stopping_the_fetch():
    class Slow(Target):
        async def get_profile(self):
            self.calls += 1
            await asyncio.sleep(5)
    t = Slow()
    with pytest.raises(TimeoutError):
        run(F.AulaGate(t, timeout=0.05, retries=1).get_profile())
    assert t.calls == 2


def test_plain_attributes_pass_straight_through():
    assert F.AulaGate(Target()).not_async() == "direkte"


def test_sub_clients_share_the_same_cap_and_counters():
    t = Target()
    t.widgets = Target()
    g = F.AulaGate(t)
    run(g.widgets.get_profile())
    assert g.stats["kald"] == 1


def test_once_runs_the_work_only_once_for_simultaneous_callers():
    g = F.AulaGate(Target())
    n = {"runs": 0}

    async def work():
        n["runs"] += 1
        await asyncio.sleep(0.01)
        return 42

    async def many():
        return await asyncio.gather(*(F._once(g, "k", work) for _ in range(5)))
    assert run(many()) == [42] * 5 and n["runs"] == 1


# ---------------------------------------------------------------- dyb kontrol hver time
@pytest.mark.parametrize("last,due", [
    (None, True), ("ikke en dato", True),
    ((NOW - dt.timedelta(minutes=59)).isoformat(), False),
    ((NOW - dt.timedelta(minutes=60)).isoformat(), True),
    ((NOW - dt.timedelta(hours=5)).isoformat(), True),
    ("2026-10-01T13:30:00", False),                                     # uden tidszone = dansk tid
])
def test_the_deep_check_is_due_every_hour(last, due):
    assert F._full_sweep_due(last, NOW, {}) is due


def test_the_interval_can_be_set_in_config():
    last = (NOW - dt.timedelta(minutes=90)).isoformat()
    assert F._full_sweep_due(last, NOW, {"deep_check_minutes": 120}) is False
    assert F._full_sweep_due(last, NOW, {"deep_check_minutes": 30}) is True


# ---------------------------------------------------------------- beskeder: genbrug og dyb kontrol
class People:
    by_id = {"carla": {"role": "child"}}

    def by_aula_name(self, name):
        return "carla" if name and "Carla" in name else None

    def in_text(self, text):
        return []


class Media:
    def __init__(self):
        self.used = set()

    async def images(self, attachments, html):
        return []


def thread(i, updated="2026-10-01T08:00:00"):
    return {"id": i, "subject": f"Tråd {i}", "lastUpdatedDate": updated, "participants": [], "regardingChildren": []}


class Aula:
    """Aulas besked-API: getThreads side for side og getMessagesForThread."""
    api_url = "https://aula/api"

    def __init__(self, threads, texts=None, page_size=10):
        self.threads, self.texts, self.page_size = threads, texts or {}, page_size
        self.pages, self.thread_calls = 0, []

    async def _request_with_version_retry(self, method, url):
        q = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))
        if q["method"] == "messaging.getThreads":
            self.pages += 1
            page = int(q["page"])
            body = {"data": {"threads": self.threads[page * self.page_size:(page + 1) * self.page_size]}}
        else:
            tid = int(q["threadId"])
            self.thread_calls.append(tid)
            body = {"data": {"moreMessagesExist": False, "messages": [
                {"id": f"{tid}-1", "messageType": "Message", "text": {"html": self.texts.get(tid, f"Tekst {tid}")},
                 "sender": {"fullName": "Lærer"}, "sendDateTime": "2026-10-01T08:00:00+02:00"}]}}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    async def get_message_threads(self, filter_on=None):
        return []


def fetch(aula, previous=None, full_sweep=False, **acfg):
    report = {}
    out = run(F._fetch_messages(aula, People(), {"messages_stop_after_unchanged": 5, **acfg}, Media(), previous,
                                full_sweep=full_sweep, report=report))
    return out, report


def test_unchanged_threads_are_reused_without_new_calls():
    threads = [thread(i) for i in range(1, 4)]
    first, _ = fetch(Aula(threads), full_sweep=True)
    aula = Aula(threads)
    again, rep = fetch(aula, previous=first)
    assert aula.thread_calls == [] and rep["cached"] == 3 and [m["text"] for m in again] == [m["text"] for m in first]


def test_a_thread_with_new_activity_is_fetched_again():
    first, _ = fetch(Aula([thread(1), thread(2)]), full_sweep=True)
    aula = Aula([thread(1, "2026-10-01T12:00:00"), thread(2)], texts={1: "Ny besked"})
    again, _ = fetch(aula, previous=first)
    assert aula.thread_calls == [1] and again[0]["text"] == "Ny besked"


def test_an_edit_that_does_not_change_the_list_is_caught_by_the_deep_check():
    threads = [thread(1), thread(2)]
    first, _ = fetch(Aula(threads), full_sweep=True)
    edited = Aula(threads, texts={2: "Rettet: mødet er kl. 10"})
    normal, _ = fetch(edited, previous=first)
    assert edited.thread_calls == [] and normal[1]["text"] == "Tekst 2"         # en almindelig hentning ser det ikke
    deep_aula = Aula(threads, texts={2: "Rettet: mødet er kl. 10"})
    deep, rep = fetch(deep_aula, previous=first, full_sweep=True)
    assert sorted(deep_aula.thread_calls) == [1, 2] and deep[1]["text"] == "Rettet: mødet er kl. 10"
    assert rep["complete"] is True and rep["cached"] == 0


def test_the_list_stops_after_unchanged_threads_and_carries_over_the_rest():
    threads = [thread(i) for i in range(1, 31)]
    first, _ = fetch(Aula(threads, page_size=10), full_sweep=True)
    aula = Aula(threads, page_size=10)
    again, rep = fetch(aula, previous=first)
    assert aula.pages == 1 and rep["early_stop"] is True and rep["complete"] is False
    assert len(again) == 30 and rep["carried"] == 20                     # resten overført fra forrige hentning


def test_the_deep_check_reads_the_whole_list():
    threads = [thread(i) for i in range(1, 31)]
    first, _ = fetch(Aula(threads, page_size=10), full_sweep=True)
    aula = Aula(threads, page_size=10)
    _, rep = fetch(aula, previous=first, full_sweep=True)
    assert aula.pages >= 3 and rep["complete"] is True and rep["early_stop"] is False


def test_a_thread_that_fails_keeps_its_previous_version():
    first, _ = fetch(Aula([thread(1)]), full_sweep=True)

    class Broken(Aula):
        async def _request_with_version_retry(self, method, url):
            if "getMessagesForThread" in url:
                raise http_error(500)
            return await super()._request_with_version_retry(method, url)
    again, rep = fetch(Broken([thread(1, "2026-10-01T12:00:00")]), previous=first)
    assert again[0]["text"] == first[0]["text"] and rep["failed"] == 1


# ---------------------------------------------------------------- en del, der fejler, står under ⚠
def test_a_part_that_fails_is_shown_and_cleared_when_it_works(monkeypatch, tmp_path):
    class Profile:
        children, institution_profile_ids = [], []

    class Client:
        async def get_profile(self):
            return Profile()

        async def get_calendar_events(self, ids, start, end):
            return []

    class Ctx:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *a):
            return False

    async def open_client(cfg):
        return Ctx()

    async def nothing(*a, **k):
        return []

    state = {"albums": RuntimeError("Aula svarede ikke")}

    async def albums(*a, **k):
        if state["albums"]:
            raise state["albums"]
        return []
    monkeypatch.setattr(F, "open_aula_client", open_client)
    for name in ("_lesson_notes", "_enrich_aula_events", "_fetch_mu_tasks", "_fetch_meebook", "_fetch_posts", "_fetch_messages"):
        monkeypatch.setattr(F, name, nothing)
    monkeypatch.setattr(F, "_fetch_gallery", albums)

    class P:
        def by_aula_name(self, n):
            return None

        def aula_self(self):
            return None
    cfg = {"aula": {}, "output": str(tmp_path / "family.json")}
    res = run(F.fetch_aula(cfg, P(), NOW, NOW))
    assert res["albums"] is None                                         # forrige data genbruges
    (p,) = problems.snapshot()
    assert p["key"] == "aula.part:albums" and p["title"] == "Aula: billeder kunne ikke hentes" and "Aula svarede ikke" in p["detail"]
    state["albums"] = None
    run(F.fetch_aula(cfg, P(), NOW, NOW))
    assert problems.snapshot() == []
