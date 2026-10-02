"""Analyse af Aula-beskeder: hvem handler de om, hvad skal I gøre, og hvornår sker der noget.

Bygget ud fra en gennemgang af jeres egne beskeder. Iagttagelser:

* Aula knytter ofte beskeder til alle børn på skolen, men teksten siger som regel
  hvilken klasse eller årgang det gælder ("forældre i 6B", "3. årgang").
* Nogle tråde er personlige samtaler mellem jer og personalet. De markeres som private,
  så teksten ikke står fremme på en fælles skærm.
* Handlinger ("Skriv jer på senest fredag d.25.9", "vær sød og skriv til mig"),
  arrangementer ("onsdag d. 23/9 … kl. 13.00-15.00 på Greve Stadion", "forældremødet
  i morgen") og ting der skal med (punktlister efter "skal medbringe") står i fri tekst.
* Høflige tilbud ("Hvis I har spørgsmål er I velkomne til at skrive") er ikke handlinger.
"""
from __future__ import annotations

import datetime as dt
import re

from homework import WEEKDAYS, _WD, clean, sentences

MONTH_WORDS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6, "jul": 7, "aug": 8,
               "sep": 9, "okt": 10, "nov": 11, "dec": 12}

# ---------------------------------------------------------------- hvem
GRADE_RX = [
    re.compile(r"\b(\d{1,2})\s?\.?\s?(?:årgang|klasse|kl\b)", re.I),   # "3. årgang", "6.årgang", "3 klasse"
    re.compile(r"\b(\d{1,2})\s?\.?\s?([a-e])\b(?![.\w])", re.I),        # "6B", "6b", "6.B"
]


def grades_in(text: str) -> set[int]:
    found = set()
    for rx in GRADE_RX:
        for m in rx.finditer(text):
            g = int(m[1])
            if 0 <= g <= 10:
                found.add(g)
    return found


def grade_of(person: dict) -> int | None:
    if person.get("grade") is not None:
        return int(person["grade"])
    m = re.match(r"\s*(\d{1,2})", str(person.get("class") or ""))
    return int(m[1]) if m else None


# ---------------------------------------------------------------- private samtaler
def is_private(msg: dict, family_names: list[str]) -> bool:
    sender = (msg.get("from") or "").lower()
    if any(sender.startswith(n.lower()) for n in family_names):
        return True                                   # en af jer har skrevet seneste besked
    head = (msg.get("text") or "")[:80].lower()
    if re.match(r"\s*(kære|hej|til)\s+(\w+\s+(og|&)\s+)?\w+", head) and any(n.lower() in head for n in family_names):
        return True                                   # "Kære Andreas og Monica"
    return False


# ---------------------------------------------------------------- datoer og tider
def _date_near(day: int, month: int, ref: dt.date) -> dt.date | None:
    try:
        d = dt.date(ref.year, month, day)
    except ValueError:
        return None
    if d < ref - dt.timedelta(days=120):
        d = d.replace(year=d.year + 1)
    return d


def find_date(s: str, ref: dt.date) -> tuple[dt.date | None, bool]:
    """(dato, hele_ugen). ref = beskedens dato."""
    d, week, _ = find_date_pos(s, ref)
    return d, week


def find_date_pos(s: str, ref: dt.date) -> tuple[dt.date | None, bool, int]:
    """Som find_date, men returnerer også hvor i sætningen datoen står."""
    t = s.lower()
    m = re.search(r"\bd\.?\s?(\d{1,2})\s?[./]\s?(\d{1,2})\b|\b(\d{1,2})/(\d{1,2})\b", t)
    if m:
        day, month = (m[1], m[2]) if m[1] else (m[3], m[4])
        return _date_near(int(day), int(month), ref), False, m.start()
    m = re.search(r"\b(\d{1,2})\.\s?(" + "|".join(MONTH_WORDS) + r")\w*", t)
    if m:
        return _date_near(int(m[1]), MONTH_WORDS[m[2]], ref), False, m.start()
    m = re.search(r"\bi morgen\b", t)
    if m:
        return ref + dt.timedelta(days=1), False, m.start()
    m = re.search(r"\bi dag\b|\bi aften\b", t)
    if m:
        return ref, False, m.start()
    m = re.search(r"\b(?:på|næste|til|senest)\s+(" + _WD + r")\b", t)
    if m:
        days = (WEEKDAYS[m[1]] - ref.weekday()) % 7 or 7
        return ref + dt.timedelta(days=days), False, m.start()
    m = re.search(r"\buge\s?(\d{1,2})\b", t)
    if m:
        wk = int(m[1])
        year = ref.isocalendar()[0] + (1 if wk < ref.isocalendar()[1] - 10 else 0)
        try:
            return dt.date.fromisocalendar(year, wk, 1), True, m.start()
        except ValueError:
            pass
    return None, False, -1


TIME_RX = re.compile(r"\b(?:kl\.?\s?)?(\d{1,2})[.:](\d{2})\b")


def find_times(s: str) -> list[dt.time]:
    out = []
    for m in TIME_RX.finditer(s):
        h, mi = int(m[1]), int(m[2])
        # "d.25.9" og "23/9" må ikke læses som klokkeslæt
        if h < 24 and mi < 60 and not re.search(r"d\.?\s?$", s[: m.start()]):
            out.append(dt.time(h, mi))
    return out


# ---------------------------------------------------------------- handlinger, arrangementer, medbring
ACTION_RX = re.compile(
    r"\b(skriv (jer |dig )?på|tilmeld|meld (til|fra|jer|dig)|giv(e)? besked|svar(e)?|udfyld|underskriv|"
    r"betal|send|aflever|vær sød (og|at) \w+|skriv til|husk at give besked|skriv på aula)\b", re.I)
POLITE_RX = re.compile(r"\bhvis i har spørgsmål\b|\bvelkomne? til at\b|\bkan man sende\b|"
                       r"\bhvis det giver anledning\b|\btak for\b", re.I)
EVENT_STEMS = ["forældremøde", "møde", "samtale", "udflugt", "turnering", "arrangement", "fest", "lejrskole",
               "koloni", "test", "fremlæggelse", "motionsdag", "halloween", "show", "afslutning",
               "fotografering", "skolefoto"]
# "tur" kun som ordbegyndelse – ellers rammer den "natur", "kultur" …
EVENT_RX = re.compile(r"\b(tur(?:en|e)?\b|[\wæøå]*(?:" + "|".join(EVENT_STEMS) + r")[\wæøå]*)", re.I)
LOCATION_RX = re.compile(r"\bpå ([A-ZÆØÅ][\wæøå]+(?:\s[A-ZÆØÅ][\wæøå]+)*)")
EMOJI_RX = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]")


def _bring_list(text: str) -> list[str]:
    """Punktliste efter "skal medbringe:" / "medbring:" / "husk:"."""
    m = re.search(r"(skal (have )?med(bringe)?|medbring|husk)\s*:?\s*\n((?:\s*[*\-•]\s*.+\n?)+)", text, re.I)
    if not m:
        return []
    items = []
    for line in m[4].splitlines():
        line = EMOJI_RX.sub("", re.sub(r"^\s*[*\-•]\s*", "", line)).strip()
        if line:
            items.append(line)
    return items


def _stem_of(word: str) -> str:
    w = word.lower()
    return next((st for st in EVENT_STEMS if st in w), "tur" if w.startswith("tur") else "")


def _indefinite(word: str) -> str:
    """"forældremødet" → "forældremøde", "testen" → "test", men "halloween" forbliver."""
    w, stem = word.lower(), _stem_of(word)
    for end in ("erne", "ene", "et", "en", "t", "n"):
        if w.endswith(end) and w[: -len(end)].endswith(stem) and len(w) - len(end) >= len(stem):
            return w[: -len(end)]
    return w


def _event_title(sentence: str, subject: str, date_pos: int) -> str:
    words = list(EVENT_RX.finditer(sentence))
    if not words:
        return subject.rstrip(". ")
    # Et arrangements-ord der også står i emnet → emnet er den bedste titel ("testen" + "Færdighedstest")
    for m in words:
        if _stem_of(m[1]) and _stem_of(m[1]) in subject.lower():
            return subject.rstrip(". ")
    # Ellers ordet tættest på datoen ("… holdt møde … om Halloweenaktiviteter i uge 44")
    best = min(words, key=lambda m: abs(m.start() - date_pos)) if date_pos >= 0 else words[0]
    return _indefinite(best[1]).capitalize()


def _short(s: str, n: int = 110) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def analyse(msg: dict, people: list[dict], family_names: list[str]) -> dict:
    """Returnér {people, private, category, actions, events, bring}."""
    text_raw = msg.get("text") or ""
    subject = msg.get("subject") or ""
    ts = msg.get("timestamp")
    ref = dt.datetime.fromisoformat(ts).date() if ts else dt.date.today()
    # Punktlister ("- Horror-kælder", "* Drikkedunk") er selvstændige sætninger
    bullets = re.sub(r"\\-", "-", text_raw)
    bullets = re.sub(r"\n\s*[*\-•]\s+", ". ", bullets)
    bullets = re.sub(r"\n\s*\n", ". ", bullets)            # tomme linjer = nyt afsnit
    text = clean(bullets)
    sents = sentences(text)
    result = {"people": msg.get("people") or ["family"], "private": False, "category": "info",
              "actions": [], "events": [], "bring": []}

    # 1) Hvem: klasse/årgang i emne eller tekst slår Aulas grove tilknytning
    g = grades_in(f"{subject} {text[:300]}")
    if g:
        match = [p["id"] for p in people if p.get("role") == "child" and grade_of(p) in g]
        if match:
            result["people"] = match

    # 2) Private samtaler
    if is_private(msg, family_names):
        result["private"] = True
        result["category"] = "samtale"
        return result                                     # vi trækker ikke opgaver ud af private tråde
    if not text.strip():
        result["category"] = "tom"
        return result

    # 3) Handlinger
    for s in sents:
        if ACTION_RX.search(s) and not POLITE_RX.search(s):
            due, _ = find_date(s, ref) if re.search(r"\bsenest|\binden|\bd\.|\d/\d", s, re.I) else (None, False)
            conditional = bool(re.match(r"\s*hvis\b", s, re.I) or re.search(r",?\s*hvis\b", s, re.I))
            result["actions"].append({"title": _short(EMOJI_RX.sub("", s)),
                                      "due": due.isoformat() if due else None,
                                      "confidence": "middel" if conditional else "høj"})

    # 4) Arrangementer: en sætning med både en dato og et arrangements-ord eller klokkeslæt
    seen_dates = set()
    for s in sents:
        if ACTION_RX.search(s) and not EVENT_RX.search(s):
            continue
        if re.search(r"\btak(ke)? for\b|\bhar været\b", s, re.I):   # tak for turen = noget der er sket
            continue
        d, whole_week, dpos = find_date_pos(s, ref)
        if not d or d in seen_dates:
            continue
        times = find_times(s)
        if not (EVENT_RX.search(s) or times):
            continue
        seen_dates.add(d)
        loc = LOCATION_RX.search(s)
        result["events"].append({
            "title": _event_title(s, subject, dpos),
            "date": d.isoformat(),
            "start": min(times).strftime("%H:%M") if times else None,
            "end": max(times).strftime("%H:%M") if len(times) > 1 else None,
            "allWeek": whole_week,
            "location": loc[1] if loc else None,
            "source_sentence": _short(s, 160),
        })

    # 5) Ting der skal med – knyttes til arrangementets dag, hvis der er et
    items = _bring_list(text_raw)
    if items:
        short_items = [re.split(r",| de | der | hvis ", it, 1)[0].strip() for it in items]
        result["bring"].append({"title": "Medbring: " + ", ".join(short_items),
                                "due": result["events"][0]["date"] if result["events"] else None,
                                "items": items})

    # En handling uden dato i en besked om ét arrangement hører til arrangementets dag
    if len(result["events"]) == 1:
        for a in result["actions"]:
            a["due"] = a["due"] or result["events"][0]["date"]

    if result["actions"] or result["bring"]:
        result["category"] = "handling"
    elif result["events"]:
        result["category"] = "arrangement"
    elif re.search(r"\btak for\b", f"{subject} {text[:120]}", re.I):
        result["category"] = "hilsen"
    return result
