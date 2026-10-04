"""Drift: push-beskeder, tilsyn med forældede data og en vagthund, der genstarter baggrundsopgaver.

Alt her er skrevet, så det kan testes uden at vente på ure: tiden gives med som argument.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

TZ = ZoneInfo("Europe/Copenhagen")
log = logging.getLogger("familieplan.ops")
DAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]


def in_night(t: datetime, start: str, end: str) -> bool:
    cur = t.strftime("%H:%M")
    return (start <= cur or cur < end) if start > end else (start <= cur < end)


# ---------------------------------------------------------------- push
class Notifier:
    """ntfy via JSON-grænsefladen, så titler og tekst kan indeholde æ, ø og å.

    OBS: på den offentlige ntfy.sh kan enhver, der kender emnets navn, læse beskederne. Derfor indeholder standardbeskederne
    kun tal. Vil du have navne og detaljer med, så brug din egen ntfy-server."""

    def __init__(self, url: str, public_url: str = ""):
        self.url = (url or "").strip()
        self.public_url = public_url.rstrip("/")
        u = urlparse(self.url)
        self.base, self.topic = (f"{u.scheme}://{u.netloc}", u.path.strip("/")) if self.url else ("", "")

    @property
    def enabled(self) -> bool:
        return bool(self.base and self.topic)

    async def send(self, title: str, message: str, path: str = "/", tags: tuple[str, ...] = (), priority: int = 3) -> bool:
        if not self.enabled:
            return False
        body: dict = {"topic": self.topic, "title": title, "message": message, "priority": priority, "tags": list(tags)}
        if self.public_url:
            body["click"] = self.public_url + path
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.post(self.base, json=body)
                r.raise_for_status()
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("Kunne ikke sende besked via ntfy: %s", e)
            return False


def load_dotenv(*paths: Path) -> list[str]:
    """Læs KEY=værdi fra .env-filer, når appen kører direkte (fx på Windows) og ikke via Docker.
    Variabler, der allerede er sat (setx, Docker, systemd), vinder altid. Returnerer de navne, der blev sat."""
    import os
    done, seen = [], set()
    for p in paths:
        p = Path(p).resolve()
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        for line in p.read_text("utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip().removeprefix("export ").strip(), val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            if key and val and key not in os.environ:
                os.environ[key] = val
                done.append(key)
    return done


class StateFile:
    """Små ting, der skal overleve en genstart (fx at aftenpushet allerede er sendt i dag)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def get(self, key: str, default=None):
        return self._read().get(key, default)

    def set(self, **kw) -> None:
        data = self._read()
        data.update(kw)
        for k in [k for k, v in data.items() if v is None]:
            data.pop(k)
        tmp = self.path.with_suffix(".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self.path)


# ---------------------------------------------------------------- aftenpush
SECTION_WORDS = {"Husk": "at huske", "Skal gøres": "skal gøres", "Praktisk info": "praktisk info", "Kommende frister": "frister på vej"}


def evening_push_text(briefing: dict, details: str = "summary") -> tuple[str, str, bool]:
    """(titel, tekst, er der noget at fortælle). 'summary' = kun tal; 'full' = punkterne i klar tekst (kun til egen ntfy-server)."""
    day = datetime.fromisoformat(briefing["period"][0])
    title = f"I morgen, {DAYS[day.weekday()]}"
    secs = {s["titel"]: s.get("punkter", []) for s in briefing.get("afsnit", [])}
    core = sum(len(secs.get(k, [])) for k in ("Husk", "Skal gøres", "Praktisk info"))
    if details == "full":
        lines = [f"{s['titel']}: " + "; ".join(p["tekst"] for p in s.get("punkter", [])[:4]) for s in briefing.get("afsnit", []) if s.get("punkter")]
        return title, "\n".join(lines) or "Intet særligt i morgen.", core > 0
    parts = [f"{len(v)} {SECTION_WORDS[k]}" for k, v in secs.items() if v and k in SECTION_WORDS]
    return title, (" · ".join(parts) or "Intet særligt i morgen.") + ". Åbn Familieplan for detaljer.", core > 0


# ---------------------------------------------------------------- tilsyn
class Watchdog:
    """Slår alarm, når data bliver gamle – og melder, at de er tilbage. Tavst om natten, og højst én besked pr. døgn pr. problem."""

    def __init__(self, settings, state, notifier: Notifier, statefile: StateFile):
        self.s, self.st, self.n, self.sf = settings, state, notifier, statefile

    @staticmethod
    def _iso(v: str | None) -> datetime | None:
        return datetime.fromisoformat(v) if v else None

    async def _check(self, key: str, last_ok: datetime, now: datetime, what: str) -> str | None:
        limit = timedelta(hours=self.s.stale_hours)
        alerted = self._iso(self.sf.get(key))
        if now - last_ok > limit:
            if in_night(now, *self.s.night):
                return None
            if not alerted or now - alerted > timedelta(hours=24):
                since = last_ok.strftime("%H.%M") if last_ok.date() == now.date() else last_ok.strftime("%d/%m kl. %H.%M")
                await self.n.send("Familieplan", f"{what} er ikke blevet opdateret siden {since}. Tjek serveren.", path="/", tags=("warning",))
                self.sf.set(**{key: now.isoformat(timespec="seconds")})
                return "alarm"
        elif alerted:
            await self.n.send("Familieplan", f"{what} bliver igen opdateret.", path="/", tags=("white_check_mark",))
            self.sf.set(**{key: None})
            return "tilbage"
        return None

    async def check(self, now: datetime) -> list[str]:
        if not self.s.stale_hours:
            return []
        started = self._iso(self.st.started) or now
        out = []
        r = await self._check("stale_general", self._iso(self.st.last_success) or started, now, "Data")
        if r:
            out.append("data:" + r)
        if self.s.use_aula and self.st.aula != "login_required":            # udløbet login har sin egen besked
            r = await self._check("stale_aula", self._iso(self.st.aula_last_ok) or started, now, "Aula-data")
            if r:
                out.append("aula:" + r)
        return out


# ---------------------------------------------------------------- vagthund
async def supervise(name: str, factory, backoff: float = 10.0) -> None:
    """Kører en baggrundsopgave for evigt. Går den ned, logges det, og den startes igen – i stedet for at dø lydløst."""
    while True:
        try:
            await factory()
            log.error("Baggrundsopgaven %s stoppede uventet – starter den igen", name)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Baggrundsopgaven %s gik ned – starter den igen om %.0f sek.", name, backoff)
        await asyncio.sleep(backoff)
