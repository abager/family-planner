"""Test af aktivitetsgenkendelsen (activities.py).

  python activities_test.py                    # konstruerede tilfælde
  python activities_test.py aula_feed.json     # + dine egne beskeder (datoen sættes til beskedens dato)
"""
import datetime as dt
import json
import sys

import activities as A

PEOPLE = {"hugo": "Hugo", "carla": "Carla", "leo": "Leo"}
D = dt.date.fromisoformat


def run(kind, subject, text, ref, people=("hugo",), default=None, today=None):
    ref = D(ref)
    data = {"messages": [], "posts": [], "weekplan": [], "events": []}
    if kind == "message":
        data["messages"] = [{"id": "msg:1", "subject": subject, "text": text, "timestamp": ref.isoformat() + "T10:00+02:00", "people": list(people)}]
    elif kind == "post":
        data["posts"] = [{"id": "post:1", "title": subject, "text": text, "timestamp": ref.isoformat() + "T10:00+02:00", "people": list(people)}]
    else:
        data["weekplan"] = [{"id": "w1", "person": people[0], "date": (default or ref).isoformat() if hasattr(default or ref, "isoformat") else default, "subject": subject, "text": text}]
    sg, opts = A.find_all(data, {}, D(today) if today else ref, PEOPLE)
    return sg, [o for lst in opts.values() for o in lst]


def brief(s):
    t = f"{s['start']}" + (f"–{s['end']}" if s["end"] != s["start"] else "") + (f" {s['start_time']}" + (f"–{s['end_time']}" if s.get("end_time") else "") if s.get("start_time") else "")
    return f"{s.get('category', 'ev'):10} {s['title']!r:34} {t}  {s.get('location') or ''}"


CASES = [
    # (navn, art, emne, tekst, beskeddato, forventet kategori|None, forventet start, forventet starttid, ekstra)
    ("Zoo-tur", "message", "Tur til Zoo", "Kære forældre. Vi tager på tur til Zoo torsdag den 8. oktober. Vi mødes på skolen kl. 8.30, og er tilbage kl. 14.00. Husk madpakke.", "2026-10-01", "tur", "2026-10-08", "08:30"),
    ("Lejrskole med interval", "message", "Lejrskole", "Lejrskolen ligger fra mandag d. 12/10 til fredag d. 16/10. Afrejse kl. 6.30 fra skolen.", "2026-09-20", "lejrskole", "2026-10-12", "06:30"),
    ("Koloni med måned", "message", "Koloni", "Koloniturene afholdes 5.-9. oktober. Vi mødes kl. 7.45.", "2026-09-20", "lejrskole", "2026-10-05", "07:45"),
    ("Fødselsdagsinvitation", "message", "Invitation", "Karla har fødselsdag og inviterer alle fra 2.B til fest hos hende lørdag d. 10/10 kl. 14-17 på Strandvejen 12, 2900 Hellerup. Svar senest 5/10.", "2026-10-01", "fødselsdag", "2026-10-10", "14:00"),
    ("Fødselsdag i klassen", "message", "Fødselsdag", "Hugo har fødselsdag i morgen, og vi fejrer det i klassen med kage kl. 10.", "2026-10-01", None, None, None),
    ("Emneuge", "message", "Emneuge", "Næste uge er det emneuge om vikinger. Alle skal have praktisk tøj på. Vi starter kl. 8.15 mandag.", "2026-10-01", None, None, None),
    ("Nationale test", "message", "Test", "Husk at nationale test i matematik afvikles tirsdag den 13. oktober kl. 9.00.", "2026-10-01", "test", "2026-10-13", "09:00"),
    ("Forældremøde", "message", "Invitation til forældremøde", "Vi holder forældremøde onsdag den 21. oktober kl. 19.00 på Greve Skole.", "2026-10-01", "møde", "2026-10-21", "19:00"),
    ("Tak for turen (fortid)", "message", "Tak for turen", "Tak for en god tur til Zoo i går!", "2026-10-01", None, None, None),
    ("Skolens interne show", "weekplan", "Dansk", "5.kl.´s SensommerShow i Festsalen kl.10:15.", "2026-09-30", None, None, None),
    ("Arrangement for forældre", "message", "Julefest", "Forældre er velkomne til juleafslutning i hallen fredag den 18/12 kl. 14.00.", "2026-12-01", "arrangement", "2026-12-18", "14:00"),
    ("Omlagt dag (ugeplan)", "weekplan", "Dansk", "Omlagt dag kl.8 - 13. Skolernes Motionsdag (Sammen med ERna). 8- 8:30: Egen klasse.", "2026-10-09", "omlagt", "2026-10-09", "08:00"),
    ("SFO lukker", "message", "SFO lukker", "SFO lukker kl. 15 fredag pga. personalemøde.", "2026-09-28", "lukket", "2026-10-02", "15:00"),
    ("Torsdag i morgen", "message", "Fodboldturnering", "Fodboldturneringen foregår på Greve Stadion i morgen kl. 13.00-15.00.", "2026-09-30", "turnering", "2026-10-01", "13:00"),
    ("'i morgen (torsdag)' + 'næste uge'", "message", "Færdighedstest", "Da vi er på lejrskole i næste uge har vi besluttet at gennemføre testen i morgen (torsdag).", "2026-09-16", "test", "2026-09-17", None),
    ("Ugedag i næste uge", "message", "Tur", "Vi tager på udflugt til Naturcentret fredag i næste uge kl. 9.", "2026-09-30", "tur", "2026-10-09", "09:00"),
    ("Tema + ingen ændring", "message", "Halloween", "Halloween-aktiviteter i uge 44. Børnene maler figurer.", "2026-10-01", None, None, None),
    ("Kun kollegiale møder", "weekplan", "Matematik", "Elevsamtaler + forskellige aktiviteter.", "2026-10-07", None, None, None),
]
MANUAL = [
    # (navn, art, emne, tekst, beskeddato, standarddato, forventet start+tid eller None)
    ("Tandlæge uden kategori", "message", "Aftale", "Hugo skal til tandlæge d. 14/10 kl. 15.30.", "2026-10-01", None, ("2026-10-14", "15:30")),
    ("Ugeplan: mødetid", "weekplan", "Madkundskab", "Vi mødes i madkundskabs-lokalet 9:50", "2026-10-02", "2026-10-02", ("2026-10-02", "09:50")),
    ("Mangler tid", "message", "Aflevering", "Husk at aflevere skemaet d. 14/10.", "2026-10-01", None, None),
    ("To forskellige datoer i samme sætning", "message", "Møder", "Vi ses d. 12/10 kl. 17 eller d. 20/10 kl. 17.", "2026-10-01", None, None),
    ("Dato i fortiden", "message", "Gammelt", "Mødet var d. 12/9 kl. 17.", "2026-10-01", None, None),
]


def main():
    ok = bad = 0
    print("=== Kategorier (bruges til titler på \"Føj til kalender\" og til aflysninger)")
    for name, kind, subj, text, ref, cat, start, st in CASES:
        default = D(ref) if kind == "weekplan" else None
        sg, _ = run(kind, subj, text, ref, default=default)
        got = sg[0] if sg else None
        good = (got is None) if cat is None else (got is not None and got["category"] == cat and got["start"] == start and (st is None or got["start_time"] == st))
        ok, bad = ok + good, bad + (not good)
        print(f"{'OK ' if good else 'FEJL'} {name:36} → {brief(got) if got else '(intet forslag)'}" + ("" if good else f"   forventet {cat} {start} {st}"))
    print("\n=== Manuel vej (entydig dato + starttid)")
    for name, kind, subj, text, ref, default, exp in MANUAL:
        _, opts = run(kind, subj, text, ref, default=D(default) if default else None)
        got = (opts[0]["start"], opts[0]["start_time"]) if opts else None
        good = got == exp
        ok, bad = ok + good, bad + (not good)
        print(f"{'OK ' if good else 'FEJL'} {name:36} → {got or '(ingen mulighed)'}" + ("" if good else f"   forventet {exp}"))
    print(f"\n{ok} bestået, {bad} fejlet")

    if len(sys.argv) > 1:
        print("\n=== Dine egne beskeder (datoen sat til beskedens dato)")
        feed = json.loads(open(sys.argv[1], "rb").read().decode("utf-8-sig"))
        for m in feed.get("messages", []):
            if m.get("private") or not (m.get("text") or "").strip():
                continue
            ref = (m.get("timestamp") or "")[:10]
            if not ref:
                continue
            sg, opts = A.find_all({"messages": [m], "posts": [], "weekplan": [], "events": []}, {}, D(ref), PEOPLE)
            if sg or opts:
                print(f"\n[{ref}] {m['subject']}")
                for s in sg:
                    print("   FORSLAG ", brief(s), f"[{s['confidence']}]", "—", s["reason"])
                for o in [x for lst in [opts] for l in lst.values() for x in l] if isinstance(opts, dict) else opts:
                    print("   mulighed", brief({**o, 'category': 'ev'}))


if __name__ == "__main__":
    main()
