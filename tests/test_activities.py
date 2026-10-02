"""Genkendelse af aktiviteter i beskeder: den oprindelige testsuite plus aflysninger, udsættelser og flytninger."""
import datetime as dt
import subprocess
import sys

import pytest

import activities as A
from conftest import ROOT

D = dt.date.fromisoformat
PEOPLE = {"hugo": "Hugo", "carla": "Carla", "leo": "Leo"}
ZOO = {"key": "sg_aaaaaaaaaaaa", "event_id": "fp1", "title": "Hugo: Tur til Zoo", "start": "2026-10-08", "end": "2026-10-08", "source": "app"}
MOD = {"key": None, "event_id": None, "title": "Forældremøde 6.B", "start": "2026-10-21", "end": "2026-10-21", "source": "google"}
MOD2 = {**MOD, "title": "Forældremøde 3.A", "start": "2026-11-10", "end": "2026-11-10"}


def run(texts, targets=(), ref="2026-10-01"):
    msgs = []
    for i, x in enumerate(texts):
        t, when = (x, ref) if isinstance(x, str) else x
        msgs.append({"id": f"msg:{i}", "subject": "Besked", "text": t, "timestamp": when + "T10:00+02:00", "people": ["hugo"]})
    sg, opts = A.find_all({"messages": msgs, "posts": [], "weekplan": [], "events": []}, {}, D(ref), PEOPLE, None, list(targets))
    return sg, [o for lst in opts.values() for o in lst]


def shape(sg):
    return [(s.get("kind", "create"), s["start"], s["start_time"], s.get("old_start"), (s.get("target") or {}).get("source")) for s in sg]


def test_original_suite_passes():
    r = subprocess.run([sys.executable, "activities_test.py"], cwd=ROOT, capture_output=True, text=True)
    assert " 0 fejlet" in r.stdout, r.stdout[-600:]


CASES = [
    # (navn, tekster, aftaler i kalenderen, forventede forslag: (art, dato, tid, gammel dato, målets kilde))
    ("aflyst, aftalen findes (oprettet af appen)", ["Turen til Zoo torsdag den 8. oktober kl. 8.30 er desværre aflyst."], [ZOO], [("cancel", "2026-10-08", "08:30", "2026-10-08", "app")]),
    ("aflyst, ingen aftale at fjerne", ["Turen til Zoo torsdag den 8. oktober kl. 8.30 er desværre aflyst."], [], []),
    ("'ingen tur' er en aflysning", ["Der er ingen tur til Zoo den 8. oktober kl. 8.30 i år."], [ZOO], [("cancel", "2026-10-08", "08:30", "2026-10-08", "app")]),
    ("aflysningen står i næste sætning", ["Turen til Zoo er torsdag den 8. oktober kl. 8.30. Desværre er den aflyst."], [ZOO], [("cancel", "2026-10-08", "08:30", "2026-10-08", "app")]),
    ("flyttet fra … til … (Google-aftale)", ["Forældremødet er flyttet fra 21. oktober til 4. november kl. 19.00."], [MOD], [("move", "2026-11-04", "19:00", "2026-10-21", "google")]),
    ("flyttet, men ingen aftale: ny aktivitet på den NYE dato", ["Forældremødet er flyttet fra 21. oktober til 4. november kl. 19.00."], [], [("create", "2026-11-04", "19:00", None, None)]),
    ("flyttes til … uden gammel dato, én mulig aftale", ["Forældremødet flyttes til onsdag den 4. november kl. 19.00."], [MOD], [("move", "2026-11-04", "19:00", "2026-10-21", "google")]),
    ("flyttes til … uden aftale", ["Forældremødet flyttes til onsdag den 4. november kl. 19.00."], [], [("create", "2026-11-04", "19:00", None, None)]),
    ("udsat uden dato, én mulig aftale", ["Forældremødet er udsat på ubestemt tid."], [MOD], [("hold", "2026-10-21", None, "2026-10-21", "google")]),
    ("udsat uden dato, to mulige aftaler: gæt ikke", ["Forældremødet er udsat på ubestemt tid."], [MOD, MOD2], []),
    ("tidligere annoncering udelukkes af senere aflysning", [("Vi tager på tur til Zoo torsdag den 8. oktober kl. 8.30.", "2026-10-01"), ("Turen til Zoo torsdag den 8. oktober er aflyst.", "2026-10-03")], [], []),
    ("ny annoncering efter en aflysning bevares", [("Turen til Zoo torsdag den 8. oktober er aflyst.", "2026-10-01"), ("Vi tager på tur til Zoo torsdag den 8. oktober kl. 8.30.", "2026-10-03")], [], [("create", "2026-10-08", "08:30", None, None)]),
    ("et spørgsmål er ikke en melding", ["Skal vi på tur til Zoo torsdag den 8. oktober? Svar senest i morgen."], [], []),
    ("sætning der starter med '6.B' deles, så begge ture findes", ["3.A tager på tur til Zoo d. 8/10 kl. 8.30. 6.B tager på tur til Zoo d. 9/10 kl. 8.30."], [], [("create", "2026-10-08", "08:30", None, None), ("create", "2026-10-09", "08:30", None, None)]),
    ("kl. 7 om aftenen er 19.00", ["Vi mødes til forældremøde onsdag den 21. oktober kl. 7 om aftenen."], [], [("create", "2026-10-21", "19:00", None, None)]),
    ("almindelig annoncering er uændret", ["Vi tager på tur til Zoo torsdag den 8. oktober kl. 8.30."], [], [("create", "2026-10-08", "08:30", None, None)]),
]


@pytest.mark.parametrize("name,texts,targets,expected", CASES, ids=[c[0] for c in CASES])
def test_changes(name, texts, targets, expected):
    sg, _ = run(texts, targets)
    assert shape(sg) == expected


def test_no_manual_calendar_option_on_cancelled_or_negated_sentences():
    for texts, targets in ((["Turen til Zoo torsdag den 8. oktober kl. 8.30 er aflyst."], []),
                           (["Der er ingen tur til Zoo den 8. oktober kl. 8.30 i år."], []),
                           (["Turen til Zoo er torsdag den 8. oktober kl. 8.30. Desværre er den aflyst."], [ZOO]),
                           ([("Vi tager på tur til Zoo torsdag den 8. oktober kl. 8.30.", "2026-10-01"), ("Turen til Zoo torsdag den 8. oktober er aflyst.", "2026-10-03")], [])):
        _, options = run(texts, targets, ref="2026-10-04" if isinstance(texts[0], tuple) else "2026-10-01")
        assert options == [], texts


def test_change_suggestion_carries_what_the_ui_needs():
    (s,), _ = run(["Forældremødet er flyttet fra 21. oktober til 4. november kl. 19.00."], [MOD])
    assert s["label"] == "Flyttet" and s["category"] == "ændring" and s["start"] == "2026-11-04" and s["old_start"] == "2026-10-21"
    assert s["target"] == {k: MOD[k] for k in ("key", "event_id", "title", "start", "end", "source")}
    assert s["sources"][0]["id"] == "msg:0" and s["id"].startswith("sg_")
