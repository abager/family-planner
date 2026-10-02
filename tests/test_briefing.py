"""Overblikket: hvad der renses væk, før noget sendes til en sprogmodel. Opdigtede data."""
import pytest

import briefing as B


@pytest.mark.parametrize("text", ["CPR 120515-1234", "cpr: 1205151234", "Barnets nr. er 311299 4321", "(010101-0001)"])
def test_cpr_numbers_are_removed(text):
    out = B._scrub(text, 200)
    assert "[fjernet]" in out
    assert not any(ch.isdigit() for ch in out.replace("[fjernet]", "")), out


@pytest.mark.parametrize("text", ["ring 22 33 44 55", "+45 22334455", "skriv til lærer@skole.dk"])
def test_phone_numbers_and_mail_are_still_removed(text):
    assert "[fjernet]" in B._scrub(text, 200)


@pytest.mark.parametrize("text", ["Uge 41: side 12-34 i matematikbogen", "Mødet er 14.30-15.15 i lokale 112",
                                  "Afleveres 23/10 2026", "Ordre 4567 er på vej", "Lektion 3 af 12"])
def test_ordinary_numbers_are_left_alone(text):
    assert B._scrub(text, 200) == text
