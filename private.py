"""Private tråde: indholdet ligger adskilt fra family.json og udleveres kun mod en ekstra kode.

Før lå private samtaler (fx med skolen om et barn) i fuld tekst i den fil, alle indloggede enheder henter – de var kun
skjult på skærmen. Nu står der kun "Privat samtale" og et tidspunkt i family.json. Selve indholdet ligger i en fil sammen
med Aula-nøglerne (aldrig i den mappe, der serveres), og serveren udleverer det først, når koden er indtastet.

Beskyttelsen kræver serveren. Uden den (fx `python -m http.server`) kan intet beskyttes: sæt `[private] protect = false`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REDACTED_SUBJECT = "Privat samtale"


def store_path(cfg: dict) -> Path:
    """Samme mappe som Aula-nøglerne (secrets/) – aldrig under web/."""
    return Path(cfg.get("aula", {}).get("token_file", "secrets/aula_tokens.json")).parent / "private_messages.json"


def enabled(cfg: dict) -> bool:
    return bool(cfg.get("private", {}).get("protect", True))


def load(path: Path) -> dict[str, dict]:
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save(path: Path, store: dict[str, dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False), "utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)


def redact(m: dict) -> dict:
    """Det, der må stå i family.json om en privat tråd: at den findes, hvornår, og hvem det handler om."""
    return {"id": m["id"], "subject": REDACTED_SUBJECT, "from": "", "timestamp": m.get("timestamp"), "unread": bool(m.get("unread")),
            "text": "", "images": [], "thread": [], "participants": [], "people": m.get("people") or ["family"],
            "source": m.get("source", "aula"), "private": True, "redacted": True, "category": "samtale",
            "actions": [], "suggested_events": [], "bring": []}


def protect(messages: list[dict], path: Path) -> list[dict]:
    """Returnerer beskederne, som de må stå i family.json, og skriver de private til den beskyttede fil.

    Er en privat tråd allerede udtømt (fordi Aula ikke svarede, og vi genbruger forrige data), beholdes den gamle fulde udgave."""
    old = load(path)
    store: dict[str, dict] = {}
    public: list[dict] = []
    for m in messages:
        if not m.get("private"):
            public.append(m)
        elif m.get("redacted"):
            if m["id"] in old:
                store[m["id"]] = old[m["id"]]
            public.append(m)
        else:
            store[m["id"]] = m
            public.append(redact(m))
    save(path, store)
    return public


def hydrate(messages: list[dict] | None, path: Path) -> list[dict] | None:
    """Gendanner private tråde til genbrug i hentningen (så de ikke skal hentes igen fra Aula ved hver kørsel)."""
    if not messages:
        return messages
    store = load(path)
    return [store.get(m["id"], m) if m.get("redacted") else m for m in messages]


def media_names(store: dict[str, dict]) -> set[str]:
    """Filnavnene på billeder i private tråde – dem må /media ikke udlevere uden kode."""
    names: set[str] = set()
    for m in store.values():
        for img in [*m.get("images", []), *[i for x in m.get("thread", []) for i in x.get("images", [])]]:
            names.add(os.path.basename(str(img)))
    return names
