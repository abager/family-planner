"""Sprogmodel bag én grænseflade: Google Gemini (standard) eller Claude – valgt i config.toml under [ai].

Alt, der kalder en sprogmodel, går herigennem, så de samme regler gælder overalt:

- Svaret skal være JSON. Ugyldigt svar = fejl (kalderen falder tilbage til appens egne regler).
- Samme indhold koster kun én forespørgsel: svar gemmes i `ai_cache.json` under en hash af indholdet.
- Dagsbudget og minutgrænse i `ai_usage.json`. Dagen følger Stillehavstid (America/Los_Angeles), fordi det er
  dér, Googles gratis-kvote nulstilles (kl. 9 dansk tid, 8 når tidsforskellen er en anden ved sommertidsskift).
- Med `schema` beder vi udbyderen om at overholde et bestemt JSON-format (Gemini: responseSchema). Afviser udbyderen
  formatet (HTTP 400), prøves samme forespørgsel én gang uden.
- 429/402/401/403/5xx/timeout giver en pause (backoff) for al AI, så en fejl ikke brænder kvoten af.
- Ugyldigt svar (ikke JSON: `ugyldigt_svar`, eller afvist af kalderens tjek: `fejlede_tjek`) prøves straks én gang til. Fejler det igen, sættes pausen
  kun for netop det indhold – nye data og andre funktioner (fx kalenderforslag) kan stadig spørge.
  Det rå, ugyldige svar gemmes i `ai_last_invalid.json` (kun det seneste, kun lokalt, kun ejeren kan læse det).
- Grunde, der ikke sender noget (manglende nøgle, pause, budget …), logges én gang – ikke ved hver hentning.
- Ingen SDK: almindelig HTTPS med httpx. Nøglen sendes i en header, aldrig i adressen, og logges aldrig.

Denne fil ved intet om familien. Den, der kalder, sørger for at rense data (briefing._scrub) og for, at private
samtaler aldrig kommer med.

Kaldes sådan:

    client = ai.Client(ai.settings_from(cfg), state_dir=Path("web"))
    try:
        svar = client.generate_json(system, prompt, validate=tjek, cache_key=fingerprint)
    except ai.AIUnavailable as e:
        ...  # e.reason fortæller hvorfor; brug reserven
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger("familieplanner.ai")
PACIFIC = ZoneInfo("America/Los_Angeles")
UTC = dt.timezone.utc

CACHE_FILE = "ai_cache.json"
USAGE_FILE = "ai_usage.json"
INVALID_FILE = "ai_last_invalid.json"     # det seneste ugyldige svar – indeholder familiens data, ligger i secrets/
INVALID_MAX_CHARS = 50_000
CACHE_MAX_ENTRIES = 500
CACHE_MAX_DAYS = 30

# Grunde til, at sprogmodellen ikke kunne bruges. Vises aldrig direkte for børnene – kun i log og /api/status.
REASONS = {
    "slaaet_fra": "AI er ikke slået til",
    "mangler_noegle": "API-nøglen mangler i .env",
    "ukendt_udbyder": "ukendt udbyder i [ai] provider",
    "pause": "venter efter en tidligere fejl",
    "dagsbudget": "dagens budget af forespørgsler er brugt",
    "minutgraense": "for mange forespørgsler i dette minut",
    "kvote": "udbyderens kvote er brugt (429)",
    "betaling": "udbyderen kræver betaling (402)",
    "afvist": "udbyderen afviste nøglen eller forespørgslen",
    "serverfejl": "udbyderen har problemer (5xx)",
    "netvaerk": "kunne ikke nå udbyderen",
    "ugyldigt_svar": "svaret var ikke gyldigt JSON i det rigtige format",
    "fejlede_tjek": "svaret holdt ikke appens tjek",
}

# Pause efter fejl: (første pause i sekunder, højeste pause). Fordobles for hver fejl i træk.
BACKOFF = {
    "kvote": (60, 6 * 3600),
    "betaling": (6 * 3600, 6 * 3600),
    "afvist": (30 * 60, 12 * 3600),
    "serverfejl": (120, 3600),
    "netvaerk": (120, 3600),
}
INVALID_BACKOFF = (15 * 60, 6 * 3600)     # pause for ét bestemt indhold, der bliver ved med at give ugyldigt svar


class AIUnavailable(Exception):
    """Sprogmodellen kan ikke bruges lige nu. `reason` er en af nøglerne i REASONS."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason, self.detail = reason, detail
        super().__init__(f"{reason}: {REASONS.get(reason, reason)}" + (f" ({detail})" if detail else ""))


# ---------------------------------------------------------------- indstillinger
@dataclass(frozen=True)
class Settings:
    provider: str = "gemini"
    model: str = "gemini-3.5-flash-lite"
    api_key_env: str = "GEMINI_API_KEY"
    daily_cap: int = 100
    rpm: int = 5
    timeout_seconds: float = 30.0
    max_output_tokens: int = 2000
    temperature: float = 0.2
    max_wait_seconds: float = 30.0          # så længe ventes der højst på minutgrænsen, før der gives op
    base_url: str = ""                      # tom = udbyderens normale adresse


DEFAULTS = {
    "gemini": {"model": "gemini-3.5-flash-lite", "api_key_env": "GEMINI_API_KEY"},
    "claude": {"model": "claude-sonnet-5-5", "api_key_env": "ANTHROPIC_API_KEY"},
}


def settings_from(cfg: dict) -> Settings:
    """[ai] i config.toml. Ældre opsætning med [assistant] mode = "claude" (og model/api_key_env dér) virker stadig."""
    a = dict(cfg.get("ai") or {})
    asst = cfg.get("assistant") or {}
    if asst.get("mode") == "claude":              # betyder altid Claude – også hvis [ai] siger noget andet
        if str(a.get("provider", "claude")).lower() != "claude":
            for k in ("model", "api_key_env"):    # [ai]'s model/nøgle hører til en anden udbyder
                a.pop(k, None)
        a["provider"] = "claude"
        for k in ("model", "api_key_env"):
            if k in asst:
                a[k] = asst[k]
        if "max_tokens" in asst:
            a["max_output_tokens"] = asst["max_tokens"]
    provider = str(a.get("provider", "gemini")).lower()
    d = DEFAULTS.get(provider, {})
    return Settings(
        provider=provider,
        model=str(a.get("model", d.get("model", ""))),
        api_key_env=str(a.get("api_key_env", d.get("api_key_env", ""))),
        daily_cap=int(a.get("daily_cap", Settings.daily_cap)),
        rpm=int(a.get("rpm", Settings.rpm)),
        timeout_seconds=float(a.get("timeout_seconds", Settings.timeout_seconds)),
        max_output_tokens=int(a.get("max_output_tokens", Settings.max_output_tokens)),
        temperature=float(a.get("temperature", Settings.temperature)),
        max_wait_seconds=float(a.get("max_wait_seconds", Settings.max_wait_seconds)),
        base_url=str(a.get("base_url", "")),
    )


# ---------------------------------------------------------------- udbydere
# En udbyder skal kunne to ting: bygge forespørgslen (request) og pakke svarteksten ud (text).
class Gemini:
    url = "https://generativelanguage.googleapis.com/v1beta"

    @staticmethod
    def request(s: Settings, key: str, system: str, prompt: str, schema: dict | None = None) -> tuple[str, dict, dict]:
        base = (s.base_url or Gemini.url).rstrip("/")
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": s.temperature,
                                 "maxOutputTokens": s.max_output_tokens},
        }
        if schema:
            body["generationConfig"]["responseSchema"] = schema   # Gemini skal overholde formatet – ikke bare prøve
        return f"{base}/models/{s.model}:generateContent", {"x-goog-api-key": key, "content-type": "application/json"}, body

    @staticmethod
    def text(data: dict) -> str:
        if (data.get("promptFeedback") or {}).get("blockReason"):
            raise ValueError(f"blokeret: {data['promptFeedback']['blockReason']}")
        cands = data.get("candidates") or []
        if not cands:
            raise ValueError("intet svar")
        c = cands[0]
        if c.get("finishReason") not in (None, "STOP"):
            raise ValueError(f"stoppede: {c.get('finishReason')}")       # fx MAX_TOKENS → halvt JSON
        return "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts", []) if not p.get("thought"))


class Claude:
    url = "https://api.anthropic.com/v1"

    @staticmethod
    def request(s: Settings, key: str, system: str, prompt: str, schema: dict | None = None) -> tuple[str, dict, dict]:
        base = (s.base_url or Claude.url).rstrip("/")             # schema bruges ikke: Claude styres af prompten
        body = {"model": s.model, "max_tokens": s.max_output_tokens, "temperature": s.temperature, "system": system,
                "messages": [{"role": "user", "content": prompt}]}
        return f"{base}/messages", {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, body

    @staticmethod
    def text(data: dict) -> str:
        if data.get("stop_reason") == "max_tokens":
            raise ValueError("stoppede: max_tokens")
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


PROVIDERS = {"gemini": Gemini, "claude": Claude}


# ---------------------------------------------------------------- hjælpere
def parse_json(text: str) -> dict:
    """Tåler ```json-hegn om svaret; skal være et JSON-objekt."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    obj = json.loads(t)
    if not isinstance(obj, dict):
        raise ValueError("svaret er ikke et JSON-objekt")
    return obj


def raw_text(data) -> str | None:
    """Svarteksten uden tjek – til at gemme et ugyldigt svar, så man kan se, hvad modellen skrev."""
    if not isinstance(data, dict):
        return None
    try:
        parts = [p.get("text", "") for c in data.get("candidates") or [] for p in (c.get("content") or {}).get("parts", [])]
        parts += [b.get("text", "") for b in data.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"]
    except AttributeError:
        return None
    return "".join(parts) if parts else None


def content_key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    try:
        os.chmod(tmp, 0o600)                      # svar kan indeholde familiens oplysninger
    except OSError:
        pass
    tmp.replace(path)


def _error_text(body: dict) -> str:
    err = body.get("error") if isinstance(body, dict) else None
    return str((err or {}).get("message") or (err or {}).get("type") or "") if isinstance(err, dict) else ""


def _retry_seconds(resp: httpx.Response, body: dict) -> float | None:
    """Hvor længe udbyderen beder os vente: Retry-After-headeren eller Googles RetryInfo ("37s")."""
    ra = resp.headers.get("retry-after")
    if ra:
        try:
            return float(ra)
        except ValueError:
            pass
    for d in ((body.get("error") or {}).get("details") or []):
        m = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(d.get("retryDelay", "")))
        if m:
            return float(m.group(1))
    return None


def _is_daily_quota(body: dict) -> bool:
    """Googles 429 siger i detaljerne, hvilken kvote der er brugt; en dagskvote kommer først igen ved midnat (Stillehavstid)."""
    for d in ((body.get("error") or {}).get("details") or []):
        for v in d.get("violations") or []:
            if "perday" in str(v.get("quotaId", "")).lower() or "per_day" in str(v.get("quotaMetric", "")).lower():
                return True
    return False


def next_pacific_midnight(now: dt.datetime) -> dt.datetime:
    p = now.astimezone(PACIFIC)
    nxt = dt.datetime.combine(p.date() + dt.timedelta(days=1), dt.time(0, 0), tzinfo=PACIFIC)
    return nxt.astimezone(UTC)


# ---------------------------------------------------------------- klienten
class Client:
    def __init__(self, settings: Settings, state_dir: Path, *, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], dt.datetime] | None = None, sleep: Callable[[float], None] | None = None,
                 env: dict | None = None, invalid_path: Path | None = None):
        self.s = settings
        self.state_dir = Path(state_dir)
        self.cache_path = self.state_dir / CACHE_FILE
        self.usage_path = self.state_dir / USAGE_FILE
        self.invalid_path = Path(invalid_path) if invalid_path else None   # None = ugyldige svar gemmes ikke
        self.transport = transport
        self.clock = clock or (lambda: dt.datetime.now(UTC))
        self.sleep = sleep or time.sleep
        self.env = os.environ if env is None else env

    # ----- offentligt
    def generate_json(self, system: str, prompt: str, *, validate: Callable[[dict], None] | None = None,
                      cache_key: str | None = None, ignore_pause: bool = False, schema: dict | None = None) -> dict:
        """Svaret som dict. Rejser AIUnavailable, hvis der ikke kan svares (kalderen bruger så sin reserve).
        schema: JSON-formatet, svaret skal have (Gemini overholder det; Claude styres af prompten).
        ignore_pause: kun til selvtesten – prøv selvom en tidligere fejl har sat en pause (budgettet gælder stadig)."""
        provider = PROVIDERS.get(self.s.provider)
        if provider is None:
            self._unavailable("ukendt_udbyder", self.s.provider)
        key = (self.env.get(self.s.api_key_env) or "").strip().strip("\"'")
        if not key:
            self._unavailable("mangler_noegle", self.s.api_key_env)
        if not key.isascii() or any(ch.isspace() for ch in key):
            self._unavailable("mangler_noegle", f"{self.s.api_key_env} indeholder ugyldige tegn (mellemrum, æøå …)")

        ckey = content_key(self.s.provider, self.s.model, system, cache_key or prompt)
        hit = None if ignore_pause else self._cache_get(ckey)
        if hit is not None:
            log.info("AI: svar fra cache")
            return hit
        if not ignore_pause:
            self._check_invalid(ckey)             # samme indhold gav for nylig ugyldigt svar – rejser AIUnavailable

        error, reason, data = "", "ugyldigt_svar", {}
        for attempt in (1, 2):                    # et ugyldigt svar prøves straks én gang til
            self._admit(ignore_pause)             # budget, pause og minutgrænse – rejser AIUnavailable
            data, schema = self._post(provider, key, system, prompt, schema, ignore_pause)
            reason = "ugyldigt_svar"
            try:
                result = parse_json(provider.text(data))
                if validate:
                    reason = "fejlede_tjek"       # gyldigt JSON – men kalderens tjek siger nej
                    validate(result)
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                error = str(e)[:200]
                if attempt == 1:
                    log.info("AI: %s (%s) – prøver én gang til", REASONS[reason], error)
                continue
            self._ok(ckey)
            if not ignore_pause:                  # selvtestens svar skal ikke fylde i cachen
                self._cache_put(ckey, result)
            return result
        self._save_invalid(data, error)
        self._fail_invalid(ckey, error, reason)

    def cache_get(self, key: str) -> dict | None:
        """Gemt svar for ét punkt (fx et kalendertjek af én besked), uafhængigt af hvilken samlet forespørgsel det kom fra."""
        return self._cache_get(content_key("punkt", self.s.provider, self.s.model, key))

    def cache_put(self, key: str, value: dict) -> None:
        self._cache_put(content_key("punkt", self.s.provider, self.s.model, key), value)

    def status(self) -> dict:
        """Til /api/status og log. Ingen nøgler, intet indhold."""
        u = self._usage()
        return {"provider": self.s.provider, "model": self.s.model, "day": u.get("day"),
                "requests_today": u.get("requests", 0), "daily_cap": self.s.daily_cap,
                "backoff_until": u.get("backoff_until"), "last_error": u.get("last_error"), "last_ok": u.get("last_ok")}

    # ----- forespørgslen
    def _post(self, provider, key: str, system: str, prompt: str, schema: dict | None,
              ignore_pause: bool = False) -> tuple[dict, dict | None]:
        """Én forespørgsel (to, hvis udbyderen afviser formatet). Fejl hos udbyderen rejser AIUnavailable (med pause).
        Returnerer svaret og det schema, der virkede (None, hvis det måtte droppes)."""
        resp, data = self._send(provider, key, system, prompt, schema)
        if resp.status_code == 400 and schema:
            log.warning("AI: udbyderen afviste svarformatet (%s) – prøver uden", _error_text(data)[:200] or "HTTP 400")
            schema = None
            self._admit(ignore_pause)
            resp, data = self._send(provider, key, system, prompt, None)
        if resp.status_code >= 400:
            self._http_error(resp, data)
        return data, schema

    def _send(self, provider, key: str, system: str, prompt: str, schema: dict | None) -> tuple[httpx.Response, dict]:
        url, headers, body = provider.request(self.s, key, system, prompt, schema)
        self._count_request()
        try:
            with httpx.Client(transport=self.transport, timeout=self.s.timeout_seconds) as http:
                resp = http.post(url, headers=headers, json=body)
        except httpx.TimeoutException as e:
            self._fail("netvaerk", f"timeout: {type(e).__name__}")
        except httpx.HTTPError as e:
            self._fail("netvaerk", type(e).__name__)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return resp, data if isinstance(data, dict) else {}

    # ----- budget og pause
    def _usage(self) -> dict:
        u = _read(self.usage_path)
        today = self.clock().astimezone(PACIFIC).date().isoformat()
        if u.get("day") != today:                 # ny dag hos Google: tælleren starter forfra
            u = {**u, "day": today, "requests": 0}
        return u

    def _unavailable(self, reason: str, detail: str = ""):
        """AI kan ikke bruges, uden at noget er sendt. Logges én gang pr. tilstand – ikke ved hver hentning."""
        u = self._usage()
        notice = f"{u['day']}|{reason}|{detail}"
        if u.get("notice") != notice:
            log.warning("AI ikke tilgængelig: %s%s", REASONS.get(reason, reason), f" ({detail})" if detail else "")
            u["notice"] = notice
            _write(self.usage_path, u)
        raise AIUnavailable(reason, detail)

    def _admit(self, ignore_pause: bool = False) -> None:
        now = self.clock()
        u = self._usage()
        until = u.get("backoff_until")
        if until and not ignore_pause and dt.datetime.fromisoformat(until) > now:
            if not u.get("pause_reason") and (u.get("last_error") or {}).get("reason") in ("ugyldigt_svar", "fejlede_tjek"):
                u.update(backoff_until=None, failures=0)   # pause fra en ældre version, der pausede al AI ved ugyldigt svar
                _write(self.usage_path, u)
            else:
                self._unavailable("pause", f"til {until}")
        if u.get("requests", 0) >= self.s.daily_cap:
            self._unavailable("dagsbudget", f"{u.get('requests')}/{self.s.daily_cap}")
        recent = [t for t in u.get("recent", []) if now.timestamp() - t < 60]
        if self.s.rpm > 0 and len(recent) >= self.s.rpm:
            wait = 60 - (now.timestamp() - min(recent)) + 0.5
            if wait > self.s.max_wait_seconds:
                self._unavailable("minutgraense", f"{len(recent)}/{self.s.rpm}")
            log.info("AI: venter %.0f s på minutgrænsen", wait)
            self.sleep(wait)

    def _count_request(self) -> None:
        now = self.clock()
        u = self._usage()
        u["requests"] = u.get("requests", 0) + 1
        u["recent"] = [t for t in u.get("recent", []) if now.timestamp() - t < 60] + [now.timestamp()]
        _write(self.usage_path, u)

    def _ok(self, ckey: str | None = None) -> None:
        u = self._usage()
        u.update(failures=0, backoff_until=None, pause_reason=None, notice=None,
                 last_ok=self.clock().isoformat(timespec="seconds"))
        if ckey:
            (u.get("invalid") or {}).pop(ckey, None)
        _write(self.usage_path, u)

    def _fail(self, reason: str, detail: str = "", wait: float | None = None, until: dt.datetime | None = None):
        """Udbyderen svarer ikke, som den skal: pause for al AI."""
        now = self.clock()
        u = self._usage()
        n = u.get("failures", 0) + 1
        if until is None:
            first, cap = BACKOFF.get(reason, (300, 3600))
            secs = min(cap, max(first * 2 ** (n - 1), wait or 0))
            until = now + dt.timedelta(seconds=secs)
        u.update(failures=n, backoff_until=until.isoformat(timespec="seconds"), pause_reason=reason, notice=None,
                 last_error={"reason": reason, "detail": detail, "at": now.isoformat(timespec="seconds")})
        _write(self.usage_path, u)
        log.warning("AI ikke tilgængelig: %s (%s) – pause til %s", REASONS.get(reason, reason), detail, u["backoff_until"])
        raise AIUnavailable(reason, detail)

    def _check_invalid(self, ckey: str) -> None:
        mark = (self._usage().get("invalid") or {}).get(ckey)
        if mark and dt.datetime.fromisoformat(mark["until"]) > self.clock():
            self._unavailable(mark.get("reason", "ugyldigt_svar"), f"samme data gav ugyldigt svar – prøver igen efter {mark['until']}")

    def _fail_invalid(self, ckey: str, detail: str, reason: str = "ugyldigt_svar"):
        """To ugyldige svar i træk: pause for netop dette indhold. Al anden AI kører videre."""
        now = self.clock()
        u = self._usage()
        marks = {k: v for k, v in (u.get("invalid") or {}).items()            # glem gamle mærker, så filen ikke vokser
                 if now - dt.datetime.fromisoformat(v["until"]) < dt.timedelta(days=1)}
        n = (marks.get(ckey) or {}).get("n", 0) + 1
        first, cap = INVALID_BACKOFF
        until = now + dt.timedelta(seconds=min(cap, first * 2 ** (n - 1)))
        marks[ckey] = {"n": n, "until": until.isoformat(timespec="seconds"), "reason": reason}
        u.update(invalid=marks, notice=None,
                 last_error={"reason": reason, "detail": detail, "at": now.isoformat(timespec="seconds")})
        _write(self.usage_path, u)
        saved = f" – svaret er gemt i {self.invalid_path}" if self.invalid_path else ""
        log.warning("AI ikke tilgængelig: %s (%s) – samme data prøves igen efter %s%s",
                    REASONS[reason], detail, u["invalid"][ckey]["until"], saved)
        raise AIUnavailable(reason, detail)

    def _save_invalid(self, data: dict, error: str) -> None:
        """Det rå svar, så man kan se, hvad modellen skrev. Kun det seneste; aldrig i loggen."""
        if not self.invalid_path:
            return
        text = raw_text(data)
        cand = ((data.get("candidates") or [{}])[0] if isinstance(data, dict) and data.get("candidates") else {})
        record = {"saved": self.clock().isoformat(timespec="seconds"), "provider": self.s.provider, "model": self.s.model,
                  "error": error, "finish": cand.get("finishReason") or (data or {}).get("stop_reason"),
                  "text": text[:INVALID_MAX_CHARS] if text is not None else None,
                  "truncated": bool(text and len(text) > INVALID_MAX_CHARS)}
        if text is None and len(json.dumps(data)) <= INVALID_MAX_CHARS:
            record["response"] = data             # ingen tekst (fx blokeret): udbyderens svar viser grunden
        try:
            _write(self.invalid_path, record)
        except OSError as e:
            log.warning("AI: kunne ikke gemme det ugyldige svar (%s)", type(e).__name__)

    def _http_error(self, resp: httpx.Response, body: dict):
        code = resp.status_code
        msg = _error_text(body)[:200]
        detail = f"HTTP {code}" + (f": {msg}" if msg else "")
        if code == 429:
            if _is_daily_quota(body):
                self._fail("kvote", detail, until=next_pacific_midnight(self.clock()))
            self._fail("kvote", detail, wait=_retry_seconds(resp, body))
        if code == 402:
            self._fail("betaling", detail)
        if code >= 500:                           # inkl. Claudes 529 "overloaded"
            self._fail("serverfejl", detail, wait=_retry_seconds(resp, body))
        self._fail("afvist", detail)              # 400 (fx FAILED_PRECONDITION), 401, 403, 404 (forkert model)

    # ----- cache
    def _cache_get(self, key: str) -> dict | None:
        e = (_read(self.cache_path).get("entries") or {}).get(key)
        if not e:
            return None
        saved = dt.datetime.fromisoformat(e["saved"])
        if self.clock() - saved > dt.timedelta(days=CACHE_MAX_DAYS):
            return None
        return json.loads(json.dumps(e["result"]))  # en kopi, så kalderen ikke ændrer i cachen

    def _cache_put(self, key: str, result: dict) -> None:
        now = self.clock()
        entries = _read(self.cache_path).get("entries") or {}
        entries[key] = {"saved": now.isoformat(timespec="seconds"), "result": result}
        fresh = {k: v for k, v in entries.items()
                 if now - dt.datetime.fromisoformat(v["saved"]) <= dt.timedelta(days=CACHE_MAX_DAYS)}
        newest = sorted(fresh.items(), key=lambda kv: kv[1]["saved"], reverse=True)[:CACHE_MAX_ENTRIES]
        _write(self.cache_path, {"v": 1, "entries": dict(newest)})
