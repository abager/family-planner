"""Overblik uden sprogmodel.

Den svære del – at finde datoer, frister, hvem det gælder og hvad der er usædvanligt – er allerede
løst af jeres egne regler (homework.py, messages.py, schedule.py). Her sorteres resultatet bare
i faste afsnit med skabelon-tekst. Intet forlader maskinen, og det koster ingenting.

Overblikket handler kun om det, der er SÆRLIGT for dagen/ugen: ting at huske, frister, og afvigelser
(vikar, lukkedag, noter, praktisk info). Almindelige aftaler og nyheder fra skolen er med vilje
udeladt – de står i "Dagens aftaler" og i Beskeder/Feed. Stående lektier ("hver dag") nævnes kun
i ugeoverblikket.

Hvad man ikke får, sammenlignet med Claude-versionen: ingen skønsmæssig prioritering på tværs af
kilder, ingen smidig formulering, og ingen viden om familiens egne regler.

Input er det samme privatlivsfiltrerede uddrag som sendes til Claude (build_digest i briefing.py),
så private samtaler indgår aldrig, heller ikke her – overblikket vises jo på en fælles skærm.
"""
from __future__ import annotations

import weather
import datetime as dt
import re

DAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
CLOSURE_RX = re.compile(r"\b(lukkedag|lukket|skolefri|fri\b|ferie|temadag|motionsdag|skolefoto|aflyst|omlagt|sommerfest|julefrokost)", re.I)
DAY_CAPS = {"Husk": 8, "Skal gøres": 6, "Praktisk info": 6, "Kommende frister": 5}
WEEK_CAPS = {"Skal gøres": 8, "Husk og lektier": 12, "Praktisk info": 8}
LOOKAHEAD_DAYS = 21


# ---------------------------------------------------------------- små hjælpere
def _date(s: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def _times(s: str | None) -> str:
    return (s or "").replace(":", ".")


def _join(names: list[str]) -> str:
    names = [n for n in names if n]
    return names[0] if len(names) == 1 else (", ".join(names[:-1]) + " og " + names[-1] if names else "")


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _names(hvem: list[str] | None) -> list[str]:
    return [n for n in (hvem or []) if n and n != "hele familien"]


def _prefix(hvem: list[str] | None) -> str:
    n = _names(hvem)
    return f"{_join(n)}: " if n else ""


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def day_label(d: dt.date, today: dt.date) -> str:
    """"i dag", "i morgen", "torsdag" (inden for en uge) eller "mandag 19/10"."""
    diff = (d - today).days
    if diff == 0:
        return "i dag"
    if diff == 1:
        return "i morgen"
    if 2 <= diff <= 6:
        return DAYS[d.weekday()]
    return f"{DAYS[d.weekday()]} {d.day}/{d.month}"


def _deadline(frist: str | None, today: dt.date) -> str:
    d = _date(frist)
    if d is None:
        return "ingen frist angivet"
    if d < today:
        return f"frist overskredet ({DAYS[d.weekday()]} {d.day}/{d.month})"
    return f"senest {day_label(d, today)}"


class _Out:
    """Samler afsnit og slår kilde-id'er op til læsbare kilder."""

    def __init__(self, digest: dict, caps: dict[str, int]):
        self.caps = caps
        self.refs = digest.get("_refs", {})
        self.sections: dict[str, list[dict]] = {}

    def add(self, section: str, text: str, hvem: list[str] | None = None, refs: list[str] | None = None,
            kilder: list[str] | None = None) -> None:
        sources = kilder if kilder is not None else [self.refs[r] for r in (refs or []) if r in self.refs]
        self.sections.setdefault(section, []).append(
            {"tekst": text, "hvem": _names(hvem), "refs": refs or [], "kilder": sources})

    def result(self, order: list[str]) -> list[dict]:
        out = []
        for title in order:
            items = self.sections.get(title) or []
            cap = self.caps.get(title, 8)
            if items:
                extra = len(items) - cap
                shown = items[:cap]
                if extra > 0:
                    shown.append({"tekst": f"…og {extra} mere – se i appen", "hvem": [], "refs": [], "kilder": []})
                out.append({"titel": title, "punkter": shown})
        return out


# ---------------------------------------------------------------- fælles udtræk
def _family_order(digest: dict, names: list[str]) -> list[str]:
    order = [p["navn"] for p in digest.get("familie", [])]
    return sorted(names, key=lambda n: order.index(n) if n in order else 99)


def _vikar_by_child(digest: dict, only: dt.date | None = None) -> dict[str, list[tuple[dt.date, str, str]]]:
    """barn → [(dato, fag, tid)]. Dobbelttimer (samme fag i træk) tæller som én, med starttidspunktet."""
    res: dict[str, list[tuple[dt.date, str, str]]] = {}
    for s in digest.get("skema", []):
        d = _date(s.get("dato"))
        if d is None or (only and d != only):
            continue
        for child in _names(s.get("hvem")):
            seen: list[str] = []
            for v in sorted(s.get("vikar", []), key=lambda v: v["tid"]):
                if v["fag"] not in seen:
                    seen.append(v["fag"])
                    res.setdefault(child, []).append((d, v["fag"], v["tid"]))
    return res


def _is_closure(e: dict) -> bool:
    return bool(e.get("hele_dagen")) and bool(CLOSURE_RX.search(e.get("titel") or ""))


def _info_text(w: dict) -> str:
    fag = w.get("fag") or ""
    txt = (w.get("info") or "").strip()
    if not txt:
        txt = re.split(r"(?<=[.!?])\s+", (w.get("tekst") or "").strip())[0]
    txt = txt if len(txt) <= 120 else txt[:119].rsplit(" ", 1)[0] + "…"
    return f"{fag}: {txt}" if fag and txt else (txt or fag)


def _classify_tasks(tasks: list[dict], today: dt.date) -> dict[str, list[dict]]:
    """Sortér opgaver til dagens afsnit. Stående lektier ("hver dag") er ikke særlige for netop i dag."""
    buckets: dict[str, list[dict]] = {"Husk": [], "Skal gøres": [], "Kommende frister": []}
    for t in tasks:
        due = _date(t.get("frist"))
        if t["type"] == "skal gøres af forældre":
            if due is None or due <= today + dt.timedelta(days=7):
                buckets["Skal gøres"].append(t)
            elif due <= today + dt.timedelta(days=LOOKAHEAD_DAYS):
                buckets["Kommende frister"].append(t)
        elif t.get("gentages"):
            continue
        elif due == today:
            buckets["Husk"].append(t)
        elif due is None or today < due <= today + dt.timedelta(days=LOOKAHEAD_DAYS):
            buckets["Kommende frister"].append(t)
    far = dt.date.max
    buckets["Skal gøres"].sort(key=lambda t: (_date(t.get("frist")) or far))
    buckets["Kommende frister"].sort(key=lambda t: (_date(t.get("frist")) or far))
    return buckets


def _src_task(t: dict) -> list[str]:
    subj = (t.get("fra_besked") or "").strip(" .")
    return [f"Besked: {subj if len(subj) <= 50 else subj[:49].rsplit(' ', 1)[0] + '…'}"] if subj else [t.get("kilde") or "Aula"]


def _task_text(t: dict, today: dt.date, with_deadline: bool) -> str:
    maybe = "Muligvis: " if t.get("sikkerhed") == "middel" else ""
    text = f"{_prefix(t.get('hvem'))}{maybe}{t['titel']}"
    if t.get("gentages"):
        text += " (hver dag)"
    if with_deadline:
        text += f" – {_deadline(t.get('frist'), today)}"
    return text


# ---------------------------------------------------------------- dagens overblik
def _special(out: "_Out", digest: dict, today: dt.date | None) -> tuple[list[dict], dict]:
    """Afsnittet "Praktisk info": vikarer, lektionsnoter, lukkedage og praktisk info. today = dagsoverblik, ellers ugens."""
    week = today is None
    closures = [e for e in digest.get("aftaler", []) if _is_closure(e) and (week or _date(e["dato"]) == today)]
    for e in sorted(closures, key=lambda e: e["dato"]):
        d = _date(e["dato"])
        label = f"{_cap(DAYS[d.weekday()])}: " if week else ""
        out.add("Praktisk info", f"{label}{_prefix(e['hvem'])}{e['titel']}", e["hvem"], [e["id"]], kilder=[e.get("kilde") or "Aula"])

    vikar = _vikar_by_child(digest, today)
    for child in _family_order(digest, list(vikar)):
        items = sorted(vikar[child], key=lambda x: (x[0], x[2]))
        if week:
            by_day: dict[dt.date, list[str]] = {}
            for d, fag, _ in items:
                by_day.setdefault(d, []).append(fag)
            text = f"{child} har vikar " + _join([f"{DAYS[d.weekday()]} ({_join(f)})" for d, f in sorted(by_day.items())])
        else:
            text = f"{child} har vikar i " + _join([f"{fag} ({_times(tid)})" for _, fag, tid in items])
        out.add("Praktisk info", text, [child], kilder=["Skema"])

    for s_ in digest.get("skema", []):
        d = _date(s_.get("dato"))
        if d is None or (not week and d != today):
            continue
        for n in s_.get("noter", []):
            label = f"{_cap(DAYS[d.weekday()])}: " if week else ""
            out.add("Praktisk info", f"{label}{_prefix(s_['hvem'])}note til {n['fag']} ({_times(n['tid'])}): {n['tekst']}", s_["hvem"], kilder=["Skema"])

    for w in sorted(digest.get("ugeplan", []), key=lambda w: w["dato"]):
        if w["kategori"] == "info" and (week or _date(w["dato"]) == today):
            d = _date(w["dato"])
            label = f"{_cap(DAYS[d.weekday()])}: " if week else ""
            out.add("Praktisk info", f"{label}{_prefix(w['hvem'])}{_info_text(w)}", w["hvem"], [w["id"]], kilder=["Ugeplan (Meebook)"])
    return closures, vikar


def day_briefing(digest: dict, today: dt.date, real_today: dt.date | None = None) -> dict:
    """today = dagen overblikket handler om (efter kl. 18: i morgen). real_today = dagens rigtige dato, som frister måles mod."""
    real = real_today or today
    out = _Out(digest, DAY_CAPS)
    tasks = _classify_tasks(digest.get("opgaver", []), today)

    for t in tasks["Husk"]:
        out.add("Husk", _task_text(t, today, False), t["hvem"], [t["id"]], kilder=_src_task(t))
    for t in tasks["Skal gøres"]:
        out.add("Skal gøres", _task_text(t, real, True), t["hvem"], [t["id"]], kilder=_src_task(t))
    _special(out, digest, today)
    for t in tasks["Kommende frister"]:
        due = _date(t.get("frist"))
        when = f"frist {day_label(due, real)}" if due else "ingen dato i beskeden"
        maybe = "Muligvis: " if t.get("sikkerhed") == "middel" else ""
        out.add("Kommende frister", f"{_prefix(t['hvem'])}{maybe}{t['titel']} – {when}", t["hvem"], [t["id"]], kilder=_src_task(t))

    _weather(out, digest, [today])
    return {"afsnit": out.result(["Vejr", "Husk", "Skal gøres", "Praktisk info", "Kommende frister"])}


def _weather(out, digest: dict, days: list[dt.date] | None) -> None:
    """Ét punkt pr. dag med vejr fra MET Norway: kort prognose og råd. days=None: alle dage i perioden (ugen)."""
    for v in digest.get("vejr", []):
        d = _date(v.get("dato"))
        if days is not None and d not in days:
            continue
        text = weather.short_text(v)
        if days is None:
            text = f"{_cap(v['ugedag'])}: {text}"
        else:
            text = _cap(text)
        if v.get("raad"):
            text += " – " + ", ".join(v["raad"])
        out.add("Vejr", text, [], [v["id"]], kilder=["Vejr (MET Norway)"])


# ---------------------------------------------------------------- ugens overblik
def week_briefing(digest: dict, start: dt.date, end: dt.date, today: dt.date) -> dict:
    out = _Out(digest, WEEK_CAPS)
    tasks = digest.get("opgaver", [])
    far = dt.date.max

    actions = sorted((t for t in tasks if t["type"] == "skal gøres af forældre"), key=lambda t: _date(t.get("frist")) or far)
    for t in actions:
        out.add("Skal gøres", _task_text(t, today, True), t["hvem"], [t["id"]], kilder=_src_task(t))

    deadlines = [t for t in tasks if t["type"] != "skal gøres af forældre"]
    for t in sorted(deadlines, key=lambda t: (not t.get("gentages"), _date(t.get("frist")) or far)):
        due = _date(t.get("frist"))
        if t.get("gentages"):
            when = "hver dag"
        elif due and start <= due <= end:
            when = DAYS[due.weekday()]
        else:
            when = f"{DAYS[due.weekday()]} {due.day}/{due.month}" if due else "ingen dato"
        maybe = "Muligvis: " if t.get("sikkerhed") == "middel" else ""
        out.add("Husk og lektier", f"{_cap(when)}: {_prefix(t['hvem'])}{maybe}{t['titel']}", t["hvem"], [t["id"]], kilder=_src_task(t))

    _special(out, digest, None)

    _weather(out, digest, None)
    return {"afsnit": out.result(["Vejr", "Skal gøres", "Husk og lektier", "Praktisk info"])}


def offline_briefing(digest: dict, mode: str, now: dt.datetime) -> dict:
    """mode: "day" eller "week". Returnerer {afsnit} i samme format som Claude-versionen."""
    start, end = dt.date.fromisoformat(digest["periode"]["fra"]), dt.date.fromisoformat(digest["periode"]["til"])
    return week_briefing(digest, start, end, now.date()) if mode == "week" else day_briefing(digest, start, now.date())
