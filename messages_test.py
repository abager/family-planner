"""Kør besked-analysen på en udtrukket aula_feed.json og vis resultatet.

  python messages_test.py aula_feed.json
"""
import json
import sys
import tomllib

import messages

cfg = tomllib.load(open("config.toml", "rb"))
family = [p["name"] for p in cfg["people"] if p.get("role") == "adult"]
path = sys.argv[1] if len(sys.argv) > 1 else "aula_feed.json"
data = json.loads(open(path, "rb").read().decode("utf-8-sig"))
for m in data.get("messages") or []:
    r = messages.analyse(m, cfg["people"], family)
    print(f"{(m.get('timestamp') or '')[:10]:10}  {r['category']:11} {','.join(r['people']):12} {m.get('subject', '')[:50]}")
    for a in r["actions"]:
        print(f"            → gøre [{a['confidence']}] {a['due'] or '':10} {a['title']}")
    for e in r["events"]:
        print(f"            → aftale {e['date']} {e['start'] or ''} {e['title']} {e['location'] or ''}")
    for b in r["bring"]:
        print(f"            → medbring {b['due'] or ''} {b['title']}")
