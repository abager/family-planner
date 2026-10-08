#!/usr/bin/env python3
"""Familieplan-server: henter data løbende og serverer appen bag en adgangskode.

  FAMILIEPLAN_PASSWORD=... python server.py --host 0.0.0.0 --port 8080

Én proces gør tre ting:
  1. Henter fra Aula og Google efter en tidsplan (hvert kvarter om dagen, sjældnere om natten).
  2. Serverer appen og dataene – kun til dem, der har indtastet familiens adgangskode.
  3. Har en side (/auth), hvor man logger ind i Aula med MitID, når loginet udløber. Der er ingen terminal på en server,
     så QR-koden vises på siden i stedet.

Aulas login fornyes af sig selv, så MitID kun skal bruges ind imellem. Udløber det, fortsætter serveren med de seneste
Aula-data, viser en advarsel i appen og (hvis slået til) sender en besked via ntfy.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import sys
import time
import tomllib
from contextlib import asynccontextmanager
import datetime as dt
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import fetch_family
import ops
import private as private_mod
import problems
import progress
import suggestions as sugg

TZ = ZoneInfo("Europe/Copenhagen")
APP_WEB = Path(__file__).resolve().parent / "web"
COOKIE = "fp_session"
PRIV_COOKIE = "fp_private"
# Filer med personlige data eller nøgler må aldrig udleveres af den statiske del – uanset hvor datamappen ligger
DENY_NAMES = {"private_messages.json", "suggestions_state.json", "learned_rules.json", "server_state.json", "config.toml", "aula_tokens.json",
              "session.key", "google_service_account.json", ".env",
              "ai_cache.json", "ai_usage.json", "ai_last_invalid.json",
              "home_location.json", "weather_cache.json",
}
PUBLIC_PATHS = {"/login", "/api/health", "/favicon.svg", "/favicon-32.png", "/apple-touch-icon.png", "/favicon.ico",
                "/manifest.webmanifest", "/icon-192.png", "/icon-512.png"}      # ikoner og manifest indeholder intet hemmeligt og hentes uden cookie
log = logging.getLogger("familieplan.server")
# Appen bruger indlejret script/stil og Google Fonts; alt andet lukkes. Begrænser skaden, hvis tekst fra Aula nogensinde slap igennem uden at blive escapet.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


# ---------------------------------------------------------------- indstillinger og tilstand
class Settings:
    def __init__(self, cfg: dict):
        s = cfg.get("server", {})
        self.interval = int(s.get("interval_minutes", 15)) * 60
        self.night_interval = int(s.get("night_interval_minutes", 120)) * 60
        self.night = (s.get("night_start", "23:00"), s.get("night_end", "05:30"))
        self.use_aula = bool(s.get("aula", True))
        self.login_timeout = float(s.get("login_timeout_minutes", 10)) * 60
        self.run_timeout = float(s.get("run_timeout_minutes", 45)) * 60
        self.session_days = int(s.get("session_days", 90))
        self.trust_proxy = bool(s.get("trust_proxy", False))
        self.ntfy = str(s.get("notify_ntfy", "")).strip()
        self.public_url = str(s.get("public_url", "")).rstrip("/")
        self.mark_read = bool(s.get("mark_read_in_aula", True))     # skriv "læst" tilbage til Aula, når en besked åbnes i appen
        d = cfg.get("display", {})
        self.evening_hour = int(d.get("evening_hour", 18))          # efter dette klokkeslæt handler "I dag" om i morgen
        self.evening_push = bool(s.get("evening_push", True))       # kræver notify_ntfy
        self.push_details = str(s.get("push_details", "summary"))   # "summary" = kun tal · "full" = indhold (kun egen ntfy-server)
        self.push_only_if_content = bool(s.get("evening_push_only_if_content", True))
        self.stale_hours = float(s.get("stale_alert_hours", 4))     # 0 = slå tilsynet fra
        self.private_code = os.environ.get("FAMILIEPLAN_PRIVATE_CODE", "")
        self.private_minutes = float(s.get("private_unlock_minutes", 10))


in_night = ops.in_night


class State:
    """Det, appen og /auth-siden får at vide. Indeholder ingen hemmeligheder."""

    def __init__(self):
        self.started = now_iso()
        self.running = False
        self.runs = 0                  # antal afsluttede kørsler – lader appen vide, hvornår en bestilt opdatering er færdig
        self.pending_reads = 0                 # tråde, der venter på at blive markeret som læst i Aula
        self.mark_error: str | None = None
        self.last_start: str | None = None
        self.last_end: str | None = None
        self.last_success: str | None = None
        self.aula_last_ok: str | None = None   # sidste kørsel hvor Aula-data blev hentet (eller Aula var slået fra)
        self.internal_errors = 0               # uventede fejl, planlæggeren har fanget og kørt videre fra
        self.last_error: str | None = None
        self.aula = "unknown"          # unknown | ok | login_required | waiting_for_login | error | skipped
        self.counts: dict = {}
        self.next_run: str | None = None
        self.login = self.idle_login()
        self.notified_at: float | None = None

    @staticmethod
    def idle_login() -> dict:
        return {"active": False, "qr": None, "otp": None, "since": None, "message": ""}

    def public(self) -> dict:
        return {"started": self.started, "running": self.running, "runs": self.runs, "last_start": self.last_start, "last_end": self.last_end,
                "last_success": self.last_success, "last_error": self.last_error, "aula": self.aula,
                "counts": self.counts, "next_run": self.next_run, "aula_last_ok": self.aula_last_ok, "internal_errors": self.internal_errors,
                "pending_reads": self.pending_reads, "mark_error": self.mark_error}


# ---------------------------------------------------------------- MitID-login på en webside
def _ascii_to_matrix(text: str) -> list[list[bool]]:
    rows: list[list[bool]] = []
    for line in text.splitlines():
        top = [c in "█▀" for c in line]
        bottom = [c in "█▄" for c in line]
        rows += [top, bottom]
    return rows


def qr_svg(qr) -> str:
    """qrcode.QRCode → SVG (hvid baggrund, så den kan scannes på både lys og mørk side)."""
    try:
        matrix = qr.get_matrix()
    except Exception:  # noqa: BLE001
        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=False)
        matrix = _ascii_to_matrix(buf.getvalue())
    n = max((len(r) for r in matrix), default=0)
    path = "".join(f"M{x},{y}h1v1h-1z" for y, row in enumerate(matrix) for x, dark in enumerate(row) if dark)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {len(matrix)}" shape-rendering="crispEdges" role="img" '
            f'aria-label="MitID QR-kode"><rect width="{n}" height="{len(matrix)}" fill="#fff"/><path d="{path}" fill="#000"/></svg>')


class WebAuthHooks(fetch_family.AuthHooks):
    """Erstatter terminal-udskriften: QR-koden havner i tilstanden, som /auth-siden viser."""

    def __init__(self, state: State):
        self.state = state

    def on_login_required(self) -> None:
        if not self.interactive:
            raise fetch_family.LoginRequired("Aula-login er udløbet")
        self.state.aula = "waiting_for_login"
        self.state.login = {"active": True, "qr": None, "otp": None, "since": now_iso(), "message": "Forbereder MitID-login …"}

    def on_qr_codes(self, qr1, qr2) -> None:
        self.state.login.update(qr=[qr_svg(qr1), qr_svg(qr2)], otp=None, message="Scan QR-koden med MitID-appen")

    def on_qr_done(self) -> None:
        self.state.login.update(qr=None, message="QR-koden er læst – godkend i MitID-appen")

    def on_otp_code(self, code: str) -> None:
        self.state.login.update(otp=str(code), qr=None, message="Indtast koden i MitID-appen")


# ---------------------------------------------------------------- tidsplan
class Runner:
    def __init__(self, cfg: dict, settings: Settings, state: State, hooks: WebAuthHooks):
        self.cfg, self.s, self.state, self.hooks = cfg, settings, state, hooks
        self.out_dir = Path(cfg.get("output", "web/family.json")).resolve().parent
        self.notifier = ops.Notifier(settings.ntfy, settings.public_url)
        self.sf = ops.StateFile(self.out_dir / "server_state.json")
        self.wake = asyncio.Event()
        self.interactive_next = False
        self.error_backoff = 5.0
        self.lock = asyncio.Lock()

    def trigger(self, interactive: bool = False) -> None:
        self.interactive_next = self.interactive_next or interactive
        self.wake.set()

    def wait_seconds(self, now: datetime | None = None) -> float:
        """Normalt hvert kvarter (om natten sjældnere) – men præcis når visningen skifter til i morgen og ved midnat."""
        now = now or datetime.now(TZ)
        base = self.s.night_interval if in_night(now, *self.s.night) else self.s.interval
        marks = [now.replace(hour=self.s.evening_hour, minute=0, second=5, microsecond=0),
                 (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)]
        until = min(((m if m > now else m + timedelta(days=1)) - now).total_seconds() for m in marks)
        return max(5.0, min(base, until))

    async def loop(self) -> None:
        await self.safe_run(False)
        while True:
            wait = self.wait_seconds()
            self.state.next_run = (datetime.now(TZ) + timedelta(seconds=wait)).isoformat(timespec="seconds")
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
            interactive, self.interactive_next = self.interactive_next, False
            self.wake.clear()
            await self.safe_run(interactive)

    async def safe_run(self, interactive: bool = False) -> None:
        """En uventet fejl må aldrig slå planlæggeren ihjel: log den, vis den i status, og kør videre ved næste tur."""
        try:
            await self.run(interactive)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("Uventet fejl i planlæggeren – fortsætter")
            st = self.state
            st.running, st.internal_errors = False, st.internal_errors + 1
            st.last_error = f"Intern fejl: {e.__class__.__name__}: {e}"
            self.hooks.interactive = False
            await asyncio.sleep(self.error_backoff)

    async def run(self, interactive: bool = False) -> None:
        async with self.lock:
            st = self.state
            before = st.aula                       # tilstanden FØR kørslen – hooks ændrer den undervejs
            st.running, st.last_start = True, now_iso()
            self.hooks.interactive = interactive
            st.login = State.idle_login()
            timeout = self.s.login_timeout if interactive else self.s.run_timeout
            try:
                res = await asyncio.wait_for(fetch_family.run_once(self.cfg, self.s.use_aula), timeout)
                st.last_success = now_iso()
            except asyncio.TimeoutError:
                waited_for_login = st.login["active"]
                res = {"aula": "login_required" if waited_for_login else "error",
                       "error": "MitID-login blev ikke godkendt i tide" if waited_for_login else "Hentningen tog for lang tid"}
            except Exception as e:  # noqa: BLE001
                log.exception("Hentning fejlede")
                res = {"aula": "error", "error": str(e) or e.__class__.__name__}
            progress.end()                         # også efter timeout eller fejl: det, der ikke nåede i mål, er fejlet
            if not isinstance(res, dict):
                res = {"aula": "error", "error": "Hentningen gav et uventet svar"}
            st.aula, st.last_error = res.get("aula", "error"), res.get("error")
            if st.aula in ("ok", "skipped"):
                st.aula_last_ok = now_iso()
            st.counts = res.get("counts", st.counts)
            st.login = State.idle_login()
            st.running, st.last_end = False, now_iso()
            st.runs += 1
            self.hooks.interactive = False
            # Besked kun for ubemandede kørsler (en bruger, der selv prøver at logge ind, ved det jo), én gang pr. udløb – og igen efter et døgn
            if not interactive and st.aula == "login_required":
                if before != "login_required" or (st.notified_at and time.time() - st.notified_at > 24 * 3600):
                    st.notified_at = time.time()
                    await self.notifier.send("Familieplan", "Aula-login er udløbet. Åbn Familieplan og log ind med MitID igen.", path="/auth", tags=("warning",))
            log.info("Kørsel færdig: Aula=%s%s", st.aula, f" ({st.last_error})" if st.last_error else "")
        await self.maybe_evening_push()

    async def maybe_evening_push(self, now: datetime | None = None) -> str:
        """Aftenpush: efter kl. `evening_hour` får du én besked om i morgen – kun hvis der er noget særligt, og kun én gang pr. dag."""
        now = now or datetime.now(TZ)
        if not (self.s.evening_push and self.notifier.enabled):
            return "fra"
        if not (self.s.evening_hour <= now.hour < 23):
            return "ikke endnu"
        today = now.date().isoformat()
        if self.sf.get("evening_push") == today:
            return "allerede sendt"
        try:
            b = json.loads((self.out_dir / "briefing.json").read_text("utf-8"))
        except (OSError, ValueError):
            return "intet overblik"
        if b.get("period", [None])[0] != (now.date() + timedelta(days=1)).isoformat():
            return "overblikket handler ikke om i morgen endnu"
        title, text, has_content = ops.evening_push_text(b, self.s.push_details)
        if self.s.push_only_if_content and not has_content:
            self.sf.set(evening_push=today)
            return "intet særligt"
        if await self.notifier.send(title, text, path="/", tags=("calendar",)):
            self.sf.set(evening_push=today)
            return "sendt"
        return "fejlede"


# ---------------------------------------------------------------- markér som læst i Aula
class ReadMarker:
    """Markerer beskedtråde som læst i Aula. Appen får svar med det samme; selve kaldet køres i baggrunden, samlet og med genforsøg.

    Samme lås som den almindelige hentning bruges, så de to aldrig bruger Aula-loginet samtidigt."""

    MAX_ATTEMPTS = 5
    DEBOUNCE = 1.5        # sekunder: saml flere hurtige markeringer til ét kald
    RETRY = 60            # sekunder til næste forsøg, hvis noget mislykkedes

    def __init__(self, cfg: dict, settings: Settings, state: State, runner: Runner, out_dir: Path):
        self.cfg, self.s, self.state, self.runner, self.out_dir = cfg, settings, state, runner, out_dir
        self.pending: dict[str, int] = {}          # tråd-id → antal mislykkede forsøg
        self.event = asyncio.Event()

    def known_ids(self) -> set[str]:
        try:
            data = json.loads((self.out_dir / "family.json").read_text("utf-8"))
        except (OSError, ValueError):
            return set()
        return {m["id"] for m in data.get("messages", []) if m.get("id")}

    def add(self, ids: list[str]) -> int:
        known = self.known_ids()
        added = 0
        for i in ids:
            if i in known:                          # kun tråde, vi selv har hentet – ikke vilkårlige id'er
                self.pending.setdefault(i.removeprefix("msg:"), 0)
                added += 1
        self.state.pending_reads = len(self.pending)
        if added:
            self.event.set()
        return added

    async def loop(self) -> None:
        while True:
            await self.event.wait()
            self.event.clear()
            await asyncio.sleep(self.DEBOUNCE)
            if await self.flush():
                await asyncio.sleep(self.RETRY)
                self.event.set()

    async def flush(self) -> bool:
        """Returnerer True, hvis der er noget tilbage, som skal prøves igen."""
        if not self.pending:
            return False
        batch = list(self.pending)
        done: list[str] = []
        async with self.runner.lock:
            fetch_family.auth_hooks.interactive = False
            try:
                async with await fetch_family.open_aula_client(self.cfg) as client:
                    for tid in batch:
                        try:
                            await client.mark_thread_read(tid)       # False = ingen besked at markere; også fint
                            done.append(tid)
                        except AttributeError:
                            self.state.mark_error = "Aula-pakken mangler mark_thread_read – opgradér `aula`"
                            self.pending.clear()
                            break
                        except ValueError as e:                       # ikke i din egen indbakke: kan ikke løses ved at prøve igen
                            log.warning("Kan ikke markere tråd %s som læst: %s", tid, e)
                            done.append(tid)
                        except Exception as e:  # noqa: BLE001
                            self.pending[tid] += 1
                            self.state.mark_error = f"Kunne ikke markere som læst i Aula: {e or e.__class__.__name__}"
                            log.warning("Markering af tråd %s fejlede (forsøg %d): %s", tid, self.pending[tid], e)
                            if self.pending[tid] >= self.MAX_ATTEMPTS:
                                done.append(tid)
            except fetch_family.LoginRequired:
                self.state.mark_error = "Aula-login er udløbet – læst-markeringer venter"
                for tid in batch:
                    self.pending[tid] += 1
            except Exception as e:  # noqa: BLE001
                self.state.mark_error = f"Kunne ikke forbinde til Aula: {e or e.__class__.__name__}"
                log.warning("Læst-markering fejlede: %s", e)
            for tid in done:
                self.pending.pop(tid, None)
            if done:
                self.patch_family([f"msg:{t}" for t in done])
            if not self.pending and done and self.state.mark_error and not self.state.mark_error.startswith("Aula-pakken"):
                self.state.mark_error = None
        self.state.pending_reads = len(self.pending)
        return bool(self.pending)

    def patch_family(self, ids: list[str]) -> None:
        """Opdatér family.json med det samme, så alle enheder ser tråden som læst uden at vente på næste hentning."""
        f = self.out_dir / "family.json"
        try:
            data = json.loads(f.read_text("utf-8"))
            hit = False
            for m in data.get("messages", []):
                if m.get("id") in ids and m.get("unread"):
                    m["unread"], hit = False, True
            if hit:
                tmp = f.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
                tmp.replace(f)
        except (OSError, ValueError) as e:
            log.warning("Kunne ikke opdatere family.json efter læst-markering: %s", e)


# ---------------------------------------------------------------- adgangskode og sessioner
class PrivateGate:
    """Ekstra kode til private tråde. Koden er en anden end familiens adgangskode, og oplåsningen udløber efter få minutter."""

    def __init__(self, code: str, secret: bytes, minutes: float, path: Path):
        self.code, self.minutes, self.path = code, minutes, Path(path)
        self.key = hmac.new(secret, hashlib.sha256(b"private:" + code.encode()).digest(), hashlib.sha256).digest()
        self._mtime: float | None = None
        self._store: dict[str, dict] = {}
        self._names: set[str] = set()

    @property
    def configured(self) -> bool:
        return len(self.code) >= 4

    def token(self) -> str:
        exp = int(time.time() + self.minutes * 60)
        return f"{exp}.{hmac.new(self.key, str(exp).encode(), hashlib.sha256).hexdigest()}"

    def remaining(self, token: str | None) -> int:
        try:
            exp, sig = (token or "").split(".", 1)
            ok = self.configured and hmac.compare_digest(sig, hmac.new(self.key, exp.encode(), hashlib.sha256).hexdigest())
            return max(0, int(exp) - int(time.time())) if ok else 0
        except ValueError:
            return 0

    def valid(self, token: str | None) -> bool:
        return self.remaining(token) > 0

    def check_code(self, given: str) -> bool:
        return self.configured and hmac.compare_digest(hashlib.sha256(given.encode()).digest(), hashlib.sha256(self.code.encode()).digest())

    def _refresh(self) -> None:
        try:
            m = self.path.stat().st_mtime
        except OSError:
            self._mtime, self._store, self._names = None, {}, set()
            return
        if m != self._mtime:
            self._mtime, self._store = m, private_mod.load(self.path)
            self._names = private_mod.media_names(self._store)

    def messages(self) -> list[dict]:
        self._refresh()
        return sorted(self._store.values(), key=lambda x: x.get("timestamp") or "", reverse=True)

    def is_private_media(self, url_path: str) -> bool:
        self._refresh()
        return os.path.basename(url_path) in self._names


class Guard:
    def __init__(self, password: str, secret: bytes, settings: Settings, no_auth: bool):
        self.password, self.no_auth, self.s = password, no_auth, settings
        # Skifter man adgangskode, bliver alle logget ud
        self.key = hmac.new(secret, hashlib.sha256(password.encode()).digest(), hashlib.sha256).digest()
        self.fail: dict[str, list[float]] = {}

    def token(self) -> str:
        exp = int(time.time() + self.s.session_days * 86400)
        return f"{exp}.{hmac.new(self.key, str(exp).encode(), hashlib.sha256).hexdigest()}"

    def valid(self, token: str | None) -> bool:
        if self.no_auth:
            return True
        try:
            exp, sig = (token or "").split(".", 1)
            return int(exp) > time.time() and hmac.compare_digest(sig, hmac.new(self.key, exp.encode(), hashlib.sha256).hexdigest())
        except ValueError:
            return False

    def blocked(self, ip: str) -> bool:
        recent = [t for t in self.fail.get(ip, []) if time.time() - t < 300]
        self.fail[ip] = recent
        return len(recent) >= 5

    def failed(self, ip: str) -> None:
        self.fail.setdefault(ip, []).append(time.time())

    def check_password(self, given: str) -> bool:
        return hmac.compare_digest(hashlib.sha256(given.encode()).digest(), hashlib.sha256(self.password.encode()).digest())


def safe_next(value: str | None) -> str:
    return value if value and value.startswith("/") and not value.startswith("//") and "\\" not in value else "/"


STYLE = """
:root{--paper:#F2F5F9;--surface:#fff;--ink:#1B2638;--muted:#5F6B7D;--line:#D9E0EA;--blue:#2F6FDE;--red:#E4572E}
@media (prefers-color-scheme:dark){:root{--paper:#121824;--surface:#1B2331;--ink:#E8EDF5;--muted:#9AA6B8;--line:#2C3748}}
*{box-sizing:border-box}body{margin:0;min-height:100dvh;display:grid;place-items:center;background:var(--paper);color:var(--ink);
font:16px/1.5 "Atkinson Hyperlegible","Segoe UI",system-ui,sans-serif;padding:16px}
.card{width:min(100%,420px);background:var(--surface);border:1px solid var(--line);border-radius:20px;padding:26px}
h1{font:800 1.9rem/1.1 "Bricolage Grotesque","Segoe UI",system-ui,sans-serif;margin:0 0 6px}p{margin:0 0 14px;color:var(--muted)}
input{width:100%;font:inherit;color:var(--ink);background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:6px 0 14px}
button,.btn{display:inline-block;font-weight:700;font-size:1rem;font-family:inherit;line-height:1.2;border:1px solid var(--ink);border-radius:999px;background:var(--ink);color:var(--paper);padding:11px 22px;cursor:pointer;text-decoration:none}
button.secondary,.btn.secondary{background:none;color:var(--ink);border-color:var(--line)}
.err{color:var(--red);font-weight:700}a{color:var(--blue)}
"""
HEAD = ('<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="icon" href="/favicon-32.png" sizes="32x32">'
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png"><meta name="robots" content="noindex">')


def login_page(error: str = "", nxt: str = "/") -> str:
    return f"""<!doctype html><html lang="da"><head>{HEAD}<title>Familieplan – log ind</title><style>{STYLE}</style></head><body>
<form class="card" method="post" action="/login"><h1>Familieplan</h1><p>Indtast familiens adgangskode.</p>
{f'<p class="err" role="alert">{escape(error)}</p>' if error else ''}
<input type="hidden" name="next" value="{escape(nxt)}"><label for="pw">Adgangskode</label>
<input id="pw" name="password" type="password" autocomplete="current-password" autofocus required><button>Log ind</button></form></body></html>"""


AUTH_PAGE = """<!doctype html><html lang="da"><head>""" + HEAD + """<title>Familieplan – Aula-login</title><style>""" + STYLE + """
.card{width:min(100%,460px)}.qr{width:min(100%,300px);aspect-ratio:1;margin:8px auto 14px;border-radius:12px;overflow:hidden;border:1px solid var(--line);background:#fff}
.qr svg{width:100%;height:100%;display:block}.otp{font:800 2.4rem/1 "Bricolage Grotesque",system-ui,sans-serif;letter-spacing:.2em;text-align:center;margin:14px 0}
.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px}.pill{display:inline-block;font:700 .8rem system-ui;padding:3px 10px;border-radius:999px}
.ok{background:#DDF3EE;color:#0E6B5B}.warn{background:#FDE2D9;color:#9C2F12}.wait{background:#E4ECFB;color:#1F4FA8}small{color:var(--muted)}
</style></head><body><div class="card"><h1>Aula-login</h1><div id="out"><p>Henter status …</p></div>
<div class="row" style="margin-top:14px"><a class="btn secondary" href="/">Tilbage til appen</a></div></div>
<script>
const H={'X-Requested-With':'familieplan'};let qr=[],i=0;
const t=s=>s?new Date(s).toLocaleTimeString('da-DK',{hour:'2-digit',minute:'2-digit'}):'–';
async function post(u){await fetch(u,{method:'POST',headers:H});poll()}
function draw(s){
  const o=document.getElementById('out'),L=s.login||{};
  if(L.active){
    qr=L.qr||[];
    o.innerHTML=`<span class="pill wait">Venter på MitID</span><p style="margin-top:10px">${L.message||''}</p>`+
      (qr.length?`<div class="qr" id="qr">${qr[0]}</div><p><small>Koden skifter, mens du scanner. Åbn MitID-appen, vælg QR-kode og scan.</small></p>`:'')+
      (L.otp?`<div class="otp">${L.otp}</div>`:'')+`<p><small>Siden venter op til 10 minutter.</small></p>`;return}
  qr=[];
  if(s.aula==='ok'||s.aula==='skipped'){
    o.innerHTML=`<span class="pill ok">${s.aula==='ok'?'Logget ind i Aula':'Aula er slået fra'}</span>
    <p style="margin-top:10px">Sidst hentet kl. ${t(s.last_success)}.${s.running?' Henter nu …':''}</p>`;return}
  if(s.aula==='login_required'||s.aula==='unknown'){
    o.innerHTML=`<span class="pill warn">Login mangler</span><p style="margin-top:10px">Aula-loginet er udløbet. Appen viser de seneste data, men henter ikke nyt, før du har logget ind med MitID.</p>
    ${s.last_error?`<p class="err">${s.last_error}</p>`:''}<div class="row"><button onclick="post('/api/auth/start')">Log ind med MitID</button></div>`;return}
  o.innerHTML=`<span class="pill warn">Fejl</span><p style="margin-top:10px">${s.last_error||'Ukendt fejl'}</p>
    <div class="row"><button onclick="post('/api/auth/start')">Prøv igen med MitID</button></div>`}
async function poll(){
  const r=await fetch('/api/auth/status',{headers:H});if(r.status===401){location='/login?next=/auth';return}
  draw(await r.json())}
setInterval(poll,2000);poll();
setInterval(()=>{if(qr.length>1){i=(i+1)%qr.length;const e=document.getElementById('qr');if(e)e.innerHTML=qr[i]}},1200);
</script></body></html>"""


# ---------------------------------------------------------------- app
def create_app(cfg: dict, settings: Settings, password: str, secret: bytes, no_auth: bool) -> FastAPI:
    problems.reset()                           # fejllisten ved titlen starter forfra ved hver (gen)start
    progress.reset()
    state = State()
    hooks = WebAuthHooks(state)
    fetch_family.auth_hooks = hooks
    runner = Runner(cfg, settings, state, hooks)
    guard = Guard(password, secret, settings, no_auth)
    pgate = PrivateGate(settings.private_code, secret, settings.private_minutes, private_mod.store_path(cfg))
    out_dir = Path(cfg.get("output", "web/family.json")).resolve().parent
    (out_dir / "media").mkdir(parents=True, exist_ok=True)
    marker = ReadMarker(cfg, settings, state, runner, out_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        watchdog = ops.Watchdog(settings, state, runner.notifier, runner.sf)

        async def watch():
            while True:
                await asyncio.sleep(300)
                await watchdog.check(datetime.now(TZ))

        tasks = [asyncio.create_task(ops.supervise("planlægger", runner.loop)), asyncio.create_task(ops.supervise("læst-markering", marker.loop)),
                 asyncio.create_task(ops.supervise("tilsyn", watch))]
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runner, app.state.status, app.state.marker = runner, state, marker
    app.state.watchdog = ops.Watchdog(settings, state, runner.notifier, runner.sf)

    @app.middleware("http")
    async def gate(request: Request, call_next):
        path = request.url.path
        if path not in PUBLIC_PATHS and not guard.valid(request.cookies.get(COOKIE)):
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                return RedirectResponse(f"/login?next={quote(path)}", status_code=303)
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if os.path.basename(path) in DENY_NAMES or path.endswith(".tmp"):
            return JSONResponse({"error": "not found"}, status_code=404)
        if path.startswith("/media/") and pgate.is_private_media(path) and not pgate.valid(request.cookies.get(PRIV_COOKIE)):
            return JSONResponse({"error": "not found"}, status_code=404)       # billeder fra private tråde kræver oplåsning
        # Beskytter mod, at andre sider udløser handlinger i dit navn (cookien sendes ellers med)
        if request.method not in ("GET", "HEAD", "OPTIONS") and path != "/login" and request.headers.get("x-requested-with") != "familieplan":
            return JSONResponse({"error": "forbidden"}, status_code=403)
        resp: Response = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Content-Security-Policy", CSP)
        if path.startswith("/media/"):
            resp.headers["Cache-Control"] = "private, max-age=604800"
        elif path not in PUBLIC_PATHS:
            resp.headers["Cache-Control"] = "no-cache"          # altid tjek igen, men 304 hvis uændret
        return resp

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request, next: str = "/"):
        if guard.valid(request.cookies.get(COOKIE)) and not guard.no_auth:
            return RedirectResponse(safe_next(next), status_code=303)
        return HTMLResponse(login_page(nxt=safe_next(next)))

    @app.post("/login")
    async def login(request: Request):
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        ip = request.client.host if request.client else "?"
        nxt = safe_next((form.get("next") or ["/"])[0])
        if guard.blocked(ip):
            return HTMLResponse(login_page("For mange forsøg. Vent et par minutter.", nxt), status_code=429)
        if not guard.check_password((form.get("password") or [""])[0]):
            guard.failed(ip)
            log.warning("Forkert adgangskode fra %s", ip)
            return HTMLResponse(login_page("Forkert adgangskode.", nxt), status_code=401)
        resp = RedirectResponse(nxt, status_code=303)
        resp.set_cookie(COOKIE, guard.token(), max_age=settings.session_days * 86400, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https")
        return resp

    @app.post("/logout")
    async def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE)
        return resp

    def _problems() -> list[dict]:
        out = problems.snapshot()
        if state.mark_error:                   # læst-markering i Aula: tilstanden ligger i ReadMarker
            out.append({"key": "aula.mark", "area": "aula", "area_title": problems.AREAS["aula"],
                        "title": "Læst-markering blev ikke gemt i Aula", "detail": problems.scrub(state.mark_error),
                        "hint": "Appen prøver igen af sig selv.", "action": None, "since": None, "last": None, "count": 1})
        return out

    def _ai_status():
        try:
            import briefing
            return briefing.ai_status(cfg)
        except Exception as e:  # noqa: BLE001 – status må aldrig vælte på grund af AI
            log.warning("Kunne ikke læse AI-status: %s", e)
            return None

    def _weather_status() -> dict:
        try:
            import weather
            return weather.status(cfg, out_dir)
        except Exception:  # noqa: BLE001 – status må aldrig fejle på grund af vejret
            return {"enabled": False, "home": False}

    @app.get("/api/status")
    async def status():
        return {**state.public(), "interval_minutes": settings.interval // 60, "aula_enabled": settings.use_aula,
                "mark_read_enabled": settings.mark_read and settings.use_aula,
                "ai": _ai_status(),            # sprogmodellens tilstand (ingen nøgle, intet indhold) – kun efter login
                "problems": _problems(),       # aktuelle fejl i integrationerne – til ⚠ ved titlen
                "progress": progress.snapshot(),   # fremdrift pr. datatype i den igangværende hentning – til bjælkerne
                "weather": _weather_status()} # vejret slået til / hjemmet sat (kun ja/nej, aldrig placeringen)



    # ----- private tråde: indholdet udleveres kun mod den ekstra kode
    @app.get("/api/private/status")
    async def private_status(request: Request):
        tok = request.cookies.get(PRIV_COOKIE)
        return {"configured": pgate.configured, "unlocked": pgate.valid(tok), "expires_in": pgate.remaining(tok), "protect": private_mod.enabled(cfg)}

    @app.post("/api/private/unlock")
    async def private_unlock(request: Request):
        if not pgate.configured:
            return JSONResponse({"error": "Der er ikke sat en kode til private samtaler på serveren (FAMILIEPLAN_PRIVATE_CODE, mindst 4 tegn)."}, status_code=400)
        ip = "priv:" + (request.client.host if request.client else "?")
        if guard.blocked(ip):
            return JSONResponse({"error": "For mange forsøg. Vent et par minutter."}, status_code=429)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not pgate.check_code(str(body.get("code", "")) if isinstance(body, dict) else ""):
            guard.failed(ip)
            log.warning("Forkert kode til private tråde fra %s", ip)
            return JSONResponse({"error": "Forkert kode."}, status_code=403)       # 401 er forbeholdt "ikke logget ind"
        resp = JSONResponse({"unlocked": True, "expires_in": int(pgate.minutes * 60)})
        resp.set_cookie(PRIV_COOKIE, pgate.token(), max_age=int(pgate.minutes * 60), httponly=True, samesite="lax", secure=request.url.scheme == "https")
        return resp

    @app.post("/api/private/lock")
    async def private_lock():
        resp = JSONResponse({"unlocked": False})
        resp.delete_cookie(PRIV_COOKIE)
        return resp

    @app.get("/api/private")
    async def private_messages(request: Request):
        if not pgate.valid(request.cookies.get(PRIV_COOKIE)):
            return JSONResponse({"error": "locked"}, status_code=403)
        return {"messages": pgate.messages(), "expires_in": pgate.remaining(request.cookies.get(PRIV_COOKIE))}

    @app.post("/api/refresh")
    async def refresh():
        runner.trigger(interactive=False)
        return {"ok": True}

    # ----- hjemmets placering til vejret (sættes én gang med knappen i indstillinger)
    @app.get("/api/home-location")
    async def home_get():
        import weather
        h = weather.load_home(out_dir)
        if not h:
            return {"set": False}
        return {"set": True, "lat": h["lat"], "lon": h["lon"], "set_at": h.get("set"),
                "map": f"https://www.openstreetmap.org/?mlat={h['lat']}&mlon={h['lon']}#map=13/{h['lat']}/{h['lon']}"}

    @app.post("/api/home-location")
    async def home_set(request: Request):
        import weather
        try:
            body = await request.json()
            lat, lon = float(body["lat"]), float(body["lon"])
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"error": "bad request"}, status_code=400)
        try:
            weather.save_home(out_dir, lat, lon, datetime.now(TZ))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        log.info("Hjemmets placering er sat (afrundet)")      # aldrig koordinaterne i loggen
        runner.trigger(interactive=False)                     # hent vejret med det samme
        return await home_get()



    @app.post("/api/messages/read")
    async def messages_read(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "bad request"}, status_code=400)
        ids = body.get("ids") if isinstance(body, dict) else None
        if not (isinstance(ids, list) and 0 < len(ids) <= 50 and all(isinstance(i, str) and re.fullmatch(r"msg:[\w\-]{1,64}", i) for i in ids)):
            return JSONResponse({"error": "bad request"}, status_code=400)
        if not (settings.mark_read and settings.use_aula):
            return {"enabled": False, "queued": 0}
        return {"enabled": True, "queued": marker.add(ids)}

    # ----- kalender: opret, afvis, fortryd, fjern
    store = sugg.Store(out_dir / "suggestions_state.json")
    gcal = sugg.GoogleCalendar(cfg)
    if cfg.get("calendar_write", {}).get("enabled") and not gcal.enabled:     # slået til, men kan ikke bruges
        problems.report("google.write.setup", "google", "Oprettelse i Google Kalender er ikke sat rigtigt op",
                        detail=str(gcal.problem), hint="Se [calendar_write] i config.toml og README (servicekonto).")
    cal_lock = asyncio.Lock()

    async def cal_body(request: Request) -> dict | JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "bad request"}, status_code=400)
        if not isinstance(body, dict) or not isinstance(body.get("key"), str) or not sugg.KEY_RX.match(body["key"]):
            return JSONResponse({"error": "bad request"}, status_code=400)
        return body

    def patch_family(add: dict | None = None, remove_id: str | None = None, event_id: str | None = None, replace: dict | None = None) -> None:
        """Retter kalenderen i family.json med det samme. event_id = Googles id: rammer både den viste kopi (gc:) og iCal-kopien (g:…@)."""
        f = out_dir / "family.json"
        mine = lambda e: bool(event_id) and (e.get("id") == f"gc:{event_id}" or str(e.get("id", "")).startswith(f"g:{event_id}@"))
        try:
            data = json.loads(f.read_text("utf-8"))
            evs = [e for e in data.get("events", []) if e.get("id") != remove_id and not (remove_id is None and replace is None and mine(e))]
            if replace:
                for e in evs:
                    if mine(e):
                        e.update(replace)
            if add and not any(e.get("id") == add["id"] for e in evs):
                evs.append(add)
            evs.sort(key=lambda x: x["start"])
            data["events"] = evs
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
            tmp.replace(f)
        except (OSError, ValueError) as e:
            log.warning("Kunne ikke opdatere family.json efter kalenderændring: %s", e)

    @app.get("/api/calendar")
    async def cal_state():
        wcal = next((g for g in cfg.get("google", []) if sugg.is_write_calendar(cfg, g)), {})
        return {"enabled": gcal.enabled, "problem": gcal.problem, "calendar_id": gcal.calendar_id, "calendar_name": gcal.calendar_name,
                "default_people": wcal.get("default_people", ["family"]), **store.public_state()}

    def change_target(body: dict) -> tuple[str, dict] | JSONResponse:
        """Den aftale, en aflysning/flytning gælder: skal være oprettet af appen og stadig findes."""
        tkey = body.get("target_key")
        if not isinstance(tkey, str) or not sugg.KEY_RX.match(tkey):
            return JSONResponse({"error": "bad request"}, status_code=400)
        store.reload()
        t = store.get(tkey)
        if not t or t.get("status") != "created" or not t.get("event_id"):
            return JSONResponse({"error": "Aftalen findes ikke længere i appen – tjek Google Kalender."}, status_code=404)
        return tkey, t

    @app.post("/api/calendar/cancel")
    async def cal_cancel(request: Request):
        """Aflysning: fjern den aftale, appen har oprettet, og markér forslaget som behandlet."""
        body = await cal_body(request)
        if isinstance(body, JSONResponse):
            return body
        tgt = change_target(body)
        if isinstance(tgt, JSONResponse):
            return tgt
        tkey, t = tgt
        if not gcal.enabled:
            return JSONResponse({"error": f"Oprettelse i Google Kalender er ikke sat op ({gcal.problem})."}, status_code=400)
        try:
            async with cal_lock:
                await gcal.delete(t["event_id"])
        except sugg.CalendarError as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)
        store.set(tkey, "deleted", version=int(t.get("version", 0)) + 1)
        store.set(body["key"], "applied")
        patch_family(event_id=t["event_id"])
        runner.trigger()
        return {"status": "applied"}

    @app.post("/api/calendar/move")
    async def cal_move(request: Request):
        """Flytning: ret dato/tid på den aftale, appen har oprettet. Titlen beholdes."""
        body = await cal_body(request)
        if isinstance(body, JSONResponse):
            return body
        tgt = change_target(body)
        if isinstance(tgt, JSONResponse):
            return tgt
        tkey, t = tgt
        if not gcal.enabled:
            return JSONResponse({"error": f"Oprettelse i Google Kalender er ikke sat op ({gcal.problem})."}, status_code=400)
        old = t.get("event", {})
        payload = {"title": body.get("title") or old.get("title"), "date": body.get("date"), "end_date": body.get("end_date") or body.get("date"),
                   "all_day": bool(body.get("all_day")), "start_time": body.get("start_time"), "end_time": body.get("end_time"),
                   "location": body.get("location") if body.get("location") is not None else old.get("location"),
                   "description": old.get("notes") or ""}
        try:
            async with cal_lock:
                res = await gcal.patch(t["event_id"], payload)
                ev = sugg.app_event(payload, t["event_id"])
        except sugg.CalendarError as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)
        store.set(tkey, "created", **{**{k: v for k, v in t.items() if k not in ("status", "at")}, "event": ev, "date": payload["date"]})
        store.set(body["key"], "applied")
        patch_family(event_id=t["event_id"], replace={"start": ev["start"], "end": ev["end"], "allDay": ev["allDay"], "title": ev["title"], "location": ev.get("location"),
                                                       "endInferred": bool(ev.get("endInferred"))})
        runner.trigger()
        return {"status": "applied", "html_link": res.get("html_link")}

    @app.post("/api/calendar/events")
    async def cal_create(request: Request):
        body = await cal_body(request)
        if isinstance(body, JSONResponse):
            return body
        if not gcal.enabled:
            return JSONResponse({"error": f"Oprettelse i Google Kalender er ikke sat op ({gcal.problem})."}, status_code=400)
        key = body["key"]
        store.reload()
        prev = store.get(key) or {}
        if prev.get("status") == "created":
            return {"status": "created", "already": True, "html_link": prev.get("html_link")}
        try:
            async with cal_lock:
                res = await gcal.create(key, body, int(prev.get("version", 0)))
                ev = sugg.app_event(body, res["id"])
                src = body.get("source")
                source = ({"type": str(src.get("type", ""))[:20], "id": str(src["id"])}
                          if isinstance(src, dict) and re.fullmatch(r"[\w:.\-]{1,80}", str(src.get("id", ""))) else None)
                store.set(key, "created", event_id=res["id"], version=res.get("version", 0), html_link=res.get("html_link"), event=ev,
                          source=source, date=str(body.get("date")))
        except sugg.CalendarError as e:
            log.warning("Kunne ikke oprette aftale: %s", e)
            return JSONResponse({"error": str(e)}, status_code=e.status)
        people = fetch_family.People(cfg["people"])
        patch_family(add={"id": f"gc:{res['id']}", "title": ev["title"], "start": ev["start"], "end": ev["end"], "allDay": ev["allDay"],
                          "people": people.in_text(ev["title"]) or ["family"], "source": "google", "calendar": "Familiekalender",
                          "location": ev.get("location"), "notes": ev.get("notes"), "pending": True, "appCreated": True,
                          **({"endInferred": True} if ev.get("endInferred") else {})})
        runner.trigger()                                  # opdatér data hurtigt, så andre enheder også ser den
        return {"status": "created", "already": res.get("already", False), "html_link": res.get("html_link")}

    @app.post("/api/calendar/dismiss")
    async def cal_dismiss(request: Request):
        body = await cal_body(request)
        if isinstance(body, JSONResponse):
            return body
        store.set(body["key"], "dismissed")
        return {"status": "dismissed"}

    @app.post("/api/calendar/restore")
    async def cal_restore(request: Request):
        body = await cal_body(request)
        if isinstance(body, JSONResponse):
            return body
        store.reload()
        if (store.get(body["key"]) or {}).get("status") == "dismissed":
            store.clear(body["key"])
        return {"status": "new"}

    @app.post("/api/calendar/remove")
    async def cal_remove(request: Request):
        """Fortryd en oprettelse: sletter aftalen i Google Kalender igen."""
        body = await cal_body(request)
        if isinstance(body, JSONResponse):
            return body
        store.reload()
        st = store.get(body["key"]) or {}
        if st.get("status") != "created":
            return {"status": "new"}
        try:
            if gcal.enabled and st.get("event_id"):
                await gcal.delete(st["event_id"])
        except sugg.CalendarError as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)
        store.set(body["key"], "deleted", version=int(st.get("version", 0)) + 1)     # gammelt id er optaget hos Google; næste oprettelse bruger et nyt
        patch_family(event_id=st.get("event_id"))
        return {"status": "new"}

    @app.post("/api/auth/start")
    async def auth_start():
        runner.trigger(interactive=True)
        return {"ok": True}

    @app.get("/api/auth/status")
    async def auth_status():
        return {**state.public(), "login": state.login}

    @app.get("/auth", response_class=HTMLResponse)
    async def auth_page():
        return HTMLResponse(AUTH_PAGE)

    def data_route(name: str):
        async def handler(request: Request):
            f = out_dir / name
            if not f.exists():
                return JSONResponse({"error": "not found"}, status_code=404)
            st = f.stat()
            etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
            if request.headers.get("if-none-match") == etag:           # uændret siden sidst: send ikke hele filen igen
                return Response(status_code=304, headers={"ETag": etag})
            return FileResponse(f, media_type="application/json", headers={"ETag": etag})
        return handler

    async def manifest():
        return FileResponse(APP_WEB / "manifest.webmanifest", media_type="application/manifest+json")
    app.add_api_route("/manifest.webmanifest", manifest, methods=["GET"])

    for fname in ("family.json", "briefing.json", "briefing_uge.json"):
        app.add_api_route(f"/{fname}", data_route(fname), methods=["GET"])
    app.mount("/media", StaticFiles(directory=out_dir / "media"), name="media")
    app.mount("/", StaticFiles(directory=APP_WEB, html=True), name="app")
    return app


def load_secret() -> bytes:
    env = os.environ.get("FAMILIEPLAN_SECRET")
    if env:
        return env.encode()
    f = Path("secrets/session.key")
    f.parent.mkdir(parents=True, exist_ok=True)
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        try:
            f.chmod(0o600)
        except OSError:
            pass
    return f.read_text().strip().encode()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 for at nå serveren fra andre enheder")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--no-aula", action="store_true", help="spring Aula over (til test)")
    ap.add_argument("--no-auth", action="store_true", help="ingen adgangskode – kun til test på localhost")
    ap.add_argument("--selftest", action="store_true", help="tjek opsætningen (Aula, Google, push, rettigheder …), skriv en rapport og afslut")
    ap.add_argument("--no-notify", action="store_true", help="sammen med --selftest: send ikke en prøvebesked via ntfy")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        sys.exit(f"Fandt ikke {cfg_path}. Kopiér config.example.toml til config.toml og udfyld den.")
    import ops
    loaded = ops.load_dotenv(Path.cwd() / ".env", cfg_path.parent / ".env")   # Docker sætter dem selv; direkte kørsel læser .env
    if loaded:
        logging.getLogger("familieplanner").info("Læste fra .env: %s", ", ".join(loaded))
    cfg = tomllib.loads(cfg_path.read_text("utf-8"))
    settings = Settings(cfg)
    if args.no_aula:
        settings.use_aula = False

    if args.selftest:
        import selftest
        sys.exit(selftest.main(cfg, settings, notify=not args.no_notify))

    password = os.environ.get("FAMILIEPLAN_PASSWORD", "")
    if args.no_auth:
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            sys.exit("--no-auth må kun bruges sammen med --host 127.0.0.1")
    elif len(password) < 8:
        sys.exit("Sæt miljøvariablen FAMILIEPLAN_PASSWORD til en adgangskode på mindst 8 tegn.")

    app = create_app(cfg, settings, password, load_secret(), args.no_auth)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning",
                proxy_headers=settings.trust_proxy, forwarded_allow_ips="*" if settings.trust_proxy else None)


if __name__ == "__main__":
    main()
