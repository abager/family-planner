"""Finder aktiviteter i ugeplaner, opslag og beskeder, som fortjener en plads i familiekalenderen.

Princippet er det samme som ellers i projektet: faste regler, der kan forklares og rettes, og ingen sprogmodel.
Hver aktivitet skal have (1) en kategori, familien vil have i kalenderen, og (2) en dato, vi kan regne ud.

Kommer i kalenderen:  lejrskole/koloni, ture (zoo, museum …), turneringer, planlagte test, forældremøder og
                      skole-hjem-samtaler, fødselsdagsinvitationer hvor man skal et andet sted hen, arrangementer
                      forældre deltager i, lukkedage og omlagte skoledage.
Bliver i Aula:        fødselsdage der fejres i klassen/på stuen, temadage og -uger, motionsdag, Halloween og lignende,
                      der ikke ændrer på hverdagen. (En temadag med "omlagt dag" eller ændret sluttid ændrer hverdagen
                      og kommer med.)

Datoer læses ud fra beskedens tidspunkt, så "i morgen", "på torsdag" og "næste uge" bliver til rigtige datoer.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass

from homework import clean

WEEKDAYS = {"mandag": 0, "tirsdag": 1, "onsdag": 2, "torsdag": 3, "fredag": 4, "lørdag": 5, "søndag": 6}
MONTHS = {"januar": 1, "jan": 1, "februar": 2, "feb": 2, "marts": 3, "mar": 3, "april": 4, "apr": 4, "maj": 5,
          "juni": 6, "jun": 6, "juli": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
          "oktober": 10, "okt": 10, "november": 11, "nov": 11, "december": 12, "dec": 12}
_WD = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_PFX = rf"(?:(?:{_WD})\s*)?(?:den\s+|d\.\s*)?"            # "torsdag d. ", "den ", "d."
_SEP = r"\s*(?:[-–—]|til(?:\s+og\s+med)?)\s*"             # 5.-9.  ·  5 til 9
_NOT_UNIT = r"(?!\s*(?:time|timer|dl|kg|liter|cm|mm|stk|kr)\b)(?![\d])"

# ---------------------------------------------------------------- datoer
R_RANGE_MON = re.compile(rf"{_PFX}(\d{{1,2}})\.?{_SEP}{_PFX}(\d{{1,2}})\.?\s*({_MON})\b", re.I)
R_NUM = re.compile(rf"{_PFX}(\d{{1,2}})\s*/\s*(\d{{1,2}})(?:\s*/\s*(\d{{2,4}}))?{_NOT_UNIT}", re.I)
R_DOT = re.compile(rf"(?:(?:{_WD})\s*)?(?:den\s+|d\.\s*)(\d{{1,2}})\.(\d{{1,2}})\b(?!\.?\d)", re.I)
R_MON = re.compile(rf"{_PFX}(\d{{1,2}})\.?\s*({_MON})\b(?:\s+(\d{{4}}))?", re.I)
R_WEEKNO = re.compile(r"\buge\s*(\d{1,2})(?:\s*(?:[-–—]|og|til)\s*(?:uge\s*)?(\d{1,2}))?\b", re.I)
R_NEXTWEEK = re.compile(r"\b(?:i\s+)?(?:den\s+)?(næste|kommende|denne)\s+uge\b", re.I)
R_WEEKDAY = re.compile(rf"\b({_WD})\b", re.I)
R_REL = re.compile(r"\bi\s+(morgen|overmorgen|dag|aften)\b", re.I)
R_JOIN = re.compile(r"^\s*(?:[-–—]|til(?:\s+og\s+med)?)\s*$", re.I)
R_AND = re.compile(r"^\s*og\s*$", re.I)


@dataclass
class DateHit:
    start: dt.date
    end: dt.date
    a: int
    b: int
    kind: str                     # date | range | week | weekday | rel


def _mk(y: int, m: int, d: int) -> dt.date | None:
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def _in_year(day: int, month: int, ref: dt.date, year: int | None = None) -> dt.date | None:
    """Dato uden årstal: samme år som ref – men ligger den mere end to måneder før ref, så er det næste år."""
    if year:
        return _mk(year + 2000 if year < 100 else year, month, day)
    d = _mk(ref.year, month, day)
    if d and d < ref - dt.timedelta(days=60):
        d = _mk(ref.year + 1, month, day)
    return d


def _monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def _iso_week_monday(week: int, ref: dt.date) -> dt.date | None:
    year = ref.year + (1 if week < ref.isocalendar()[1] - 20 else 0)
    try:
        return dt.date.fromisocalendar(year, week, 1)
    except ValueError:
        return None


def find_dates(text: str, ref: dt.date) -> list[DateHit]:
    """Alle datoer i teksten, med position. Overlappende fund løses til fordel af det længste."""
    raw: list[DateHit] = []
    for m in R_RANGE_MON.finditer(text):
        mon = MONTHS[m[3].lower()]
        d1, d2 = _in_year(int(m[1]), mon, ref), _in_year(int(m[2]), mon, ref)
        if d1 and d2 and d1 <= d2:
            raw.append(DateHit(d1, d2, m.start(), m.end(), "range"))
    for m in R_NUM.finditer(text):
        d = _in_year(int(m[1]), int(m[2]), ref, int(m[3]) if m[3] else None)
        if d and 1 <= int(m[2]) <= 12:
            raw.append(DateHit(d, d, m.start(), m.end(), "date"))
    for m in R_DOT.finditer(text):
        d = _in_year(int(m[1]), int(m[2]), ref)
        if d and 1 <= int(m[2]) <= 12:
            raw.append(DateHit(d, d, m.start(), m.end(), "date"))
    for m in R_MON.finditer(text):
        d = _in_year(int(m[1]), MONTHS[m[2].lower()], ref, int(m[3]) if m[3] else None)
        if d:
            raw.append(DateHit(d, d, m.start(), m.end(), "date"))
    for m in R_WEEKNO.finditer(text):
        w1, w2 = int(m[1]), int(m[2]) if m[2] else None
        mon = _iso_week_monday(w1, ref)
        mon2 = _iso_week_monday(w2, ref) if w2 else mon
        if mon and mon2 and 1 <= w1 <= 53 and mon <= mon2:
            raw.append(DateHit(mon, mon2 + dt.timedelta(days=4), m.start(), m.end(), "week"))
    rel_week = R_NEXTWEEK.search(text)
    shift = {"næste": 7, "kommende": 7, "denne": 0}[rel_week[1].lower()] if rel_week else None
    for m in R_REL.finditer(text):
        if text[max(0, m.start() - 6):m.start()].lower().endswith("for "):
            continue                                                  # "tak for i dag"
        off = {"morgen": 1, "overmorgen": 2, "dag": 0, "aften": 0}[m[1].lower()]
        d = ref + dt.timedelta(days=off)
        raw.append(DateHit(d, d, m.start(), m.end(), "rel"))
    bound = False
    for m in R_WEEKDAY.finditer(text):
        wd = WEEKDAYS[m[1].lower()]
        # "torsdag i næste uge" / "næste uge torsdag": kun hvis "næste uge" står lige op ad ugedagen
        near = rel_week and (0 <= rel_week.start() - m.end() <= 14 or 0 <= m.start() - rel_week.end() <= 3)
        if near:
            d, bound = _monday(ref) + dt.timedelta(days=wd + shift), True
        else:
            d = ref + dt.timedelta(days=(wd - ref.weekday()) % 7 or 7)        # næste forekomst efter ref
        raw.append(DateHit(d, d, m.start(), m.end(), "weekday"))
    if rel_week and not bound:
        mon = _monday(ref) + dt.timedelta(days=shift)
        raw.append(DateHit(mon, mon + dt.timedelta(days=4), rel_week.start(), rel_week.end(), "week"))
    return _resolve(raw, text)


def _resolve(raw: list[DateHit], text: str) -> list[DateHit]:
    """Fjern overlap (længste vinder), slå "5/10 - 9/10" og "mandag til fredag" sammen til intervaller."""
    raw.sort(key=lambda h: (h.a, -(h.b - h.a)))
    kept: list[DateHit] = []
    for h in raw:
        if kept and h.a < kept[-1].b:
            continue
        kept.append(h)
    merged: list[DateHit] = []
    for h in kept:
        prev = merged[-1] if merged else None
        if prev and prev.kind in ("date", "weekday", "rel") and h.kind in ("date", "weekday", "rel"):
            between = text[prev.b:h.a]
            if R_JOIN.match(between) and prev.start <= h.start:
                merged[-1] = DateHit(prev.start, h.end, prev.a, h.b, "range")
                continue
            if R_AND.match(between) and (h.start - prev.end).days == 1:
                merged[-1] = DateHit(prev.start, h.end, prev.a, h.b, "range")
                continue
        merged.append(h)
    # samme dato nævnt to gange ("i morgen (torsdag)") tæller kun én gang
    uniq: list[DateHit] = []
    for h in merged:
        if not any(u.start == h.start and u.end == h.end for u in uniq):
            uniq.append(h)
    return uniq


# ---------------------------------------------------------------- sætninger
_ABBR_END = re.compile(r"(?:\bkl|\bfx|\bbl\.a|\bca|\bevt|\bosv|\bdvs|\bnr|\bf\.eks|\beks|\bm\.fl|\bpga|\bvha|\bhr|\bfru|\bmv|\bdr|\bmvh|\bd)\.$", re.I)


def split_sentences(text: str) -> list[str]:
    """Opdeler kun, hvor et punktum efterfølges af stort bogstav. "d. 12/10", "8. oktober" og "kl. 8.30" holdes samlet."""
    t = clean(text)
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-ZÆØÅ\"“(]|\d{1,2}\.[A-ZÆØÅ])|;\s+", t)
    merged: list[str] = []
    for p in pieces:
        if merged and _ABBR_END.search(merged[-1]):
            merged[-1] += " " + p
        else:
            merged.append(p)
    return [x.strip(" ;.") for x in merged if x.strip(" ;.")]


# ---------------------------------------------------------------- klokkeslæt
R_TRANGE = re.compile(r"(?:\bkl\.?[,\s]*)?(\d{1,2})[.:](\d{2})\s*(?:[-–—]|til)\s*(?:kl\.?\s*)?(\d{1,2})[.:](\d{2})", re.I)
R_TRANGE_H = re.compile(r"\bkl\.?\s*(\d{1,2})\s*(?:[-–—]|til)\s*(?:kl\.?\s*)?(\d{1,2})\b(?![.:/]\d)", re.I)
R_TIME = re.compile(r"\b(?:kl\.?|klokken)[,\s]*(\d{1,2})(?:[.:](\d{2}))?(?![\d/])", re.I)
R_TIME_TRIGGER = re.compile(r"\b(?:mødes|mødetid|afgang|afrejse|starter|begynder|hentes|afhentning|ankomst|fremmøde|kl)\W+(?:\w+\W+){0,3}?(\d{1,2})[.:](\d{2})(?![\d/])", re.I)


@dataclass
class TimeHit:
    start: tuple[int, int]
    end: tuple[int, int] | None
    a: int
    b: int


def _ok_hm(h: int, m: int) -> bool:
    return 0 <= h <= 23 and 0 <= m <= 59


def find_times(text: str, date_hits: list[DateHit]) -> list[TimeHit]:
    """Klokkeslæt og tidsrum. "kl.10:15 - 9:15" (slut før start) giver kun starten."""
    spans = [(h.a, h.b) for h in date_hits]
    out: list[TimeHit] = []

    def free(a: int, b: int) -> bool:
        return not any(a < sb and b > sa for sa, sb in spans + [(t.a, t.b) for t in out])

    for m in R_TRANGE.finditer(text):
        h1, m1, h2, m2 = int(m[1]), int(m[2]), int(m[3]), int(m[4])
        has_kl = bool(re.match(r"\s*kl", m[0], re.I))
        if not _ok_hm(h1, m1) or not _ok_hm(h2, m2) or not free(m.start(), m.end()):
            continue
        if not has_kl and not (m[2] in ("00", "15", "30", "45") and m[4] in ("00", "15", "30", "45")):
            continue                                                  # "9.10-10.10" kan lige så godt være datoer
        out.append(TimeHit((h1, m1), (h2, m2) if (h2, m2) > (h1, m1) else None, m.start(), m.end()))
    for m in R_TRANGE_H.finditer(text):
        h1, h2 = int(m[1]), int(m[2])
        if _ok_hm(h1, 0) and _ok_hm(h2, 0) and free(m.start(), m.end()):
            out.append(TimeHit((h1, 0), (h2, 0) if h2 > h1 else None, m.start(), m.end()))
    for m in R_TIME.finditer(text):
        h, mi = int(m[1]), int(m[2] or 0)
        if _ok_hm(h, mi) and free(m.start(), m.end()):
            out.append(TimeHit((h, mi), None, m.start(), m.end()))
    for m in R_TIME_TRIGGER.finditer(text):
        h, mi = int(m[1]), int(m[2])
        a, b = m.end() - len(m[1]) - len(m[2]) - 1, m.end()
        if _ok_hm(h, mi) and free(a, b):
            out.append(TimeHit((h, mi), None, a, b))
    out.sort(key=lambda t: t.a)
    return out


def span_of_times(times: list[TimeHit]) -> tuple[str | None, str | None]:
    """Tidligste start og seneste slut ("afgang kl. 12, turnering 13-15" → 12.00–15.00)."""
    if not times:
        return None, None
    start = min(t.start for t in times)
    ends = [t.end for t in times if t.end and t.end > start] + [t.start for t in times if t.start > start]
    end = max(ends) if ends else None
    f = lambda hm: f"{hm[0]:02d}:{hm[1]:02d}"
    return f(start), (f(end) if end else None)


# ---------------------------------------------------------------- steder
R_ADDR = re.compile(r"[A-ZÆØÅ][\wæøå]+(?:vej|gade|allé|alle|vænge|stræde|plads|torv|boulevard|park|sti)\s+\d+\w?(?:,?\s*\d{4}\s+[A-ZÆØÅ][\wæøå]+)?")
R_PLACE = re.compile(r"\b(?:på|ved|hos|til|i)\s+((?:[A-ZÆØÅ][\wæøå'’-]+)(?:\s+[A-ZÆØÅ][\wæøå'’-]+){0,3})")
_PLACE_STOP = {"Aula", "Jeres", "Jer", "Vi", "Jeg", "De", "Det", "Den", "Dem", "Danmark", "Sverige", "Mandag", "Tirsdag", "Onsdag",
               "Torsdag", "Fredag", "Lørdag", "Søndag", "Januar", "Februar", "Marts", "April", "Maj", "Juni", "Juli", "August",
               "September", "Oktober", "November", "December", "Skolen", "Klassen", "SFO", "Mvh", "Hilsen"}
_VENUE_IN = re.compile(r"\bi\s+((?:Festsalen|Hallen|Sporthallen|Gymnastiksalen|Aulaen|Kulturhuset|Biblioteket|Kirken|Svømmehallen|Idrætshallen|Medborgerhuset|Forsamlingshuset)\b)")


def find_location(text: str) -> str | None:
    m = R_ADDR.search(text)
    if m:
        return m[0].strip(" ,")
    for m in R_PLACE.finditer(text):
        first = m[1].split()[0]
        if first in _PLACE_STOP or text[m.start():m.start() + 2] == "i " and first not in {"Festsalen", "Hallen"}:
            continue
        return m[1].strip()
    m = _VENUE_IN.search(text)
    return m[1] if m else None


# ---------------------------------------------------------------- kategorier
@dataclass
class Cat:
    key: str
    label: str
    base: int
    rx: re.Pattern
    title: str | None = None


def _rx(p: str) -> re.Pattern:
    return re.compile(p, re.I)


CATS = [
    Cat("lejrskole", "Lejrskole / koloni", 3, _rx(r"\b(lejrskole\w*|koloni\w*|skolelejr\w*|sommerlejr\w*|vinterlejr\w*|overnatningstur\w*|weekendtur\w*)")),
    Cat("møde", "Møde / samtale", 3, _rx(r"\b(forældremøde\w*|skole[- ]?hjem[- ]?samtale\w*|forældresamtale\w*|informationsmøde\w*|opstartsmøde\w*|forældrearrangement\w*|forældrecafé\w*)")),
    Cat("test", "Test / prøve", 2, _rx(r"(?<!demo)(?<!pro)\b((?:nationale?\s+)?(?:færdigheds|national\w*\s+)?test(?:en|s)?\b|(?<!at )prøve(?:n|r)?\b|terminsprøve\w*|årsprøve\w*|afgangsprøve\w*|eksamen\w*|læsetest\w*)")),
    Cat("turnering", "Turnering / stævne", 2, _rx(r"\b(\w*turnering\w*|\w*stævne\w*|opvisning\w*|\w+cup\b)")),
    Cat("lukket", "Lukket / tidlig fri", 3, _rx(r"\b(lukkedag\w*|skolefri\b|(?:sfo|klub|børnehave|vuggestue)\w*\s+(?:holder\s+)?lukk\w*|holder\s+lukket|lukker\s+(?:kl|tidligt)|lukket\s+(?:om|på|i\b|d\.|den))")),
    Cat("omlagt", "Ændret skoledag", 2, _rx(r"\b(omlagt\s+(?:skole)?dag\w*|skolen\s+slutter\s+kl|tidlig\s+fri|fri\s+kl\.?\s*\d|starter\s+først\s+kl)")),
    Cat("tur", "Tur / udflugt", 2, _rx(r"\b(udflugt\w*|ekskursion\w*|tur\s+(?:til|ind\s+til|ud\s+til)\s+[A-ZÆØÅa-zæøå]\w+|(?:tager|skal|kører|cykler|går)\s+(?:vi\s+|alle\s+)?(?:ud\s+)?på\s+tur|på\s+tur\s+til|zoo\b|zoologisk\w*|museum\w*|teater\w*|biograf\w*|planetarium\w*|tivoli\b|naturcent\w*|naturskole\w*|besøg\s+(?:på|i|hos)\s+[A-ZÆØÅ]\w+)")),
    Cat("arrangement", "Arrangement", 2, _rx(r"\b(forestilling\w*|koncert\w*|fremvisning\w*|skolefest\w*|sommerfest\w*|julefest\w*|juleafslutning\w*|afslutning\w*|loppemarked\w*|åbent\s+hus|fællesspisning\w*|bankospil\w*|talentshow\w*|fernisering\w*)")),
]
CAT_BY_KEY = {c.key: c for c in CATS}
CAT_BY_KEY["fødselsdag"] = Cat("fødselsdag", "Fødselsdagsinvitation", 3, _rx(r"fødselsdag\w*"))

R_PAST = _rx(r"\b(tak\s+for|tusind\s+tak|var\s+en\b|var\s+på\b|har\s+været|havde\s+vi|fik\s+vi|i\s+går|sidste\s+(?:uge|gang|år)|tilbage\s+fra)")
R_THEME = _rx(r"\b(temadag\w*|temauge\w*|emneuge\w*|emnedag\w*|projektuge\w*|projektdag\w*|motionsdag\w*|kagedag|pyjamasdag|sjov\s+dag|fastelavn\w*|halloween\w*|legepatrulje\w*|teamdag|elevsamtale\w*)")
# Ændringer: en aflysning, udsættelse eller flytning er ikke en ny aktivitet, men en ændring af en, der allerede står i kalenderen
R_CANCEL = _rx(r"\b(aflyst\w*|aflyses|aflysning\w*|udgår|udgået|bliver\s+ikke\s+afholdt|afholdes\s+ikke|gennemføres\s+ikke|bliver\s+desværre\s+ikke)\b")
R_HOLD = _rx(r"\b(udsat\w*|udskudt\w*|udskydes)\b")
R_MOVE = _rx(r"\b(flyttes|flyttet|rykkes|rykket|rykker|ændres\s+til|ændret\s+til|ligger\s+nu)\b")
R_ANAPHORA = _rx(r"\b(det|den|turen|mødet|testen|prøven|arrangementet|lejrskolen|forestillingen|stævnet|turneringen|samtalen)\b")
R_EVENING = _rx(r"\bom\s+(?:aftenen|eftermiddagen)\b|\bi\s+aften\b")
# En aftale i skoletiden ("3. lektion", "i matematiktimen" er for usikkert): uden sluttid varer den en lektion
R_LESSON = re.compile(r"\b(?:\d\.\s*)?(?:lektion(?:en|er|erne)?|modul(?:et|er)?)\b", re.I)


def lesson_end_time(st: str | None, en: str | None, text: str, cat: str | None = None, minutes: int = 45) -> str | None:
    """Sluttid for en aktivitet med starttid men uden sluttid, når den ligger i en lektion (eller er en test/prøve)."""
    if not st or en or not (R_LESSON.search(text or "") or cat == "test"):
        return en
    import schedule
    return schedule.lesson_end(st, minutes)
R_UNSURE = _rx(r"\b(måske|eventuelt|evt\.|hvis\s+vejret|forbehold|ikke\s+afklaret)")
R_PARENTS = _rx(r"forældre\w*\s+(?:er\s+)?(?:velkomne|inviteret|indkaldt)|\bkom\s+og\b|\binviterer\b|alle\s+er\s+velkomne|pårørende|familie\w*\s+(?:er\s+)?velkomne|\bindkald")
R_BDAY = _rx(r"fødselsdag\w*|børnefødselsdag\w*")
R_INVITE = _rx(r"\binvit\w*|\bkom\s+(?:og|til)\b|\bsvar\s+(?:senest|inden)|\bRSVP\b|\bfest\b")
R_AWAY = _rx(r"\bhos\s+[A-ZÆØÅ]|\badresse|(?:vej|gade|allé|alle|vænge|stræde|plads|torv)\s+\d|\bbowling|\bbiograf|\blegeland|\btrampolin|\bcafé|\bpizzeria|\brestaurant|\bmcdonald|\bklatre\w*|\bhjem\s+til\s+os")
R_CLASS_CELEB = _rx(r"\bi\s+klassen\b|\bpå\s+stuen\b|\bi\s+børnehaven\b|\bi\s+sfo\b|\bfejrer\s+vi\b|\bflag\b|\bkage\b|\bfødselsdagsbarn")


_DEFINITE = [("mødet", "møde"), ("møderne", "møde"), ("skolen", "skole"), ("prøven", "prøve"), ("samtalen", "samtale"), ("samtalerne", "samtale"),
             ("turneringen", "turnering"), ("testen", "test"), ("testene", "test"), ("festen", "fest"), ("ningen", "ning"), ("koncerten", "koncert"),
             ("udflugten", "udflugt"), ("ekskursionen", "ekskursion"), ("kolonien", "koloni"), ("eksamen", "eksamen"), ("showet", "show")]


def _norm_word(w: str) -> str:
    """"forældremødet" → "forældremøde", "testen" → "test". Ukendte ord (fx stednavne) røres ikke."""
    w = w.strip().lower()
    for end, base in _DEFINITE:
        if w.endswith(end):
            return w[: -len(end)] + base
    return w


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def title_for(cat: str, sentence: str, subject: str, ctx: str, cats: dict | None = None) -> str:
    c = (cats or CAT_BY_KEY).get(cat)
    if c is not None and c.title:
        return c.title
    word = ""
    if c:
        for src in (subject, sentence):
            m = c.rx.search(src or "")
            if m:
                word = _norm_word(m[0]); break
    if cat == "lejrskole":
        return _cap(word or "lejrskole")
    if cat == "tur":
        m = re.search(r"\btur\s+(?:til|ind\s+til|ud\s+til)\s+([A-ZÆØÅa-zæøå][\wæøå]+(?:\s+[A-ZÆØÅ][\wæøå]+)?)", sentence) or re.search(r"\bpå\s+tur\s+til\s+([A-ZÆØÅ][\wæøå]+(?:\s+[A-ZÆØÅ][\wæøå]+)?)", sentence)
        if m:
            return f"Tur til {m[1]}"
        m = re.search(r"\b(zoo|zoologisk\w*|museum\w*|teater\w*|biograf\w*|planetarium\w*|tivoli|naturcent\w*|naturskole\w*)", sentence, re.I)
        return f"Tur til {_cap(m[0].lower())}" if m else _cap(word or "tur")
    if cat == "test":
        return _cap(re.sub(r"\s+", " ", word) or "test")
    if cat in ("møde", "arrangement", "turnering"):
        return _cap(word or CAT_BY_KEY[cat].label)
    if cat == "lukket":
        m = re.search(r"\b(sfo|klub|børnehave|vuggestue)\w*", sentence, re.I)
        if m and re.search(r"lukk", sentence, re.I):
            return f"{m[1].upper() if m[1].lower()=='sfo' else _cap(m[1].lower())} lukket"
        return "Lukkedag"
    if cat == "omlagt":
        t = R_THEME.search(ctx)
        return "Omlagt skoledag" + (f" ({_cap(_norm_word(t[0]))})" if t else "")
    if cat == "fødselsdag":
        m = re.search(r"fødselsdag\s+(?:hos|for)\s+([A-ZÆØÅ][\wæøå]+)", sentence)
        if m:
            return f"Fødselsdag hos {m[1]}"
        m = re.search(r"\b([A-ZÆØÅ][\wæøå]+)\s+har\s+fødselsdag", sentence)
        if m and m[1] not in {"Min", "Vores", "Hans", "Hendes", "Jeg", "Vi", "Det", "Der"}:
            return f"{m[1]}{'' if m[1].endswith('s') else 's'} fødselsdag"
        m = re.search(r"\b([A-ZÆØÅ][\wæøå]+?)s?\s+fødselsdag", sentence)
        if m and m[1] not in {"Min", "Vores", "Hans", "Hendes", "Din", "Jeres", "En", "Til", "Sin"}:
            return f"Fødselsdag hos {m[1]}"
        return "Fødselsdagsinvitation"
    return _cap(word)


# ---------------------------------------------------------------- udtræk
@dataclass
class Source:
    kind: str                                   # message | post | weekplan
    id: str
    label: str                                  # "Aula-besked", "Opslag", "Ugeplan"
    subject: str
    texts: list[tuple[str, dt.date]]            # (tekst, dato teksten er skrevet) – en tråd har flere
    people: list[str]
    default_date: dt.date | None = None         # ugeplan: punktets dag
    when: dt.date | None = None                 # hvornår kilden er fra (til beskrivelsen)


def _negated(s: str) -> bool:
    """"Der er ingen tur til Zoo …": en nægtelse lige før et kategoriord."""
    for c in CATS:
        m = c.rx.search(s)
        if m and re.search(r"\b(ingen|ikke)\b[\wæøå\s,]{0,25}$", s[:m.start()], re.I):
            return True
    return False


def _hit_cats(s: str, ctx: str, subject_has: set[str], disabled: set[str]) -> list[str]:
    """Kategorier i en sætning – efter at past/tema-reglerne er anvendt."""
    if R_PAST.search(s):
        return []
    hits = [c.key for c in CATS if c.key not in disabled and c.rx.search(s)]
    if "fødselsdag" not in disabled and R_BDAY.search(s) and (R_INVITE.search(ctx) or re.search(r"\bhos\s+[A-ZÆØÅ]", ctx)) and R_AWAY.search(ctx):
        if not R_CLASS_CELEB.search(ctx) or re.search(r"\bhos\s+[A-ZÆØÅ]|\badresse|(?:vej|gade|allé)\s+\d", ctx):
            hits.append("fødselsdag")
    if R_THEME.search(s):
        hits = [h for h in hits if h in ("omlagt", "lukket")]      # temadage mv. bliver i Aula – medmindre dagen ændres
    return hits


def _nearest(hits: list[DateHit], cat_span: tuple[int, int]) -> DateHit:
    return min(hits, key=lambda h: min(abs(h.a - cat_span[1]), abs(cat_span[0] - h.b)))


def _scan_text(text: str, ref: dt.date, subject: str, default_date: dt.date | None, cfg: dict) -> tuple[list[dict], list[dict]]:
    """Returnerer (kandidater i kategorier, generelle dato+tid-fund til den manuelle vej). Datoer er date-objekter."""
    disabled = set(cfg.get("disabled_categories", []))
    body = split_sentences(text)
    subj = (subject or "").strip(" .")
    sents = ([subj] if subj else []) + body
    info = []
    for s in sents:
        dh = find_dates(s, ref)
        info.append({"s": s, "dates": dh, "times": find_times(s, dh)})
    subj_cats = {c.key for c in CATS if subj and c.rx.search(subj)}
    cats_by_key = CAT_BY_KEY
    cands: list[dict] = []
    for i, it in enumerate(info):
        s = it["s"]
        ctx = " ".join(x["s"] for x in info[max(0, i - 1): i + 2])
        for key in _hit_cats(s, ctx, subj_cats, disabled):
            cat = cats_by_key[key]
            m = cat.rx.search(s)
            span = (m.start(), m.end()) if m else (0, 0)
            if s.rstrip().endswith("?"):
                continue                                               # et spørgsmål er ikke en melding
            nxt = info[i + 1]["s"] if i + 1 < len(info) else ""
            neg = bool(m and re.search(r"\b(ingen|ikke)\b[\wæøå\s,]{0,25}$", s[:m.start()], re.I))     # "ingen tur til Zoo"
            change = None
            if R_CANCEL.search(s) or neg or (R_CANCEL.search(nxt) and R_ANAPHORA.search(nxt)):
                change = "cancel"
            elif R_HOLD.search(s) or (R_HOLD.search(nxt) and R_ANAPHORA.search(nxt)):
                change = "hold"
            elif R_MOVE.search(s) and it["dates"]:
                change = "move"
            old_start = None
            via, hits_for = "", []
            # 1) i samme sætning (nærmeste dato til nøgleordet)  2) nabosætning uden eget nøgleord  3) punktets egen dag
            if it["dates"]:
                via, hits_for = "same", [_nearest(it["dates"], span)]
                if key in ("lejrskole", "tur") and len([h for h in it["dates"] if h.kind in ("date", "weekday")]) >= 2:
                    ds = [h for h in it["dates"] if h.kind in ("date", "weekday")]
                    lo, hi = min(h.start for h in ds), max(h.end for h in ds)
                    if (hi - lo).days <= 14:
                        hits_for = [DateHit(lo, hi, ds[0].a, ds[-1].b, "range")]
            else:
                for j in (i + 1, i - 1, i + 2):
                    if 0 <= j < len(info) and info[j]["dates"] and not _hit_cats(info[j]["s"], info[j]["s"], subj_cats, disabled):
                        via, hits_for = "neighbor", [info[j]["dates"][0]]
                        break
            if not hits_for and default_date:
                via, hits_for = "default", [DateHit(default_date, default_date, 0, 0, "date")]
            if change == "move":                                       # "flyttet fra 21/10 til 4/11" → gammel og ny dato
                ds = sorted([d for d in it["dates"] if d.kind != "week"], key=lambda d: d.a)
                mark = lambda d: ("fra" if re.search(r"\bfra\s*(?:den\s+|d\.\s*)?$", s[max(0, d.a - 10):d.a].lower())
                                  else "til" if re.search(r"\btil\s*(?:den\s+|d\.\s*)?$", s[max(0, d.a - 10):d.a].lower()) else "")
                old = next((d for d in ds if mark(d) == "fra"), None)
                new = next((d for d in reversed(ds) if mark(d) == "til"), None)
                if len(ds) == 1 and ds[0].kind == "range":                   # "fra X til Y" blev læst som et interval: X er gammel, Y ny
                    old, new = DateHit(ds[0].start, ds[0].start, ds[0].a, ds[0].b, "date"), DateHit(ds[0].end, ds[0].end, ds[0].a, ds[0].b, "date")
                elif len(ds) >= 2:
                    old, new = old or ds[0], new or ds[-1]
                    if old is new:
                        old = None
                elif old is not None and new is None:
                    change, hits_for, via = "hold", [old], "same"       # kun en gammel dato nævnt: aftalen er rykket, men ikke til hvornår
                else:
                    new = new or (ds[0] if ds else None)
                if change == "move" and new is not None:
                    hits_for, via, old_start = [new], "same", (old.start if old else None)
            if not hits_for and change in ("cancel", "hold"):
                hits_for, via = [None], "none"                          # "Forældremødet er udsat" – uden dato kobles til en aftale via titlen
            if not hits_for:
                continue
            h = hits_for[0]
            # klokkeslæt: sætningen, datosætningen og en efterfølgende sætning uden egen dato
            times = list(it["times"])
            if via == "neighbor":
                j = next(j for j in (i + 1, i - 1, i + 2) if 0 <= j < len(info) and info[j]["dates"])
                times += info[j]["times"]
            if not times and i + 1 < len(info) and not info[i + 1]["dates"]:
                times += info[i + 1]["times"]
            st, en = span_of_times(times)
            if st and R_EVENING.search(s) and int(st[:2]) < 12:        # "kl. 7 om aftenen" er 19.00
                st = f"{int(st[:2]) + 12:02d}{st[2:]}"
                en = f"{int(en[:2]) + 12:02d}{en[2:]}" if en and int(en[:2]) < 12 else en
            en = lesson_end_time(st, en, s, key)
            loc = find_location(s) or (find_location(info[i + 1]["s"]) if i + 1 < len(info) else None)
            if key == "arrangement" and not (R_PARENTS.search(ctx) or (st and st >= "15:00")):
                continue                                               # skolens interne show o.l. er ikke et familieanliggende
            score = (cat.base + (1 if via == "same" else 0) + (1 if st else 0) + (1 if loc else 0) + (1 if key in subj_cats else 0)
                     + (1 if change else 0) - (1 if R_UNSURE.search(s) else 0))
            if score < 3:
                continue
            cands.append({"cat": key, "label": cat.label, "title": title_for(key, s, subj, ctx, cats_by_key),
                          "start": h.start if h else None, "end": h.end if h else None, "change": change, "old_start": old_start,
                          "start_time": st, "end_time": en, "location": loc, "score": score,
                          "confidence": "høj" if score >= 5 else "middel", "evidence": s, "idx": i, "via": via,
                          "reason": f"nævner «{(m[0] if m else key).strip()}»" + {"same": " og en dato", "neighbor": " og en dato i nabosætningen", "default": " på ugeplanens dag", "none": ""}[via]})
    # samme kategori i samme tekst: behold det mest specifikke datointerval (en dag slår en hel uge)
    out: list[dict] = [c for c in cands if c["change"]]
    for c in sorted((c for c in cands if not c["change"]), key=lambda c: ((c["end"] - c["start"]).days, -c["score"])):
        if any(o["cat"] == c["cat"] and o["start"] <= c["start"] and c["end"] <= o["end"] and (c["end"] - c["start"]).days > (o["end"] - o["start"]).days for o in out):
            continue
        if any(o["cat"] == c["cat"] and o["start"] == c["start"] for o in out):
            continue
        out.append(c)

    # --- manuel vej: én mulighed pr. entydig dato (sætninger med flere forskellige datoer springes over)
    groups: dict[tuple, dict] = {}
    for i, it in enumerate(info):
        nxt = info[i + 1]["s"] if i + 1 < len(info) else ""
        if (R_CANCEL.search(it["s"]) or R_HOLD.search(it["s"]) or R_MOVE.search(it["s"]) or it["s"].rstrip().endswith("?") or _negated(it["s"])
                or ((R_CANCEL.search(nxt) or R_HOLD.search(nxt)) and R_ANAPHORA.search(nxt))):
            continue
        dh = [h for h in it["dates"] if h.kind != "week"]
        distinct = {(h.start, h.end) for h in dh}
        if len(distinct) != 1:
            continue                                                  # ingen eller flere forskellige datoer: ikke entydigt
        h = dh[0]
        times = list(it["times"])
        if not times and i + 1 < len(info) and not info[i + 1]["dates"]:
            times = info[i + 1]["times"]
        g = groups.setdefault((h.start, h.end), {"start": h.start, "end": h.end, "times": [], "sents": [], "idx": i, "loc": None})
        g["times"] += times
        g["sents"].append(it["s"])
        g["loc"] = g["loc"] or find_location(it["s"])
    if default_date:                                                  # ugeplan: punktets dag + tider i alle sætninger uden egen dato
        undated = [it for it in info if not it["dates"] and it["times"]]
        times = [t for it in undated for t in it["times"]]
        if times and (default_date, default_date) not in groups:
            groups[(default_date, default_date)] = {"start": default_date, "end": default_date, "times": times, "idx": 0,
                                                     "sents": [it["s"] for it in undated][:3],
                                                     "loc": next((find_location(it["s"]) for it in info if find_location(it["s"])), None)}
    options: list[dict] = []
    for g in groups.values():
        st, en = span_of_times(g["times"])
        if not st:
            continue                                                  # kræver mindst et starttidspunkt
        ev = " ".join(g["sents"])
        en = lesson_end_time(st, en, ev)
        theme = R_THEME.search(ev)
        options.append({"start": g["start"], "end": g["end"], "start_time": st, "end_time": en, "location": g["loc"],
                        "evidence": ev, "idx": g["idx"], "theme": _cap(_norm_word(theme[0])) if theme else None})
    return out, options


# ---------------------------------------------------------------- samling
def _sha(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _date(value) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _change_hits_option(ch: dict, o: dict, src: "Source", stems) -> bool:
    """Har en senere aflysning/flytning gjort en manuel mulighed ugyldig? (Samme dato, og titlerne ligner hinanden.)"""
    if ch["src"].when and src.when and src.when > ch["src"].when:
        return False
    gone = ch["old_start"] if ch["change"] == "move" else ch["start"]
    if gone is None or not (o["start"] <= gone <= o["end"]):
        return False
    return bool(stems(ch["title"]) & stems(o["evidence"] + " " + src.subject))


def calendar_title(title: str, people: list[str], people_map: dict[str, str]) -> str:
    """Familiekalenderen tildeler aftaler til personer ud fra navne i titlen: "Hugo + Carla: Tur til Zoo"."""
    names = [people_map[i] for i in people if i in people_map]
    return f"{' + '.join(names)}: {title}" if names else title


def build_sources(data: dict, today: dt.date, max_age: int) -> list[Source]:
    out: list[Source] = []
    cutoff = today - dt.timedelta(days=max_age)
    for m in data.get("messages", []):
        if m.get("private") or m.get("category") in ("samtale", "tom"):
            continue                                                  # private samtaler bliver aldrig til forslag
        thread = m.get("thread") or [{"text": m.get("text", ""), "timestamp": m.get("timestamp")}]
        texts = [(x["text"], _date(x.get("timestamp") or m.get("timestamp"))) for x in thread if x.get("text")]
        texts = [(t, d) for t, d in texts if d and d >= cutoff]
        if texts:
            out.append(Source("message", m["id"], "Aula-besked", m.get("subject") or "", texts, m.get("people") or [], None, _date(m.get("timestamp"))))
    for p in data.get("posts", []):
        d = _date(p.get("timestamp"))
        if d and d >= cutoff and p.get("text"):
            out.append(Source("post", p["id"], "Aula-opslag", p.get("title") or "", [(p["text"], d)], p.get("people") or [], None, d))
    for w in data.get("weekplan", []):
        d = _date(w.get("date"))
        if not d or d < today or w.get("category") == "anden_klasse" or not w.get("text"):
            continue
        out.append(Source("weekplan", w["id"], "Ugeplan", w.get("subject") or w.get("title") or "", [(w["text"], d)], [w["person"]], d, d))
    return out


def _stems(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[\wæøå]{5,}", text.lower())}


def in_calendar(cal_events: list[dict], start: dt.date, end: dt.date, title: str) -> bool:
    """Står der allerede noget lignende i familiekalenderen samme dag?"""
    mine = _stems(title)
    for e in cal_events:
        s, en = _date(e.get("start")), _date(e.get("end")) or _date(e.get("start"))
        if s and en and s <= end and en >= start and mine & _stems(e.get("title", "")):
            return True
    return False


def _fallback_title(src: Source, evidence: str, theme: str | None) -> str:
    if theme:
        return theme
    if src.kind == "weekplan":
        tm = find_times(evidence, find_dates(evidence, dt.date.today()))
        before = evidence[:tm[0].a] if tm else evidence
        before = re.sub(r"\bkl\.?[,\s]*$", "", before.strip(), flags=re.I).strip(" -–—:.,")
        t = before if len(before) >= 3 else re.sub(r"\s{2,}", " ", evidence[tm[0].b:] if tm else evidence).strip(" -–—:.,")
        t = re.split(r"\s[-–—]\s|:\s", t)[0].strip()
        return (t[:50].rsplit(" ", 1)[0] + "…") if len(t) > 50 else (t or src.subject)
    return (src.subject or evidence[:60]).strip(" .")


def _describe(src: Source, evidence: str) -> str:
    when = f" ({src.when.day}/{src.when.month})" if src.when else ""
    subj = f" «{src.subject.strip(' .')}»" if src.subject else ""
    return f"Fra {src.label}{subj}{when}\n\n{evidence.strip()[:500]}\n\nOprettet fra Familieplan"


CHANGE_KINDS = ("cancel", "hold", "move")       # aflyst, udsat, flyttet – de eneste forslag, appen viser (på beskeden selv)


def find_all(data: dict, cfg: dict, today: dt.date, people_map: dict[str, str],
             targets: list[dict] | None = None) -> tuple[list[dict], dict[str, list[dict]]]:
    """Returnerer (forslag, manuelle muligheder pr. kilde-id). Datoer er ISO-tekster.

    targets: aftaler, der allerede står i kalenderen ({key, event_id, title, start, end, source}). En aflysning, udsættelse eller
    flytning kobles til en af dem; findes den ikke, er der intet at aflyse (og en flytning behandles som en ny aktivitet)."""
    scfg = cfg.get("suggestions", {})
    targets = targets or []
    horizon = today + dt.timedelta(days=int(scfg.get("horizon_days", 270)))
    cal = [e for e in data.get("events", []) if e.get("source") == "google"]
    raw: list[dict] = []
    opts_raw: dict[str, list[dict]] = {}
    for src in build_sources(data, today, int(scfg.get("max_age_days", 120))):
        for text, ref in src.texts:
            cands, options = _scan_text(text, ref, src.subject, src.default_date, scfg)
            for c in cands:
                c["src"] = src
                raw.append(c)
            for o in options:
                o["src"] = src
                opts_raw.setdefault(src.id, []).append(o)

    def cat_key(c: dict) -> str:
        return c["cat"]

    # --- ændringer: aflysning / udsættelse / flytning
    changes = [c for c in raw if c.get("change")]
    raw = [c for c in raw if not c.get("change")]

    def supersedes(ch: dict, cr: dict) -> bool:
        """Udelukker en senere (eller samtidig) aflysning/flytning et tidligere forslag om det samme?"""
        if cat_key(cr) != cat_key(ch) or (cr["src"].when and ch["src"].when and cr["src"].when > ch["src"].when):
            return False
        gone = ch["old_start"] if ch["change"] == "move" else ch["start"]
        if gone:
            return cr["start"] <= gone <= cr["end"]
        return cr["title"].lower() == ch["title"].lower() and cr["start"] != ch["start"]

    raw = [cr for cr in raw if not any(supersedes(ch, cr) for ch in changes)]
    names_l = {n.lower() for n in people_map.values()}
    stop = {"til", "med", "hos", "den", "det", "for", "fra", "der", "som", "skal", "vil", "kan", "har", "bliver"}

    def stems(t: str) -> set[str]:
        return {w[:6] for w in re.findall(r"[\wæøå]{3,}", t.lower()) if w not in stop and w not in names_l}

    def find_target(ch: dict) -> dict | None:
        ref = ch["old_start"] if ch["change"] == "move" else ch["start"]
        mine, hits = stems(ch["title"]), []
        for t in targets:
            if t["end"] < today.isoformat() or not (mine & stems(t["title"])):
                continue
            if ref is None or (t["start"] <= (ref if ch["change"] == "move" else (ch["end"] or ref)).isoformat() and t["end"] >= ref.isoformat()):
                hits.append(t)
        if not hits or (ref is None and len(hits) != 1):
            return None                                                # uden dato kobler vi kun, hvis der er netop én mulig aftale
        return sorted(hits, key=lambda t: (t["source"] != "app", t["start"]))[0]

    suggestions: list[dict] = []
    seen_change: set[str] = set()
    for ch in changes:
        tgt = find_target(ch)
        if not tgt:
            if ch["change"] == "move" and ch["start"] and today <= ch["start"] <= horizon:
                raw.append({**ch, "change": None})                     # ukendt gammel aftale: behandl det som en ny aktivitet på den nye dato
            continue
        kind = ch["change"]
        start = ch["start"] or dt.date.fromisoformat(tgt["start"])
        end = ch["end"] or dt.date.fromisoformat(tgt["end"]) if kind == "move" else dt.date.fromisoformat(tgt["end"])
        if kind != "move":
            start = dt.date.fromisoformat(tgt["start"])
        sid = "sg_" + _sha("ændring", kind, start, ch["old_start"], tgt["key"] or tgt["title"].lower())
        if sid in seen_change:
            continue
        seen_change.add(sid)
        src = ch["src"]
        suggestions.append({
            "id": sid, "type": "suggestion", "kind": kind, "category": "ændring", "label": {"cancel": "Aflyst", "hold": "Udsat", "move": "Flyttet"}[kind],
            "title": ch["title"], "calendar_title": tgt["title"], "start": start.isoformat(), "end": end.isoformat(),
            "old_start": ch["old_start"].isoformat() if ch["old_start"] else tgt["start"],
            "all_day": not ch["start_time"], "start_time": ch["start_time"], "end_time": ch["end_time"], "location": ch["location"],
            "people": [p for p in src.people if p != "family"], "description": _describe(src, ch["evidence"]),
            "confidence": ch["confidence"], "reason": ch["reason"],
            "sources": [{"type": src.kind, "id": src.id, "title": src.subject.strip(" ."), "label": src.label, "date": src.when.isoformat() if src.when else None}],
            "target": {k: tgt[k] for k in ("key", "event_id", "title", "start", "end", "source")},
        })

    # forslag: slå ens aktiviteter sammen på tværs af kilder (samme kategori og startdag)
    merged: dict[tuple, dict] = {}

    def named(k: str) -> bool:
        return k in ("fødselsdag", "tur")                 # her er titlen en del af identiteten (hvem / hvor)

    for c in sorted(raw, key=lambda c: -c["score"]):
        if not (today <= c["start"] <= horizon):
            continue
        key = (cat_key(c), c["start"], c["title"].lower() if named(cat_key(c)) else "")
        m = merged.get(key)
        if not m:
            merged[key] = m = {**c, "sources": [], "people": []}
        for f in ("start_time", "end_time", "location"):
            m[f] = m.get(f) or c.get(f)
        if c["src"].id not in {x["id"] for x in m["sources"]}:
            m["sources"].append({"type": c["src"].kind, "id": c["src"].id, "title": c["src"].subject.strip(" ."), "label": c["src"].label,
                                 "date": c["src"].when.isoformat() if c["src"].when else None})
        for p in c["src"].people:
            if p != "family" and p not in m["people"]:
                m["people"].append(p)
        m["evidences"] = m.get("evidences", []) + [(c["src"], c["evidence"])]
    for m in merged.values():
        if in_calendar(cal, m["start"], m["end"], m["title"]):
            continue
        ck = cat_key(m)
        sid = "sg_" + _sha(ck, m["start"], m["title"].lower() if named(ck) else "")
        src0, ev0 = m["evidences"][0]
        suggestions.append({
            "id": sid, "type": "suggestion", "category": ck, "label": m["label"], "title": m["title"],
            "calendar_title": calendar_title(m["title"], m["people"], people_map),
            "start": m["start"].isoformat(), "end": m["end"].isoformat(), "all_day": not m["start_time"],
            "start_time": m["start_time"], "end_time": m["end_time"], "location": m["location"], "people": m["people"],
            "description": _describe(src0, ev0), "confidence": m["confidence"], "reason": m["reason"], "sources": m["sources"],
        })
    suggestions.sort(key=lambda x: (x["start"], x["title"]))

    # manuelle muligheder: enhver entydig dato + starttid, uanset kategori
    by_source_start = {(s["id"], sg["start"]): sg for sg in suggestions for s in sg["sources"]}
    options: dict[str, list[dict]] = {}
    for sid_, lst in opts_raw.items():
        seen = set()
        for o in lst:
            if not (today <= o["start"] <= horizon):
                continue
            src: Source = o["src"]
            if any(_change_hits_option(ch, o, src, stems) for ch in changes):
                continue
            sg = by_source_start.get((src.id, o["start"].isoformat()))
            people = [p for p in src.people if p != "family"]
            title = sg["title"] if sg else _fallback_title(src, o["evidence"], o.get("theme"))
            oid = sg["id"] if sg else "ev_" + _sha(src.kind, src.id, o["start"], o["start_time"])
            if oid in seen:
                continue
            seen.add(oid)
            options.setdefault(sid_, []).append({
                "id": oid, "title": title, "calendar_title": calendar_title(title, people, people_map),
                "start": o["start"].isoformat(), "end": o["end"].isoformat(), "start_time": o["start_time"], "end_time": o["end_time"],
                "all_day": False, "location": o["location"], "people": people, "description": _describe(src, o["evidence"]),
                "exists": in_calendar(cal, o["start"], o["end"], title),
                "source": {"type": src.kind, "id": src.id, "title": src.subject.strip(" ."), "label": src.label},
            })
    return suggestions, options
