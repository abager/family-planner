"""Syntetiske data til browsertestene (opfundne navne og tekster)."""
from __future__ import annotations

import datetime as dt

PEOPLE = [
    {"id": "andreas", "name": "Andreas", "role": "adult", "color": "#2F6FDE", "note": "Far"},
    {"id": "monica", "name": "Monica", "role": "adult", "color": "#1F9E89", "note": "Mor"},
    {"id": "hugo", "name": "Hugo", "role": "child", "color": "#E4572E", "note": "12 år", "icon": "🦊"},
    {"id": "carla", "name": "Carla", "role": "child", "color": "#8E5CD6", "note": "9 år", "icon": "🦋"},
    {"id": "leo", "name": "Leo", "role": "child", "color": "#D9921E", "note": "5 år", "icon": "🐻"},
]


def message(i: int, *, unread=False, private=False, redacted=False, subject=None, text=None, sender="Klasselærer") -> dict:
    ts = (dt.datetime(2026, 9, 30, 12, 0) - dt.timedelta(hours=i * 5)).isoformat() + "+02:00"
    if redacted:
        return {"id": f"msg:{i}", "subject": "Privat samtale", "from": "", "timestamp": ts, "unread": unread, "text": "", "images": [], "thread": [], "participants": [],
                "people": ["hugo"], "source": "aula", "private": True, "redacted": True, "category": "samtale", "actions": [], "suggested_events": [], "bring": []}
    body = text or f"Hej alle. Dette er besked nummer {i} med lidt almindelig information."
    return {"id": f"msg:{i}", "subject": subject or f"Besked {i}", "from": sender, "timestamp": ts, "unread": unread, "text": body,
            "thread": [{"id": f"t{i}", "from": sender, "timestamp": ts, "text": body, "images": []}], "participants": [sender], "people": ["hugo"], "source": "aula",
            "images": [], "private": private, "category": "info", "actions": [], "suggested_events": [], "bring": []}


def family(messages=None, **over) -> dict:
    d = {"generated": dt.datetime.now().astimezone().isoformat(), "people": PEOPLE, "events": [], "tasks": [], "weekplan": [], "posts": [], "albums": [],
         "messages": messages or [], "suggestions": [], "settings": {"hidePrivate": True, "eveningHour": 17}, "demo": False}
    d.update(over)
    return d
