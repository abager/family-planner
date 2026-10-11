"""Skolefravær fra Tabulex ("Fravær – forældreindberetning", Aula-widget 0047).

Login: appen henter selv et widget-token hos Aula (samme Aula-login som resten af hentningen) og afleverer det til
Tabulex' Aula-login, præcis som portalens link gør. Tabulex svarer med en kæde af omdirigeringer og selv-indsendende
formularer (SAML → WS-Federation), som følges her. Der er ingen ekstra MitID. Verificeret mod den rigtige Tabulex
11/10-2026 med et læse-script (variant: POST aulaToken + userProfile=guardian + isMobileApp=false).

Sessionen gemmes ikke: der logges ind på ny ved hver hentning og hver sygemelding (ca. 10 kald). Så er der ingen
cookies eller tokens på disken, og intet kan udløbe mellem to hentninger.

CPR: Tabulex bruger barnets CPR-nummer som id i alle kald. CPR findes KUN i hukommelsen under et kald – det skrives
aldrig i family.json, i logs, i fejlbeskeder eller på disken. Fejlbeskeder bygges derfor her og indeholder aldrig
en URL (httpx' egne fejltekster indeholder adressen og dermed CPR).

Tabulex' forældreside kan kun melde syg (i dag, eller i morgen efter kl. 18) – den kan ikke fortryde. Appen tilbyder
kun "i dag" (brugerens beslutning). Fraværsdata sendes aldrig til sprogmodellen.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import html
import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx

log = logging.getLogger("familieplanner.tabulex")

WIDGET_ID = "0047"
SSO_URL = "https://saml.borger.tabulex.dk/simplesaml/module.php/tabulexaulalogin/aula.php?appid=fravaerforaeldre"
BASE = "https://foraeldre.tabulex.net"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
MAX_FORMS = 6
REASON_TEXT = {"Sygdom": "Meldt syg", "Lovligt": "Lovligt fravær", "Ulovligt": "Ulovligt fravær"}
TRANSPORT: httpx.AsyncBaseTransport | None = None      # testene sætter en simuleret Tabulex her
_CPR = re.compile(r"(?<!\d)[0-3]\d[01]\d{3}-?\d{4}(?!\d)")


class _NoCpr(logging.Filter):
    """httpx/httpcore logger adresser (med CPR) på INFO/DEBUG. Serveren sætter httpx til WARNING, men skulle nogen
    skrue op for logningen, fjernes CPR-numre alligevel her."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        if _CPR.search(msg):
            record.msg, record.args = _CPR.sub("***", msg), ()
        return True


for _name in ("httpx", "httpcore", "httpcore.http11", "httpcore.http2", "httpcore.connection", "httpcore.proxy"):
    logging.getLogger(_name).addFilter(_NoCpr())


class TabulexError(Exception):
    """En fejl, der kan vises direkte i appen (dansk, uden URL, CPR eller tokens)."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message, self.status = message, status


def settings(cfg: dict) -> dict:
    t = cfg.get("tabulex", {})
    return {"enabled": bool(t.get("enabled", False)), "timeout": float(t.get("request_timeout", 30))}


# ---------------------------------------------------------------- tolkning af Tabulex' svar
def is_sick(d: dict | None) -> bool:
    """Er barnet meldt syg hele dagen? Samme regel som Tabulex' egen side (halvdagsfravær: fra 7. klasse skal både
    første og sidste lektion være 'Sygdom', ellers første; uden halvdagsfravær: Dag + Aarsag)."""
    if not d:
        return False
    if d.get("HarHalvdagsFravaerSetting"):
        if (d.get("Trin") or 0) > 6:
            return d.get("FoersteLektionAarsag") == "Sygdom" and d.get("SidsteLektionAarsag") == "Sygdom"
        return d.get("FoersteLektionAarsag") == "Sygdom"
    return d.get("Dag") is not None and d.get("Aarsag") == "Sygdom"


def reason(d: dict | None) -> str | None:
    """Fraværsårsagen for dagen, hvis der er nogen ('Sygdom', 'Lovligt', 'Ulovligt')."""
    if not d:
        return None
    if d.get("HarHalvdagsFravaerSetting"):
        r = d.get("FoersteLektionAarsag") or d.get("SidsteLektionAarsag")
    else:
        r = d.get("Aarsag")
    return r if r in REASON_TEXT else None


def _school_day(days: list | None, date: str) -> bool | None:
    for x in days or []:
        if str(x.get("Dato", ""))[:10] == date:
            return bool(x.get("SkoleDag"))
    return None


def entry(person: str, kid: dict, today: dict | None, days: list | None, date: dt.date) -> dict:
    """Det, appen gemmer for et barn i family.json (`absence`). Ingen CPR."""
    day = str((today or {}).get("Dato") or date.isoformat())[:10]
    r = reason(today)
    return {"person": person, "school": kid.get("SkoleNavn") or None, "class": kid.get("Klasse") or None,
            "date": day, "schoolDay": _school_day(days, day), "sick": is_sick(today), "reason": r,
            "text": REASON_TEXT.get(r) if r else None}


# ---------------------------------------------------------------- login og kald
def _auto_form(text: str):
    """Find en selv-indsendende formular (SAML/WS-Fed). (action, method, felter) eller None."""
    m = re.search(r"<form[^>]*>", text, re.I)
    if not m:
        return None
    tag = m.group(0)
    action = re.search(r'action\s*=\s*["\']([^"\']*)', tag, re.I)
    method = re.search(r'method\s*=\s*["\']?(\w+)', tag, re.I)
    fields = {}
    for inp in re.finditer(r"<input[^>]*>", text, re.I):
        t = inp.group(0)
        n = re.search(r'name\s*=\s*["\']([^"\']*)', t, re.I)
        v = re.search(r'value\s*=\s*["\']([^"\']*)', t, re.I)
        if n:
            fields[html.unescape(n.group(1))] = html.unescape(v.group(1)) if v else ""
    if not any(k in fields for k in ("SAMLResponse", "wresult")):
        return None
    return (html.unescape(action.group(1)) if action else ""), (method.group(1).upper() if method else "POST"), fields


def _http_error(e: Exception, what: str) -> TabulexError:
    if isinstance(e, httpx.TimeoutException):
        return TabulexError(f"Tabulex svarede ikke i tide ({what}).")
    if isinstance(e, httpx.HTTPStatusError):
        return TabulexError(f"Tabulex svarede {e.response.status_code} ({what}).")
    return TabulexError(f"Kunne ikke kontakte Tabulex ({what}: {type(e).__name__}).")


class Session:
    """En logget-ind forbindelse til Tabulex' forældreside. Brug `async with await open_session(...) as s`."""

    def __init__(self, http: httpx.AsyncClient):
        self.http = http

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        await self.http.aclose()
        return False

    def _headers(self) -> dict:
        h = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json", "Referer": BASE + "/"}
        xsrf = self.http.cookies.get("XSRF-TOKEN")
        if xsrf:
            h["X-XSRF-TOKEN"] = xsrf
        return h

    async def _get(self, path: str, what: str):
        try:
            r = await self.http.get(BASE + path, headers=self._headers())
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise _http_error(e, what) from None
        if "json" not in r.headers.get("content-type", ""):
            raise TabulexError(f"Tabulex svarede ikke med data ({what}) – loginet er måske udløbet.")
        return r.json()

    async def children(self) -> list[dict]:
        kids = await self._get("/api/PersonsAdgangTilBoern", "børn")
        return [k for k in kids or [] if k.get("Cpr")]

    async def today(self, kid: dict) -> dict:
        return await self._get(f"/api/Fravaer/Idag/{kid['Cpr']}", "fravær i dag") or {}

    async def school_days(self, kid: dict) -> list:
        return await self._get(f"/api/Fravaer/skoledage/{kid['Cpr']}", "skoledage") or []

    async def put_sick_today(self, kid: dict) -> None:
        try:
            r = await self.http.put(BASE + "/api/Fravaer/MeldSygIdag/", json={"Cpr": kid["Cpr"]}, headers=self._headers())
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise _http_error(e, "sygemelding") from None


async def open_session(aula_client, cfg: dict) -> Session:
    """Log ind i Tabulex med et widget-token fra den åbne Aula-klient. Kaster TabulexError, hvis det ikke lykkes."""
    s = settings(cfg)
    try:
        token = str(await aula_client.widgets._get_bearer_token(WIDGET_ID)).removeprefix("Bearer ")   # pakkens metode (aula==1.12.0)
    except Exception as e:  # noqa: BLE001 – fx LoginRequired håndteres af kalderen via Aula-klienten selv
        if type(e).__name__ == "LoginRequired":
            raise
        raise TabulexError(f"Aula udleverede ikke adgang til Tabulex ({type(e).__name__}).") from None
    http = httpx.AsyncClient(follow_redirects=True, timeout=s["timeout"], headers={"User-Agent": UA}, transport=TRANSPORT)
    try:
        r = await http.post(SSO_URL, data={"aulaToken": token, "userProfile": "guardian", "isMobileApp": "false"})
        r.raise_for_status()
        for _ in range(MAX_FORMS):
            form = _auto_form(r.text) if "html" in r.headers.get("content-type", "") else None
            if not form:
                break
            action, method, fields = form
            target = urljoin(str(r.url), action)
            r = await (http.post(target, data=fields) if method == "POST" else http.get(target, params=fields))
        session = Session(http)
        await session.children()                 # bevis på, at vi er logget ind (ellers HTML i stedet for JSON)
        return session
    except httpx.HTTPError as e:
        await http.aclose()
        raise _http_error(e, "login") from None
    except TabulexError as e:
        await http.aclose()
        why = "" if "ikke med data" in e.message else f" ({e.message.rstrip('.')})"
        raise TabulexError(f"Login i Tabulex via Aula mislykkedes{why}.") from None
    except BaseException:
        await http.aclose()
        raise


def match(kids: list[dict], people) -> dict[str, dict]:
    """Person-id → Tabulex-barn (på fornavn, som Aula-børnene). Børn uden fraværsadgang springes over."""
    out: dict[str, dict] = {}
    for k in kids:
        if k.get("SkoleFravaerAdgang") is False:
            continue
        pid = people.by_aula_name(f"{k.get('Fornavn', '')} {k.get('Efternavn', '')}")
        if pid:
            out.setdefault(pid, k)
        else:
            log.warning("Barn i Tabulex matcher ingen person i config: %s", (k.get("Fornavn") or "?").split()[0])
    return out


async def read_child(session: Session, person: str, kid: dict, date: dt.date) -> dict:
    today, days = await asyncio.gather(session.today(kid), session.school_days(kid))
    return entry(person, kid, today, days, date)


# ---------------------------------------------------------------- læsning (i den almindelige hentning)
async def fetch(aula_client, people, date: dt.date, cfg: dict) -> list[dict]:
    """Skolefravær i dag for alle børn i Tabulex, der matcher en person i config."""
    async with await open_session(aula_client, cfg) as s:
        kids = match(await s.children(), people)
        out = [await read_child(s, pid, kid, date) for pid, kid in kids.items()]
    log.info("Tabulex: %d børn med skolefravær", len(out))
    return out


# ---------------------------------------------------------------- skrivning
async def report_sick_today(session: Session, person: str, people, date: dt.date) -> tuple[dict, bool]:
    """Meld barnet syg i dag hos skolen og læs det tilbage. Returnerer (barnets nye data, var allerede syg)."""
    kid = match(await session.children(), people).get(person)
    if kid is None:
        raise TabulexError("Barnet findes ikke i Tabulex.", 404)
    before = await read_child(session, person, kid, date)
    if before["schoolDay"] is False:
        raise TabulexError("Ikke skoledag i dag.", 409)
    if before["sick"]:
        return before, True
    await session.put_sick_today(kid)
    after = await read_child(session, person, kid, date)
    if not after["sick"]:
        raise TabulexError("Tabulex modtog sygemeldingen, men viser den ikke endnu. Tjek i Tabulex.", 502)
    return after, False


def patch_family(path: Path, child: dict) -> None:
    """Skriv barnets nye skolefravær i family.json med det samme, så alle enheder ser det."""
    path = Path(path)
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return
    rows = list(data.get("absence") or [])
    idx = next((i for i, r in enumerate(rows) if r.get("person") == child["person"]), None)
    if idx is None:
        rows.append(child)
    else:
        rows[idx] = child
    data["absence"] = rows
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)
