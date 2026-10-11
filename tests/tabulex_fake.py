"""En simuleret Tabulex til testene (httpx.MockTransport). Login-kæden og svarene er bygget ud fra en rigtig
HAR-optagelse og læse-scriptet 11/10-2026: Aula-login → SAML-formular → WS-Fed-formular → FedAuth-cookie, og
JSON-svar med samme felter som /api/PersonsAdgangTilBoern, /api/Fravaer/Idag og /api/Fravaer/skoledage.
Opfundne navne og CPR-numre – aldrig rigtige data."""
from __future__ import annotations

import datetime as dt
import json
from urllib.parse import parse_qs

import httpx

HUGO_CPR, CARLA_CPR = "0101140001", "0202170002"   # opfundne, men med CPR-form, så testene kan lede efter læk
TOKEN = "fake-aula-widget-token"

SSO = "https://saml.borger.tabulex.dk/simplesaml/module.php/tabulexaulalogin/aula.php"
IDP = "https://borger-aula-login.tabulex.dk/simplesaml/saml2/idp/SSOService.php"
ACS = "https://saml.borger.tabulex.dk/simplesaml/module.php/saml/sp/saml2-acs.php/borger"
PRP = "https://saml.borger.tabulex.dk/simplesaml/module.php/adfs/idp/prp.php"
BASE = "https://foraeldre.tabulex.net"


def _html_form(action: str, fields: dict) -> str:
    inputs = "".join(f'<input type="hidden" name="{k}" value="{v}">' for k, v in fields.items())
    return f'<html><body onload="document.forms[0].submit()"><form method="post" action="{action}">{inputs}</form></body></html>'


class FakeTabulex:
    def __init__(self, today: dt.date):
        self.today = today
        self.kids = [
            {"Fornavn": "Carla Mellem", "Efternavn": "Test", "Cpr": CARLA_CPR, "Klasse": "3A", "SkoleNavn": "Testskolen",
             "SkoleKode": "999999", "SkoleFravaerAdgang": True, "SkoleBestyrelsesValgAdgang": True, "Pige": True},
            {"Fornavn": "Hugo Mellem", "Efternavn": "Test", "Cpr": HUGO_CPR, "Klasse": "6B", "SkoleNavn": "Testskolen",
             "SkoleKode": "999999", "SkoleFravaerAdgang": True, "SkoleBestyrelsesValgAdgang": True, "Pige": False},
        ]
        self.trin = {CARLA_CPR: 3, HUGO_CPR: 6}
        self.sick = {CARLA_CPR: False, HUGO_CPR: False}
        self.school_day = True
        self.accept_token = True           # False = Aula-tokenet afvises (login mislykkes)
        self.ignore_writes = False         # svarer 200 på sygemelding, men gemmer intet
        self.fail_put = None               # statuskode, Tabulex svarer på sygemeldingen
        self.down = False                  # alt svarer 503
        self.puts: list[str] = []
        self.logins = 0
        self.transport = httpx.MockTransport(self.handle)

    # ------------------------------------------------------------ svar
    def _today_json(self, cpr: str) -> dict:
        sick = self.sick[cpr]
        trin = self.trin[cpr]
        return {"HarHalvdagsFravaerSetting": True, "Lektioner": None, "Trin": trin,
                "FoersteLektionAarsag": "Sygdom" if sick else None,
                "SidsteLektionAarsag": "Sygdom" if sick and trin > 6 else None,
                "Lektion": None, "Dag": False, "Aarsag": None, "Dato": f"{self.today.isoformat()}T00:00:00+02:00", "Note": None}

    def _days_json(self) -> list:
        tomorrow = self.today + dt.timedelta(days=1)
        return [{"Dato": f"{self.today.isoformat()}T00:00:00+02:00", "SkoleDag": self.school_day},
                {"Dato": f"{tomorrow.isoformat()}T00:00:00+02:00", "SkoleDag": True}]

    def handle(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url).split("?")[0]
        if self.down:
            return httpx.Response(503, text="Service Unavailable")
        if url == SSO and req.method == "POST":
            form = parse_qs(req.content.decode())
            if not self.accept_token or form.get("aulaToken") != [TOKEN] or form.get("userProfile") != ["guardian"]:
                return httpx.Response(200, html="<html>Adgang nægtet</html>")
            return httpx.Response(302, headers={"Location": IDP + "?SAMLRequest=x"})
        if url == IDP:
            return httpx.Response(200, html=_html_form(ACS, {"SAMLResponse": "PHNhbWw+", "RelayState": BASE}))
        if url == ACS and req.method == "POST":
            return httpx.Response(303, headers={"Location": BASE, "Set-Cookie": "SimpleSAMLCitizenAuthToken=a; Domain=.tabulex.dk; Path=/"})
        if url == BASE and req.method == "GET" and "FedAuth" not in req.headers.get("cookie", ""):
            return httpx.Response(302, headers={"Location": PRP + "?wa=wsignin1.0"})
        if url == PRP:
            return httpx.Response(200, html=_html_form(BASE + "/", {"wa": "wsignin1.0", "wresult": "&lt;t&gt;", "wctx": "rm=0"}))
        if url == BASE + "/" and req.method == "POST":
            self.logins += 1
            return httpx.Response(302, headers=[("Location", BASE + "/"), ("Set-Cookie", "FedAuth=f; Path=/; HttpOnly"),
                                                ("Set-Cookie", "XSRF-TOKEN=x1; Path=/")])
        if url in (BASE, BASE + "/") and req.method == "GET":
            return httpx.Response(200, html="<html><body>Tabulex Forældre</body></html>")
        if url.startswith(BASE + "/api/"):
            if "FedAuth" not in req.headers.get("cookie", ""):
                return httpx.Response(200, html="<html>login</html>")     # som den rigtige: HTML i stedet for JSON
            return self._api(req, url[len(BASE):])
        return httpx.Response(404)

    def _api(self, req: httpx.Request, path: str) -> httpx.Response:
        if path == "/api/PersonsAdgangTilBoern":
            return httpx.Response(200, json=self.kids)
        for prefix, fn in (("/api/Fravaer/Idag/", self._today_json), ("/api/Fravaer/skoledage/", lambda c: self._days_json())):
            if path.startswith(prefix):
                cpr = path[len(prefix):]
                if cpr not in self.sick:
                    return httpx.Response(404)
                return httpx.Response(200, json=fn(cpr))
        if path == "/api/Fravaer/MeldSygIdag/" and req.method == "PUT":
            if req.headers.get("X-XSRF-TOKEN") != "x1":
                return httpx.Response(403)
            cpr = json.loads(req.content)["Cpr"]
            self.puts.append(cpr)
            if self.fail_put:
                return httpx.Response(self.fail_put, text=f"fejl for {cpr}")
            if not self.school_day:
                return httpx.Response(400)
            if not self.ignore_writes:
                self.sick[cpr] = True
            return httpx.Response(200)
        return httpx.Response(404)


class Widgets:
    """Aula-klientens `widgets` – kun tokenet til Tabulex-widgetten."""

    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.asked: list[str] = []

    async def _get_bearer_token(self, widget_id: str) -> str:
        self.asked.append(widget_id)
        if self.fail:
            raise self.fail
        return "Bearer " + TOKEN
