"""Hjælpere til testene: simuleret Google, ntfy-modtager, små webservere og syntetiske data.

Ingen rigtige tjenester og ingen rigtige data. Alt er opfundet til formålet.
"""
from __future__ import annotations

import base64
import datetime as dt
import http.server
import json
import threading
import urllib.parse
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


def serve(handler_cls, port: int = 0) -> _Server:
    srv = _Server(("127.0.0.1", port), handler_cls)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    return srv


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------- simuleret Google
class MockGoogle:
    """Token-endpoint (validerer servicekontoens JWT-signatur) og Calendar events: insert, get, patch, delete.

    fail: {"kalender-præfix": statuskode} får alle kald til en kalender, hvis id starter sådan, til at svare med fejlen."""

    def __init__(self, tmp_path: Path):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        self.pub = key.public_key()
        self.events: dict[str, dict] = {}
        self.log: list[tuple] = []
        self.fail: dict[str, int] = {}
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, obj=None):
                body = json.dumps(obj).encode() if obj is not None else b""
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length", 0))
                return self.rfile.read(n) if n else b""

            def _route(self):
                """(kalender-id, event-id eller None), eller None, hvis kaldet afvises."""
                if self.headers.get("Authorization") != "Bearer tok123":
                    self._send(401, {"error": {"message": "Invalid Credentials"}})
                    return None
                parts = self.path.split("?")[0].split("/")             # /calendar/v3/calendars/<id>/events[/<eid>]
                cal = urllib.parse.unquote(parts[4])
                for prefix, code in outer.fail.items():
                    if cal.startswith(prefix):
                        self._send(code, {"error": {"message": "Forbidden" if code == 403 else "Not Found"}})
                        return None
                return cal, (parts[6] if len(parts) > 6 else None)

            def do_POST(self):
                raw = self._body()
                if self.path == "/token":
                    form = urllib.parse.parse_qs(raw.decode())
                    h, c, s = form["assertion"][0].split(".")
                    try:
                        outer.pub.verify(b64d(s), f"{h}.{c}".encode(), padding.PKCS1v15(), hashes.SHA256())
                        ok = True
                    except Exception:  # noqa: BLE001
                        ok = False
                    claims = json.loads(b64d(c))
                    outer.log.append(("token", ok, json.loads(b64d(h)).get("alg"), claims.get("iss"), claims.get("scope"), claims.get("aud")))
                    return self._send(200, {"access_token": "tok123", "expires_in": 3600, "token_type": "Bearer"}) if ok else self._send(400, {"error": "invalid_grant"})
                r = self._route()
                if r:
                    body = json.loads(raw)
                    outer.log.append(("insert", r[0], body))
                    if body.get("id") in outer.events:
                        return self._send(409, {"error": {"message": "The requested identifier already exists."}})
                    outer.events[body["id"]] = {**body, "status": "confirmed", "htmlLink": f"https://www.google.com/calendar/event?eid={body['id']}"}
                    self._send(200, outer.events[body["id"]])

            def do_GET(self):
                r = self._route()
                if r:
                    self._send(200, outer.events[r[1]]) if r[1] in outer.events else self._send(404, {"error": {"message": "Not Found"}})

            def do_PATCH(self):
                raw = self._body()
                r = self._route()
                if r:
                    ev = outer.events.get(r[1])
                    if not ev or ev["status"] == "cancelled":
                        return self._send(404, {"error": {"message": "Not Found"}})
                    outer.log.append(("patch", r[1], json.loads(raw)))
                    ev.update(json.loads(raw))
                    self._send(200, ev)

            def do_DELETE(self):
                r = self._route()
                if r:
                    if r[1] in outer.events:
                        outer.events[r[1]]["status"] = "cancelled"
                        outer.log.append(("delete", r[1]))
                        return self._send(204)
                    self._send(404, {})

        self.srv = serve(H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.sa_file = tmp_path / "sa.json"
        self.sa_file.write_text(json.dumps({"type": "service_account", "project_id": "familieplan-test", "private_key_id": "abc123", "private_key": pem,
                                            "client_email": "familieplan@familieplan-test.iam.gserviceaccount.com", "client_id": "1",
                                            "token_uri": f"{self.base}/token"}))

    @property
    def api(self) -> str:
        return f"{self.base}/calendar/v3"

    def live(self) -> dict[str, dict]:
        return {k: v for k, v in self.events.items() if v["status"] != "cancelled"}

    def close(self) -> None:
        self.srv.shutdown()


# ---------------------------------------------------------------- ntfy
class NtfySink:
    """Modtager ntfys JSON-publicering (POST til roden) og husker beskederne."""

    def __init__(self):
        self.msgs: list[dict] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                outer.msgs.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.end_headers()

        self.srv = serve(H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/familieplan-test"

    def close(self) -> None:
        self.srv.shutdown()


# ---------------------------------------------------------------- iCal
def make_ics(items: list[tuple[str, str, dt.date]], recurring: bool = False) -> str:
    """items: (uid, titel, dato). Hele-dags-aftaler."""
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//da//"]
    for uid, title, d in items:
        out += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{d:%Y%m%d}T080000Z", f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + dt.timedelta(days=1):%Y%m%d}", f"SUMMARY:{title}"]
        if recurring:
            out.append("RRULE:FREQ=WEEKLY;COUNT=3")
        out.append("END:VEVENT")
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


class StaticDir:
    """Serverer en mappe på en tilfældig port (til iCal og til browsertests)."""

    def __init__(self, directory: Path):
        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(directory), **k)

            def log_message(self, *a):
                pass

        self.srv = serve(H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self) -> None:
        self.srv.shutdown()


# ---------------------------------------------------------------- syntetiske Aula-data
def msg(i: int, subject: str, text: str, ts: str, *, unread: bool = False, sender: str = "Klasselærer", people=("hugo",), thread_len: int = 1) -> dict:
    thread = [{"id": f"m{i}-{k}", "from": sender, "timestamp": ts, "text": text if k == 0 else f"Svar {k}", "images": []} for k in range(thread_len)]
    return {"id": f"msg:{i}", "subject": subject, "from": sender, "timestamp": ts, "unread": unread, "text": text, "thread": thread,
            "participants": [sender], "people": list(people), "images": [], "source": "aula", "sig": f"s{i}"}


def aula_payload(messages: list[dict] | None = None) -> dict:
    return {"events": [], "tasks": [], "weekplan": [], "posts": [], "albums": [], "messages": messages or []}
