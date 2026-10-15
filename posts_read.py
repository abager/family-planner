"""Læst/ulæst på Aula-opslag – appens egen, fælles status for husstanden.

Aula har ingen læst-markering pr. opslag. Den har kun én "set til"-dato for hele feedet (`profileLastSeenPostDate`), og
den flytter sig, hver gang opslagene hentes (konstateret med `fetch_family.py --dump-posts`). Derfor holder appen selv
styr på statussen i `posts_read.json` ved siden af family.json:

- `since`: tidspunktet, hvor funktionen blev slået til. Opslag fra før da regnes som læste (brugerens beslutning).
- `read`: id'er på opslag, nogen har markeret som læst i appen.

Et opslag er ulæst, når det er nyere end `since` og ikke er markeret. Markering sker kun manuelt (knap i feedet) og kan
ikke fortrydes (brugerens beslutning). Intet skrives til Aula. Statussen er fælles: markerer én, er det læst for alle.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path

log = logging.getLogger("familieplanner.posts_read")

FILE = "posts_read.json"
MAX_IDS = 1000                       # opslags-id'er vokser; de ældste smides ud først
ID_RE = re.compile(r"post:\d{1,15}")


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if isinstance(data, dict) and isinstance(data.get("read", []), list):
                return {"since": data.get("since"), "read": [i for i in data.get("read", []) if isinstance(i, str)]}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("Kunne ikke læse %s – starter forfra med læst-status på opslag: %s", self.path.name, e)
        return {"since": None, "read": []}

    def save(self, data: dict) -> None:
        ids = sorted(set(data["read"]), key=lambda i: int(i.split(":")[1]))[-MAX_IDS:]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"since": data["since"], "read": ids}, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self.path)

    def apply(self, posts: list[dict], now: dt.datetime | None = None) -> int:
        """Sæt `unread` på hvert opslag. Første gang (og kun når der faktisk er opslag) gemmes `since` = nu,
        så alt eksisterende regnes som læst. Returnerer antal ulæste."""
        data = self.load()
        if not data["since"]:
            if not posts:                     # fx Aula nede ved første kørsel: vent, ellers bliver alt senere "nyt"
                return 0
            data["since"] = (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
            self.save(data)
            log.info("Læst-status på opslag slået til: %d eksisterende opslag regnes som læste", len(posts))
        since, read = _parse(data["since"]), set(data["read"])
        n = 0
        for p in posts:
            ts = _parse(p.get("timestamp"))
            p["unread"] = bool(ts and since and ts > since and p.get("id") not in read)
            n += p["unread"]
        return n

    def mark(self, ids: list[str], known: set[str]) -> list[str]:
        """Markér opslag som læst. Kun id'er på opslag, vi selv har hentet. Returnerer dem, der blev markeret."""
        data = self.load()
        fresh = [i for i in dict.fromkeys(ids) if i in known and i not in data["read"]]
        if fresh:
            data["read"] += fresh
            if not data["since"]:             # markeret før første hentning efter opdateringen – usandsynligt, men sikkert
                data["since"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            self.save(data)
        return fresh


def _parse(s) -> dt.datetime | None:
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def patch_family(family: Path, ids: list[str]) -> None:
    """Opdatér family.json med det samme, så alle enheder ser opslaget som læst uden at vente på næste hentning."""
    try:
        data = json.loads(family.read_text("utf-8"))
        hit = False
        for p in data.get("posts", []):
            if p.get("id") in ids and p.get("unread"):
                p["unread"], hit = False, True
        if hit:
            tmp = family.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
            tmp.replace(family)
    except (OSError, ValueError) as e:
        log.warning("Kunne ikke opdatere family.json efter læst-markering af opslag: %s", e)
