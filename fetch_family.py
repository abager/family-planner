#!/usr/bin/env python3
"""Henter data fra Aula og Google Kalender (iCal) og skriver family.json til familieplanneren.

Brug:
  python fetch_family.py                 # hent én gang (første gang: MitID-login via QR i terminalen)
  python fetch_family.py --watch 900     # hent hvert 15. minut
  python fetch_family.py --no-aula       # kun Google (godt til at teste opsætningen)
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import datetime as dt
import json
import logging
import random
import re
import sys
import time
import tomllib
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import icalendar
import recurring_ical_events

import activities
import homework
import private as private_mod
import problems
import messages as msg_analysis
import schedule
import suggestions as sugg_store

TZ = ZoneInfo("Europe/Copenhagen")
log = logging.getLogger("familieplanner")


# ---------------------------------------------------------------- helpers
def to_local(value) -> tuple[dt.datetime, bool]:
    """Returnér (tz-aware datetime, all_day)."""
    if isinstance(value, dt.datetime):
        return (value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)), False
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time(0, 0), TZ), True
    raise ValueError(f"Ukendt datotype: {value!r}")


def lesson_minutes(cfg: dict) -> int:
    """En lektions længde, når skolen ikke selv har angivet sluttid (standard 45 min)."""
    return int(cfg.get("aula", {}).get("lesson_minutes", schedule.LESSON_MINUTES))


def iso(d: dt.datetime) -> str:
    return d.astimezone(TZ).isoformat(timespec="minutes")


class People:
    def __init__(self, people: list[dict]):
        self.people = people
        self.by_id = {p["id"]: p for p in people}
        self._alias_re = [
            (p["id"], re.compile(r"(?<!\w)(" + "|".join(map(re.escape, p.get("aliases", [p["name"]]))) + r")(?!\w)", re.I))
            for p in people
        ]

    def in_text(self, text: str) -> list[str]:
        return [pid for pid, rx in self._alias_re if rx.search(text or "")]

    def by_aula_name(self, name: str) -> str | None:
        parts = (name or "").split()
        if not parts:
            return None
        first = parts[0].lower()
        for p in self.people:
            aula_parts = p.get("aula_name", "").split()
            if aula_parts and aula_parts[0].lower() == first:
                return p["id"]
        return None

    def aula_self(self) -> str | None:
        return next((p["id"] for p in self.people if p.get("aula_self")), None)

    def public(self) -> list[dict]:
        keys = ("id", "name", "role", "color", "note", "icon", "aliases")      # aliases: så appen kan vise, hvem en ny aftale tildeles
        return [{k: p[k] for k in keys if k in p} for p in self.people]


# ---------------------------------------------------------------- Google (iCal)
def _api_time(v: dict) -> tuple[dt.datetime, bool]:
    if v.get("dateTime"):
        d = dt.datetime.fromisoformat(v["dateTime"])
        if d.tzinfo is None and v.get("timeZone"):
            d = d.replace(tzinfo=ZoneInfo(v["timeZone"]))
        return to_local(d)
    return to_local(dt.date.fromisoformat(v["date"]))


def api_event(item: dict, name: str, cal_cfg: dict, people: People) -> dict | None:
    """Én aftale fra Google Calendar API i appens format. Samme id-form som fra iCal (g:<iCalUID>:<start>), så alt andet virker uændret."""
    if item.get("status") == "cancelled" or "start" not in item:
        return None
    s, all_day = _api_time(item["start"])
    e = _api_time(item["end"])[0] if item.get("end") else s + (dt.timedelta(days=1) if all_day else dt.timedelta(0))
    priv = (item.get("extendedProperties") or {}).get("private") or {}
    title = item.get("summary") or "(uden titel)"
    ev = {
        "id": f"g:{item.get('iCalUID') or item['id']}:{s.isoformat()}",
        "title": title,
        "start": iso(s),
        "end": iso(e - dt.timedelta(minutes=1) if all_day else e),
        "allDay": all_day,
        "people": people.in_text(title) or cal_cfg.get("default_people", ["family"]),
        "source": "google",
        "calendar": name,
        "location": item.get("location") or None,
        "notes": (item.get("description") or "").strip()[:500] or None,
    }
    if priv.get("familieplan"):
        ev["appCreated"] = True                       # oprettet af appen: kan flyttes og aflyses herfra
    if (not all_day and e <= s) or priv.get("endInferred") == "1":
        ev["endInferred"] = True
        if e <= s:
            ev["end"] = iso(s + dt.timedelta(hours=1))   # kun så aftalen har en plads på dagen; vises som "kl. 14.00"
    return ev


async def fetch_google_api(cfg: dict, cal_cfg: dict, people: People, start: dt.datetime, end: dt.datetime) -> list[dict]:
    g = sugg_store.GoogleCalendar(cfg)
    items = await g.list_events(start, end)
    name = cal_cfg.get("name", "Google")
    return [ev for it in items if (ev := api_event(it, name, cal_cfg, people))]


def _reads_via_api(cfg: dict, cal_cfg: dict) -> bool:
    cw = cfg.get("calendar_write", {})
    return bool(cw.get("enabled")) and cw.get("read_via_api", True) and sugg_store.is_write_calendar(cfg, cal_cfg) and sugg_store.GoogleCalendar(cfg).enabled


async def fetch_google(cfg: dict, people: People, start: dt.datetime, end: dt.datetime, failed: list[str] | None = None) -> list[dict]:
    """failed: får navnene på de kalendere, der ikke kunne hentes, så kaldet kan beholde deres seneste aftaler.

    Skrivekalenderen læses via Googles API, når servicekontoen er sat op (ændringer ses med det samme). Fejler det,
    bruges iCal-adressen, hvis den findes."""
    events: list[dict] = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
        for cal_cfg in cfg.get("google", []):
            name = cal_cfg.get("name", "Google")
            if _reads_via_api(cfg, cal_cfg):
                try:
                    got = await fetch_google_api(cfg, cal_cfg, people, start, end)
                    events += got
                    log.info("Google «%s» via API: %d aftaler", name, len(got))
                    problems.clear(f"google.api:{name}")
                    problems.clear(f"google.read:{name}")
                    continue
                except Exception as e:  # noqa: BLE001
                    log.warning("Kunne ikke læse «%s» via Google API: %s%s", name, e, " – prøver iCal" if cal_cfg.get("ical_url") else "")
                    problems.report(f"google.api:{name}", "google", f"Kalenderen «{name}» kunne ikke læses via Google API",
                                    detail=f"{type(e).__name__}: {e}",
                                    hint=("Appen bruger iCal-adressen imens, så ændringer kan være op til et par timer forsinkede. "
                                          if cal_cfg.get("ical_url") else "")
                                    + "Tjek servicekontoen (secrets/google_service_account.json), og at kalenderen er delt med den.")
                    if not cal_cfg.get("ical_url"):
                        if failed is not None:
                            failed.append(name)
                        problems.report(f"google.read:{name}", "google", f"Kalenderen «{name}» kunne ikke hentes",
                                        detail=f"{type(e).__name__}: {e}", hint="Appen viser de seneste kendte aftaler fra kalenderen.")
                        continue
            try:
                resp = await http.get(cal_cfg["ical_url"])
                resp.raise_for_status()
                cal = icalendar.Calendar.from_ical(resp.content)
            except Exception as e:  # noqa: BLE001
                log.warning("Kunne ikke hente Google-kalender %s: %s", name, e)
                if failed is not None:
                    failed.append(name)
                problems.report(f"google.read:{name}", "google", f"Kalenderen «{name}» kunne ikke hentes",
                                detail=f"{type(e).__name__}: {e}",
                                hint="Appen viser de seneste kendte aftaler fra kalenderen. Tjek iCal-adressen i config.toml og internetforbindelsen.")
                continue
            problems.clear(f"google.read:{name}")
            for comp in recurring_ical_events.of(cal).between(start, end):
                if str(comp.get("STATUS", "")).upper() == "CANCELLED":
                    continue
                s, all_day = to_local(comp.decoded("DTSTART"))
                e_raw = comp.decoded("DTEND") if comp.get("DTEND") else None
                dur = comp.decoded("DURATION") if comp.get("DURATION") else None
                e = to_local(e_raw)[0] if e_raw else s + dur if dur else s + (dt.timedelta(days=1) if all_day else dt.timedelta(0))
                inferred = not all_day and e <= s              # ingen sluttid i kalenderen: vis kun starttidspunktet
                if inferred:
                    e = s + dt.timedelta(hours=1)              # kun så aftalen har en plads på dagen
                title = str(comp.get("SUMMARY", "(uden titel)"))
                who = people.in_text(title) or cal_cfg.get("default_people", ["family"])
                uid = str(comp.get("UID", title))
                events.append({**({"endInferred": True} if inferred else {}),
                    "id": f"g:{uid}:{s.isoformat()}",
                    "title": title,
                    "start": iso(s),
                    "end": iso(e - dt.timedelta(minutes=1) if all_day else e),
                    "allDay": all_day,
                    "people": who,
                    "source": "google",
                    "calendar": name,
                    "location": str(comp.get("LOCATION", "")) or None,
                    "notes": str(comp.get("DESCRIPTION", "")).strip()[:500] or None,
                })
    log.info("Google: %d aftaler", len(events))
    return events


# ---------------------------------------------------------------- Aula
# Skema som tekst: "08.00-08.45 Dansk", "1. lektion 8:00 – 8:45 Matematik (JS)", "kl. 10.15 - 11.00: Idræt"
_SCHEDULE_LINE = re.compile(
    r"^\s*(?:\d{1,2}\.\s*(?:lektion|modul|time)\s*:?\s*)?(?:kl\.?\s*)?(\d{1,2})[.:](\d{2})\s*[-–]\s*(\d{1,2})[.:](\d{2})\s*:?\s*(.+?)\s*$",
    re.I)


def parse_schedule_text(text: str) -> list[dict]:
    lessons = []
    for line in (text or "").splitlines():
        m = _SCHEDULE_LINE.match(line.replace("\u00a0", " "))
        if m and m[5].strip(" -–:"):
            lessons.append({"start": f"{int(m[1]):02d}:{m[2]}", "end": f"{int(m[3]):02d}:{m[4]}",
                            "title": m[5].strip(" -–:"), "teacher": None, "substitute": None, "location": None})
    return lessons if len(lessons) >= 2 else []   # én linje med et klokkeslæt er ikke et skema


def _plain_keep_lines(html: str) -> str:
    """Som _plain, men bevarer linjeskift mellem afsnit/linjer, så skemalinjer kan genkendes."""
    import html as _h
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", html or "")
    t = _h.unescape(re.sub(r"<[^>]+>", " ", t))
    return "\n".join(re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in t.splitlines() if ln.strip())


async def _enrich_aula_events(client, events: list[dict], acfg: dict) -> None:
    """Hent beskrivelsen for Aula-aftaler de næste dage og find skema-tekst i den. Hentes samtidigt (loftet sidder i AulaGate)."""
    today = dt.date.today()
    horizon = today + dt.timedelta(days=acfg.get("event_details_days", 7))
    todo = [e for e in events if e.get("_aula_id")
            and today - dt.timedelta(days=1) <= dt.date.fromisoformat(e["start"][:10]) <= horizon]
    todo = todo[: acfg.get("event_details_max", 60)]

    async def one(e: dict) -> str:
        try:
            detail = await client.get_calendar_event(e["_aula_id"])
        except Exception as ex:  # noqa: BLE001
            log.debug("Ingen detaljer for %s: %s", e["title"], _describe(ex))
            return "fejl"
        if not detail:
            return "tom"
        desc = detail.get("description")
        html = desc.get("html") if isinstance(desc, dict) else desc
        text = _plain_keep_lines(html) if isinstance(html, str) else ""
        if text:
            e["notes"] = text[:3000]
        lessons = parse_schedule_text(text) or parse_schedule_text(e["title"].replace(";", "\n"))
        if lessons:
            e["lessons"], e["schedule"] = lessons, True
            return "skema"
        return "ok"

    results = await asyncio.gather(*(one(e) for e in todo))
    if todo:
        failed = results.count("fejl")
        log.log(logging.WARNING if failed else logging.INFO, "Aula: detaljer for %d aftaler, skema fundet i tekst for %d%s",
                len(todo), results.count("skema"), f", {failed} kunne ikke hentes" if failed else "")


async def _lesson_notes(client, events: list[dict], acfg: dict) -> list[dict]:
    """Hent noten på de lektioner, Aula markerer med hasRelevantNote (samtidigt). Returnerer rå detaljer til --dump-aula."""
    today = dt.date.today()
    horizon = today + dt.timedelta(days=acfg.get("lesson_notes_days", 3))
    todo = [(e, l) for e in events if e.get("lessons") and today <= dt.date.fromisoformat(e["start"][:10]) <= horizon
            for l in e["lessons"] if l.get("hasNote") and l.get("id")]
    picked = todo[: acfg.get("lesson_notes_max", 15)]

    async def one(e: dict, l: dict) -> dict | None:
        try:
            detail = await client.get_calendar_event(l["id"])
        except Exception as ex:  # noqa: BLE001
            log.debug("Ingen note for %s: %s", l.get("title"), _describe(ex))
            return None
        text = _find_note_text(detail)
        if text:
            l["note"] = text[:600]
        return {"id": l["id"], "title": l["title"], "date": e["start"][:10], "detail": detail}

    raw_details = [d for d in await asyncio.gather(*(one(e, l) for e, l in picked)) if d]
    if todo:
        failed = len(picked) - len(raw_details)
        log.log(logging.WARNING if failed else logging.INFO, "Aula: %d lektioner med note, %d hentet%s",
                len(todo), sum(1 for _, l in todo if l.get("note")), f", {failed} kunne ikke hentes" if failed else "")
    for e in events:
        for l in e.get("lessons") or []:
            l.pop("id", None)
            l.pop("hasNote", None) if not l.get("note") else None
    return raw_details


def _find_note_text(obj, depth: int = 0) -> str:
    """Notens præcise placering kender vi ikke – find tekstfelter, hvis navn indeholder 'note'."""
    if depth > 5 or obj is None:
        return ""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "note" in k.lower() and "has" not in k.lower():
                if isinstance(v, str) and v.strip():
                    return _plain_keep_lines(v)
                if isinstance(v, dict):
                    t = v.get("html") or v.get("text") or v.get("content")
                    if isinstance(t, str) and t.strip():
                        return _plain_keep_lines(t)
        for v in obj.values():
            t = _find_note_text(v, depth + 1)
            if t:
                return t
    elif isinstance(obj, list):
        for v in obj:
            t = _find_note_text(v, depth + 1)
            if t:
                return t
    return ""


def _dump_aula(path: Path, raw_events: list, events: list[dict], lesson_details: list[dict] | None = None) -> None:
    """Gem rå Aula-kalenderdata til fejlsøgning (--dump-aula)."""
    today = dt.date.today()
    near = [ev for ev in raw_events if abs((ev.start_datetime.astimezone(TZ).date() - today).days) <= 3]
    data = {"raw_events": [ev._raw for ev in near][:200],
            "processed_events": [e for e in events if abs((dt.date.fromisoformat(e["start"][:10]) - today).days) <= 3],
            "lesson_details": lesson_details or []}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), "utf-8")
    log.info("Skrev rå Aula-kalenderdata til %s", path)


def _qr_printer(qr1, qr2):
    print("\nScan QR-koden med MitID-appen (koden skifter mellem to billeder):\n")
    qr1.print_ascii(invert=True)
    print("\n— eller —\n")
    qr2.print_ascii(invert=True)


class LoginRequired(Exception):
    """Aula kræver nyt MitID-login, og kørslen er ubemandet (ingen kan scanne en QR-kode)."""


class AuthHooks:
    """Hvordan et MitID-login vises for brugeren. Standard er terminalen; serveren (server.py) erstatter dem med en webside.

    interactive = False: en ubemandet kørsel afbryder rent i stedet for at vente på en QR-kode, der aldrig bliver scannet."""

    interactive = True

    def on_login_required(self) -> None:
        if not self.interactive:
            raise LoginRequired("Aula-login er udløbet")
        print("MitID-login kræves – godkend i MitID-appen.")

    def on_qr_codes(self, qr1, qr2) -> None:
        _qr_printer(qr1, qr2)

    def on_qr_done(self) -> None:
        pass

    def on_otp_code(self, code: str) -> None:
        print(f"Bekræft koden i MitID-appen: {code}")


auth_hooks = AuthHooks()


# ---------------------------------------------------------------- Aula: kald med loft, timeout og genforsøg
_RETRY_STATUS = {429, 500, 502, 503, 504}
_URL_QUERY = re.compile(r"\?[^\s'\"]*")


def _describe(e: BaseException) -> str:
    """Kort fejltekst til loggen – uden query-strenge fra URL'er (de kan indeholde id'er og tokens)."""
    text = _URL_QUERY.sub("?…", str(e)) or "-"
    return f"{e.__class__.__name__}: {text[:200]}"


def _transient(e: BaseException) -> str | None:
    """Slags midlertidig fejl, der er værd at prøve igen. None = prøv ikke igen (fx login udløbet eller en programfejl)."""
    if isinstance(e, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(e, httpx.HTTPStatusError):
        return "http" if e.response is not None and e.response.status_code in _RETRY_STATUS else None
    if isinstance(e, httpx.TransportError):
        return "netværk"
    return None


def _retry_after(obj) -> float | None:
    """Aulas Retry-After (sekunder) fra et svar eller en HTTP-fejl – højst 30 s."""
    resp = getattr(obj, "response", obj)
    try:
        value = resp.headers.get("Retry-After")
        return min(30.0, max(0.0, float(value))) if value else None
    except (AttributeError, TypeError, ValueError):
        return None


class AulaGate:
    """Alle kald til Aula i én hentning går herigennem.

    - ét fælles loft over samtidige kald (max_concurrent), så de dele, der hentes samtidigt, tilsammen er skånsomme mod Aula
    - timeout pr. kald, så ét hængende kald ikke stopper hele hentningen
    - få genforsøg med stigende pause ved midlertidige fejl (timeout, netværk, 429/5xx) – aldrig ved login- eller programfejl
    - tæller kald, genforsøg og fejl til loggens opsummering
    Biblioteket kalder sine egne metoder direkte (ikke gennem porten), så et kald tæller kun én gang og kan ikke låse sig selv fast.
    """

    backoff = 1.0                 # sekunder før første genforsøg; ganges med 3 for hvert nyt forsøg
    _SUBCLIENTS = ("widgets",)    # under-klienter, hvis kald også skal gennem porten

    def __init__(self, target, max_concurrent: int = 3, timeout: float = 30.0, retries: int = 2, *, _shared: dict | None = None):
        self._target = target
        if _shared is None:
            _shared = {"sem": asyncio.Semaphore(max(1, int(max_concurrent))), "stats": defaultdict(int), "memo": {},
                       "timeout": float(timeout), "retries": max(0, int(retries))}
        self._shared = _shared
        self.stats: defaultdict[str, int] = _shared["stats"]
        self.memo: dict = _shared["memo"]

    def __getattr__(self, name: str):
        if name.startswith("__") or name in ("_target", "_shared"):
            raise AttributeError(name)
        attr = getattr(self._target, name)
        if name in self._SUBCLIENTS and attr is not None:
            return AulaGate(attr, _shared=self._shared)
        if inspect.iscoroutinefunction(attr):
            async def call(*args, **kwargs):
                return await self._call(name, attr, args, kwargs)
            return call
        return attr

    def _delay(self, attempt: int) -> float:
        base = type(self).backoff
        return base * 3 ** (attempt - 1) + random.uniform(0, 0.3 * base)

    async def _call(self, name: str, fn, args, kwargs):
        sh, attempt = self._shared, 0
        while True:
            attempt += 1
            try:
                async with sh["sem"]:
                    self.stats["kald"] += 1
                    result = await asyncio.wait_for(fn(*args, **kwargs), sh["timeout"])
            except Exception as e:  # noqa: BLE001 – kun midlertidige fejl prøves igen, resten sendes videre
                kind = _transient(e)
                if kind is None:
                    raise
                if kind == "timeout":
                    self.stats["timeout"] += 1
                if attempt > sh["retries"]:
                    self.stats["opgivet"] += 1
                    log.warning("Aula-kald %s opgivet efter %d forsøg: %s", name, attempt, _describe(e))
                    raise
                self.stats["genforsøg"] += 1
                delay = _retry_after(e) or self._delay(attempt)
                log.debug("Aula-kald %s: %s – prøver igen om %.1f s", name, kind, delay)
                await asyncio.sleep(delay)
                continue
            status = getattr(result, "status_code", None)
            if isinstance(status, int) and status in _RETRY_STATUS:
                if attempt > sh["retries"]:
                    self.stats["opgivet"] += 1
                    log.warning("Aula-kald %s opgivet efter %d forsøg: HTTP %d", name, attempt, status)
                    return result                 # kalderen afgør selv, hvad svaret betyder (raise_for_status)
                self.stats["genforsøg"] += 1
                delay = _retry_after(result) or self._delay(attempt)
                log.debug("Aula-kald %s: HTTP %d – prøver igen om %.1f s", name, status, delay)
                await asyncio.sleep(delay)
                continue
            return result


async def _once(client, key: str, factory):
    """Kør factory højst én gang pr. hentning (pr. AulaGate) – også når flere dele beder om det samtidigt."""
    if not isinstance(client, AulaGate):
        return await factory()
    task = client.memo.get(key)
    if task is None:
        task = client.memo[key] = asyncio.ensure_future(factory())
        task.add_done_callback(lambda t: t.cancelled() or t.exception())   # ingen "exception never retrieved"
    return await asyncio.shield(task)        # én del, der timer ud, må ikke afbryde opgaven for de andre


def _full_sweep_due(last: str | None, now: dt.datetime, acfg: dict) -> bool:
    """Skal der laves en dyb kontrol af beskederne? Den gennemgår hele trådlisten (fanger nye, slettede og arkiverede
    tråde) og henter indholdet af alle tråde igen (fanger rettede beskeder). Hver `deep_check_minutes` (standard 60)."""
    if not last:
        return True
    try:
        last_dt = dt.datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return True
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=TZ)
    return now - last_dt >= dt.timedelta(minutes=max(1, int(acfg.get("deep_check_minutes", 60))))


async def open_aula_client(cfg: dict):
    """Forbindelse til Aula med de gemte tokens (bruges som `async with await open_aula_client(cfg) as client`).
    Kræves et nyt MitID-login, afgør auth_hooks, hvad der sker (terminal, webside eller LoginRequired)."""
    from aula import FileTokenStorage
    from aula.auth_flow import authenticate_and_create_client

    acfg = cfg["aula"]
    token_path = Path(acfg.get("token_file", "secrets/aula_tokens.json"))
    token_path.parent.mkdir(parents=True, exist_ok=True)
    hooks = {"on_qr_codes": auth_hooks.on_qr_codes, "on_qr_done": auth_hooks.on_qr_done,
             "on_login_required": auth_hooks.on_login_required, "on_otp_code": auth_hooks.on_otp_code}
    supported = inspect.signature(authenticate_and_create_client).parameters       # ældre versioner mangler fx on_qr_done
    return await authenticate_and_create_client(
        acfg["mitid_username"], FileTokenStorage(str(token_path)), **{k: v for k, v in hooks.items() if k in supported},
    )


PART_LABELS = {"tasks": "opgaver", "weekplan": "ugeplanen", "posts": "opslag", "messages": "beskeder", "albums": "billeder"}


async def fetch_aula(cfg: dict, people: People, start: dt.datetime, end: dt.datetime, dump_path: Path | None = None,
                     previous_messages: list[dict] | None = None, full_sweep: bool = True) -> dict:
    acfg = cfg["aula"]
    events: list[dict] = []
    result: dict = {"tasks": [], "weekplan": [], "posts": [], "messages": [], "albums": []}
    meta = {"full_sweep_done": False}
    timings: dict[str, float] = {}
    t_start = time.perf_counter()

    async with await open_aula_client(cfg) as raw_client:
        client = AulaGate(raw_client, max_concurrent=acfg.get("max_concurrent", 3),
                          timeout=acfg.get("request_timeout", 30), retries=acfg.get("max_retries", 2))
        t0 = time.perf_counter()
        profile = await client.get_profile()

        # Aula institution-profil-id → vores person-id
        owner: dict[int, str] = {}
        for child in profile.children:
            pid = people.by_aula_name(child.name)
            if pid:
                owner[child.id] = pid
            else:
                log.warning("Barn i Aula matcher ingen person i config: %s", child.name)
        me = people.aula_self()
        if me:
            for inst_id in profile.institution_profile_ids:
                owner.setdefault(inst_id, me)

        raw_events = await client.get_calendar_events(profile.institution_profile_ids, start, end)
        lessons: dict[tuple[str, dt.date], list] = defaultdict(list)

        for ev in raw_events:
            raw = ev._raw or {}
            who = owner.get(ev.belongs_to)
            if not who:
                continue
            s, e = ev.start_datetime.astimezone(TZ), ev.end_datetime.astimezone(TZ)
            if cfg.get("collapse_lessons", True) and raw.get("type") == "lesson":
                lessons[(who, s.date())].append(ev)
                continue
            events.append({
                "id": f"a:{ev.id}",
                "title": ev.title or "(Aula)",
                "start": iso(s),
                "end": iso(e),
                "allDay": bool(raw.get("allDay")),
                "people": [who],
                "source": "aula",
                "type": raw.get("type"),
                "location": ev.location,
                "notes": None,
                "_aula_id": ev.id,
            })

        subj_map = cfg.get("subjects", {})
        for (who, day), evs in lessons.items():
            evs.sort(key=lambda x: x.start_datetime)
            lesson_list = schedule.build_lessons(evs, TZ, subj_map, acfg.get("hidden_subjects"), acfg.get("secondary_subjects"),
                                                 lesson_minutes=lesson_minutes(cfg))
            if not lesson_list:
                continue
            subs = any(l["substitute"] for l in lesson_list)
            day_start = dt.datetime.combine(day, dt.time.fromisoformat(lesson_list[0]["start"]), TZ)
            day_end = dt.datetime.combine(day, dt.time.fromisoformat(max(l["end"] for l in lesson_list)), TZ)
            events.append({
                "lessons": lesson_list,
                "id": f"a:skema:{who}:{day}",
                "title": "Skole" + (" · vikar" if subs else ""),
                "start": iso(day_start),
                "end": iso(day_end),
                "allDay": False,
                "people": [who],
                "source": "aula",
                "location": None,
                "notes": schedule.schedule_summary(lesson_list),
            })
        timings["kalender"] = time.perf_counter() - t0

        # Noter på lektioner (fx besked til vikaren) – kun de næste dage, og kun lektioner Aula markerer med en note
        t0 = time.perf_counter()
        lesson_details = await _lesson_notes(client, events, acfg)

        # Detaljer (beskrivelse) for kommende Aula-aftaler – her står skemaet nogle gange som tekst
        await _enrich_aula_events(client, events, acfg)
        timings["detaljer"] = time.perf_counter() - t0
        if dump_path:
            _dump_aula(dump_path, raw_events, events, lesson_details)

        # De fem dele hentes samtidigt (AulaGate holder det samlede antal kald nede). Hver del fejler for sig,
        # så én fejl eller ét hængende kald ikke vælter resten – en del, der fejler, genbruger forrige data.
        out_dir = Path(cfg.get("output", "web/family.json")).parent
        media = MediaStore(client, out_dir / "media", acfg.get("images_per_item", 8))
        msg_report: dict = {}
        extras = [
            ("tasks", acfg.get("fetch_tasks", True), lambda: _fetch_mu_tasks(client, profile, people)),
            ("weekplan", acfg.get("fetch_weekplan", True), lambda: _fetch_meebook(client, profile, people)),
            ("posts", acfg.get("fetch_posts", True), lambda: _fetch_posts(client, profile, owner, acfg, media)),
            ("messages", acfg.get("fetch_messages", True),
             lambda: _fetch_messages(client, people, acfg, media, previous_messages, full_sweep=full_sweep, report=msg_report)),
            ("albums", acfg.get("fetch_gallery", True), lambda: _fetch_gallery(client, profile, owner, people, acfg, media)),
        ]
        part_timeout = float(acfg.get("part_timeout", 900))
        for key, enabled, _fn in extras:
            if not enabled:
                problems.clear(f"aula.part:{key}")          # slået fra i config – ikke en fejl

        async def run_part(key: str, fn) -> None:
            t = time.perf_counter()
            try:
                result[key] = await asyncio.wait_for(fn(), part_timeout)
                problems.clear(f"aula.part:{key}")
            except Exception as e:  # noqa: BLE001
                slow = isinstance(e, TimeoutError) and time.perf_counter() - t >= part_timeout - 1
                why = f"tog over {part_timeout:.0f} s" if slow else _describe(e)
                log.warning("Aula %s fejlede (%s) – genbruger forrige data", key, why)
                problems.report(f"aula.part:{key}", "aula", f"Aula: {PART_LABELS.get(key, key)} kunne ikke hentes", detail=why,
                                hint="Appen viser de seneste hentede data for denne del og prøver igen ved næste hentning.")
                result[key] = None  # None = genbrug forrige data
            finally:
                timings[key] = time.perf_counter() - t

        await asyncio.gather(*(run_part(key, fn) for key, enabled, fn in extras if enabled))
        # Ryd kun op, når både opslag og beskeder blev hentet, ellers slettes billeder vi stadig viser
        media.report()
        if all(result.get(k) is not None for k in ("posts", "messages", "albums")):
            media.cleanup()
        meta["full_sweep_done"] = bool(full_sweep and result.get("messages") is not None and msg_report.get("complete"))

    for e in events:
        e.pop("_aula_id", None)
    result["events"] = events
    log.info("Aula: %d aftaler, %s", len(events),
             ", ".join(f"{len(v or [])} {k}" for k, v in result.items() if k != "events"))
    st = client.stats
    log.info("Aula-tider: %s · i alt %.1f s · %d kald, %d genforsøg, %d timeouts, %d opgivet",
             " · ".join(f"{k} {v:.1f} s" for k, v in timings.items()), time.perf_counter() - t_start,
             st["kald"], st["genforsøg"], st["timeout"], st["opgivet"])
    result["meta"] = meta
    return result


async def _fetch_mu_tasks(client, profile, people: People) -> list[dict]:
    """Best effort: lektier/opgaver fra Min Uddannelse-widgetten. Ikke alle skoler har den."""
    try:
        from aula.const import MIN_UDDANNELSE_TASK_WIDGETS
    except ImportError:
        return []
    child_filter, inst_filter = _widget_filters(profile)
    try:
        ctx = await _once(client, "profile_context", client.get_profile_context)
        session_uuid = ctx["data"]["userId"]
    except Exception as e:  # noqa: BLE001
        log.info("Ingen widget-kontekst (opgaver springes over): %s", e)
        return []

    today = dt.date.today()
    weeks = {f"{d.isocalendar()[0]}-W{d.isocalendar()[1]}" for d in (today, today + dt.timedelta(days=7))}
    out: list[dict] = []
    for widget_id in MIN_UDDANNELSE_TASK_WIDGETS:
        try:
            for week in weeks:
                for t in await client.widgets.get_mu_tasks(widget_id, child_filter, inst_filter, week, session_uuid):
                    pid = people.by_aula_name(t.student_name)
                    if not pid or t.is_completed or not t.due_date:
                        continue
                    subject = t.course.name if t.course else (t.classes[0].subject_name if t.classes else "")
                    out.append({
                        "id": f"mu:{t.id}:{pid}",
                        "title": f"{subject}: {t.title}" if subject else t.title,
                        "due": t.due_date.astimezone(TZ).date().isoformat(),
                        "person": pid,
                        "kind": "lektie",
                        "source": "aula",
                        "url": t.deep_link,
                    })
            return out
        except Exception as e:  # noqa: BLE001
            log.info("Min Uddannelse-widget %s gav ingen opgaver: %s", widget_id, e)
    return out


# ---------------------------------------------------------------- Aula: ugeplan, opslag, beskeder
# Aulas tekstomdannelse sætter backslash foran tegn, der ligner markdown ("1\.", "\-")
_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~])")


def _plain(html: str | None, limit: int = 2000) -> str:
    if not html:
        return ""
    try:
        from aula.utils.html import html_to_plain
        text = html_to_plain(html)
    except Exception:  # noqa: BLE001
        text = re.sub(r"<[^>]+>", " ", html)
    text = _MD_ESCAPE.sub(r"\1", text.replace("\u00a0", " "))      # "1\." → "1."
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]


_IMG_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def _sniff(data: bytes) -> str | None:
    """Find billedtypen ud fra filens indhold – filnavne i Aula mangler ofte endelse."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"GIF8":
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"hevc"):
        return ".heic"
    return None


def _heic_to_jpg(data: bytes) -> bytes | None:
    """iPhone-billeder (HEIC) kan ikke vises i Chrome/Edge. Konvertér hvis pillow-heif er installeret."""
    try:
        import io
        import pillow_heif
        from PIL import Image
        pillow_heif.register_heif_opener()
        img = Image.open(io.BytesIO(data))
        img.thumbnail((1600, 1600))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=85)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


class MediaStore:
    """Downloader billeder fra opslag/beskeder til web/media, så appen kan vise dem lokalt.

    Aulas billed-links er tidsbegrænsede, så de kan ikke bruges direkte i browseren.
    Filer navngives efter Aulas id, så de kun hentes én gang. Indholdet tjekkes, så en
    fejlside aldrig gemmes som et "billede".
    """

    _THUMB_KEYS = ("largeThumbnailUrl", "mediumThumbnailUrl", "thumbnailUrl", "smallThumbnailUrl")

    def __init__(self, client, folder: Path, max_per_item: int):
        self.client, self.folder, self.max = client, folder, max_per_item
        self.folder.mkdir(parents=True, exist_ok=True)
        self.used: set[str] = set()
        self.stats: defaultdict[str, int] = defaultdict(int)
        self._locks: dict[str, asyncio.Lock] = {}

    def _existing(self, key: str) -> str | None:
        """Genbrug en tidligere hentet fil – men kun hvis den faktisk er et billede.

        Tidligere versioner gemte hvad som helst Aula svarede (fx en fejlside) som .jpg.
        Den slags filer slettes her, så billedet hentes igen.
        """
        for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"):
            path = self.folder / f"{key}{ext}"
            if not path.exists():
                continue
            with path.open("rb") as fh:
                kind = _sniff(fh.read(16))
            if kind == ext.replace(".jpeg", ".jpg"):
                return path.name
            self.stats["ugyldig fil i cache slettet"] += 1
            path.unlink(missing_ok=True)
        return None

    def _candidates(self, att) -> list[str]:
        """Mulige adresser for et billede, bedste først."""
        raw = att._raw or {}
        media_raw = raw.get("media") or {}
        mtype = (att.media.media_type if att.media else "") or media_raw.get("mediaType", "")
        if "video" in mtype.lower():
            # Til videoer tager vi kun et stillbillede
            return [media_raw[k] for k in self._THUMB_KEYS if media_raw.get(k)]
        urls = []
        name = ((att.file.name if att.file else "") or att.name or "").lower()
        is_doc = name.endswith((".pdf", ".doc", ".docx", ".xlsx", ".pptx", ".txt"))
        if att.file and att.file.url and not is_doc:
            urls.append(att.file.url)
        urls += [media_raw[k] for k in self._THUMB_KEYS if media_raw.get(k)]
        if att.media and att.media.thumbnail_url:
            urls.append(att.media.thumbnail_url)
        return list(dict.fromkeys(urls))          # uden dubletter, rækkefølge bevaret

    async def _fetch(self, key: str, urls: list[str]) -> str | None:
        # Samme billede kan dukke op flere steder samtidigt: kun én henter, de andre venter og får filen fra cachen
        async with self._locks.setdefault(key, asyncio.Lock()):
            if (have := self._existing(key)):
                self.stats["fra cache"] += 1
                return have
            for url in urls:
                try:
                    data = await self.client.download_file(url)
                except Exception as e:  # noqa: BLE001
                    self.stats["download fejlede"] += 1
                    log.debug("Billede %s: download fejlede: %s", key, _describe(e))
                    continue
                ext = _sniff(data)
                if ext == ".heic":
                    converted = _heic_to_jpg(data)
                    if converted is None:
                        self.stats["HEIC uden konvertering"] += 1
                        continue                      # prøv thumbnail i stedet
                    data, ext = converted, ".jpg"
                if not ext:
                    self.stats["ikke et billede"] += 1
                    log.debug("Billede %s: indholdet er ikke et billede (%r…)", key, data[:40])
                    continue
                fname = f"{key}{ext}"
                tmp = self.folder / f"{fname}.part"
                try:
                    tmp.write_bytes(data)
                    tmp.replace(self.folder / fname)   # atomisk: en afbrudt hentning efterlader aldrig et halvt billede
                except OSError as e:
                    self.stats["kunne ikke gemmes"] += 1
                    log.warning("Billede %s kunne ikke gemmes: %s", key, e)
                    tmp.unlink(missing_ok=True)
                    return None
                self.stats["hentet"] += 1
                return fname
            if urls:
                self.stats["ikke hentet"] += 1        # ingen af adresserne gav et billede
            return None

    async def urls(self, key: str, urls: list[str]) -> str | None:
        """Hent ét billede ud fra en liste af mulige adresser (bruges til galleriet)."""
        fname = await self._fetch(key, [u for u in urls if u])
        if fname:
            self.used.add(fname)
            return f"{self.folder.name}/{fname}"
        return None

    async def images(self, attachments, html: str | None = None) -> list[str]:
        out: list[str] = []
        jobs: list[tuple[str, list[str]]] = []
        for att in attachments or []:
            urls = self._candidates(att)
            if urls:
                key = str(att.id or (att.file.id if att.file else None)
                          or hashlib.sha1(urls[0].split("?")[0].encode()).hexdigest()[:16])
                jobs.append((key, urls))
            else:
                self.stats["vedhæftning uden billede"] += 1
        # Billeder indsat direkte i opslagets tekst
        for src in re.findall(r"<img[^>]+src=[\"']([^\"']+)", html or "", re.I):
            if src.startswith("http"):
                jobs.append((hashlib.sha1(src.split("?")[0].encode()).hexdigest()[:16], [src]))
        names = await asyncio.gather(*(self._fetch(key, urls) for key, urls in jobs[: self.max]), return_exceptions=True)
        for fname in names:                       # samme rækkefølge som vedhæftningerne
            if isinstance(fname, BaseException):
                self.stats["fejl"] += 1
                log.debug("Billede fejlede: %s", _describe(fname))
                continue
            if fname:
                self.used.add(fname)
                out.append(f"{self.folder.name}/{fname}")
        return out

    def report(self) -> None:
        if self.stats:
            bad = any(self.stats.get(k) for k in ("ikke hentet", "kunne ikke gemmes", "fejl"))
            log.log(logging.WARNING if bad else logging.INFO, "Billeder: %s", ", ".join(f"{v} {k}" for k, v in self.stats.items()))

    def cleanup(self) -> None:
        """Slet billeder, der ikke længere hører til et opslag eller en besked."""
        for f in self.folder.iterdir():
            if f.is_file() and f.name not in self.used:
                f.unlink(missing_ok=True)


def _widget_filters(profile) -> tuple[list[str], list[str]]:
    child_filter = [str(c._raw["userId"]) for c in profile.children if c._raw and "userId" in c._raw]
    inst_filter = sorted({
        str(c._raw.get("institutionProfile", {}).get("institutionCode"))
        for c in profile.children if c._raw and c._raw.get("institutionProfile", {}).get("institutionCode")
    })
    return child_filter, inst_filter


_DK_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"], start=1)}
_DK_DAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]


def _parse_plan_date(text: str, monday: dt.date) -> str | None:
    """Meebook-datoer kommer i forskellige formater; prøv de mest almindelige."""
    t = (text or "").strip().lower()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return f"{m[1]}-{m[2]}-{m[3]}"
    m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", t)
    if m:
        y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        return dt.date(y, int(m[2]), int(m[1])).isoformat()
    m = re.search(r"(\d{1,2})\.?\s*([a-zæøå]{3})", t)
    if m and m[2] in _DK_MONTHS:
        return dt.date(monday.year, _DK_MONTHS[m[2]], int(m[1])).isoformat()
    for i, name in enumerate(_DK_DAYS):
        if t.startswith(name):
            return (monday + dt.timedelta(days=i)).isoformat()
    return None


async def _fetch_meebook(client, profile, people: People) -> list[dict]:
    child_filter, inst_filter = _widget_filters(profile)
    ctx = await _once(client, "profile_context", client.get_profile_context)
    session_uuid = ctx["data"]["userId"]
    today = dt.date.today()
    out: list[dict] = []
    this_monday = today - dt.timedelta(days=today.weekday())
    for monday in (this_monday, this_monday + dt.timedelta(days=7)):
        y, w, _ = monday.isocalendar()
        week = f"{y}-W{w:02d}"
        for student in await client.widgets.get_meebook_weekplan(child_filter, inst_filter, week, session_uuid):
            pid = people.by_aula_name(student.name)
            if not pid:
                continue
            for day in student.week_plan:
                date = _parse_plan_date(day.date, monday)
                for task in day.tasks:
                    subject = task.pill or task.title or "Ugeplan"
                    out.append({
                        "id": f"mb:{pid}:{task.id}:{date}",
                        "person": pid,
                        "date": date,
                        "week": week,
                        "dayLabel": day.date,
                        "type": task.type,          # Meebooks egen markering: "task" eller "comment"
                        "subject": subject,
                        "title": task.title or subject,
                        "text": _plain(task.content, 1500),
                        "source": "meebook",
                    })
    return out


async def _child_groups(client, profile, owner: dict[int, str]) -> tuple[dict[int, set[str]], dict[str, set[str]]]:
    """Barnets grupper (klasse, SFO, stue …) → barn, og institutionskode → børn.

    Hentes én gang pr. hentning og deles af opslag og galleri (tidligere en cache, der aldrig blev tømt)."""
    async def load():
        group_owner: dict[int, set[str]] = defaultdict(set)
        inst_owner: dict[str, set[str]] = defaultdict(set)

        async def groups_for(child, pid: str) -> None:
            try:
                for g in await client.get_groups(child_institution_profile_ids=[child.id]):
                    group_owner[g.id].add(pid)
            except Exception as e:  # noqa: BLE001
                log.debug("Kunne ikke hente grupper for %s: %s", child.name, _describe(e))

        jobs = []
        for child in profile.children:
            pid = owner.get(child.id)
            if not pid:
                continue
            code = str((child._raw or {}).get("institutionProfile", {}).get("institutionCode") or "")
            if code:
                inst_owner[code].add(pid)
            jobs.append(groups_for(child, pid))
        await asyncio.gather(*jobs)
        return group_owner, inst_owner

    return await _once(client, "groups", load)


def _parse_ts(value) -> str | None:
    if not value:
        return None
    try:
        return iso(dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return str(value)


async def _fetch_gallery(client, profile, owner: dict[int, str], people: People, acfg: dict, media: MediaStore) -> list[dict]:
    """Galleri-albums. Mange institutioner lægger billeder her i stedet for i opslag. Albums og billeder hentes samtidigt."""
    group_owner, inst_owner = await _child_groups(client, profile, owner)
    ids = profile.institution_profile_ids
    albums = await client.get_gallery_albums(ids, limit=acfg.get("gallery_albums", 8))
    log.info("Aula: %d albums i galleriet", len(albums))
    per_album = acfg.get("images_per_album", 12)

    async def one_album(a: dict) -> dict | None:
        album_id = a.get("id")
        if not isinstance(album_id, int):
            return None
        try:
            pics = await client.get_album_pictures(ids, album_id, limit=per_album)
        except Exception as e:  # noqa: BLE001
            log.warning("Kunne ikke hente billeder i album %s: %s", a.get("title"), _describe(e))
            return None
        jobs, tagged = [], set()
        for pic in pics[:per_album]:
            for t in pic.get("tags") or []:        # børn, der er tagget på billedet
                pid = people.by_aula_name(t.get("name") or "")
                if pid:
                    tagged.add(pid)
            f = pic.get("file") or {}
            is_video = "video" in str(pic.get("mediaType", "")).lower()
            urls = ([] if is_video else [f.get("url")]) + [pic.get(k) for k in MediaStore._THUMB_KEYS]
            jobs.append(media.urls(f"g{pic.get('id') or f.get('id')}", urls))
        images = [path for path in await asyncio.gather(*jobs) if path]
        who: set[str] = set(tagged)
        for g in a.get("sharedWithGroups") or []:
            if isinstance(g, dict):
                who |= group_owner.get(g.get("id"), set())
        if not who:
            code = str(a.get("institutionCode") or (a.get("creator") or {}).get("institutionCode") or "")
            who = inst_owner.get(code, set())
        creator = a.get("creator") or {}
        return {
            "id": f"album:{album_id}",
            "title": a.get("title") or "Album",
            "author": creator.get("name") or creator.get("fullName") or a.get("creatorName"),
            "timestamp": _parse_ts(a.get("creationDate")),
            "text": _plain(a.get("description") or "", 600),
            "images": images,
            "imageCount": a.get("size") or a.get("totalSize") or len(pics),
            "tagged": sorted(tagged),
            "people": sorted(who) or ["family"],
            "source": "aula",
        }

    return [x for x in await asyncio.gather(*(one_album(a) for a in albums[: acfg.get("gallery_albums", 8)])) if x]


async def _fetch_posts(client, profile, owner: dict[int, str], acfg: dict, media: MediaStore) -> list[dict]:
    """Opslag hentes med ALLE profil-id'er (som Aulas egen app gør) og fordeles på børn via grupper.

    Aula viser forældres opslag ud fra forælderens egen profil, så et kald med kun barnets id
    giver typisk ingenting. Hvem et opslag handler om, findes via de grupper det er delt med.
    """
    limit = acfg.get("posts_limit", 10) * max(1, len(profile.children))
    group_owner, inst_owner = await _child_groups(client, profile, owner)

    raw_posts = await client.get_posts(profile.institution_profile_ids, limit=limit)
    log.info("Aula: %d opslag hentet", len(raw_posts))
    all_images = await asyncio.gather(*(media.images(p.attachments, p.content_html) for p in raw_posts))
    out: list[dict] = []
    for p, imgs in zip(raw_posts, all_images):
        who: set[str] = set()
        for g in p.shared_with_groups or []:
            gid = g.get("id") if isinstance(g, dict) else None
            who |= group_owner.get(gid, set())
        if not who:  # fx opslag til hele institutionen
            code = str(getattr(p.owner, "_raw", {}) and (p.owner._raw or {}).get("institutionCode") or "")
            who = inst_owner.get(code, set())
        out.append({
            "id": f"post:{p.id}",
            "title": p.title,
            "author": p.owner.full_name if p.owner else None,
            "timestamp": iso(p.timestamp) if p.timestamp else None,
            "important": bool(p.is_important),
            "text": _plain(p.content_html),
            "images": imgs,
            "attachments": len(p.attachments or []) - len(imgs),
            "groups": [g.get("name") for g in (p.shared_with_groups or []) if isinstance(g, dict) and g.get("name")],
            "people": sorted(who) or ["family"],
            "source": "aula",
        })
    return sorted(out, key=lambda x: x["timestamp"] or "", reverse=True)


async def _api_json(client, query: str) -> dict:
    """Kald Aulas API direkte med side-parameter. Biblioteket henter altid kun side 0."""
    resp = await client._request_with_version_retry("get", f"{client.api_url}?{query}")
    resp.raise_for_status()
    return resp.json()


def _thread_sig(raw: dict, per_thread: int) -> str:
    """Fingeraftryk af en tråd i trådlisten. Ændrer Aula noget ved tråden (ny besked, læst, deltagere …), ændres det."""
    return hashlib.sha1(f"{per_thread}|{json.dumps(raw, sort_keys=True, default=str)}".encode()).hexdigest()[:16]


async def _list_threads(client, max_threads: int, max_pages: int, unchanged=None, stop_after: int = 0) -> tuple[list[dict], dict]:
    """Beskedtråde (nyeste først), side for side, til der ikke kommer nye.

    Med unchanged + stop_after stopper vi efter en side, når de seneste stop_after tråde alle var uændrede: Aula
    sorterer efter seneste aktivitet, så resten er så godt som altid også uændret. info["complete"] fortæller,
    om hele listen blev set – ellers overfører kalderen resten fra forrige hentning.
    """
    info = {"pages": 0, "complete": True, "early_stop": False}
    if not (hasattr(client, "_request_with_version_retry") and hasattr(client, "api_url")):
        log.warning("Aula-biblioteket kan ikke side-inddele beskeder i denne version – henter kun første side")
        info["complete"] = False
        return [t._raw or {} for t in await client.get_message_threads()][:max_threads], info
    seen: set = set()
    out: list[dict] = []
    run = 0
    for page in range(max_pages):
        try:
            data = await _api_json(client, f"method=messaging.getThreads&sortOn=date&orderDirection=desc&page={page}")
        except Exception as e:  # noqa: BLE001
            if page == 0:
                raise                           # ingen liste overhovedet: hele beskeddelen genbruger forrige data
            log.warning("Beskedtråde: side %d kunne ikke hentes (%s) – resten genbruges fra forrige hentning", page + 1, _describe(e))
            info["complete"] = False
            break
        info["pages"] += 1
        fresh = [t for t in ((data.get("data") or {}).get("threads") or []) if t.get("id") is not None and t["id"] not in seen]
        if not fresh:
            break                               # tom side, eller serveren gentager sig selv
        for t in fresh:
            seen.add(t["id"])
            out.append(t)
            if unchanged is not None:
                run = run + 1 if unchanged(t) else 0
        if len(out) >= max_threads:
            break
        if stop_after and run >= stop_after:
            info["early_stop"], info["complete"] = True, False
            break
        await asyncio.sleep(0.15)
    return out[:max_threads], info


async def _all_threads(client, max_threads: int, max_pages: int) -> list[dict]:
    """Alle beskedtråde (nyeste først), side for side, til der ikke kommer nye."""
    return (await _list_threads(client, max_threads, max_pages))[0]


async def _thread_messages(client, thread_id, per_thread: int, max_pages: int = 4) -> list:
    """Beskederne i en tråd, nyeste først (op til per_thread)."""
    if not (hasattr(client, "_request_with_version_retry") and hasattr(client, "api_url")):
        msgs = await client.get_messages_for_thread(thread_id, limit=per_thread)
    else:
        from aula.models import Message
        seen: set = set()
        msgs = []
        for page in range(max_pages):
            data = await _api_json(client, f"method=messaging.getMessagesForThread&threadId={thread_id}&page={page}&limit={per_thread}")
            d = data.get("data") or {}
            fresh = [m for m in (d.get("messages") or [])
                     if m.get("messageType") in ("Message", "MessageEdited") and m.get("id") not in seen]
            if not fresh:
                break
            for m in fresh:
                seen.add(m.get("id"))
                try:
                    msgs.append(Message.from_dict(m))
                except (TypeError, ValueError) as e:
                    log.debug("Besked sprunget over: %s", e)
            if len(msgs) >= per_thread or d.get("moreMessagesExist") is False:
                break
            await asyncio.sleep(0.1)
    msgs.sort(key=lambda x: x.send_datetime or dt.datetime.min.replace(tzinfo=TZ), reverse=True)
    return msgs[:per_thread]


async def _fetch_messages(client, people: People, acfg: dict, media: MediaStore,
                          previous: list[dict] | None = None, full_sweep: bool = True, report: dict | None = None) -> list[dict]:
    """Hele beskedhistorikken.

    - Uændrede tråde genbruges fra forrige family.json uden nye kald.
    - Uden full_sweep stopper trådlisten, når den når uændrede tråde, og resten overføres fra forrige hentning.
    - full_sweep = dyb kontrol (hver time): hele listen gennemgås, og alle tråde hentes igen – så både slettede og
      arkiverede tråde og rettede beskeder fanges (en rettelse ændrer ikke altid trådens linje i listen).
    - Ændrede tråde og deres billeder hentes samtidigt (loftet over samtidige kald sidder i AulaGate).
    - Kan en tråd ikke hentes, vises forrige version, og tråden prøves igen næste gang.
    """
    max_threads = acfg.get("messages_limit", 500)
    per_thread = acfg.get("messages_per_thread", 50)
    image_days = acfg.get("message_images_days", 90)
    stop_after = 0 if full_sweep else int(acfg.get("messages_stop_after_unchanged", 5))
    prev_list = [m for m in (previous or []) if str(m.get("id", "")).startswith("msg:")]
    prev_by_id = {m["id"]: m for m in prev_list}

    def unchanged(raw: dict) -> bool:
        old = prev_by_id.get(f"msg:{raw.get('id')}")
        return bool(old and old.get("thread") and old.get("sig") == _thread_sig(raw, per_thread))

    t0 = time.perf_counter()
    threads, info = await _list_threads(client, max_threads, acfg.get("messages_max_pages", 40), unchanged, stop_after)
    t_list = time.perf_counter() - t0
    try:
        unread = {f"msg:{t.thread_id}" for t in await client.get_message_threads(filter_on="unread")}
    except Exception:  # noqa: BLE001
        unread = set()

    def reuse(old: dict) -> dict:
        entry = dict(old)                                       # ingen nye kald
        entry["unread"] = entry["id"] in unread
        entry["people"] = old.get("people_aula") or ["family"]  # analysen kører igen på alle beskeder
        for src in [*entry.get("images", []), *[i for m in entry.get("thread") or [] for i in m.get("images", [])]]:
            media.used.add(Path(src).name)                      # billederne må ikke ryddes væk
        return entry

    def regarding(raw: dict) -> list[str]:
        # Hvem handler tråden om? Prøv Aulas "angående"-felt, ellers navne i emnet
        who: list[str] = []
        for c in raw.get("regardingChildren") or []:
            pid = people.by_aula_name(c.get("displayName") or c.get("name") or "")
            if pid and pid not in who:
                who.append(pid)
        return who or [p for p in people.in_text(raw.get("subject")) if people.by_id[p].get("role") == "child"]

    def make_entry(raw: dict, who: list[str], thread: list[dict]) -> dict:
        latest = thread[0] if thread else {}
        return {
            "id": f"msg:{raw.get('id')}",
            "sig": _thread_sig(raw, per_thread),
            "subject": raw.get("subject") or "(uden emne)",
            "from": latest.get("from"),
            "timestamp": latest.get("timestamp") or raw.get("lastUpdatedDate"),
            "unread": f"msg:{raw.get('id')}" in unread,
            "text": latest.get("text", ""),        # seneste besked – det er den, analysen kigger på
            "images": latest.get("images", []),
            "thread": thread,
            "participants": [p.get("name") for p in raw.get("participants", []) if p.get("name")][:12],
            "people": who or ["family"],
            "people_aula": who or ["family"],
            "source": "aula",
        }

    today = dt.date.today()

    async def fetch_one(raw: dict) -> dict:
        msgs = await _thread_messages(client, raw.get("id"), per_thread)   # fejl sendes videre og håndteres nedenfor

        async def one(i: int, mm) -> dict:
            age = (today - mm.send_datetime.astimezone(TZ).date()).days if mm.send_datetime else 0
            return {
                "from": mm.sender_name,
                "timestamp": iso(mm.send_datetime) if mm.send_datetime else None,
                "text": _plain(mm.content_html, 6000 if i == 0 else 3000),
                "images": await media.images(mm.attachments, mm.content_html) if age <= image_days else [],
                "files": [a.name for a in (mm.attachments or []) if a.name],
            }

        return make_entry(raw, regarding(raw), list(await asyncio.gather(*(one(i, mm) for i, mm in enumerate(msgs)))))

    out: list = [None] * len(threads)
    todo: list[int] = []
    cached = 0
    for i, raw in enumerate(threads):
        if not full_sweep and unchanged(raw):
            out[i] = reuse(prev_by_id[f"msg:{raw.get('id')}"])
            cached += 1
        else:
            todo.append(i)

    t1 = time.perf_counter()
    results = await asyncio.gather(*(fetch_one(threads[i]) for i in todo), return_exceptions=True)
    t_fetch = time.perf_counter() - t1
    fetched, kept_old, failed = 0, 0, []
    for i, res in zip(todo, results):
        raw = threads[i]
        if not isinstance(res, BaseException):
            out[i] = res
            fetched += 1
            continue
        if isinstance(res, asyncio.CancelledError):
            raise res
        failed.append(str(raw.get("id")))
        log.debug("Kunne ikke hente beskeder i tråd %s: %s", raw.get("id"), _describe(res))
        old = prev_by_id.get(f"msg:{raw.get('id')}")
        if old and old.get("thread"):
            out[i] = reuse(old)          # behold forrige version – dens gamle sig gør, at tråden prøves igen næste gang
            kept_old += 1
        else:
            out[i] = make_entry(raw, regarding(raw), [])   # tom tråd prøves igen næste gang

    carried = 0
    if not info["complete"]:
        listed = {f"msg:{t.get('id')}" for t in threads}
        for old in prev_list:            # resten af listen, i samme rækkefølge som sidst
            if len(out) >= max_threads:
                break
            if old["id"] not in listed:
                out.append(reuse(old))
                carried += 1

    mode = "dyb kontrol" if full_sweep else ("stoppede ved uændrede tråde" if info["early_stop"] else "hele listen")
    log.info("Beskeder: %d tråde (%d hentet, %d uændrede, %d overført fra forrige hentning, %d fejlede) · "
             "trådliste %d side(r) på %.1f s (%s) · hentning %.1f s",
             len(out), fetched, cached, carried, len(failed), info["pages"], t_list, mode, t_fetch)
    if failed:
        log.warning("Beskeder: %d tråd(e) kunne ikke hentes, %d viser forrige version – prøves igen næste gang: %s",
                    len(failed), kept_old, ", ".join(failed[:10]) + (" …" if len(failed) > 10 else ""))
    if report is not None:
        report.update(info, fetched=fetched, cached=cached, carried=carried, failed=len(failed))
    return out


# ---------------------------------------------------------------- lektie-genkendelse
_CONF = {"lav": 0, "middel": 1, "høj": 2}
_PLAN_WORDS = re.compile(r"plan|kalender", re.I)


def _clean_subject(s: str) -> str:
    """"Musik, Årsplan" → "Musik"; "aktivitetsplan, Natur teknik, …" → "Natur teknik"."""
    parts = [p.strip() for p in (s or "").split(",") if p.strip()]
    return next((p for p in parts if not _PLAN_WORDS.search(p)), parts[0] if parts else "Ugeplan")


def analyse_weekplan(cfg: dict, weekplan: list[dict]) -> list[dict]:
    """Kør homework-algoritmen på ugeplanen: markér hvert punkt og lav lektier/husk-opgaver."""
    classes = {p["id"]: p.get("class") for p in cfg["people"]}
    min_conf = _CONF.get(cfg.get("aula", {}).get("homework_min_confidence", "middel"), 1)
    tasks: list[dict] = []
    for w in weekplan:
        # Bagudkompatibelt med ældre family.json, hvor Meebook-typen lå i "title" og faget i "label"
        wtype = w.get("type") or (w.get("title") if w.get("title") in ("task", "comment") else "")
        subject = _clean_subject(w.get("subject") or w.get("label") or w.get("title") or "Ugeplan")
        w.update(type=wtype, subject=subject, title=subject if w.get("title") in (None, "", "task", "comment") else w["title"])
        w.pop("label", None)
        if not w.get("date"):
            w["category"] = "undervisning"
            continue
        r = homework.analyse({"type": wtype, "text": w.get("text", ""), "date": w["date"]}, classes.get(w["person"]))
        w["category"], w["info"] = r["category"], r["info"]
        for i, t in enumerate(r["tasks"]):
            if _CONF[t["confidence"]] < min_conf:
                continue
            tasks.append({
                "id": f"{w['id']}:{i}",
                "title": f"{subject}: {t['title']}" if t["kind"] == "lektie" else t["title"],
                "due": t["due"],
                "recurring": t["recurring"],
                "from": w["date"],         # hvornår lektien blev givet – lange afleveringer vises fra denne dag
                "person": w["person"],
                "kind": t["kind"],
                "confidence": t["confidence"],
                "text": w.get("text", ""),
                "source": "meebook",
            })
    return homework.dedupe(tasks)


def change_targets(store, events: list[dict]) -> list[dict]:
    """Aftaler, en aflysning eller flytning kan høre til: dem appen har oprettet (kan ændres her) og øvrige i familiekalenderen (kun til info)."""
    out = []
    for k, v in store.data["items"].items():
        if v.get("status") == "created" and v.get("event_id") and v.get("event"):
            ev = v["event"]
            out.append({"key": k, "event_id": v["event_id"], "title": ev["title"], "start": ev["start"][:10], "end": ev["end"][:10], "source": "app"})
    app_ids = {t["event_id"] for t in out}
    for e in events:
        if e.get("source") != "google" or str(e["id"]).startswith("gc:"):
            continue
        uid = e["id"].split(":")[1] if e["id"].startswith("g:") else ""
        if any(uid.startswith(a) for a in app_ids):
            continue
        out.append({"key": None, "event_id": None, "title": e["title"], "start": e["start"][:10], "end": e["end"][:10], "source": "google"})
    return out


def created_events(store, events: list[dict], people: People) -> list[dict]:
    """Aftaler oprettet via appen vises med det samme, selv om Googles iCal-adresse først opdateres om lidt."""
    have = {e["id"].split(":")[1] for e in events if e.get("source") == "google" and e["id"].startswith("g:")}
    out = []
    for st in store.recent_created():
        ev, eid = st["event"], st.get("event_id")
        if not eid or any(uid.startswith(eid) for uid in have):
            continue
        out.append({"id": f"gc:{eid}", "title": ev["title"], "start": ev["start"], "end": ev["end"], "allDay": ev["allDay"],
                    "people": people.in_text(ev["title"]) or ["family"], "source": "google", "calendar": "Familiekalender",
                    "location": ev.get("location"), "notes": ev.get("notes"), "pending": True, "appCreated": True,
                    **({"endInferred": True} if ev.get("endInferred") else {})})
    return out


def analyse_messages(cfg: dict, msgs: list[dict], events: list[dict]) -> tuple[list[dict], list[dict]]:
    """Kør besked-analysen: ret hvem beskeden angår, markér private tråde, og træk
    handlinger, arrangementer og medbring-lister ud."""
    people = cfg["people"]
    family_names = [p["name"] for p in people if p.get("role") == "adult"]
    tasks: list[dict] = []
    new_events: list[dict] = []
    existing = {(e["start"][:10], w) for e in events for w in re.findall(r"[\wæøå]{5,}", e["title"].lower())}
    cutoff = (dt.date.today() - dt.timedelta(days=cfg.get("aula", {}).get("messages_analyse_days", 60))).isoformat()
    for m in msgs:
        if m.get("redacted"):
            continue                    # allerede analyseret som privat; uden indhold ville analysen komme til et andet resultat
        r = msg_analysis.analyse(m, people, family_names)
        m.update(people=r["people"], private=r["private"], category=r["category"],
                 actions=r["actions"], suggested_events=r["events"], bring=r["bring"])
        sent = (m.get("timestamp") or "")[:10] or None
        if sent and sent < cutoff:
            continue        # gamle beskeder vises og kan søges, men giver ikke opgaver eller aftaler i kalenderen
        base = {"person": r["people"][0] if len(r["people"]) == 1 else None, "people": r["people"],
                "source": "besked", "message": m["id"], "from": sent, "subject": m.get("subject")}
        for i, a in enumerate(r["actions"]):
            tasks.append({**base, "id": f"{m['id']}:a{i}", "kind": "handling", "title": a["title"],
                          "due": a["due"] or sent, "openEnded": a["due"] is None,
                          "confidence": a["confidence"], "text": m.get("subject")})
        for i, b in enumerate(r["bring"]):
            tasks.append({**base, "id": f"{m['id']}:b{i}", "kind": "husk", "title": b["title"],
                          "due": b["due"] or sent, "openEnded": b["due"] is None,
                          "confidence": "høj", "text": "\n".join(b["items"])})
        for i, e in enumerate(r["events"]):
            # Spring over hvis Aula-kalenderen allerede har en aftale samme dag med samme ord
            words = re.findall(r"[\wæøå]{5,}", e["title"].lower())
            if any((e["date"], w) in existing for w in words):
                continue
            day = dt.date.fromisoformat(e["date"])
            if e["start"]:
                s_ = dt.datetime.combine(day, dt.time.fromisoformat(e["start"]), TZ)
                e_ = dt.datetime.combine(day, dt.time.fromisoformat(e["end"]), TZ) if e["end"] else s_ + dt.timedelta(hours=1)
                all_day, inferred = False, not e["end"]
            else:
                s_ = dt.datetime.combine(day, dt.time(0, 0), TZ)
                e_ = dt.datetime.combine(day + dt.timedelta(days=4 if e["allWeek"] else 0), dt.time(23, 59), TZ)
                all_day, inferred = True, False
            new_events.append({**({"endInferred": True} if inferred else {}), "id": f"{m['id']}:e{i}", "title": e["title"], "start": iso(s_), "end": iso(e_),
                               "allDay": all_day, "people": r["people"], "source": "besked",
                               "location": e["location"], "notes": f"Fra beskeden \"{m.get('subject')}\":\n{e['source_sentence']}"})
    return tasks, new_events


# ---------------------------------------------------------------- main
async def run_once(cfg: dict, use_aula: bool, dump: bool = False) -> dict:
    """Én hentning. Returnerer status til serveren: {"aula": skipped|ok|login_required|error, "error": str|None, "counts": {...}}."""
    people = People(cfg["people"])
    now = dt.datetime.now(TZ)
    t_cycle = time.perf_counter()
    start = (now - dt.timedelta(days=cfg.get("days_back", 7))).replace(hour=0, minute=0, second=0, microsecond=0)
    end = (now + dt.timedelta(days=cfg.get("days_ahead", 28))).replace(hour=23, minute=59, second=0, microsecond=0)

    out_path = Path(cfg.get("output", "web/family.json"))
    previous = json.loads(out_path.read_text("utf-8")) if out_path.exists() else {}

    google_failed: list[str] = []
    events = await fetch_google(cfg, people, start, end, google_failed)
    for name in google_failed:                      # Google nede: behold kalenderens seneste aftaler i stedet for at vise en tom kalender
        kept = [e for e in previous.get("events", []) if e.get("source") == "google" and e.get("calendar") == name and not str(e.get("id", "")).startswith("gc:")]
        events += kept
        log.warning("Beholder %d aftaler fra Google-kalenderen «%s» fra forrige kørsel", len(kept), name)
    extra_keys = ("tasks", "weekplan", "posts", "messages", "albums")
    extra = {k: previous.get(k, []) for k in extra_keys}
    aula_state, aula_error = "skipped", None
    last_sweep = previous.get("health", {}).get("aula", {}).get("last_full_sweep")
    sweep_done = False
    if not use_aula:
        problems.clear("aula.fetch")             # Aula er slået fra – ikke en fejl
    if use_aula:
        try:
            prev_msgs = previous.get("messages")
            if private_mod.enabled(cfg):                 # private tråde ligger ikke i family.json – hent dem frem, så de ikke hentes forfra
                prev_msgs = private_mod.hydrate(prev_msgs, private_mod.store_path(cfg))
            aula = await fetch_aula(cfg, people, start, end, Path("aula_dump.json") if dump else None, previous_messages=prev_msgs,
                                    full_sweep=_full_sweep_due(last_sweep, now, cfg["aula"]))
            sweep_done = bool((aula.get("meta") or {}).get("full_sweep_done"))
            events += aula["events"]
            for k in extra_keys:
                if aula.get(k) is not None:  # None = den del fejlede, behold forrige
                    extra[k] = aula[k]
            aula_state = "ok"
            problems.clear("aula.fetch")
        except LoginRequired:
            aula_state = "login_required"
            log.warning("Aula-login er udløbet – genbruger forrige Aula-data. Log ind igen.")
            problems.report("aula.fetch", "aula", "Aula-login er udløbet", detail="Aula kræver et nyt MitID-login.",
                            hint="Log ind med MitID igen. Appen viser imens de seneste hentede Aula-data.",
                            action={"label": "Log ind", "href": "auth"})
            events += [x for x in previous.get("events", []) if x.get("source") == "aula"]
        except Exception as e:  # noqa: BLE001
            # Behold sidste gode Aula-data frem for at vise et tomt overblik
            aula_state, aula_error = "error", str(e) or e.__class__.__name__
            log.error("Aula-hentning fejlede, genbruger forrige data: %s", e)
            problems.report("aula.fetch", "aula", "Aula-hentningen fejlede", detail=f"{e.__class__.__name__}: {e}",
                            hint="Appen viser de seneste hentede Aula-data og prøver igen ved næste hentning. "
                                 "Bliver det ved, så prøv at logge ind igen.", action={"label": "Log ind", "href": "auth"})
            events += [x for x in previous.get("events", []) if x.get("source") == "aula"]

    msg_tasks, msg_events = analyse_messages(cfg, extra["messages"], events)
    extra["tasks"] = ([t for t in extra["tasks"] if t.get("source") not in ("meebook", "besked")]
                      + analyse_weekplan(cfg, extra["weekplan"]) + msg_tasks)
    events += msg_events
    now_s = iso(now)
    prev_h = previous.get("health", {})
    health = {"google": {"ok": not google_failed, "failed": google_failed, "last_ok": now_s if not google_failed else prev_h.get("google", {}).get("last_ok")},
              "aula": {"state": aula_state, "last_ok": now_s if aula_state in ("ok", "skipped") else prev_h.get("aula", {}).get("last_ok"),
                       "last_full_sweep": now_s if sweep_done else last_sweep}}

    # Forslag til familiekalenderen + manuelle "føj til kalender"-muligheder på beskeder, opslag og ugeplanspunkter
    suggestions_list: list[dict] = []
    new_ids: list[str] = []
    if cfg.get("suggestions", {}).get("enabled", True):
        try:
            store = sugg_store.Store(out_path.with_name("suggestions_state.json"))
            people_map = {p["id"]: p["name"] for p in cfg["people"]}
            view = {"messages": extra["messages"], "posts": extra["posts"], "weekplan": extra["weekplan"], "events": events}
            import calendar_ai               # AI afgør, hvor den kan; reglerne i activities.py er reserve pr. punkt
            found, options = calendar_ai.find_all(view, cfg, now.date(), people_map, change_targets(store, events))
            # Kun aflysninger og flytninger af aftaler i kalenderen vises (på beskeden selv). Nye aktiviteter foreslås ikke
            # automatisk – de oprettes kun manuelt via "Føj til kalender", som bruger forslagenes titler.
            suggestions_list = [s for s in found if s.get("kind") in activities.CHANGE_KINDS]
            for kind in ("messages", "posts", "weekplan"):
                for item in extra[kind]:
                    item["cal"] = options.get(item["id"], [])
            new_ids = store.annotate(suggestions_list, options)
            events += created_events(store, events, people)
        except Exception as e:  # noqa: BLE001 – forslag må aldrig vælte hentningen
            log.warning("Kunne ikke finde kalenderforslag: %s", e)
    events.sort(key=lambda x: x["start"])
    try:
        import weather
        weather_data = weather.for_family(cfg, now)        # groft dagsresumé, aldrig placeringen; None uden hjem
    except Exception as e:  # noqa: BLE001 – vejret er et ekstra og må aldrig vælte hentningen
        log.warning("Vejret sprunget over: %s", e)
        problems.report("weather", "weather", "Vejret kunne ikke laves", detail=f"{type(e).__name__}: {e}")
        weather_data = None
    data = {
        "generated": iso(now),
        "people": people.public(),
        "events": events,
        "tasks": sorted(extra["tasks"], key=lambda t: t["due"]),
        "weekplan": extra["weekplan"],
        "posts": extra["posts"],
        "messages": (private_mod.protect(extra["messages"], private_mod.store_path(cfg)) if private_mod.enabled(cfg) else extra["messages"]),
        "albums": extra["albums"],
        "suggestions": suggestions_list,
        "health": health,
        "weather": weather_data,
        "settings": {"hidePrivate": cfg.get("aula", {}).get("hide_private", True), "eveningHour": int(cfg.get("display", {}).get("evening_hour", 18)),
                     "lessonMinutes": lesson_minutes(cfg)},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(out_path)  # atomisk, så frontenden aldrig læser en halv fil
    log.info("Skrev %s (%d aftaler, %s)", out_path, len(events),
             ", ".join(f"{len(extra[k])} {k}" for k in extra_keys))

    # Overblik: "ai" = sprogmodel via ai.py med egne regler som reserve; "offline" = kun egne regler (standard uden config)
    acfg = cfg.get("assistant", {})
    try:
        import briefing
        if briefing.assistant_mode(acfg) != "off":
            briefing.make_briefing(cfg, data, "day")
            wk = {**cfg, "assistant": {**acfg, "min_minutes_between": acfg.get("week_min_minutes_between", 360)}}
            briefing.make_briefing(wk, data, "week")
    except SystemExit as e:
        log.warning("Overblik springes over: %s", e)
        problems.report("ai.briefing.error", "ai", "Overblikket kunne ikke laves", detail=str(e))
    except Exception as e:  # noqa: BLE001
        log.warning("Kunne ikke lave overblik: %s", e)
        problems.report("ai.briefing.error", "ai", "Overblikket kunne ikke laves", detail=f"{type(e).__name__}: {e}")
    else:
        problems.clear("ai.briefing.error")
    log.info("Hentning færdig på %.1f s", time.perf_counter() - t_cycle)
    return {"aula": aula_state, "error": aula_error, "generated": data["generated"], "google_ok": not google_failed,
            "counts": {"events": len(events), **{k: len(extra[k]) for k in extra_keys},
                       "suggestions": sum(1 for x in suggestions_list if x.get("status") == "new")}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--watch", type=int, metavar="SEK", help="hent igen hvert SEK sekund")
    ap.add_argument("--no-aula", action="store_true", help="spring Aula over")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--dump-aula", action="store_true", help="gem rå kalenderdata i aula_dump.json til fejlsøgning")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        sys.exit(f"Fandt ikke {cfg_path}. Kopiér config.example.toml til config.toml og udfyld den.")
    import ops
    loaded = ops.load_dotenv(Path.cwd() / ".env", cfg_path.parent / ".env")   # Docker sætter dem selv; direkte kørsel læser .env
    if loaded:
        logging.getLogger("familieplanner").info("Læste fra .env: %s", ", ".join(loaded))
    cfg = tomllib.loads(cfg_path.read_text("utf-8"))

    async def loop():
        while True:
            try:
                await run_once(cfg, use_aula=not args.no_aula, dump=args.dump_aula)
            except Exception:  # noqa: BLE001
                log.exception("Hentning fejlede")
            if not args.watch:
                return
            await asyncio.sleep(args.watch)

    asyncio.run(loop())


if __name__ == "__main__":
    main()
