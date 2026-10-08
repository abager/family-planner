#!/usr/bin/env python3
"""Familieassistent: dagens/ugens overblik skrevet af en sprogmodel (ai.py) – med appens egne regler som reserve.

Princippet: jeres egne regler (homework.py, messages.py) står for fakta – datoer, frister,
hvem der har hvad. Sprogmodellen får kun et færdigt, renset uddrag og skal formulere og
prioritere. Private samtaler sendes ALDRIG med. Kan sprogmodellen ikke bruges (ingen nøgle,
kvote brugt, fejl, ugyldigt svar), bruges det seneste AI-overblik for samme periode – markeret
som måske forældet – eller ellers offline_briefing.py. Appen viser så "AI ikke tilgængelig".

  python briefing.py --offline          # overblik uden sprogmodel – intet forlader maskinen
  python briefing.py --dry-run          # vis hvad der ville blive sendt, uden at gemme eller sende
  python briefing.py                    # lav overblik for i dag
  python briefing.py --week             # ugens overblik
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import re
import sys
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

import ai
import private as private_mod

TZ = ZoneInfo("Europe/Copenhagen")
log = logging.getLogger("familieplanner.briefing")
DAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]

# CPR-numre, telefonnumre og mailadresser har assistenten ikke brug for.
# CPR: ddmmåå-xxxx, ddmmåå xxxx eller ddmmååxxxx med gyldig dag og måned (så fx et ordrenummer ikke rammes for tit).
_CPR = r"\b(?:0[1-9]|[12]\d|3[01])(?:0[1-9]|1[0-2])\d{2}[\s-]?\d{4}\b"
_PII = re.compile(_CPR + r"|\b(?:\+?45[\s-]?)?(?:\d[\s-]?){8}\b|[\w.+-]+@[\w-]+\.[\w.]+")


def _scrub(text: str | None, limit: int) -> str:
    t = re.sub(r"\s+", " ", _PII.sub("[fjernet]", text or "")).strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _date(s: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def target_window(mode: str, now: dt.datetime, evening_hour: int = 18) -> tuple[dt.date, dt.date, str]:
    """(første dag, sidste dag, overskrift). Dagsoverblikket handler om i dag – men efter kl. `evening_hour` om i morgen."""
    if mode == "week":
        start = now.date() if now.weekday() < 5 or now.hour < 16 else now.date() + dt.timedelta(days=1)
        if start.weekday() >= 5:                      # weekend → kommende uge
            start += dt.timedelta(days=7 - start.weekday())
        end = start + dt.timedelta(days=6 - start.weekday())
        return start, end, f"Uge {start.isocalendar()[1]}"
    day = now.date() + dt.timedelta(days=1 if now.hour >= evening_hour else 0)
    when = "i morgen" if now.hour >= evening_hour else "i dag"
    return day, day, f"{when}, {DAYS[day.weekday()]} {day.day}/{day.month}"


# ---------------------------------------------------------------- uddrag
def build_digest(data: dict, start: dt.date, end: dt.date, now: dt.datetime) -> dict:
    """Det, der sendes til sprogmodellen. Alt har et kilde-id, så svaret kan henvise til det."""
    people = {p["id"]: p for p in data.get("people", [])}
    name = lambda ids: [people[i]["name"] for i in ids if i in people] if "family" not in ids else ["hele familien"]
    in_window = lambda d: d is not None and start <= d <= end
    refs: dict[str, str] = {}

    def ref(prefix: str, key: str, label: str) -> str:
        rid = f"{prefix}{len([k for k in refs if k.startswith(prefix)]) + 1}"
        refs[rid] = label
        return rid

    events, schedule = [], []
    for e in sorted(data.get("events", []), key=lambda x: x["start"]):
        s, en = _date(e["start"]), _date(e["end"])
        if not s or not (s <= end and (en or s) >= start):
            continue
        who = name(e.get("people", []))
        if e.get("lessons"):
            ls = e["lessons"]
            entry = {
                "dato": e["start"][:10], "hvem": who,
                "fra": ls[0]["start"], "til": max(l.get("end") or l["start"] for l in ls),
                "lektioner": [f"{l['start']} {l['title']}" + (" (vikar)" if l.get("substitute") else "") for l in ls],
            }
            if any(l.get("substitute") for l in ls):
                entry["vikar"] = [{"fag": l["title"], "tid": l["start"]} for l in ls if l.get("substitute")]
            if any(l.get("note") for l in ls):
                entry["noter"] = [{"fag": l["title"], "tid": l["start"], "tekst": _scrub(l["note"], 240)} for l in ls if l.get("note")]
            schedule.append(entry)
            continue
        events.append({
            "id": ref("A", e["id"], e["title"]),
            "dato": e["start"][:10], "hele_dagen": e.get("allDay", False),
            "tid": None if e.get("allDay") else f"{e['start'][11:16]}–{e['end'][11:16]}",
            "titel": _scrub(e["title"], 120), "hvem": who, "sted": _scrub(e.get("location"), 80) or None,
            "kilde": {"aula": "Aula-kalender", "google": "familiekalender", "besked": "udledt af Aula-besked"}.get(e.get("source"), e.get("source")),
            "note": _scrub(e.get("notes"), 200) or None,
        })

    tasks = []
    for t in data.get("tasks", []):
        due, frm = _date(t.get("due")), _date(t.get("from"))
        active = (in_window(due)
                  or (t.get("recurring") == "daily" and frm and frm <= end and start <= frm + dt.timedelta(days=6))
                  or (t.get("openEnded") and frm and frm <= end and start <= frm + dt.timedelta(days=7))
                  or (frm and frm <= end and due and due > end))          # lang aflevering, der allerede er givet
        if not active:
            continue
        who = t.get("people") or [t.get("person")]
        tasks.append({
            "id": ref("O", t["id"], t["title"]),
            "type": {"lektie": "lektie", "husk": "husk / medbring", "handling": "skal gøres af forældre"}.get(t.get("kind"), t.get("kind")),
            "titel": _scrub(t["title"], 160), "hvem": name(who),
            "frist": None if t.get("openEnded") else t.get("due"),
            "gentages": "hver dag" if t.get("recurring") == "daily" else None,
            "sikkerhed": t.get("confidence", "høj"),
            "kilde": {"meebook": "Ugeplan (Meebook)", "besked": "Aula-besked"}.get(t.get("source"), "Aula"),
            "fra_besked": (_scrub(t.get("subject") or (t.get("text") if t.get("kind") != "husk" else None), 120)
                           if t.get("source") == "besked" else None) or None,
        })

    plan = []
    for w in data.get("weekplan", []):
        if in_window(_date(w.get("date"))) and w.get("category") in ("lektie", "husk", "info"):
            plan.append({"id": ref("U", w["id"], w.get("subject") or ""), "dato": w["date"],
                         "hvem": name([w["person"]]), "fag": w.get("subject"),
                         "kategori": w["category"], "tekst": _scrub(w.get("text"), 300),
                         "info": _scrub(w.get("info"), 160) or None})

    # Beskeder og opslag fra de seneste dage – ALDRIG private samtaler
    since = now.date() - dt.timedelta(days=3)
    messages = []
    for m in data.get("messages", []):
        if m.get("private") or m.get("category") in ("samtale", "tom"):
            continue
        if (_date(m.get("timestamp")) or dt.date.min) < since:
            continue
        messages.append({"id": ref("B", m["id"], m.get("subject") or ""), "dato": (m.get("timestamp") or "")[:10],
                         "emne": _scrub(m.get("subject"), 120), "fra": _scrub(m.get("from"), 60),
                         "hvem": name(m.get("people", [])), "kategori": m.get("category"),
                         "tekst": _scrub(m.get("text"), 500)})
    posts = []
    for p in data.get("posts", []):
        if (_date(p.get("timestamp")) or dt.date.min) >= since:
            posts.append({"id": ref("P", p["id"], p.get("title") or ""), "dato": (p.get("timestamp") or "")[:10],
                          "titel": _scrub(p.get("title"), 120), "hvem": name(p.get("people", [])),
                          "vigtigt": bool(p.get("important")), "tekst": _scrub(p.get("text"), 300)})

    # Vejret (groft, fra weather.py via family.json) for de dage i perioden, MET Norway dækker
    vejr = []
    for d in ((data.get("weather") or {}).get("dage") or []):
        dd = _date(d.get("dato"))
        if dd and start <= dd <= end:
            vejr.append({"id": ref("V", d["dato"], "Vejr (MET Norway)"), **{k: d[k] for k in
                         ("dato", "ugedag", "min", "max", "regn", "regn_hvornaar", "himmel", "vind", "frost", "raad") if k in d}})

    family = [{"navn": p["name"], "rolle": "voksen" if p.get("role") == "adult" else "barn", "note": p.get("note")}
              for p in people.values()]
    return {
        "nu": now.strftime("%Y-%m-%d %H:%M"), "ugedag_nu": DAYS[now.weekday()],
        "periode": {"fra": start.isoformat(), "til": end.isoformat()},
        "familie": family, "skema": schedule, "aftaler": events, "opgaver": tasks,
        "ugeplan": plan, "nye_beskeder": messages, "nye_opslag": posts, "vejr": vejr,
        "_refs": refs,
    }


# ---------------------------------------------------------------- prompt
SYSTEM = """Du er familiens assistent for en dansk familie. Du skriver en varm, personlig fortælling om dagen
(eller ugen) til forældrene ud fra de data, du får. Data er allerede renset og struktureret: stol på datoer, tider,
steder, frister og hvem-feltet præcis som de står.

Regler:
- Brug KUN oplysninger fra data. Gæt aldrig tider, steder, aftaler eller hvem der henter/bringer – nævn det kun,
  hvis det står i data eller i familiens egne regler.
- Kronologisk: fra morgen til aften. Start med det, der skal med ud ad døren, så skoledagen, så eftermiddag
  (afhentning, fritid, aftaler) og til sidst aftenen. I ugens fortælling: dag for dag i rækkefølge; stille dage kan
  samles i én sætning.
- Skriv korrekt, naturligt dansk (rigsdansk) – aldrig norske eller svenske ord eller stavemåder (fx "på tværs",
  ikke "på tvers"). Brug almindelige hverdagsord ("vejrudsigten", ikke opfundne vendinger). Korte, enkle sætninger.
- Tal direkte til forældrene: "I skal huske …", "Monica, du …" (kun når data siger, hvem). Brug fornavne, aldrig
  "barnet". Varm, rolig tone uden floskler, udråbstegn eller emojis.
- Nævn IKKE det normale skoleskema (fag og tider) – det står allerede i appen. Nævn kun afvigelser fra "skema"
  (vikar, noter fra læreren).
- Væv vejret ind, hvor det betyder noget (regntøj om morgenen, koldt til fodbold). Gæt aldrig vejret.
- Opgaver med sikkerhed "middel" er usikre – skriv "muligvis".
- Tider skrives som "kl. 16.30". Datoer i ord ("fredag", "den 23. oktober") – aldrig 23/10 eller 23.10.
- Skriv aldrig "i dag", "i morgen" eller "i går" – fortællingen læses på forskellige tidspunkter. Brug ugedagen.
- Længde: dagens fortælling ca. 150 ord, ugens højst ca. 220 ord. 2–5 korte afsnit (fx morgen, eftermiddag, aften).
- Ren tekst: ingen markdown, punkttegn, overskrifter eller fed skrift.
- "kilder" skal indeholde kilde-id'erne (fx "A3", "O1", "V1") for alt, du nævner.

Svar KUN med JSON (ingen markdown, ingen forklaring) i dette format, hvor hvert afsnit er en tekst i anførselstegn:
{
  "fortaelling": ["første afsnit", "andet afsnit"],
  "kilder": ["A1", "O2"]
}
Er der intet særligt, så skriv kort og venligt, at det er en stille dag."""


# Svarformatet til sprogmodellen (Gemini overholder det; for Claude styrer prompten). validate_narrative tjekker resten.
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "fortaelling": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "afsnittene, ren tekst"},
        "kilder": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "kilde-id'er, fx A1"},
    },
    "required": ["fortaelling", "kilder"],
    "propertyOrdering": ["fortaelling", "kilder"],
}


def build_messages(digest: dict, rules: str, headline: str, mode: str) -> list[dict]:
    """Alt fra Aula, Google Kalender og MET Norway (yr.no) til fortællingen – undtagen det normale skoleskema (det står i appen)."""
    payload = {k: v for k, v in digest.items() if not k.startswith("_")}
    # Kun afvigelser fra skemaet: vikar og noter fra læreren. Fag, lektionstider og mødetider sendes ikke.
    payload["skema"] = [{k: e[k] for k in ("dato", "hvem", "vikar", "noter") if k in e}
                        for e in digest.get("skema", []) if e.get("vikar") or e.get("noter")]
    task = ("Skriv ugens fortælling, dag for dag." if mode == "week" else f"Skriv fortællingen for {headline}.")
    content = f"{task}\n\nFamiliens egne regler:\n{rules.strip() or '(ingen)'}\n\nData:\n{json.dumps(payload, ensure_ascii=False, indent=1)}"
    return [{"role": "user", "content": content}]


SECTION_TITLES = {"Vejr", "Husk", "Skal gøres", "Praktisk info", "Kommende frister"}


_TIME = re.compile(r"\b(?:kl\.?|klokken)\s*(\d{1,2})(?:[.:](\d{2}))?\b|\b(\d{1,2})[.:](\d{2})\b", re.I)
# Sluttid i et interval, der hænger på et klokkeslæt: "kl. 8-13", "8.00–13", "fra kl. 8 til 13". Kun lige efter et
# genkendt klokkeslæt – så "side 12-20" og "13-14" alene stadig ikke tæller som tider.
_RANGE_END = re.compile(r"\s*(?:-|–|—|til)\s*(?:kl\.?\s*|klokken\s*)?(\d{1,2})(?:[.:](\d{2}))?\b", re.I)
_FORMATTING = re.compile(r"(^\s*([-*•#>]|\d+\.)\s)|\*\*|__|`", re.M)


def _times(text: str) -> set[str]:
    """Klokkeslæt i teksten som "HH:MM" – også sluttiden i et interval som "kl. 8-13"."""
    out = set()

    def add(h: str, mi: str | None) -> None:
        if int(h) <= 23 and int(mi or 0) <= 59:
            out.add(f"{int(h):02d}:{mi or '00'}")

    text = text or ""
    for m in _TIME.finditer(text):
        add(*((m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))))
        end = _RANGE_END.match(text, m.end())
        if end:
            add(end.group(1), end.group(2))
    return out


def validate_narrative(result: dict, refs: dict[str, str], data_text: str, mode: str = "day") -> None:
    """Fortællingen skal have en form, appen kan vise, pege på rigtige kilder og kun bruge klokkeslæt fra data.
    Rejser ValueError – så bruges reserven."""
    f = result.get("fortaelling")
    if isinstance(f, str):
        f = [p for p in re.split(r"\n\s*\n", f) if p.strip()]
    if not isinstance(f, list) or not f or not all(isinstance(p, str) and p.strip() for p in f):
        raise ValueError("mangler 'fortaelling'")
    f = [re.sub(r"\s+", " ", p).strip() for p in f]
    if len(f) > 8:
        raise ValueError("for mange afsnit")
    text = "\n".join(f)
    words = len(text.split())
    if words < 8 or words > (330 if mode == "week" else 260):
        raise ValueError(f"forkert længde ({words} ord)")
    if _FORMATTING.search(text):
        raise ValueError("indeholder formatering")
    unknown = _times(text) - _times(data_text)
    if unknown:
        raise ValueError(f"klokkeslæt, der ikke står i data: {', '.join(sorted(unknown))}")
    kilder = [r for r in (result.get("kilder") or []) if isinstance(r, str) and r in refs]
    if refs and not kilder:
        raise ValueError("ingen gyldige kilder")
    result.pop("oplaesning", None)                # oplæsning findes ikke længere – svarer modellen med den alligevel, droppes den
    result["fortaelling"], result["kilder"] = f, kilder
    result.pop("afsnit", None)


def validate_result(result: dict, refs: dict[str, str]) -> None:
    """Svaret skal have den form, appen kan vise. Punkter uden en gyldig kilde fjernes (de kan være opdigtede).
    Rejser ValueError, hvis formen er forkert – så bruges reserven."""
    secs = result.get("afsnit")
    if not isinstance(secs, list):
        raise ValueError("mangler 'afsnit'")
    kept = []
    for sec in secs:
        if not isinstance(sec, dict) or sec.get("titel") not in SECTION_TITLES or not isinstance(sec.get("punkter"), list):
            raise ValueError(f"ugyldigt afsnit: {str(sec)[:80]}")
        points = []
        for p in sec["punkter"]:
            if not isinstance(p, dict) or not isinstance(p.get("tekst"), str) or not p["tekst"].strip():
                raise ValueError(f"ugyldigt punkt: {str(p)[:80]}")
            if not isinstance(p.get("hvem", []), list) or not all(isinstance(h, str) for h in p.get("hvem", [])):
                raise ValueError("'hvem' skal være en liste af navne")
            good = [r for r in (p.get("refs") or []) if isinstance(r, str) and r in refs]
            if not good:
                log.info("AI-punkt uden gyldig kilde fjernet: %s", p["tekst"][:60])
                continue
            points.append({**p, "refs": good})
        if points:
            kept.append({**sec, "punkter": points})
    result.pop("oplaesning", None)
    result["afsnit"] = kept


# ---------------------------------------------------------------- samlet kørsel
def assistant_mode(acfg: dict) -> str:
    """"ai" (sprogmodel via ai.py, reserve: offline), "offline" (kun egne regler), "off" (intet overblik).
    "claude" virker stadig og betyder "ai" med Claude som udbyder. Uden mode: ældre enabled = true betyder claude."""
    if acfg.get("mode") in ("offline", "ai", "claude", "off"):
        return acfg["mode"]
    return "claude" if acfg.get("enabled") else "offline"


def _read_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text("utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _write(path: Path, briefing: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(briefing, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)                             # atomisk, så appen aldrig læser en halv fil


def ai_client(cfg: dict) -> ai.Client:
    out_dir = Path(cfg.get("output", "web/family.json")).parent
    invalid = private_mod.store_path(cfg).parent / ai.INVALID_FILE   # i secrets/ ved siden af de private samtaler
    return ai.Client(ai.settings_from(cfg), state_dir=out_dir, invalid_path=invalid)


def why_unavailable(e: ai.AIUnavailable, client: ai.Client) -> str:
    """Til loggen: hvorfor AI ikke kunne bruges – og ved en pause, hvad den seneste fejl var."""
    text = ai.REASONS.get(e.reason, e.reason)
    st = client.status()
    if e.reason == "pause" and st.get("backoff_until"):
        text += " til kl. " + dt.datetime.fromisoformat(st["backoff_until"]).astimezone(TZ).strftime("%H.%M")
    elif e.detail:
        text += f" ({e.detail})"
    last = st.get("last_error") or {}
    if e.reason == "pause" and last:
        at = dt.datetime.fromisoformat(last["at"]).astimezone(TZ).strftime("%d.%m. kl. %H.%M") if last.get("at") else ""
        text += f"; seneste fejl {at}: {ai.REASONS.get(last.get('reason'), last.get('reason'))}"
        text += f" ({last['detail']})" if last.get("detail") else ""
    return text


def make_briefing(cfg: dict, data: dict, mode: str = "day", now: dt.datetime | None = None,
                  force: bool = False, dry_run: bool = False, provider: str | None = None,
                  client: ai.Client | None = None) -> dict | None:
    acfg = cfg.get("assistant", {})
    provider = provider or assistant_mode(acfg)
    if provider == "off":
        return None
    now = now or dt.datetime.now(TZ)
    start, end, headline = target_window(mode, now, int(cfg.get("display", {}).get("evening_hour", 18)))
    period = [start.isoformat(), end.isoformat()]
    digest = build_digest(data, start, end, now)
    out_path = Path(cfg.get("output", "web/family.json")).with_name(f"briefing{'_uge' if mode == 'week' else ''}.json")

    def offline(extra: dict | None = None, why: str = "") -> dict:
        from offline_briefing import offline_briefing
        result = offline_briefing(digest, mode, now)
        b = {"generated": now.isoformat(timespec="minutes"), "mode": mode, "method": "offline",
             "headline_label": headline, "period": period, **result, **(extra or {})}
        if dry_run:
            print(json.dumps(b, ensure_ascii=False, indent=1))
            return b
        _write(out_path, b)
        log.info("Skrev %s (uden sprogmodel%s)", out_path, f" – AI ikke tilgængelig: {why}" if extra else "")
        return b

    if provider == "offline":
        return offline()

    rules_path = Path(acfg.get("rules_file", "familie_regler.md"))
    rules = rules_path.read_text("utf-8") if rules_path.exists() else ""
    messages = build_messages(digest, rules, headline, mode)
    if dry_run:
        print(messages[0]["content"])
        return None

    fingerprint = hashlib.sha1((messages[0]["content"].split("Data:")[1].replace(digest["nu"], "") + rules).encode()).hexdigest()
    old = _read_json(out_path) if out_path.exists() else None
    old_ai = old if old and old.get("method") in ("ai", "claude") and old.get("period") == period else None
    if old_ai and not force:
        if old_ai.get("fingerprint") == fingerprint:
            if old_ai.pop("ai_stale", None):       # data er som da det blev skrevet: overblikket er ikke forældet
                _write(out_path, old_ai)
            log.info("Overblik uændret – springer over")
            return old_ai
        age = (now - dt.datetime.fromisoformat(old_ai["generated"])).total_seconds() / 60
        if age < acfg.get("min_minutes_between", 60) and not old_ai.get("ai_stale"):
            log.info("Overblik lavet for %d min siden – venter", age)
            return old_ai

    client = client or ai_client(cfg)
    data_text = messages[0]["content"].split("Data:", 1)[1] + "\n" + rules
    try:
        result = client.generate_json(SYSTEM, messages[0]["content"], schema=SCHEMA,
                                      validate=lambda r: validate_narrative(r, digest["_refs"], data_text, mode),
                                      cache_key="|".join([fingerprint, mode, headline, *period]))
    except ai.AIUnavailable as e:
        why = why_unavailable(e, client)
        prev = (old or {}).get("ai_stale") or (old or {}).get("ai_fallback") or {}
        flag = {"reason": e.reason, "since": prev.get("since") or now.isoformat(timespec="minutes")}
        if old_ai:                               # behold det seneste AI-overblik for perioden, men sig, at det måske er forældet
            old_ai["ai_stale"] = flag
            _write(out_path, old_ai)
            log.warning("AI ikke tilgængelig: %s – beholder overblikket fra %s", why, old_ai["generated"])
            return old_ai
        return offline({"ai_fallback": flag}, why)

    result["kilde_ids"] = result.pop("kilder", [])  # id'erne gemmes, så man kan se, hvad fortællingen bygger på
    s = client.s
    briefing = {"generated": now.isoformat(timespec="minutes"), "mode": mode, "method": "ai", "provider": s.provider,
                "model": s.model, "headline_label": headline, "period": period, "fingerprint": fingerprint, **result}
    _write(out_path, briefing)
    log.info("Skrev %s (%s)", out_path, s.provider)
    return briefing


def ai_status(cfg: dict) -> dict | None:
    """Til /api/status: hvordan det går med sprogmodellen. None, når overblikket ikke bruger en."""
    if assistant_mode(cfg.get("assistant", {})) not in ("ai", "claude"):
        return None
    return ai_client(cfg).status()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--week", action="store_true", help="ugens overblik i stedet for dagens")
    ap.add_argument("--dry-run", action="store_true", help="vis hvad der ville blive sendt – send intet")
    ap.add_argument("--force", action="store_true", help="lav nyt overblik selvom data er uændret")
    ap.add_argument("--offline", action="store_true", help="lav overblikket uden sprogmodel, uanset config")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    cfg = tomllib.loads(Path(args.config).read_text("utf-8"))
    data = json.loads(Path(cfg.get("output", "web/family.json")).read_text("utf-8"))
    make_briefing(cfg, data, "week" if args.week else "day", force=args.force, dry_run=args.dry_run,
                  provider="offline" if args.offline else None)


if __name__ == "__main__":
    sys.exit(main())
