"""Aktuelle fejl i integrationerne (AI, Aula, Google, vejr, ntfy) – til ⚠ ved appens titel.

Ligger kun i hukommelsen: en genstart rydder listen. En fejl fjernes igen, næste gang integrationen lykkes
(`clear`). Teksterne må aldrig indeholde familiens data – kun fejlbeskeden fra integrationen, og den renses for
nøgler, tokens og koordinater, før den gemmes.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Copenhagen")
MAX_DETAIL = 2000

# Område → overskrift i dialogen
AREAS = {"ai": "Familieassistent (AI)", "aula": "Aula", "google": "Google Kalender", "weather": "Vejr", "ntfy": "Beskeder (ntfy)"}

_SECRETS = [
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 ***"),
    (re.compile(r"(?i)\b(key|api_key|apikey|token|access_token|refresh_token|id_token|client_secret|password)"
                r"(\s*[=:]\s*)[^\s&,;\"']+"), r"\1\2***"),
    (re.compile(r"(?i)\b(lat|lon|latitude|longitude)(\s*[=:]\s*)-?\d+(\.\d+)?"), r"\1\2***"),
    (re.compile(r"AIza[0-9A-Za-z_-]{20,}"), "***"),                     # Google API-nøgle
    (re.compile(r"sk-ant-[0-9A-Za-z_-]{10,}"), "***"),                  # Anthropic API-nøgle
]

_lock = threading.Lock()
_items: dict[str, dict] = {}


def scrub(text: str) -> str:
    """Fjern nøgler, tokens og koordinater fra en fejlbesked."""
    text = str(text or "")
    for pat, repl in _SECRETS:
        text = pat.sub(repl, text)
    return text[:MAX_DETAIL]


def _now() -> str:
    return dt.datetime.now(TZ).isoformat(timespec="seconds")


def report(key: str, area: str, title: str, detail: str = "", hint: str = "", action: dict | None = None) -> None:
    """Registrér (eller opdatér) en aktuel fejl. `key` identificerer kilden, fx "ai.briefing.day".
    action: valgfrit link til at løse det, {"label": ..., "href": ...} (kun relative adresser i appen)."""
    now = _now()
    with _lock:
        prev = _items.get(key)
        _items[key] = {"key": key, "area": area if area in AREAS else "ai", "title": title, "detail": scrub(detail),
                       "hint": hint, "action": action if action and not str(action.get("href", "")).startswith(("http", "//")) else None,
                       "since": prev["since"] if prev else now, "last": now, "count": (prev["count"] + 1) if prev else 1}


def clear(key: str) -> None:
    """Integrationen lykkedes: fejlen er væk."""
    with _lock:
        _items.pop(key, None)


def clear_prefix(prefix: str) -> None:
    with _lock:
        for k in [k for k in _items if k.startswith(prefix)]:
            del _items[k]


def snapshot() -> list[dict]:
    """Alle aktuelle fejl, ældste først – til /api/status."""
    with _lock:
        items = [dict(v, area_title=AREAS[v["area"]]) for v in _items.values()]
    return sorted(items, key=lambda p: (list(AREAS).index(p["area"]), p["since"]))


def reset() -> None:
    """Til tests og genstart."""
    with _lock:
        _items.clear()
