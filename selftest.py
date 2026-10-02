"""Selvtest: tjekker alt, Familieplan afhænger af, og skriver en læsbar rapport.

    python server.py --selftest            (eller: python selftest.py)
    python server.py --selftest --no-notify  (send ikke en prøvebesked via ntfy)

Hvert punkt får ✔ (virker), ⚠ (virker, men bør rettes) eller ✖ (virker ikke) – og en forklaring på, hvad du skal gøre.
Testen ændrer intet af betydning: den opretter en aftale langt ude i fremtiden i familiekalenderen og sletter den igen med det samme.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import importlib.metadata
import os
import secrets
import stat
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import httpx

import fetch_family as F
import ops
import private as private_mod
import suggestions as S

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"


@dataclass
class Result:
    status: str
    title: str
    detail: str = ""
    hint: str = ""


def aula_version() -> str | None:
    try:
        return importlib.metadata.version("aula")
    except importlib.metadata.PackageNotFoundError:
        return None


def python_ok() -> bool:
    return sys.version_info >= (3, 14)


# ---------------------------------------------------------------- de enkelte punkter
def check_python(use_aula: bool) -> Result:
    v = ".".join(map(str, sys.version_info[:3]))
    if python_ok():
        return Result(OK, "Python", v)
    if use_aula:
        return Result(FAIL, "Python", f"{v} – Aula-pakken kræver Python 3.14 eller nyere", "Brug Docker-opsætningen, eller kør med `uv run --python 3.14 …` (se README).")
    return Result(WARN, "Python", f"{v} – Aula-pakken kræver 3.14, men Aula er slået fra", "")


def check_config(cfg: dict, use_aula: bool) -> list[Result]:
    out = []
    kids = [p for p in cfg.get("people", []) if p.get("role") == "child"]
    out.append(Result(OK if kids else FAIL, "Personer", f"{len(cfg.get('people', []))} personer, {len(kids)} børn", "" if kids else "Tilføj mindst ét barn som [[people]] i config.toml."))
    if use_aula:
        user = cfg.get("aula", {}).get("mitid_username", "")
        bad = not user or "DIT_MITID" in user
        out.append(Result(FAIL if bad else OK, "MitID-brugernavn", "ikke udfyldt" if bad else "udfyldt", "Sæt aula.mitid_username i config.toml." if bad else ""))
    out_dir = Path(cfg.get("output", "web/family.json")).parent
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        probe = out_dir / f".selftest-{secrets.token_hex(3)}"
        probe.write_text("x")
        probe.unlink()
        out.append(Result(OK, "Datamappe", f"{out_dir} kan skrives til"))
    except OSError as e:
        out.append(Result(FAIL, "Datamappe", f"{out_dir}: {e}", "Giv serveren skriveadgang til mappen."))
    return out


def check_access(settings) -> list[Result]:
    pw = os.environ.get("FAMILIEPLAN_PASSWORD", "")
    out = [Result(OK if len(pw) >= 8 else FAIL, "Adgangskode til appen", "sat" if len(pw) >= 8 else "mangler eller er under 8 tegn",
                  "" if len(pw) >= 8 else "Sæt miljøvariablen FAMILIEPLAN_PASSWORD (mindst 8 tegn).")]
    code = settings.private_code
    out.append(Result(OK if len(code) >= 4 else WARN, "Kode til private samtaler", "sat" if len(code) >= 4 else "ikke sat – private samtaler kan ikke åbnes i appen",
                      "" if len(code) >= 4 else "Sæt FAMILIEPLAN_PRIVATE_CODE (mindst 4 tegn, helst en anden end adgangskoden)."))
    out.append(Result(OK if settings.public_url else WARN, "Offentlig adresse", settings.public_url or "ikke sat",
                      "" if settings.public_url else "Uden server.public_url kan push-beskeder ikke åbne appen ved et tryk."))
    return out


def check_permissions(cfg: dict) -> list[Result]:
    if os.name != "posix":
        return []
    files = [Path(cfg.get("aula", {}).get("token_file", "secrets/aula_tokens.json")), private_mod.store_path(cfg), Path(cfg.get("calendar_write", {}).get("service_account_file", "secrets/google_service_account.json"))]
    loose = [str(f) for f in files if f.exists() and f.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)]
    return [Result(WARN if loose else OK, "Rettigheder på nøglefiler", "for åbne: " + ", ".join(loose) if loose else "kun ejeren kan læse dem",
                   "Kør: chmod 600 " + " ".join(loose) if loose else "")]


async def check_ical(cfg: dict) -> list[Result]:
    out = []
    for g in cfg.get("google", []):
        name, url = g.get("name", "Google"), g.get("ical_url", "")
        title = f"Google-kalender «{name}»"
        if not url or "/.../" in url:
            out.append(Result(FAIL, title, "adressen er ikke udfyldt", "Indsæt kalenderens hemmelige adresse i iCal-format (Google Kalender → Indstillinger → Integrer kalender)."))
            continue
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
                r = await http.get(url)
                r.raise_for_status()
            import icalendar
            import recurring_ical_events
            cal = icalendar.Calendar.from_ical(r.content)
            now = dt.datetime.now(F.TZ)
            n = len(list(recurring_ical_events.of(cal).between(now - dt.timedelta(days=cfg.get("days_back", 7)), now + dt.timedelta(days=cfg.get("days_ahead", 28)))))
            out.append(Result(OK, title, f"hentet – {n} aftaler i det vindue, appen viser"))
        except httpx.HTTPStatusError as e:
            out.append(Result(FAIL, title, f"Google svarede {e.response.status_code}", "Tjek adressen. Den skal være den HEMMELIGE adresse i iCal-format, og den ændres, hvis du nulstiller den i Google."))
        except Exception as e:  # noqa: BLE001
            out.append(Result(FAIL, title, f"{e.__class__.__name__}: {e}", "Kan serveren nå internettet? Er adressen skrevet rigtigt?"))
    return out


async def check_aula(cfg: dict, people: "F.People") -> list[Result]:
    ver = aula_version()
    if ver is None:
        return [Result(FAIL, "Aula-pakken", "er ikke installeret", "Installér afhængighederne: pip install -r requirements.txt (kræver Python 3.14).")]
    out = [Result(OK, "Aula-pakken", f"version {ver}")]
    F.auth_hooks.interactive = False                  # ubemandet: er login udløbet, skal det meldes, ikke vente på en QR-kode
    try:
        async with await F.open_aula_client(cfg) as client:
            profile = await client.get_profile()
            known = [c.name for c in profile.children]
            missing = [p["name"] for p in cfg.get("people", []) if p.get("role") == "child" and p.get("aula_name") and not any(people.by_aula_name(n) == p["id"] for n in known)]
            out.append(Result(OK, "Aula-login", f"logget ind; børn i Aula: {', '.join(known) or 'ingen'}"))
            if missing:
                out.append(Result(WARN, "Børn i config uden match i Aula", ", ".join(missing), "Ret aula_name i config.toml, så det stemmer med navnet i Aula."))
            can_page = hasattr(client, "_request_with_version_retry") and hasattr(client, "api_url")
            t0 = time.time()
            threads = await F._all_threads(client, 60, 3)
            out.append(Result(OK if can_page else WARN, "Beskeder og sideinddeling", f"{len(threads)} tråde hentet fra op til 3 sider på {time.time() - t0:.1f} sek."
                              if can_page else f"{len(threads)} tråde – biblioteket kan kun hente første side",
                              "" if can_page else "Opgradér `aula`, ellers ses kun de nyeste beskeder."))
            has_mark = hasattr(client, "mark_thread_read")
            out.append(Result(OK if has_mark else WARN, "Markér som læst i Aula", "understøttes" if has_mark else "understøttes ikke af den installerede version",
                              "" if has_mark else "Opgradér `aula` til 1.12 eller nyere. Indtil da markeres kun i appen, ikke i Aula."))
    except F.LoginRequired:
        out.append(Result(FAIL, "Aula-login", "udløbet – MitID skal godkendes igen", "Åbn appen på /auth og log ind med MitID, eller kør `python fetch_family.py` i en terminal."))
    except Exception as e:  # noqa: BLE001
        out.append(Result(FAIL, "Aula-login", f"{e.__class__.__name__}: {e}", "Kør `python fetch_family.py -v` for flere oplysninger."))
    return out


async def check_google_write(cfg: dict) -> list[Result]:
    cw = cfg.get("calendar_write", {})
    if not cw.get("enabled"):
        return [Result(SKIP, "Skrivning til Google Kalender", "slået fra (valgfrit)", "Slå til med [calendar_write] enabled = true for at oprette aftaler direkte fra forslag.")]
    g = S.GoogleCalendar(cfg)
    if not g.enabled:
        return [Result(FAIL, "Skrivning til Google Kalender", g.problem or "ikke sat op", "Se README: afsnittet om servicekonto.")]
    day = dt.date.today() + dt.timedelta(days=700)         # langt væk, så en glemt prøve aldrig forstyrrer
    payload = {"title": "Familieplan selvtest (slettes automatisk)", "date": day.isoformat(), "all_day": True, "description": "Oprettet af selvtesten og slettet igen."}
    created = None
    try:
        created = await g.create("st_" + secrets.token_hex(6), payload)
        await g.delete(created["id"])
        return [Result(OK, "Skrivning til Google Kalender", f"kan oprette og slette aftaler i {g.calendar_id}")]
    except S.CalendarError as e:
        hint = "Del kalenderen med servicekontoens e-mailadresse (ret: «Foretag ændringer i begivenheder»)." if e.status == 403 else "Tjek kalender-id og at Calendar API er slået til for projektet."
        if created:
            hint += f" Prøveaftalen ({created['id']}) kunne ikke slettes – fjern «Familieplan selvtest» i kalenderen."
        return [Result(FAIL, "Skrivning til Google Kalender", str(e), hint)]


async def check_push(settings, notify: bool) -> Result:
    n = ops.Notifier(settings.ntfy, settings.public_url)
    if not n.enabled:
        return Result(SKIP, "Push via ntfy", "ikke sat op (valgfrit)", "Sæt server.notify_ntfy for beskeder om udløbet login, nye forslag og aftenens overblik.")
    if not notify:
        return Result(SKIP, "Push via ntfy", "springet over (--no-notify)")
    ok = await n.send("Familieplan selvtest", "Hvis du ser denne besked, virker push. ✔", tags=("white_check_mark",))
    return Result(OK if ok else FAIL, "Push via ntfy", "prøvebesked sendt – tjek din telefon" if ok else f"kunne ikke sende til {n.base}/{n.topic}", "" if ok else "Tjek adressen og at serveren kan nå ntfy.")


def check_data(cfg: dict) -> list[Result]:
    f = Path(cfg.get("output", "web/family.json"))
    if not f.exists():
        return [Result(WARN, "Data", "family.json findes ikke endnu", "Kør `python fetch_family.py` eller start serveren; første hentning laver den.")]
    import json
    try:
        d = json.loads(f.read_text("utf-8"))
    except ValueError:
        return [Result(FAIL, "Data", "family.json kan ikke læses", "Slet den; næste hentning laver en ny.")]
    age = (dt.datetime.now(F.TZ) - dt.datetime.fromisoformat(d["generated"])).total_seconds() / 3600 if d.get("generated") else None
    out = [Result(OK if age is not None and age < 1 else WARN, "Seneste hentning", f"{age:.1f} timer siden" if age is not None else "ukendt tidspunkt", "" if age is not None and age < 1 else "Kører serveren? Se `docker compose logs` eller `journalctl`.")]
    h = d.get("health", {})
    if h.get("google", {}).get("ok") is False:
        out.append(Result(WARN, "Google Kalender i seneste hentning", "kunne ikke hentes – appen viser sidst kendte aftaler", "Kør selvtesten igen for at se årsagen."))
    exposed = [m["id"] for m in d.get("messages", []) if m.get("private") and not m.get("redacted") and (m.get("text") or m.get("thread"))]
    if private_mod.enabled(cfg):
        out.append(Result(FAIL if exposed else OK, "Private samtaler i family.json", f"{len(exposed)} ligger i klar tekst" if exposed else "ingen indhold – det ligger i den beskyttede fil",
                          "Kør en ny hentning; den flytter dem." if exposed else ""))
    else:
        out.append(Result(WARN, "Private samtaler", "beskyttelsen er slået fra ([private] protect = false)", "Kun nødvendigt, hvis du bruger appen uden serveren. Alle, der kan hente family.json, kan læse dem."))
    return out


def check_assistant(cfg: dict) -> Result:
    mode = cfg.get("assistant", {}).get("mode", "offline")
    if mode == "claude":
        env = cfg["assistant"].get("api_key_env", "ANTHROPIC_API_KEY")
        return Result(OK if os.environ.get(env) else FAIL, "Familieassistent (Claude)", "API-nøgle fundet" if os.environ.get(env) else f"{env} er ikke sat", "" if os.environ.get(env) else f"Sæt {env}, eller skift til mode = \"offline\".")
    return Result(OK, "Familieassistent", {"offline": "uden sprogmodel – intet forlader maskinen", "off": "slået fra"}.get(mode, mode))


# ---------------------------------------------------------------- samlet kørsel
async def run_checks(cfg: dict, settings, notify: bool = True) -> list[Result]:
    people = F.People(cfg["people"])
    res: list[Result] = [check_python(settings.use_aula), *check_config(cfg, settings.use_aula), *check_access(settings), *check_permissions(cfg)]
    res += await check_ical(cfg)
    res += await check_aula(cfg, people) if settings.use_aula else [Result(SKIP, "Aula", "slået fra")]
    res += await check_google_write(cfg)
    res.append(await check_push(settings, notify))
    res.append(check_assistant(cfg))
    res += check_data(cfg)
    return res


ICON = {OK: "✔", WARN: "⚠", FAIL: "✖", SKIP: "–"}
COLOR = {OK: "32", WARN: "33", FAIL: "31", SKIP: "90"}


def render(results: list[Result], color: bool = False) -> str:
    paint = (lambda s, st: f"\033[{COLOR[st]}m{s}\033[0m") if color else (lambda s, st: s)
    lines = ["", "Familieplan – selvtest", "=" * 22]
    for r in results:
        lines.append(f"{paint(ICON[r.status], r.status)} {r.title}" + (f": {r.detail}" if r.detail else ""))
        if r.hint and r.status in (WARN, FAIL):
            lines.append(f"    → {r.hint}")
    n = {s: sum(1 for r in results if r.status == s) for s in ICON}
    lines += ["", f"{n[OK]} i orden, {n[WARN]} advarsler, {n[FAIL]} fejl" + (f", {n[SKIP]} sprunget over" if n[SKIP] else "")]
    lines.append("Alt kritisk virker." if not n[FAIL] else "Ret fejlene (✖) og kør testen igen.")
    return "\n".join(lines)


def exit_code(results: list[Result]) -> int:
    return 1 if any(r.status == FAIL for r in results) else 0


def main(cfg: dict | None = None, settings=None, notify: bool = True) -> int:
    if cfg is None:
        import argparse
        ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        ap.add_argument("--config", default="config.toml")
        ap.add_argument("--no-notify", action="store_true")
        ap.add_argument("--no-aula", action="store_true")
        a = ap.parse_args()
        if not Path(a.config).exists():
            print(f"Fandt ikke {a.config}. Kopiér config.example.toml til config.toml og udfyld den.")
            return 1
        cfg = tomllib.loads(Path(a.config).read_text("utf-8"))
        import server
        settings = server.Settings(cfg)
        settings.use_aula = settings.use_aula and not a.no_aula
        notify = not a.no_notify
    results = asyncio.run(run_checks(cfg, settings, notify))
    print(render(results, color=sys.stdout.isatty()))
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
