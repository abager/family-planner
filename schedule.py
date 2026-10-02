"""Skemaet: fra Aulas rå lektioner til noget man kan læse på et kort.

Iagttagelser fra jeres egne data (aula_dump.json):

* Fagene står som forkortelser (DAN, MAT, N/T, HDS …) og skal skrives ud.
* Samme tidspunkt har ofte flere lektioner: undervisningen og en støtte-lektion (INK = inklusion)
  med en anden lærer. INK er støtte til en ANDEN elev i klassen, men Aula lægger den i alle
  klassekammeraters kalender. Den udelades derfor (se HIDDEN_DEFAULT) – ellers ser det ud, som om
  barnet har to timer på én gang.
* Lærere kommer med fulde navne ("Anni Helmiina Clausen"), som fylder for meget. De forkortes til
  fornavn og forbogstav ("Anni C.").
* Vikarer ligger i lektionens deltagerliste, og lokaler står enten som nummer/kode eller som tekst
  ("Hal 1"), som biblioteket ikke giver videre.
"""
from __future__ import annotations


SUBJECTS = {
    "DAN": "Dansk", "MAT": "Matematik", "ENG": "Engelsk", "TYS": "Tysk", "FRA": "Fransk",
    "HIS": "Historie", "KRI": "Kristendomskundskab", "SAM": "Samfundsfag", "GEO": "Geografi",
    "BIO": "Biologi", "FYS": "Fysik/kemi", "KEM": "Fysik/kemi", "N/T": "Natur/teknologi",
    "BIL": "Billedkunst", "MUS": "Musik", "IDR": "Idræt", "SVØ": "Svømning",
    "HDS": "Håndværk og design", "MAD": "Madkundskab", "INK": "Inklusion", "UUV": "Understøttende undervisning",
}
# INK = støttelærer til en anden elev, PS = klassepædagogen. Begge følger klassen og siger intet om barnet.
HIDDEN_DEFAULT = ["INK", "Inklusion", "PS"]
DEFAULT_SECONDARY: list[str] = []       # timer der vises som "samtidig", men aldrig er timens hovedfag


def subject_name(code: str, custom: dict[str, str] | None = None) -> str:
    code = (code or "").strip()
    table = {**SUBJECTS, **{k.upper(): v for k, v in (custom or {}).items()}}
    return table.get(code.upper(), code)


def short_name(full: str | None) -> str | None:
    """"Anni Helmiina Clausen" → "Anni C."; "Jeppe" → "Jeppe"."""
    parts = (full or "").split()
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0]}."


def _names(value) -> list[str]:
    if not value:
        return []
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        for part in str(item).split(","):
            s = short_name(part.strip())
            if s and s not in out:
                out.append(s)
    return out


def _location(ev) -> str | None:
    raw = getattr(ev, "_raw", None) or {}
    return ev.location or (raw.get("primaryResourceText") or "").strip() or None


LESSON_MINUTES = 45      # en skolelektion, når skolen ikke har angivet sluttid ([aula] lesson_minutes)


def lesson_end(start: str, minutes: int = LESSON_MINUTES) -> str:
    """'08:00' → '08:45'."""
    h, m = map(int, start.split(":"))
    t = h * 60 + m + minutes
    return f"{t // 60 % 24:02d}:{t % 60:02d}"


def build_lessons(evs, local_tz, custom_subjects: dict[str, str] | None = None,
                  hidden_codes: list[str] | None = None, secondary_codes: list[str] | None = None,
                  lesson_minutes: int = LESSON_MINUTES) -> list[dict]:
    """evs: kalenderlektioner for ét barn på én dag. Returnerer ét punkt pr. tidsrum.
    En lektion uden brugbar sluttid (slut = start) får lesson_minutes og markeres endInferred."""
    hidden = {c.upper() for c in (hidden_codes if hidden_codes is not None else HIDDEN_DEFAULT)}
    secondary = {c.upper() for c in (secondary_codes if secondary_codes is not None else DEFAULT_SECONDARY)}
    slots: dict[tuple[str, str], list] = {}
    for ev in evs:
        if (ev.title or "").strip().upper() in hidden:
            continue
        key = (ev.start_datetime.astimezone(local_tz).strftime("%H:%M"), ev.end_datetime.astimezone(local_tz).strftime("%H:%M"))
        slots.setdefault(key, []).append(ev)

    def one(ev) -> dict:
        raw = getattr(ev, "_raw", None) or {}
        subs = ev.substitute_names or ([ev.substitute_name] if ev.substitute_name else [])
        return {
            "code": ev.title or "",
            "title": subject_name(ev.title or "", custom_subjects),
            "teacher": ", ".join(_names(ev.teacher_names or ev.teacher_name)) or None,
            "substitute": (", ".join(_names(subs)) or "vikar") if ev.has_substitute else None,
            "location": _location(ev),
            "hasNote": bool((raw.get("lesson") or {}).get("hasRelevantNote")),
            "id": ev.id,
        }

    lessons: list[dict] = []
    for (start, end), group in sorted(slots.items()):
        items = [one(e) for e in group]
        items.sort(key=lambda i: i["code"].upper() in secondary)     # stabil: almindelige fag først
        first, *others = items
        lesson = {"start": start, "end": end, **{k: first[k] for k in ("title", "teacher", "substitute", "location", "hasNote", "id")}}
        if end <= start:
            lesson["end"], lesson["endInferred"] = lesson_end(start, lesson_minutes), True
        if others:                        # flere timer samtidig (fx holddeling)
            lesson["alt"] = [{"title": o["title"], "teacher": o["teacher"], "location": o["location"]} for o in others]
            lesson["hasNote"] = lesson["hasNote"] or any(o["hasNote"] for o in others)
        lessons.append(lesson)
    return lessons


def schedule_summary(lessons: list[dict]) -> str:
    """Tekstversion til notes/søgning: '08.00–08.45 Dansk (vikar: Jeppe L.)'."""
    lines = []
    for l in lessons:
        line = f"{l['start'].replace(':', '.')}{'–' + l['end'].replace(':', '.') if l.get('end') else ''} {l['title']}"
        if l.get("substitute"):
            line += f" (vikar: {l['substitute']})"
        lines.append(line)
    return "\n".join(lines)
