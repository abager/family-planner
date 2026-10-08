"""Fremdrift pr. datatype i den igangværende hentning – til bjælkerne i appens statuslinje.

Ligger kun i hukommelsen (som problems.py). `start` sætter de dele, kørslen skal hente, til "venter"; hver del melder
`begin`, evt. `step(done, total)` og `finish(ok)`. `total` er kun kendt for nogle dele (fx beskeder: X af Y tråde) –
uden total viser appen en "i gang"-animation. Indeholder aldrig familiens data, kun tal og tilstande.
"""
from __future__ import annotations

import datetime as dt
import threading
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Copenhagen")

# Rækkefølgen er den, bjælkerne vises i
LABELS = {
    "google": "Google",
    "aula.kalender": "Aula-kalender",
    "aula.tasks": "Opgaver",
    "aula.weekplan": "Ugeplan",
    "aula.posts": "Opslag",
    "aula.messages": "Beskeder",
    "aula.albums": "Billeder",
    "kalenderforslag": "Kalenderforslag",
    "vejr": "Vejr",
    "overblik": "Overblik",
}

_lock = threading.Lock()
_parts: dict[str, dict] = {}
_cycle: dict = {"running": False, "started": None, "finished": None}


def _now() -> str:
    return dt.datetime.now(TZ).isoformat(timespec="seconds")


def start(keys: list[str]) -> None:
    """En ny hentning: de dele, den skal hente, venter. Andre dele vises ikke."""
    with _lock:
        _parts.clear()
        for k in sorted(set(keys), key=lambda k: list(LABELS).index(k) if k in LABELS else 99):
            _parts[k] = {"key": k, "label": LABELS.get(k, k), "state": "waiting", "done": None, "total": None}
        _cycle.update(running=True, started=_now(), finished=None)


def begin(key: str, total: int | None = None) -> None:
    with _lock:
        p = _parts.get(key)
        if p and p["state"] != "failed":
            p.update(state="running", done=0 if total else None, total=total or None)


def step(key: str, done: int, total: int | None = None) -> None:
    """Fremdrift i en del: done af total (total kan komme undervejs, fx når trådlisten er læst)."""
    with _lock:
        p = _parts.get(key)
        if not p or p["state"] in ("done", "failed"):
            return
        if total is not None:
            p["total"] = max(0, int(total)) or None
        p["state"] = "running"
        p["done"] = max(0, min(int(done), p["total"] or int(done)))


def finish(key: str, ok: bool = True) -> None:
    with _lock:
        p = _parts.get(key)
        if p:
            p["state"] = "done" if ok else "failed"
            if ok and p["total"]:
                p["done"] = p["total"]


def end() -> None:
    """Hentningen er slut: dele, der stadig venter eller henter, er ikke nået i mål (fx afbrudt eller timeout)."""
    with _lock:
        for p in _parts.values():
            if p["state"] in ("waiting", "running"):
                p["state"] = "failed"
        _cycle.update(running=False, finished=_now())


def snapshot() -> dict:
    with _lock:
        return {**_cycle, "parts": [dict(p) for p in _parts.values()]}


def reset() -> None:
    with _lock:
        _parts.clear()
        _cycle.update(running=False, started=None, finished=None)
