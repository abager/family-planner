"""Kør lektie-genkendelsen på en udtrukket weekplan.json og vis resultatet.

  python homework_test.py weekplan.json
"""
import json
import sys

import homework

path = sys.argv[1] if len(sys.argv) > 1 else "weekplan.json"
items = json.loads(open(path, "rb").read().decode("utf-8-sig"))
for w in sorted(items, key=lambda x: (x["person"], x.get("date") or "")):
    wtype = w.get("type") or w.get("title", "")
    r = homework.analyse({"type": wtype, "text": w.get("text", ""), "date": w.get("date")})
    if r["category"] == "undervisning" and not r["tasks"]:
        continue
    print(f"{w['person']:6} {w.get('date')}  {r['category']:12} {(w.get('subject') or w.get('label') or '')[:20]}")
    for t in r["tasks"]:
        print(f"         → {t['kind']:6} [{t['confidence']}] {t['due']} {t['recurring'] or ''}  {t['title']}")
    if r["info"] and r["category"] == "info":
        print(f"         → info: {r['info']}")
