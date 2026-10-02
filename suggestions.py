"""Kalenderforslag: hvad er foreslået, oprettet eller afvist – og selve skrivningen til Google Kalender.

Tilstanden ligger i `suggestions_state.json` ved siden af family.json, så den overlever genstart og opdatering.
Skrivning sker med en servicekonto, som familiekalenderen er delt med ("Foretag ændringer i begivenheder"):
ingen samtykkeskærm, ingen tokens der udløber, og intet login at forny.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote, unquote

import httpx

TZ_NAME = "Europe/Copenhagen"
KEY_RX = re.compile(r"^(sg|ev)_[0-9a-f]{12}$")


# ---------------------------------------------------------------- tilstand
class Store:
    """status pr. forslag: created | dismissed. Alt andet er "new"."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {"items": {}, "seen": {}}
        try:
            self.data = {"items": {}, "seen": {}, **json.loads(self.path.read_text("utf-8"))}
        except (OSError, ValueError):
            pass

    def reload(self) -> None:
        """Hentningen og serveren skriver til samme fil: læs altid frisk lige før en ændring."""
        try:
            self.data = {"items": {}, "seen": {}, **json.loads(self.path.read_text("utf-8"))}
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self.path)

    def get(self, key: str) -> dict | None:
        return self.data["items"].get(key)

    def set(self, key: str, status: str, **extra) -> None:
        self.reload()
        self.data["items"][key] = {"status": status, "at": dt.datetime.now().isoformat(timespec="seconds"), **extra}
        self.save()

    def clear(self, key: str) -> None:
        self.reload()
        self.data["items"].pop(key, None)
        self.save()

    def _created_by_source(self) -> dict:
        """Oprettet aftale pr. (kilde, dato): en lært regel kan senere give et forslag for en aktivitet, du allerede har oprettet manuelt."""
        out = {}
        for k, v in self.data["items"].items():
            if v.get("status") == "created" and v.get("source") and v.get("date"):
                out[(v["source"]["id"], v["date"])] = v
        return out

    def annotate(self, suggestions: list[dict], options: dict[str, list[dict]]) -> list[str]:
        """Sætter status og første gang set på forslag og muligheder. Returnerer id'er, der er nye siden sidst."""
        self.reload()
        now = dt.datetime.now().isoformat(timespec="seconds")
        new_ids: list[str] = []
        live = set()
        for s in suggestions:
            live.add(s["id"])
            if s["id"] not in self.data["seen"]:
                self.data["seen"][s["id"]] = now
                new_ids.append(s["id"])
            s["first_seen"] = self.data["seen"][s["id"]]
        for lst in options.values():
            for o in lst:
                live.add(o["id"])
        by_src = self._created_by_source()
        for items in (suggestions, *options.values()):
            for x in items:
                st = self.get(x["id"])
                x["status"] = st["status"] if st and st["status"] in ("created", "dismissed", "applied") else "new"
                if st and st.get("html_link"):
                    x["html_link"] = st["html_link"]
                if x["status"] == "new":
                    ids = [s_["id"] for s_ in x.get("sources", [])] + ([x["source"]["id"]] if x.get("source") else [])
                    hit = next((by_src[(i, x["start"])] for i in ids if (i, x["start"]) in by_src), None)
                    if hit:
                        x["status"] = "created"
                        x["html_link"] = hit.get("html_link")
        # glem ting, der ikke har været aktuelle i to måneder
        cutoff = (dt.datetime.now() - dt.timedelta(days=60)).isoformat(timespec="seconds")
        for k in [k for k, v in self.data["seen"].items() if k not in live and v < cutoff]:
            self.data["seen"].pop(k, None)
        self.save()
        return new_ids

    def public_state(self) -> dict:
        self.reload()
        items = self.data["items"]
        return {"created": {k: {"html_link": v.get("html_link"), "at": v.get("at")} for k, v in items.items() if v["status"] == "created"},
                "dismissed": [k for k, v in items.items() if v["status"] == "dismissed"],
                "applied": [k for k, v in items.items() if v["status"] == "applied"],
                "by_source": {f"{src['id']}|{d}": {"html_link": v.get("html_link")} for (src_id, d), v in self._created_by_source().items() for src in [v["source"]]}}

    def recent_created(self, days: int = 3) -> list[dict]:
        cutoff = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
        return [v for v in self.data["items"].values() if v["status"] == "created" and v.get("event") and v["at"] >= cutoff]


def calendar_id_from_ical(url: str) -> str | None:
    """https://calendar.google.com/calendar/ical/<id>/private-…/basic.ics → <id>"""
    m = re.search(r"/ical/([^/]+)/", url or "")
    return unquote(m[1]) if m else None


def write_target(cfg: dict) -> tuple[str | None, str, str | None]:
    """Den kalender, appen opretter aftaler i: (kalender-id, visningsnavn, problem).

    Rækkefølge: [calendar_write] calendar_id → den [[google]]-kalender med write = true → den eneste [[google]]-kalender.
    Er der flere Google-kalendere og ingen er valgt, gættes der ikke – ellers kunne aftaler havne i en forkert kalender."""
    c = cfg.get("calendar_write", {})
    cals = cfg.get("google", [])
    cal_id = lambda g: g.get("calendar_id") or calendar_id_from_ical(g.get("ical_url", ""))
    if c.get("calendar_id"):
        name = next((g.get("name", "Google") for g in cals if cal_id(g) == c["calendar_id"]), "Familiekalender")
        return c["calendar_id"], name, None
    marked = [g for g in cals if g.get("write")]
    if len(marked) > 1:
        return None, "", "flere [[google]]-kalendere har write = true – vælg én"
    chosen = marked[0] if marked else (cals[0] if len(cals) == 1 else None)
    if chosen is None:
        return None, "", ("flere Google-kalendere – sæt write = true på den, aftaler skal oprettes i" if cals else "ingen [[google]]-kalender i config")
    cid = cal_id(chosen)
    return cid, chosen.get("name", "Google"), None if cid else "kalender-id kan ikke udledes af iCal-adressen – sæt calendar_id"


def is_write_calendar(cfg: dict, g: dict) -> bool:
    """Er denne [[google]]-kalender den, appen skriver til? Bruges til at læse den via API og til selvtesten."""
    cid, _, _ = write_target(cfg)
    return bool(cid) and (g.get("calendar_id") or calendar_id_from_ical(g.get("ical_url", ""))) == cid


# ---------------------------------------------------------------- Google Kalender
class CalendarError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _hm(t: str) -> tuple[int, int]:
    h, m = t.split(":")
    return int(h), int(m)


def build_event(p: dict) -> dict:
    """Validerer det, brugeren har rettet, og bygger Googles event-krop. Rejser CalendarError(400) ved ugyldigt input."""
    title = str(p.get("title") or "").strip()
    if not title:
        raise CalendarError("Titel mangler", 400)
    try:
        d0 = dt.date.fromisoformat(str(p.get("date")))
        d1 = dt.date.fromisoformat(str(p.get("end_date") or p.get("date")))
    except ValueError:
        raise CalendarError("Ugyldig dato", 400)
    if d1 < d0:
        raise CalendarError("Slutdato ligger før startdato", 400)
    body: dict = {"summary": title[:200], "description": str(p.get("description") or "")[:4000]}
    if p.get("location"):
        body["location"] = str(p["location"])[:300]
    if p.get("all_day"):
        body["start"] = {"date": d0.isoformat()}
        body["end"] = {"date": (d1 + dt.timedelta(days=1)).isoformat()}                 # Googles slutdato for heldagsaftaler er eksklusiv
    else:
        try:
            s_h, s_m = _hm(str(p.get("start_time")))
            e_raw = p.get("end_time")
            e_h, e_m = _hm(str(e_raw)) if e_raw else ((s_h + 1) % 24, s_m)               # uden sluttid: én time
            start = dt.datetime.combine(d0, dt.time(s_h, s_m))
            end = dt.datetime.combine(d1, dt.time(e_h, e_m))
        except (ValueError, TypeError):
            raise CalendarError("Ugyldigt klokkeslæt – angiv mindst et starttidspunkt", 400)
        if end <= start:
            end = start + dt.timedelta(hours=1)
        body["start"] = {"dateTime": start.isoformat(), "timeZone": TZ_NAME}
        body["end"] = {"dateTime": end.isoformat(), "timeZone": TZ_NAME}
    return body


def end_inferred(p: dict) -> bool:
    """Har brugeren ikke angivet en sluttid? Så får Google en tænkt sluttid, men appen viser kun starttidspunktet."""
    return not p.get("all_day") and not p.get("end_time")


def event_id_for(key: str, version: int = 0) -> str:
    """Selvvalgt, deterministisk id (base32hex: 0-9, a-v). Samme forslag kan derfor aldrig oprettes to gange."""
    return "fp" + hashlib.sha1(f"{key}:{version}".encode()).hexdigest()


class GoogleCalendar:
    API = "https://www.googleapis.com/calendar/v3"
    SCOPE = "https://www.googleapis.com/auth/calendar.events"

    def __init__(self, cfg: dict):
        c = cfg.get("calendar_write", {})
        self.key_file = Path(c.get("service_account_file", "secrets/google_service_account.json"))
        self.api = str(c.get("api_base", self.API)).rstrip("/")
        self.reminders_ignored = bool(c.get("reminder_minutes"))       # forældet indstilling, se create(); selvtesten advarer
        cal, self.calendar_name, why = write_target(cfg)
        self.calendar_id = cal
        self.enabled = bool(c.get("enabled")) and bool(cal) and self.key_file.exists()
        self.problem = None if self.enabled else (
            "slået fra" if not c.get("enabled") else why or "mangler kalender-id" if not cal else f"nøglefilen {self.key_file} findes ikke")
        self._creds = None
        self._lock = asyncio.Lock()

    async def _token(self) -> str:
        async with self._lock:
            if self._creds is None:
                from google.oauth2 import service_account
                self._creds = service_account.Credentials.from_service_account_file(str(self.key_file), scopes=[self.SCOPE])
            if not self._creds.valid:
                from google.auth.transport.requests import Request
                try:
                    await asyncio.to_thread(self._creds.refresh, Request())
                except Exception as e:  # noqa: BLE001
                    raise CalendarError(f"Kunne ikke logge ind hos Google med servicekontoen: {e}", 502)
            return self._creds.token

    def _url(self, suffix: str = "") -> str:
        return f"{self.api}/calendars/{quote(self.calendar_id, safe='')}/events{suffix}"

    async def _call(self, method: str, url: str, **kw) -> httpx.Response:
        token = await self._token()
        async with httpx.AsyncClient(timeout=20) as http:
            try:
                return await http.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kw)
            except httpx.HTTPError as e:
                raise CalendarError(f"Kunne ikke nå Google Kalender: {e}", 502)

    @staticmethod
    def _explain(r: httpx.Response) -> str:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = r.text[:200]
        hint = {403: " – er kalenderen delt med servicekontoen med ret til at foretage ændringer?", 404: " – kalender-id'et passer ikke, eller kalenderen er ikke delt med servicekontoen"}.get(r.status_code, "")
        return f"Google svarede {r.status_code}: {msg}{hint}"

    async def create(self, key: str, payload: dict, version: int = 0) -> dict:
        """Opretter aftalen. Idempotent: findes id'et allerede, genbruges den eksisterende aftale."""
        body = build_event(payload)
        eid = event_id_for(key, version)
        body["id"] = eid
        body["extendedProperties"] = {"private": {"familieplan": key, **({"endInferred": "1"} if end_inferred(payload) else {})}}
        # Ingen "reminders": hos Google gælder de kun for den bruger, der opretter aftalen – her servicekontoen, ikke familien.
        # Hver forælder får i stedet sine egne standardunderretninger for familiekalenderen (se README).
        r = await self._call("POST", self._url(), json=body)
        if r.status_code == 409:                                    # id'et findes: tjek om aftalen stadig er der
            g = await self._call("GET", self._url("/" + eid))
            if g.status_code == 200 and g.json().get("status") != "cancelled":
                return {"id": eid, "html_link": g.json().get("htmlLink"), "already": True, "version": version}
            return await self.create(key, payload, version + 1)    # tidligere slettet: id'et er optaget, brug et nyt
        if r.status_code >= 300:
            raise CalendarError(self._explain(r), 403 if r.status_code == 403 else 502)
        return {"id": eid, "html_link": r.json().get("htmlLink"), "already": False, "version": version}

    async def patch(self, event_id: str, payload: dict) -> dict:
        """Flytter/retter en eksisterende aftale (dato, tid, sted). Titlen beholdes, medmindre en ny gives."""
        body = build_event(payload)
        r = await self._call("PATCH", self._url("/" + event_id), json=body)
        if r.status_code == 404:
            raise CalendarError("Aftalen findes ikke længere i Google Kalender – måske er den slettet dér.", 404)
        if r.status_code >= 300:
            raise CalendarError(self._explain(r), 403 if r.status_code == 403 else 502)
        return {"id": event_id, "html_link": r.json().get("htmlLink")}

    async def list_events(self, start: dt.datetime, end: dt.datetime) -> list[dict]:
        """Alle aftaler i tidsrummet, gentagelser foldet ud (singleEvents). Bladrer gennem alle sider."""
        params = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true", "orderBy": "startTime",
                  "maxResults": "2500", "timeZone": TZ_NAME}
        items: list[dict] = []
        for _ in range(20):                                     # værn mod en uendelig sideløkke
            r = await self._call("GET", self._url(), params=params)
            if r.status_code >= 300:
                raise CalendarError(self._explain(r), 403 if r.status_code == 403 else 502)
            j = r.json()
            items += j.get("items", [])
            if not j.get("nextPageToken"):
                return items
            params["pageToken"] = j["nextPageToken"]
        raise CalendarError("For mange sider fra Google Kalender", 502)

    async def delete(self, event_id: str) -> None:
        r = await self._call("DELETE", self._url("/" + event_id))
        if r.status_code not in (200, 204, 404, 410):
            raise CalendarError(self._explain(r), 403 if r.status_code == 403 else 502)


def app_event(payload: dict, event_id: str) -> dict:
    """Aftalen i appens eget format, så den kan vises med det samme (før iCal-adressen har fanget den)."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(TZ_NAME)
    body = build_event(payload)
    if "date" in body["start"]:
        d0 = dt.date.fromisoformat(body["start"]["date"])
        d1 = dt.date.fromisoformat(body["end"]["date"]) - dt.timedelta(days=1)
        start, end, all_day = dt.datetime.combine(d0, dt.time(0, 0), tz), dt.datetime.combine(d1, dt.time(23, 59), tz), True
    else:
        start = dt.datetime.fromisoformat(body["start"]["dateTime"]).replace(tzinfo=tz)
        end = dt.datetime.fromisoformat(body["end"]["dateTime"]).replace(tzinfo=tz)
        all_day = False
    return {"title": body["summary"], "start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes"),
            "allDay": all_day, "location": body.get("location"), "notes": body["description"] or None}


# ---------------------------------------------------------------- lærte regler
class Learned:
    """Ord og vendinger, appen har lært af aktiviteter, du selv har tilføjet. Ligger i learned_rules.json (kan også redigeres i hånden)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.rules: list[dict] = []
        self.reload()

    def reload(self) -> None:
        try:
            self.rules = list(json.loads(self.path.read_text("utf-8")).get("rules", []))
        except (OSError, ValueError):
            self.rules = []

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"rules": self.rules}, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self.path)

    def active(self) -> list[dict]:
        self.reload()
        return [r for r in self.rules if r.get("enabled", True)]

    def add(self, kind: str, text: str, anchor: str | None, example: dict, matches: int) -> dict:
        import activities as A
        self.reload()
        rid = "lr_" + hashlib.sha1(f"{kind}|{text}".encode()).hexdigest()[:10]
        for r in self.rules:
            if r["id"] == rid:
                r["enabled"] = True
                self.save()
                return {**r, "duplicate": True}
        rule = {"id": rid, "kind": kind, "text": text, "pattern": A.rule_pattern(kind, text), "title": A._cap((anchor or text).strip()),
                "enabled": True, "created": dt.datetime.now().isoformat(timespec="seconds"), "matches": matches,
                "example": {k: str(v)[:200] for k, v in (example or {}).items() if k in ("title", "sentence", "source")}}
        self.rules.append(rule)
        self.save()
        return rule

    def remove(self, rid: str) -> bool:
        self.reload()
        n = len(self.rules)
        self.rules = [r for r in self.rules if r["id"] != rid]
        self.save()
        return len(self.rules) != n

    def toggle(self, rid: str, enabled: bool) -> bool:
        self.reload()
        hit = False
        for r in self.rules:
            if r["id"] == rid:
                r["enabled"], hit = bool(enabled), True
        self.save()
        return hit
