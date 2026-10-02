"""Finder lektier, ting der skal huskes, og praktisk info i Meebook-ugeplaner.

Bygget ud fra en gennemgang af jeres egne ugeplaner. De vigtigste iagttagelser:

* Meebook markerer selv punkter som "task" (lærerens lektie/husk) eller "comment"
  (beskrivelse af undervisningen). "task" er et stærkt signal, men ikke det eneste.
* Kommentarer er næsten altid undervisning i klassen ("Vi arbejder …", "Læs højt på
  klassen", "laves her i skolen"). Et naivt søgeord som "læs" eller "opgave" giver
  derfor mange falske lektier.
* Ting der skal huskes gemmer sig i kommentarerne: "NB! Husk passer",
  "Husk høretelefoner", "Husk løbeskoene, mad og drikke".
* Deadlines står i fri tekst: "på mandag", "i dag", "Afleveres uge 43", "hver dag".
* Ugeplanen kan indeholde flere klasser ("6.A …" / "6.B …"), så samme lektie kan
  optræde to gange.

Analysen sker sætning for sætning. Hver sætning får point for hjemme-signaler og
minuspoint for klasse-signaler; det bedste sætningsresultat afgør punktet.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata

WEEKDAYS = {"mandag": 0, "tirsdag": 1, "onsdag": 2, "torsdag": 3, "fredag": 4, "lørdag": 5, "søndag": 6}
_WD = "|".join(WEEKDAYS)

# Forkortelser, der ellers ville splitte sætninger forkert ("eks. nodens navn")
_ABBREV = ["f.eks.", "bl.a.", "evt.", "eks.", "ca.", "lign.", "kl.", "s.", "nr.", "max.", "min.", "osv.", "dvs."]

# ---------------------------------------------------------------- signaler
HOME = [  # (mønster, point)
    (r"\blektie", 3),
    (r"\b(der)?hjemme\b", 3),
    (r"\bafleve(res|ring|r)\b", 3),
    (r"\btil (i morgen|næste gang|næste time|" + _WD + r")\b", 2),
    (r"\binden (næste|" + _WD + r"|ferien)\b", 2),
    (r"\barbejd selv\b|\bøv (dig|hjemme|selv)\b", 2),
    (r"\bonline\b", 1),
    (r"^(læs|lav|skriv|øv|lån|find|forbered|tag noter|se|lyt|lær|undersøg|øv dig)\b", 1),
]
CLASS = [
    (r"\b(på|i) klassen\b|\bher i skolen\b|\bi timen\b|\bi (billedkunst|madkundskabs?)[-\w]*lokalet\b", -4),
    (r"\blaves (her|i skolen|i klassen)\b|\btil vikar\b|\bhvis i bliver færdige\b|\bprogram:", -4),
    (r"^vi\b|\bvi (arbejder|læser|gennemgår|fortsætter|går videre|taler|laver|starter|øver|skal)\b", -3),
    (r"^i (arbejder|fortsætter|har vikar|skal have vikar)\b", -3),
]
# "Husk X", "NB! Husk X", "Tag X med", "X skal med" – men ikke "Husk at …" eller "skal ikke med"
HUSK = re.compile(r"^(nb!?\s*)?husk\b(?!\s+at\b)|\btag\b.{1,60}\bmed\b|\bskal med\b", re.I)
HUSK_AT = re.compile(r"^(nb!?\s*)?husk at\b", re.I)
INFO = re.compile(
    r"\bomlagt\b|\bvikar\b|\bskolefoto\b|\btemadag\b|\bmotionsdag\b|\bferie\w*\b|\baflyst\b|"
    r"\bingen (idræt|svømning|undervisning|skole)\b|\bkommer med hjem\b|\belevsamtaler\b|\bmødes\b|\bshow\b",
    re.I,
)
CLASS_PREFIX = re.compile(r"^\s*(\d{1,2}\.\s?[A-ZÆØÅ])\b")


# ---------------------------------------------------------------- tekst
def clean(text: str) -> str:
    text = (text or "").replace("\\.", ".").replace("\u00a0", " ")
    text = re.sub(r"https?://\S+", " ", text)          # links er aldrig selve opgaven
    text = re.sub(r"\s*\n\s*", " ", text)               # html_to_plain ombryder linjer midt i sætninger
    text = text.replace("•", ". ").replace(" o ", ". ")
    return re.sub(r"\s{2,}", " ", text).strip()


def sentences(text: str) -> list[str]:
    protected = text
    for i, a in enumerate(_ABBREV):
        protected = re.sub(re.escape(a), f"§{i}§", protected, flags=re.I)
    parts = re.split(r"(?<=[.!?])\s+|\s+(?=NB!)|;\s*", protected)
    out = []
    for p in parts:
        for i, a in enumerate(_ABBREV):
            p = p.replace(f"§{i}§", a)
        p = p.strip(" .;")
        if len(p) > 1:
            out.append(p)
    return out


def _score(sentence: str) -> int:
    s = sentence.lower().strip("(\"' ")
    s = re.sub(r"^\d{1,2}\.\s?[a-zæøå]\s+", "", s)  # "6.b lektie: …" → "lektie: …"
    s = re.sub(r"^lektie:\s*", "lektie ", s)
    return sum(p for rx, p in HOME + CLASS if re.search(rx, s))


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s.lower())
    return re.sub(r"[^\wæøå]+", " ", s).strip()


# ---------------------------------------------------------------- deadlines
def _next_weekday(base: dt.date, wd: int) -> dt.date:
    days = (wd - base.weekday()) % 7 or 7
    return base + dt.timedelta(days=days)


def find_due(text: str, entry: dt.date) -> tuple[dt.date, str | None]:
    """Returnér (dato, gentagelse). Standard er ugeplanens dag."""
    t = text.lower()
    if re.search(r"\bhver dag\b|\bdagligt?\b", t):
        return entry, "daily"
    if re.search(r"\bi dag\b", t):
        return entry, None
    if re.search(r"\bi morgen\b", t):
        return entry + dt.timedelta(days=1), None
    m = re.search(r"\b(?:til|på|igen på|senest|inden)\s+(" + _WD + r")\b", t)
    if m:
        return _next_weekday(entry, WEEKDAYS[m[1]]), None
    m = re.search(r"\buge\s*(\d{1,2})\b", t)
    if m and re.search(r"afleve|senest|deadline", t):
        week = int(m[1])
        year = entry.isocalendar()[0] + (1 if week < entry.isocalendar()[1] - 10 else 0)
        try:
            return dt.date.fromisocalendar(year, week, 1), None
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})[./](\d{1,2})\b", t)
    if m and re.search(r"afleve|senest|til|deadline", t):
        try:
            d = dt.date(entry.year, int(m[2]), int(m[1]))
            return (d if d >= entry - dt.timedelta(days=60) else d.replace(year=d.year + 1)), None
        except ValueError:
            pass
    return entry, None


# ---------------------------------------------------------------- klassifikation
def _husk_title(sentence: str, previous: str | None) -> str:
    s = re.sub(r"^(nb!?\s*)", "", sentence, flags=re.I).strip()
    s = re.split(r"\s+[A-ZÆØÅ][\wæøå]*:|\s+\d+\.\s", s)[0]   # "Husk passer Program: 1. …" → "Husk passer"
    if re.search(r"\b(den|det|dem)\b", s, re.I) and previous:
        s = f"{previous.split(',')[0]}. {s}"              # "… arbejdsbogen kommer med hjem. Husk den skal med …"
    return s[0].upper() + s[1:] if s else s


def _short(s: str, n: int = 110) -> str:
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def analyse(item: dict, class_name: str | None = None) -> dict:
    """Analysér ét ugeplanpunkt.

    item: {"type": "task|comment", "subject": "Dansk", "text": "...", "date": "YYYY-MM-DD"}
    Returnerer {"category": "lektie|husk|info|undervisning|anden_klasse", "tasks": [...], "info": "..."}
    """
    raw = clean(item.get("text", ""))
    prefix = CLASS_PREFIX.match(raw)
    if prefix and class_name and _norm(prefix[1]) != _norm(class_name):
        return {"category": "anden_klasse", "tasks": [], "info": None}

    entry = dt.date.fromisoformat(item["date"]) if item.get("date") else dt.date.today()
    is_task = (item.get("type") or "").lower() == "task"
    sents = sentences(raw)
    tasks: list[dict] = []

    # 1) Ting der skal huskes
    for i, s in enumerate(sents):
        if HUSK.search(s) and not re.search(r"\bskal ikke med\b", s, re.I):
            due, rec = find_due(s, entry)
            tasks.append({"kind": "husk", "title": _short(_husk_title(s, sents[i - 1] if i else None)),
                          "due": due.isoformat(), "recurring": rec,
                          "confidence": "høj" if is_task or s.lower().lstrip("nb! ").startswith("husk") else "middel"})

    # 2) Lektier
    scored = [(s, _score(s)) for s in sents if not HUSK.search(s)]
    best = max((sc for _, sc in scored), default=0)
    threshold = 1 if is_task else 3
    if best + (2 if is_task else 0) >= threshold and (is_task or best >= 3) and not (is_task and tasks and best <= 1):
        keep = [s for s, sc in scored if sc > 0] or [s for s, _ in scored]
        text = ". ".join(keep[:2])
        text = re.sub(r"^\d{1,2}\.\s?[A-ZÆØÅ]\s+", "", text)
        text = re.sub(r"^lektie:\s*", "", text, flags=re.I)
        due, rec = find_due(raw, entry)
        tasks.append({"kind": "lektie", "title": _short(text[0].upper() + text[1:] if text else text),
                      "due": due.isoformat(), "recurring": rec,
                      "confidence": "høj" if is_task or best >= 5 else "middel"})

    # 3) "Husk at …" uden hjemme-signal er som regel arbejde i klassen – kun med som lav
    for s in sents:
        if HUSK_AT.search(s) and not tasks:
            tasks.append({"kind": "lektie", "title": _short(s), "due": entry.isoformat(),
                          "recurring": None, "confidence": "lav"})

    info_sent = next((s for s in sents if INFO.search(s)), None)
    if any(t["confidence"] != "lav" for t in tasks):
        category = "lektie" if any(t["kind"] == "lektie" for t in tasks) else "husk"
    elif info_sent:
        category = "info"
    else:
        category = "undervisning"
    return {"category": category, "tasks": tasks, "info": _short(info_sent, 90) if info_sent else None}


def dedupe(tasks: list[dict]) -> list[dict]:
    """Samme lektie for 6.A og 6.B, eller samme tekst gentaget flere dage i træk."""
    seen: dict[tuple, dict] = {}
    for t in tasks:
        key = (t["person"], t["kind"], _norm(t["title"])[:60], t["due"] if not t.get("recurring") else "rec")
        if key not in seen:
            seen[key] = t
    return list(seen.values())
