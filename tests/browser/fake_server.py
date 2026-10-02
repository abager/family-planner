"""En lille simulering af serverens API til browsertestene. Tester appen, ikke serveren (den har egne tests)."""
from __future__ import annotations

import json
from urllib.parse import urlparse


class FakeServer:
    def __init__(self):
        self.family: dict | None = None                 # None = ingen family.json (appen viser demodata)
        self.briefing: dict | None = None
        self.briefing_week: dict | None = None
        self.server = False                             # False = ingen /api/status (statisk brug)
        self.mark_read = True
        self.cal = {"enabled": True, "problem": None, "calendar_id": "family123@group.calendar.google.com", "calendar_name": "Familiekalender",
                    "default_people": ["family"], "created": {}, "dismissed": [], "applied": [], "by_source": {}}
        self.fail_event = False
        self.fail_change = None                         # fx 403 for aflys/flyt
        self.private_code = "4711"
        self.private_configured = True
        self.private_full: list[dict] = []
        self.unlocked = False
        self.expires = 600
        self.family_gets = 0
        self.calls: list[tuple[str, str, dict | None]] = []

    # ---- opsætning
    def use_demo(self, page, **over) -> None:
        """Brug appens egne demodata som 'rigtige' data (så server-funktioner vises), men som family.json fra en server."""
        demo = json.loads(page.evaluate("JSON.stringify(demoData())"))
        demo.update(demo=False, **over)
        self.family = demo
        self.server = True

    def posts(self, path: str) -> list[dict]:
        return [b for (m, p, b) in self.calls if p == path and m == "POST"]

    # ---- ruter
    def install(self, page) -> None:
        page.route("**/*", self._handle)

    def _json(self, route, obj, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(obj))

    def _handle(self, route):
        req = route.request
        path, m = urlparse(req.url).path, req.method
        if path.startswith("/api/") or path in ("/family.json", "/briefing.json", "/briefing_uge.json"):
            body = json.loads(req.post_data) if req.post_data else None
            if m == "POST":
                self.calls.append((m, path, body))
        else:
            return route.continue_()
        if path == "/family.json":
            self.family_gets += 1
            return self._json(route, self.family) if self.family is not None else route.fulfill(status=404, body="")
        if path in ("/briefing.json", "/briefing_uge.json"):
            b = self.briefing if path == "/briefing.json" else self.briefing_week
            return self._json(route, b) if b else route.fulfill(status=404, body="")
        if path == "/api/status":
            if not self.server:
                return route.fulfill(status=404, body="")
            return self._json(route, {"mark_read_enabled": self.mark_read, "running": False, "runs": 1, "aula": "ok", "pending_reads": 0, "mark_error": None, "aula_enabled": True})
        if path == "/api/messages/read":
            return self._json(route, {"enabled": True, "queued": len(body["ids"])})
        if path == "/api/calendar" and m == "GET":
            return self._json(route, self.cal)
        if path == "/api/calendar/events":
            if self.fail_event:
                return self._json(route, {"error": "Google svarede 403: Forbidden – er kalenderen delt med servicekontoen?"}, 403)
            self.cal["created"][body["key"]] = {"html_link": "https://www.google.com/calendar/event?eid=abc"}
            return self._json(route, {"status": "created", "html_link": "https://www.google.com/calendar/event?eid=abc"})
        if path == "/api/calendar/dismiss":
            self.cal["dismissed"].append(body["key"])
            return self._json(route, {"status": "dismissed"})
        if path == "/api/calendar/restore":
            self.cal["dismissed"] = [k for k in self.cal["dismissed"] if k != body["key"]]
            return self._json(route, {"status": "new"})
        if path == "/api/calendar/remove":
            self.cal["created"].pop(body["key"], None)
            return self._json(route, {"status": "new"})
        if path in ("/api/calendar/cancel", "/api/calendar/move"):
            if self.fail_change:
                return self._json(route, {"error": "Google svarede 403: Forbidden"}, self.fail_change)
            self.cal["applied"].append(body["key"])
            return self._json(route, {"status": "applied"})
        # private tråde
        if path == "/api/private/status":
            return self._json(route, {"configured": self.private_configured, "unlocked": self.unlocked, "expires_in": self.expires if self.unlocked else 0, "protect": True})
        if path == "/api/private/unlock":
            if not self.private_configured:
                return self._json(route, {"error": "Der er ikke sat en kode til private samtaler på serveren (FAMILIEPLAN_PRIVATE_CODE, mindst 4 tegn)."}, 400)
            if body.get("code") != self.private_code:
                return self._json(route, {"error": "Forkert kode."}, 403)
            self.unlocked = True
            return self._json(route, {"unlocked": True, "expires_in": self.expires})
        if path == "/api/private/lock":
            self.unlocked = False
            return self._json(route, {"unlocked": False})
        if path == "/api/private":
            return self._json(route, {"messages": self.private_full, "expires_in": 600}) if self.unlocked else self._json(route, {"error": "locked"}, 403)
        route.fulfill(status=404, body="")
