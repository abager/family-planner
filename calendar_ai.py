"""Kalenderforslag fundet af AI i Aula-beskeder, opslag og ugeplaner – med reglerne i activities.py som reserve.

Samme form som activities.find_all(): (ændringsforslag, muligheder pr. kilde-id), så appen er uændret:
"Føj til kalender" vises kun på punkter med forslag og åbner formularen udfyldt; aflyst/udsat/flyttet vises
på punktet. Intet skrives i kalenderen uden et klik.

- Hvert punkt vurderes én gang: svaret gemmes under en hash af punktets indhold (ai.Client.cache_*).
- Nye punkter sendes samlet, højst BATCH pr. forespørgsel og MAX_REQUESTS pr. hentning.
- Første gang vurderes kun punkter fra de seneste FIRST_DAYS dage; ældre punkter bruger reglerne.
- Overblikket går forud: er der under RESERVE forespørgsler tilbage i dag, venter kalendertjekket til næste dag.
- Alt AI'en foreslår kontrolleres mod punktets egen tekst (dato, klokkeslæt, sted, hvem). Fejler det, droppes
  forslaget. Private samtaler sendes aldrig (activities.build_sources udelader dem), og teksten renses.
- Kan AI ikke bruges for et punkt, gælder reglernes resultat for netop det punkt.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re

import activities as A
import ai
from briefing import _scrub, _times

log = logging.getLogger("familieplanner.calendar_ai")

BATCH = 8
MAX_REQUESTS = 3
FIRST_DAYS = 14
RESERVE = 30
MAX_PER_ITEM = 3
WEEKDAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]

SYSTEM = """Du finder aftaler, der bør i familiens kalender, i beskeder, opslag og ugeplaner fra skolen (Aula).
Du får en liste af punkter. For hvert punkt svarer du med:
- "nye": konkrete aktiviteter med en bestemt dato, som familien skal møde op til eller planlægge efter
  (tur, forældremøde, motionsdag, fotografering, lukkedag, arrangement). IKKE lektier, afleveringsfrister,
  generel info, ting der allerede er sket, eller gentagne skemaaktiviteter.
- "aendringer": hvis punktet siger, at en aktivitet er aflyst ("cancel"), udsat uden ny dato ("hold") eller flyttet
  ("move"). Angiv den gamle aktivitets titel og dato, så den kan findes.

Regler:
- Brug KUN oplysninger fra punktets tekst. Gæt aldrig dato, tid eller sted. Står der ingen tid, så "hele_dagen": true.
- Datoer som ÅÅÅÅ-MM-DD. Relative datoer ("næste torsdag", "i morgen") regnes ud fra "skrevet".
- "hvem": fornavne fra punktets "hvem". "titel": kort og konkret, højst 60 tegn, uden barnets navn.
- "citat": den korte sætning i teksten, som aktiviteten bygger på (ordret).
- Højst 3 nye pr. punkt. Ingen fund: tomme lister.

Svar KUN med JSON:
{"punkter": [{"id": "S1",
  "nye": [{"titel": "…", "dato": "ÅÅÅÅ-MM-DD", "slut_dato": "ÅÅÅÅ-MM-DD eller null", "start": "HH:MM eller null",
           "slut": "HH:MM eller null", "hele_dagen": false, "sted": "… eller null", "hvem": ["…"], "citat": "…"}],
  "aendringer": [{"type": "cancel|hold|move", "gammel_titel": "…", "gammel_dato": "ÅÅÅÅ-MM-DD eller null",
                  "ny_dato": "ÅÅÅÅ-MM-DD eller null", "start": "HH:MM eller null", "slut": "HH:MM eller null", "citat": "…"}]}]}"""


_S = {"type": "STRING"}
_N = {"type": "STRING", "nullable": True}
# Svarformatet til sprogmodellen (Gemini overholder det). Indholdet kontrolleres bagefter mod punktets tekst.
SCHEMA = {
    "type": "OBJECT",
    "properties": {"punkter": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "id": _S,
            "nye": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "titel": _S, "dato": _S, "slut_dato": _N, "start": _N, "slut": _N, "hele_dagen": {"type": "BOOLEAN"},
                "sted": _N, "hvem": {"type": "ARRAY", "items": _S}, "citat": _S},
                "required": ["titel", "dato", "hele_dagen", "citat"]}},
            "aendringer": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "type": {"type": "STRING", "enum": ["cancel", "hold", "move"]}, "gammel_titel": _S, "gammel_dato": _N,
                "ny_dato": _N, "start": _N, "slut": _N, "citat": _S},
                "required": ["type", "gammel_titel", "citat"]}},
        },
        "required": ["id", "nye", "aendringer"]}}},
    "required": ["punkter"],
}


# ---------------------------------------------------------------- hjælpere
def _iso(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _hm(v) -> str | None:
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", str(v or "").strip())
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        return None
    return f"{int(m[1]):02d}:{m[2]}"


def item_key(src: A.Source) -> str:
    body = json.dumps([src.kind, src.id, src.subject, [(t, d.isoformat() if d else None) for t, d in src.texts],
                       src.default_date.isoformat() if src.default_date else None], ensure_ascii=False)
    return "kal|" + hashlib.sha256(body.encode()).hexdigest()


def date_in_text(day: dt.date, src: A.Source) -> bool:
    """Står datoen i teksten? Som dato/periode, som ugedag (op til to uger efter, teksten er skrevet), eller er det
    ugeplanens egen dag."""
    if src.default_date and day == src.default_date:
        return True
    for text, written in src.texts:
        ref = written or day
        for h in A.find_dates(text, ref):
            if h.start <= day <= h.end:
                return True
        if WEEKDAYS[day.weekday()] in text.lower() and 0 <= (day - ref).days <= 14:
            return True
    return False


def times_in_text(src: A.Source) -> set[str]:
    out = set()
    for text, written in src.texts:
        out |= _times(text)
        for t in A.find_times(text, A.find_dates(text, written or dt.date.today())):
            out.add(f"{t.start[0]:02d}:{t.start[1]:02d}")
            if t.end:
                out.add(f"{t.end[0]:02d}:{t.end[1]:02d}")
    return out


def _in_text(word: str | None, src: A.Source) -> bool:
    w = (word or "").strip().lower()
    return bool(w) and any(w in t.lower() for t, _ in src.texts + [(src.subject, None)])


# ---------------------------------------------------------------- forespørgsel og kontrol
def build_prompt(batch: list[tuple[str, A.Source]], today: dt.date, people_map: dict[str, str]) -> str:
    items = []
    for sid, src in batch:
        items.append({"id": sid, "type": src.label, "emne": _scrub(src.subject, 120),
                      "hvem": [people_map[p] for p in src.people if p in people_map],
                      "ugeplan_dag": src.default_date.isoformat() if src.default_date else None,
                      "tekster": [{"skrevet": (d.isoformat() if d else None), "tekst": _scrub(t, 1500)} for t, d in src.texts[-4:]]})
    return f"I dag er {today.isoformat()} ({WEEKDAYS[today.weekday()]}).\n\nPunkter:\n" + json.dumps(items, ensure_ascii=False, indent=1)


def validate_batch(result: dict, ids: set[str]) -> None:
    if not isinstance(result.get("punkter"), list):
        raise ValueError("mangler 'punkter'")
    for p in result["punkter"]:
        if not isinstance(p, dict) or p.get("id") not in ids:
            raise ValueError("ukendt punkt-id")
        if not isinstance(p.get("nye", []), list) or not isinstance(p.get("aendringer", []), list):
            raise ValueError("'nye'/'aendringer' skal være lister")


def check_new(n: dict, src: A.Source, today: dt.date, horizon: dt.date, names: dict[str, str]) -> dict | None:
    """Et nyt forslag fra AI kontrolleret mod punktets tekst. None = droppes."""
    title = str(n.get("titel") or "").strip(" .")[:80]
    start = _iso(n.get("dato"))
    if not title or not start or not (today <= start <= horizon) or not date_in_text(start, src):
        return None
    end = _iso(n.get("slut_dato")) or start
    if end < start or (end != start and not date_in_text(end, src)):
        end = start
    times = times_in_text(src)
    st, et = _hm(n.get("start")), _hm(n.get("slut"))
    if st and st not in times:
        return None                                                    # opdigtet klokkeslæt: hele forslaget droppes
    if et and et not in times:
        et = None
    all_day = bool(n.get("hele_dagen")) or not st
    people = [names[h.lower()] for h in (n.get("hvem") or []) if isinstance(h, str) and h.lower() in names]
    people = [p for p in people if p in src.people] or [p for p in src.people if p != "family"]
    place = n.get("sted") if _in_text(n.get("sted"), src) else None
    return {"title": title, "start": start, "end": end, "start_time": None if all_day else st,
            "end_time": None if all_day else et, "all_day": all_day, "location": place, "people": people,
            "evidence": str(n.get("citat") or "")[:300]}


def _stem(t: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[\wæøå]{4,}", (t or "").lower())}


def check_change(c: dict, src: A.Source, targets: list[dict], today: dt.date) -> dict | None:
    """En ændring skal pege på en aftale, appen selv har oprettet, og en ny dato skal stå i teksten."""
    kind = c.get("type")
    if kind not in A.CHANGE_KINDS:
        return None
    old = _iso(c.get("gammel_dato"))
    mine = _stem(c.get("gammel_titel"))
    hits = [t for t in targets if t["source"] == "app" and t["end"] >= today.isoformat() and mine & _stem(t["title"])
            and (old is None or t["start"] <= old.isoformat() <= t["end"])]
    if len(hits) != 1:
        return None                                                    # ingen eller flere mulige aftaler: ingen gæt
    new, st, et = None, None, None
    if kind == "move":
        new = _iso(c.get("ny_dato"))
        if not new or new < today or not date_in_text(new, src):
            return None
        times = times_in_text(src)
        st = _hm(c.get("start")) if _hm(c.get("start")) in times else None
        et = _hm(c.get("slut")) if _hm(c.get("slut")) in times else None
    return {"kind": kind, "target": hits[0], "start": new, "start_time": st, "end_time": et,
            "title": str(c.get("gammel_titel") or hits[0]["title"]).strip(" .")[:80], "evidence": str(c.get("citat") or "")[:300]}


# ---------------------------------------------------------------- samlet
def _to_option(n: dict, src: A.Source, people_map: dict, cal: list[dict]) -> dict:
    oid = "ev_" + A._sha("ai", src.kind, src.id, n["start"], n["start_time"], n["title"].lower())
    return {"id": oid, "title": n["title"], "calendar_title": A.calendar_title(n["title"], n["people"], people_map),
            "start": n["start"].isoformat(), "end": n["end"].isoformat(), "start_time": n["start_time"], "end_time": n["end_time"],
            "all_day": n["all_day"], "location": n["location"], "people": n["people"],
            "description": A._describe(src, n["evidence"] or src.subject), "exists": A.in_calendar(cal, n["start"], n["end"], n["title"]),
            "source": {"type": src.kind, "id": src.id, "title": src.subject.strip(" ."), "label": src.label}, "by": "ai"}


def _to_change(ch: dict, src: A.Source) -> dict:
    t, kind = ch["target"], ch["kind"]
    start = ch["start"].isoformat() if ch["start"] else t["start"]
    end = start if ch["start"] else t["end"]
    return {"id": "sg_" + A._sha("ai-ændring", kind, start, t["key"]), "type": "suggestion", "kind": kind, "category": "ændring",
            "label": {"cancel": "Aflyst", "hold": "Udsat", "move": "Flyttet"}[kind], "title": ch["title"],
            "calendar_title": t["title"], "start": start, "end": end, "old_start": t["start"],
            "all_day": not ch["start_time"], "start_time": ch["start_time"], "end_time": ch["end_time"], "location": None,
            "people": [p for p in src.people if p != "family"], "description": A._describe(src, ch["evidence"]),
            "confidence": "høj", "reason": "fundet af AI", "by": "ai",
            "sources": [{"type": src.kind, "id": src.id, "title": src.subject.strip(" ."), "label": src.label,
                         "date": src.when.isoformat() if src.when else None}],
            "target": {k: t[k] for k in ("key", "event_id", "title", "start", "end", "source")}}


def find_all(data: dict, cfg: dict, today: dt.date, people_map: dict[str, str], targets: list[dict] | None = None,
             client: ai.Client | None = None) -> tuple[list[dict], dict[str, list[dict]]]:
    """Som activities.find_all, men AI afgør punkterne, hvor den kan; reglerne afgør resten."""
    rule_sugg, rule_opts = A.find_all(data, cfg, today, people_map, targets)
    if not cfg.get("calendar_ai", {}).get("enabled", True):
        return rule_sugg, rule_opts
    import briefing
    if briefing.assistant_mode(cfg.get("assistant", {})) not in ("ai", "claude"):
        return rule_sugg, rule_opts
    client = client or briefing.ai_client(cfg)
    targets = targets or []
    scfg = cfg.get("suggestions", {})
    horizon = today + dt.timedelta(days=int(scfg.get("horizon_days", 270)))
    sources = A.build_sources(data, today, int(scfg.get("max_age_days", 120)))
    cutoff = today - dt.timedelta(days=FIRST_DAYS)
    recent = [s for s in sources if (s.when or today) >= cutoff or s.kind == "weekplan"]

    answers: dict[str, dict] = {}
    todo = []
    for s in recent:
        hit = client.cache_get(item_key(s))
        if hit is not None:
            answers[s.id] = hit
        else:
            todo.append(s)
    st = client.status()
    budget = min(MAX_REQUESTS, max(0, st["daily_cap"] - st["requests_today"] - RESERVE))
    for i in range(0, min(len(todo), budget * BATCH), BATCH):
        batch = [(f"S{j + 1}", s) for j, s in enumerate(todo[i:i + BATCH])]
        ids = {sid for sid, _ in batch}
        try:
            res = client.generate_json(SYSTEM, build_prompt(batch, today, people_map), validate=lambda r: validate_batch(r, ids), schema=SCHEMA,
                                       cache_key="kalender|" + "|".join(item_key(s) for _, s in batch))
        except ai.AIUnavailable as e:
            log.info("Kalendertjek med AI springes over (%s) – reglerne bruges for resten", e.reason)
            break
        by_id = {p["id"]: p for p in res["punkter"]}
        for sid, s in batch:
            ans = {"nye": by_id.get(sid, {}).get("nye", [])[:MAX_PER_ITEM], "aendringer": by_id.get(sid, {}).get("aendringer", [])[:MAX_PER_ITEM]}
            client.cache_put(item_key(s), ans)
            answers[s.id] = ans
    if todo and len(answers) < len(recent):
        log.info("Kalendertjek: %d punkter venter på AI (reglerne bruges imens)", len(recent) - len(answers))

    names = {v.lower(): k for k, v in people_map.items()}
    cal = [e for e in data.get("events", []) if e.get("source") == "google"]
    by_src = {s.id: s for s in recent}
    options = {k: v for k, v in rule_opts.items() if k not in answers}          # reglerne for punkter uden AI-svar
    ai_changed_sources = set(answers)
    suggestions = [sg for sg in rule_sugg if not any(x["id"] in ai_changed_sources for x in sg.get("sources", []))]
    dropped = 0
    for sid, ans in answers.items():
        src = by_src[sid]
        for n in ans.get("nye", []):
            ok = check_new(n, src, today, horizon, names) if isinstance(n, dict) else None
            if ok:
                options.setdefault(sid, []).append(_to_option(ok, src, people_map, cal))
            else:
                dropped += 1
        for c in ans.get("aendringer", []):
            ok = check_change(c, src, targets, today) if isinstance(c, dict) else None
            if ok:
                suggestions.append(_to_change(ok, src))
            else:
                dropped += 1
    if dropped:
        log.info("Kalendertjek: %d AI-forslag droppet (dato, tid eller aftale stod ikke i teksten)", dropped)
    seen, unique = set(), []
    for sg in sorted(suggestions, key=lambda x: (x["start"], x["title"])):
        if sg["id"] not in seen:
            seen.add(sg["id"])
            unique.append(sg)
    return unique, options
