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


def learn_tests() -> tuple[int, int]:
    """Lær af manuelt tilføjede aktiviteter."""
    ok = bad = 0

    def check(name, cond, detail=""):
        nonlocal ok, bad
        ok, bad = ok + bool(cond), bad + (not cond)
        print(f"{'OK ' if cond else 'FEJL'} {name:58} {detail}")

    def rule(kind, text, anchor=None):
        """Som serveren: reglens titel er ankerordets (så en vending og et ord om det samme giver samme forslag)."""
        t, err = A.valid_rule_text(kind, text)
        assert not err, err
        return {"id": "lr_" + A._sha(kind, t), "kind": kind, "text": t, "pattern": A.rule_pattern(kind, t), "title": A._cap(anchor or t), "enabled": True}

    def run_learned(text, learned, subject="Besked", ref="2026-10-01"):
        data = {"messages": [{"id": "msg:1", "subject": subject, "text": text, "timestamp": ref + "T10:00+02:00", "people": ["carla"]}], "posts": [], "weekplan": [], "events": []}
        return A.find_all(data, {}, D(ref), PEOPLE, learned)[0]

    names = list(PEOPLE.values())
    c = A.learn_candidates("Hugo: Tandlæge", "Hugo skal til tandlæge d. 14/10 kl. 15.30", names)
    check("kandidater: ordet fra titlen er forvalgt", c and c[0]["text"] == "tandlæge" and c[0]["default"], str([x["text"] for x in c]))
    check("kandidater: vending tilbydes, men er ikke forvalgt", any(x["kind"] == "phrase" and x["text"] == "skal til tandlæge" and not x["default"] for x in c))
    check("kandidater: fornavne og tal bliver ikke til regler", not any("hugo" in x["text"] or any(ch.isdigit() for ch in x["text"]) for x in c))
    check("kandidater: kun almindelige ord giver ingen forslag", not A.learn_candidates("X", "Husk at tage madpakke med i skolen i dag", names))
    text = "Carla har tandlæge torsdag den 22. oktober kl. 14.15."
    check("uden regel: ingen forslag", not run_learned(text, []))
    sg = run_learned(text, [rule("keyword", "tandlæge")])
    check("med regel: forslag med dato og tid", sg and sg[0]["start"] == "2026-10-22" and sg[0]["start_time"] == "14:15" and sg[0]["title"] == "Tandlæge", str([(s["title"], s["start"]) for s in sg]))
    check("med regel: mærket som lært, og begrundelsen nævner reglen", sg and sg[0]["label"] == "Lært regel" and "din regel" in sg[0]["reason"])
    check("regel slået fra: ingen forslag", not run_learned(text, [{**rule("keyword", "tandlæge"), "enabled": False}]))
    mot = "Motionsdag fredag den 9/10 kl. 8.00 på stadion."
    check("temadag uden regel: bliver i Aula", not run_learned(mot, []))
    check("temadag med lært regel: kommer med (dit eget ønske)", bool(run_learned(mot, [rule("keyword", "motionsdag")])))
    P = [rule("phrase", "skal til tandlæge")]
    check("vending rammer bøjede former", bool(run_learned("Hugo skal til tandlægen d. 5/11 kl. 9.", P)))
    check("vending rammer ikke ordet alene", not run_learned("Tandlægen sender breve d. 5/11 kl. 9.", P))
    two = run_learned(text, [rule("keyword", "tandlæge"), rule("phrase", "har tandlæge", "tandlæge")])
    check("overlappende regler giver ét forslag, ikke to", len(two) == 1, str(len(two)))
    a = run_learned(text, [rule("keyword", "tandlæge")])[0]["id"]
    b = run_learned(text, [rule("phrase", "har tandlæge", "tandlæge")])[0]["id"]
    check("forslagets id afhænger ikke af hvilken regel der fandt det", a == b)
    for kind, bad_text in (("keyword", "mødes"), ("keyword", "skolen"), ("keyword", "ab"), ("keyword", "(a+)+$"), ("keyword", "to ord"), ("phrase", "i skolen"), ("regex", ".*")):
        _, err = A.valid_rule_text(kind, bad_text)
        check(f"regel afvises: {kind} {bad_text!r}", bool(err), err or "")
    check("regel accepteres: tandlæge", A.valid_rule_text("keyword", "Tandlæge") == ("tandlæge", None))
    return ok, bad


def main():
    ok = bad = 0
    print("=== Automatiske forslag")
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
    print("\n=== Lær af manuelle aktiviteter")
    lo, lb = learn_tests()
    ok, bad = ok + lo, bad + lb
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
