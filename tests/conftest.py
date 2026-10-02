"""Fælles opsætning. Alle tests kører mod simulerede tjenester i en midlertidig mappe, så intet rører rigtige data."""
from __future__ import annotations

import datetime as dt
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from helpers import MockGoogle, NtfySink, StaticDir, make_ics  # noqa: E402

FAMILY_CAL = "family123@group.calendar.google.com"


def pytest_collection_modifyitems(items):
    """Browsertestene kører sidst: Playwrights session holder en event-løkke åben, som ellers forstyrrer asyncio.run i de øvrige tests."""
    items.sort(key=lambda it: "browser" in str(it.fspath).split("/"))


@pytest.fixture
def google(tmp_path):
    g = MockGoogle(tmp_path)
    yield g
    g.close()


@pytest.fixture
def ntfy():
    n = NtfySink()
    yield n
    n.close()


@pytest.fixture
def ical(tmp_path):
    """En iCal-adresse med et par aftaler de kommende dage. Skriv til .path for at ændre indholdet."""
    d = tmp_path / "ical"
    d.mkdir()
    today = dt.date.today()
    path = d / "family.ics"
    path.write_text(make_ics([("a1@test", "Svømning", today + dt.timedelta(days=2)), ("a2@test", "Bedsteforældre på besøg", today + dt.timedelta(days=3))]))
    s = StaticDir(d)
    s.path, s.good = path, path.read_text()
    s.dead = "http://127.0.0.1:1/dead.ics"
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path, monkeypatch, google, ntfy, ical):
    """En komplet konfiguration (bygget på config.example.toml) der peger på simulerede tjenester. Arbejdsmappen er tmp_path."""
    monkeypatch.chdir(tmp_path)
    c = tomllib.loads((ROOT / "config.example.toml").read_text("utf-8"))
    c["output"] = str(tmp_path / "web" / "family.json")
    (tmp_path / "web").mkdir()
    c["aula"]["token_file"] = str(tmp_path / "secrets" / "aula_tokens.json")
    c["google"] = [{"name": "Familiekalender", "ical_url": f"{ical.url}/family.ics", "calendar_id": FAMILY_CAL, "write": True, "default_people": ["family"]}]
    c["calendar_write"] = {"enabled": True, "service_account_file": str(google.sa_file), "api_base": google.api, "read_via_api": False}
    c["assistant"] = {"mode": "offline"}
    c["server"] = {"aula": True, "notify_ntfy": ntfy.url, "public_url": "https://familieplan.example.dk", "stale_alert_hours": 4}
    return c


@pytest.fixture
def fake_aula(monkeypatch):
    """Erstatter hentningen fra Aula. fake_aula.messages sættes af testen; fake_aula.prev husker, hvad der blev givet med som cache."""
    import fetch_family as F
    from helpers import aula_payload

    class Fake:
        messages: list = []
        prev = None
        down = False

        async def __call__(self, cfg, people, start, end, dump_path=None, previous_messages=None):
            self.prev = previous_messages
            if self.down:
                raise RuntimeError("Aula er nede")
            import copy
            return aula_payload(copy.deepcopy(self.messages))

    f = Fake()
    monkeypatch.setattr(F, "fetch_aula", f)
    return f
