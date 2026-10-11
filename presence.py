"""Komme/Gå fra Aula: hvornår børnene kommer og går, hvem der henter, og sygemelding.

Læsning sker i den almindelige hentning (`fetch`, kaldt fra fetch_family) og lander i family.json under `presence`:
én post pr. barn, der har Komme/Gå. Børn uden modulet (fx et skolebarn uden SFO) springes stille over og dukker op
af sig selv, når institutionen slår det til.

Skrivning sker kun fra serveren (`Writer`), kun når `[presence] write_enabled = true`, og kun for den, der har låst op
med sin egen skrivekode (se server.py). Hver ændring læses tilbage fra Aula, før appen siger "gemt", og skrives i en
ændringslog (`presence_log.json` i secrets/). Ændringer af tider kan fortrydes; sygemeldinger kan ikke (brugerens
beslutning) – en sygemelding går samtidig til skolen via Tabulex (tabulex.py, se server.py `/api/presence/sick`).

Komme/gå-data sendes aldrig til sprogmodellen (brugerens beslutning): briefing.build_digest vælger sine felter ét for
ét og tager ikke `presence` med.

Ikke understøttet af Aula/pakken: sygemelding frem i tid, oprettelse af ferie eller fri (ferie vises kun).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import secrets
from pathlib import Path

log = logging.getLogger("familieplanner.presence")

LOG_KEEP = 500                     # så mange ændringer gemmes i loggen
COMMENT_MAX = 250
REPEATS = ("never", "weekly", "every_2_weeks")
REPEAT_TEXT = {"never": "Kun denne dag", "weekly": "Hver uge", "every_2_weeks": "Hver anden uge"}
# Aulas afhentningsformer (ActivityType). 4 (afleveringstid) bruges ikke som afhentning.
ACTIVITY_TEXT = {0: "Hentes af", 1: "Går selv", 2: "Sendes hjem", 3: "Går hjem med"}
NEEDS_NAME = {0, 3}
STATUS_TEXT = {"not_present": "Ikke kommet", "sick": "Syg", "reported_absent": "Meldt fravær", "present": "Til stede",
               "fieldtrip": "På tur", "sleeping": "Sover", "spare_time_activity": "Til fritidsaktivitet",
               "physical_placement": "Fysisk placering", "checked_out": "Gået hjem", "not_arrived": "Ikke kommet"}
NOT_UNDOABLE = ("sick", "unsick", "sick_school")       # sygemeldinger (Komme/Gå og skolen) – "unsick" fra ældre logs
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class PresenceError(Exception):
    """En ændring, der ikke kan laves. `message` er dansk og kan vises direkte i appen."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


# ---------------------------------------------------------------- indstillinger
def settings(cfg: dict) -> dict:
    p = cfg.get("presence", {})
    return {"enabled": bool(p.get("enabled", True)), "write_enabled": bool(p.get("write_enabled", False)),
            "days_ahead": max(1, min(28, int(p.get("days_ahead", 7)))),
            "edit_days_ahead": max(1, min(180, int(p.get("edit_days_ahead", 60)))),
            "unlock_minutes": float(p.get("unlock_minutes", 10)),
            "lock_wait_seconds": float(p.get("lock_wait_seconds", 60))}


def log_path(cfg: dict) -> Path:
    """Samme mappe som Aula-nøglerne (secrets/) – aldrig under web/."""
    return Path(cfg.get("aula", {}).get("token_file", "secrets/aula_tokens.json")).parent / "presence_log.json"


# ---------------------------------------------------------------- små hjælpere
def hhmm(raw) -> str | None:
    """'08:00', '08:00:00' eller '2026-10-09T08:00:00+02:00' → '08:00'. Alt andet → None."""
    if not raw or not isinstance(raw, str):
        return None
    t = raw.split("T")[-1][:5]
    return t if _TIME.match(t) else None


def _enum_name(value) -> str | None:
    name = getattr(value, "name", None)
    return name.lower() if name else None


def _date(raw) -> str | None:
    return raw[:10] if isinstance(raw, str) and len(raw) >= 10 else None


def _enum_value(value):
    return getattr(value, "value", value)


def _repeat(raw) -> str:
    r = str(raw or "never").lower().replace("every2weeks", "every_2_weeks")
    return r if r in REPEATS else "never"


def _permission(config, module: str) -> bool:
    """Må forældre ændre dette? Ukendt (Aula sagde intet) behandles som ja – Aula afviser selv kaldet, hvis ikke."""
    if config is None:
        return True
    try:
        from aula.models.presence import PresenceModule
        perm = config.permission(PresenceModule(module))
    except Exception:  # noqa: BLE001 – en ukendt opbygning må ikke vælte hentningen
        return True
    return True if perm is None else _enum_name(perm) == "editable"


def _activities(config) -> list[int]:
    """De afhentningsformer, institutionen tilbyder (Aulas flag pr. barn; ukendt = tilladt)."""
    out = []
    if config is None or config.pickup is not False:
        out.append(0)
    if config is None or config.self_decider is not False:
        out.append(1)
    out.append(2)
    if config is None or config.go_home_with is not False:
        out.append(3)
    return out


def day_entry(t) -> dict:
    """En DayTemplate fra Aula → det, appen gemmer for en dag."""
    act = _enum_value(t.activity_type)
    sta = t.spare_time_activity
    return {"date": _date(t.by_date), "templateId": t.id, "entry": hhmm(t.entry_time), "exit": hhmm(t.exit_time),
            "activity": act if isinstance(act, int) else None, "exitWith": t.exit_with or None, "comment": t.comment or None,
            "repeat": _repeat(t.repeat_pattern), "repeatFrom": _date(t.repeat_from_date), "repeatTo": _date(t.repeat_to_date),
            "vacation": bool(t.is_on_vacation),
            "spareTime": ({"start": hhmm(sta.start_time), "end": hhmm(sta.end_time), "comment": sta.comment or None} if sta else None)}


def today_entry(ov, date: dt.date) -> dict:
    status = _enum_name(ov.status) if ov is not None else None
    loc = getattr(ov, "location", None) if ov is not None else None
    return {"date": date.isoformat(), "status": status, "statusText": STATUS_TEXT.get(status or "", None),
            "checkIn": hhmm(getattr(ov, "check_in_time", None)), "checkOut": hhmm(getattr(ov, "check_out_time", None)),
            "entry": hhmm(getattr(ov, "entry_time", None)), "exit": hhmm(getattr(ov, "exit_time", None)),
            "exitWith": getattr(ov, "exit_with", None) or None, "comment": getattr(ov, "comment", None) or None,
            "location": (getattr(loc, "name", None) or None) if loc else None}


def pickup_names(rows) -> list[str]:
    """Navnene, Aula vil have i exitWith – med relation i parentes, præcis som portalen sender dem."""
    out: list[str] = []
    for r in rows or []:
        for p in getattr(r, "persons", []) or []:
            label = f"{p.name} ({p.relation})" if p.relation else p.name
            if label and label not in out:
                out.append(label)
    return out


# ---------------------------------------------------------------- læsning (i den almindelige hentning)
async def _safe(make, what: str, name: str):
    """make() bygger kaldet – inde i try, så også en manglende metode (ældre pakke) fanges her."""
    try:
        return await make()
    except Exception as e:  # noqa: BLE001 – ét barn eller én slags data må ikke vælte resten
        log.info("Komme/Gå: %s for %s kunne ikke hentes (%s)", what, name, type(e).__name__)
        return None


async def fetch_child(client, child_id: int, person: str, today: dt.date, days_ahead: int, config=None) -> dict | None:
    """Alt om ét barn. None = barnet har ikke Komme/Gå (eller Aula svarede intet)."""
    ov = await _safe(lambda: client.get_daily_overview(child_id), "dagens status", person)
    templates = await _safe(lambda: client.get_presence_templates([child_id], today, today + dt.timedelta(days=days_ahead)),
                            "planlagte tider", person)
    if ov is None and not templates:
        return None
    if config is None:
        configs = await _safe(lambda: client.get_presence_configuration([child_id]), "opsætning", person) or []
        config = next((c for c in configs if c.child_id in (child_id, None)), None)
    pickups = await _safe(lambda: client.get_pickup_responsibles([child_id]), "hvem der må hente", person)
    vacations = await _safe(lambda: client.get_vacation_registrations([child_id]), "ferie", person) or []
    days: dict[str, dict] = {}
    for wt in templates or []:
        ip = getattr(wt, "institution_profile", None)
        if ip is not None and getattr(ip, "id", child_id) != child_id:
            continue
        for t in wt.day_templates:
            d = day_entry(t)
            if d["date"]:
                days[d["date"]] = d
    return {
        "person": person,
        "institution": getattr(config, "institution_name", None) if config else None,
        "today": today_entry(ov, today),
        "days": [days[k] for k in sorted(days)],
        "pickup": pickup_names(pickups),
        "vacations": [{"title": v.title or "Ferie", "start": _date(v.start_date), "end": _date(v.end_date),
                       "missingAnswer": bool(v.is_missing_answer), "deadline": _date(v.response_deadline)} for v in vacations],
        "can": {"sick": _permission(config, "report_sick"), "times": _permission(config, "pickup_times"),
                "pickupType": _permission(config, "pickup_type")},
        "activities": _activities(config),
    }


async def fetch(client, profile, owner: dict[int, str], today: dt.date, days_ahead: int = 7) -> list[dict]:
    """Komme/Gå for alle børn i profilen, der matcher en person i config. Rækkefølgen følger Aulas."""
    ids = [c.id for c in profile.children if c.id in owner]
    configs = {}
    if ids:
        for c in await _safe(lambda: client.get_presence_configuration(ids), "opsætning", "alle børn") or []:
            if c.child_id is not None:
                configs[c.child_id] = c
    out = []
    for cid in ids:
        entry = await fetch_child(client, cid, owner[cid], today, days_ahead, configs.get(cid))
        if entry:
            out.append(entry)
    log.info("Komme/Gå: %d af %d børn har Komme/Gå", len(out), len(ids))
    return out


# ---------------------------------------------------------------- ændringsloggen
class ChangeLog:
    """Hvem ændrede hvad og hvornår – med værdien før, så en ændring kan fortrydes. Ligger i secrets/ (0600)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, rows: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows[-LOG_KEEP:], ensure_ascii=False, indent=1), "utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    def add(self, row: dict) -> dict:
        rows = self.load()
        row = {"id": secrets.token_hex(6), **row}
        rows.append(row)
        self._save(rows)
        return row

    def mark_undone(self, row_id: str, by: str, at: str) -> None:
        rows = self.load()
        for r in rows:
            if r.get("id") == row_id:
                r["undone"] = {"by": by, "at": at}
        self._save(rows)

    def last_undoable(self, person: str, today: dt.date) -> dict | None:
        """Den seneste ændring af tider for barnet, der ikke er fortrudt og ikke handler om en dag, der er gået.
        Sygemeldinger kan aldrig fortrydes (brugerens beslutning) og springes over – de rører ikke dagens tider."""
        for r in reversed(self.load()):
            if r.get("person") != person or r.get("action") in ("undo", *NOT_UNDOABLE):
                continue
            if r.get("undone"):
                return None                        # kun den seneste kan fortrydes – ellers bliver rækkefølgen forkert
            if (r.get("date") or "") < today.isoformat():
                return None
            return r
        return None

    def recent(self, person: str | None = None, limit: int = 10) -> list[dict]:
        rows = [r for r in self.load() if person is None or r.get("person") == person]
        return list(reversed(rows))[:limit]


# ---------------------------------------------------------------- skrivning
def validate_day(body: dict, child: dict, today: dt.date, edit_days_ahead: int) -> dict:
    """Tjek en ændring af en dag, før den sendes til Aula. Returnerer de rensede værdier."""
    try:
        date = dt.date.fromisoformat(str(body.get("date", "")))
    except ValueError:
        raise PresenceError("Ugyldig dato.") from None
    if date < today:
        raise PresenceError("Dagen er gået – den kan ikke ændres.")
    if date > today + dt.timedelta(days=edit_days_ahead):
        raise PresenceError(f"Du kan højst ændre {edit_days_ahead} dage frem.")
    entry, exit_ = str(body.get("entry", "")), str(body.get("exit", ""))
    if not _TIME.match(entry) or not _TIME.match(exit_):
        raise PresenceError("Skriv tiderne som tt:mm, fx 08:00.")
    if entry >= exit_:
        raise PresenceError("Gåtiden skal ligge efter kommetiden.")
    try:
        activity = int(body.get("activity", 0))
    except (TypeError, ValueError):
        raise PresenceError("Ukendt afhentning.") from None
    if activity not in ACTIVITY_TEXT or activity not in child.get("activities", list(ACTIVITY_TEXT)):
        raise PresenceError("Institutionen tilbyder ikke den afhentning.")
    exit_with = (str(body.get("exitWith") or "").strip()) or None
    if activity in NEEDS_NAME:
        if not exit_with:
            raise PresenceError("Vælg, hvem der henter.")
        if exit_with not in child.get("pickup", []):
            raise PresenceError("Personen står ikke på listen over dem, der må hente.")
    else:
        exit_with = None
    comment = str(body.get("comment") or "").strip()
    if len(comment) > COMMENT_MAX:
        raise PresenceError(f"Kommentaren må højst være {COMMENT_MAX} tegn.")
    repeat = str(body.get("repeat") or "never")
    if repeat not in REPEATS:
        raise PresenceError("Ukendt gentagelse.")
    return {"date": date, "entry": entry, "exit": exit_, "activity": activity, "exitWith": exit_with,
            "comment": comment or None, "repeat": repeat}


def _same_day(a: dict | None, want: dict) -> bool:
    if not a:
        return False
    return (a.get("entry") == want["entry"] and a.get("exit") == want["exit"] and a.get("activity") == want["activity"]
            and (a.get("exitWith") or None) == (want.get("exitWith") or None)
            and (a.get("comment") or None) == (want.get("comment") or None))


def _day_of(child: dict | None, date: str) -> dict | None:
    return next((d for d in (child or {}).get("days", []) if d.get("date") == date), None)


def describe_day(d: dict | None) -> str:
    """'08:00–15:30 · Hentes af Monica (Mor)' – til loggen og bekræftelsen."""
    if not d or not d.get("entry"):
        return "ingen tider"
    act = ACTIVITY_TEXT.get(d.get("activity"), "")
    who = f" {d['exitWith']}" if d.get("exitWith") else ""
    return f"{d['entry']}–{d['exit']}" + (f" · {act}{who}" if act else "")


class Writer:
    """Alle skrivninger til Komme/Gå. `client` er en åben Aula-klient; tiden er et argument (som resten af koden)."""

    def __init__(self, client, changelog: ChangeLog, days_ahead: int = 7, edit_days_ahead: int = 60):
        self.client, self.log, self.days_ahead, self.edit_days_ahead = client, changelog, days_ahead, edit_days_ahead

    async def child_id(self, person: str, people) -> int:
        profile = await self.client.get_profile()
        for c in profile.children:
            if people.by_aula_name(c.name) == person:
                return c.id
        raise PresenceError("Barnet findes ikke i Aula.", 404)

    async def reread(self, child_id: int, person: str, today: dt.date) -> dict | None:
        return await fetch_child(self.client, child_id, person, today, self.days_ahead)

    async def set_sick(self, child_id: int, person: str, by: str, now: dt.datetime, before: dict | None) -> tuple[dict, dict]:
        """Sygemeld barnet for i dag. Returnerer (barnets nye data, logrækken). Kan ikke fortrydes i appen
        (brugerens beslutning) – en fejlagtig sygemelding rettes i Aula."""
        from aula.models.presence import PresenceState
        today = now.date()
        if before is not None and not before.get("can", {}).get("sick", True):
            raise PresenceError("Institutionen tillader ikke sygemelding fra forældre.", 403)
        was = ((before or {}).get("today") or {}).get("status")
        target = PresenceState.SICK
        await self.client.update_presence_status([child_id], target)
        fresh = await self.reread(child_id, person, today)
        got = ((fresh or {}).get("today") or {}).get("status")
        if got != target.name.lower():
            raise PresenceError("Aula modtog ændringen, men viser den ikke endnu. Tjek i Aula, før du stoler på den.", 502)
        row = self.log.add({"at": now.isoformat(timespec="seconds"), "by": by, "person": person, "date": today.isoformat(),
                            "action": "sick", "before": {"status": was}, "after": {"status": got}, "text": "Sygemeldt i Komme/Gå"})
        return fresh, row

    async def set_day(self, child_id: int, person: str, body: dict, child: dict, by: str, now: dt.datetime,
                      action: str = "day") -> tuple[dict, dict]:
        """Ændr tider/afhentning/kommentar for en dag (evt. som gentagelse fra den dag)."""
        if not child.get("can", {}).get("times", True):
            raise PresenceError("Institutionen tillader ikke, at forældre ændrer tider.", 403)
        v = validate_day(body, child, now.date(), self.edit_days_ahead)
        day = v["date"].isoformat()
        # Hent dagens nuværende skabelon direkte fra Aula (ikke fra family.json, der kan være op til et kvarter gammel)
        cur = None
        for wt in await self.client.get_presence_templates([child_id], v["date"], v["date"]) or []:
            for t in wt.day_templates:
                if _date(t.by_date) == day:
                    cur = day_entry(t)
        from aula.models.presence import ActivityType
        await self.client.update_presence_template(
            child_id, v["date"], entry_time=v["entry"], exit_time=v["exit"], activity_type=ActivityType(v["activity"]),
            exit_with=v["exitWith"], comment=v["comment"] or "", template_id=(cur or {}).get("templateId"),
            repeat_pattern={"never": "Never", "weekly": "Weekly", "every_2_weeks": "Every2Weeks"}[v["repeat"]])
        fresh = await fetch_child(self.client, child_id, person, now.date(), max(self.days_ahead, (v["date"] - now.date()).days))
        after = _day_of(fresh, day)
        if not _same_day(after, v):
            raise PresenceError("Aula modtog ændringen, men viser den ikke endnu. Tjek i Aula, før du stoler på den.", 502)
        text = describe_day(after) + ("" if v["repeat"] == "never" else f" · {REPEAT_TEXT[v['repeat']].lower()} fra {day}")
        row = self.log.add({"at": now.isoformat(timespec="seconds"), "by": by, "person": person, "date": day, "action": action,
                            "before": cur, "after": {**after, "repeat": v["repeat"]}, "text": text})
        return fresh, row

    async def undo(self, child_id: int, person: str, child: dict, by: str, now: dt.datetime) -> tuple[dict, dict]:
        """Fortryd den seneste ændring for barnet – ved at skrive værdien fra før tilbage."""
        row = self.log.last_undoable(person, now.date())
        if not row:
            raise PresenceError("Der er ingen ændring at fortryde.", 404)
        await self._still_ours(child_id, person, row, now.date())
        before = row.get("before")
        if not before or not before.get("entry"):
            raise PresenceError("Dagen havde ingen tider før ændringen, så den kan ikke rulles tilbage herfra. Ret den i stedet.")
        act = before.get("activity")
        body = {**before, "repeat": before.get("repeat") or "never",
                "activity": act if act is not None else (0 if before.get("exitWith") else 2)}
        fresh, _ = await self.set_day(child_id, person, body, {**child, "pickup": list({*child.get("pickup", []),
                                                                 *([before["exitWith"]] if before.get("exitWith") else [])})},
                                      by, now, action="undo")
        self.log.mark_undone(row["id"], by, now.isoformat(timespec="seconds"))
        return fresh, row

    async def _still_ours(self, child_id: int, person: str, row: dict, today: dt.date) -> None:
        """Fortryd kun, hvis Aula stadig viser det, vi selv skrev – ellers overskrives en ændring, en anden har lavet siden."""
        changed = PresenceError("Dagen er ændret siden (i Aula eller af en anden), så den kan ikke fortrydes herfra. Ret den i stedet.", 409)
        after = row.get("after") or {}
        date = dt.date.fromisoformat(row["date"])
        cur = None
        for wt in await self.client.get_presence_templates([child_id], date, date) or []:
            for t in wt.day_templates:
                if _date(t.by_date) == row["date"]:
                    cur = day_entry(t)
        if not _same_day(cur, after):
            raise changed


def patch_family(path: Path, child: dict) -> None:
    """Skriv barnets nye Komme/Gå-data i family.json med det samme, så alle enheder ser ændringen."""
    path = Path(path)
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return
    rows = list(data.get("presence") or [])
    idx = next((i for i, c in enumerate(rows) if c.get("person") == child["person"]), None)
    if idx is None:
        rows.append(child)
    else:
        rows[idx] = child
    data["presence"] = rows
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)
