"""Sprogmodel bag én grænseflade: Google Gemini (standard) eller Claude – valgt i config.toml under [ai].

Alt, der kalder en sprogmodel, går herigennem, så de samme regler gælder overalt:

- Svaret skal være JSON. Ugyldigt svar = fejl (kalderen falder tilbage til appens egne regler).
- Samme indhold koster kun én forespørgsel: svar gemmes i `ai_cache.json` under en hash af indholdet.
- Dagsbudget og minutgrænse i `ai_usage.json`. Dagen følger Stillehavstid (America/Los_Angeles), fordi det er
  dér, Googles gratis-kvote nulstilles (kl. 9 dansk tid, 8 når tidsforskellen er en anden ved sommertidsskift).
- 429/402/401/403/5xx/timeout/ugyldigt svar giver en pause (backoff), så en fejl ikke brænder kvoten af.
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
}

# Pause efter fejl: (første pause i sekunder, højeste pause). Fordobles for hver fejl i træk.
BACKOFF = {
    "kvote": (60, 6 * 3600),
    "betaling": (6 * 3600, 6 * 3600),
    "afvist": (30 * 60, 12 * 3600),
    "serverfejl": (120, 3600),
    "netvaerk": (120, 3600),
    "ugyldigt_svar": (15 * 60, 6 * 3600),
}


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
    if asst.get("mode") == "claude" and "provider" not in a:
        a["provider"] = "claude"
        for k in ("model", "api_key_env", "max_output_tokens"):
            if k in asst and k not in a:
                a[k] = asst[k]
        if "max_tokens" in asst and "max_output_tokens" not in a:
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
    def request(s: Settings, key: str, system: str, prompt: str) -> tuple[str, dict, dict]:
        base = (s.base_url or Gemini.url).rstrip("/")
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": s.temperature,
                                 "maxOutputTokens": s.max_output_tokens},
        }
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
    def request(s: Settings, key: str, system: str, prompt: str) -> tuple[str, dict, dict]:
        base = (s.base_url or Claude.url).rstrip("/")
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
                 env: dict | None = None):
        self.s = settings
        self.state_dir = Path(state_dir)
        self.cache_path = self.state_dir / CACHE_FILE
        self.usage_path = self.state_dir / USAGE_FILE
        self.transport = transport
        self.clock = clock or (lambda: dt.datetime.now(UTC))
        self.sleep = sleep or time.sleep
        self.env = os.environ if env is None else env

    # ----- offentligt
    def generate_json(self, system: str, prompt: str, *, validate: Callable[[dict], None] | None = None,
                      cache_key: str | None = None, ignore_pause: bool = False) -> dict:
        """Svaret som dict. Rejser AIUnavailable, hvis der ikke kan svares (kalderen bruger så sin reserve).
        ignore_pause: kun til selvtesten – prøv selvom en tidligere fejl har sat en pause (budgettet gælder stadig)."""
        provider = PROVIDERS.get(self.s.provider)
        if provider is None:
            raise AIUnavailable("ukendt_udbyder", self.s.provider)
        key = (self.env.get(self.s.api_key_env) or "").strip().strip("\"'")
        if not key:
            raise AIUnavailable("mangler_noegle", self.s.api_key_env)
        if not key.isascii() or any(ch.isspace() for ch in key):
            raise AIUnavailable("mangler_noegle", f"{self.s.api_key_env} indeholder ugyldige tegn (mellemrum, æøå …)")

        ckey = content_key(self.s.provider, self.s.model, system, cache_key or prompt)
        hit = None if ignore_pause else self._cache_get(ckey)
        if hit is not None:
            log.info("AI: svar fra cache")
            return hit

        self._admit(ignore_pause)                 # budget, pause og minutgrænse – rejser AIUnavailable
        url, headers, body = provider.request(self.s, key, system, prompt)
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
        if resp.status_code >= 400:
            self._http_error(resp, data if isinstance(data, dict) else {})

        try:
            result = parse_json(provider.text(data))
            if validate:
                validate(result)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            self._fail("ugyldigt_svar", str(e)[:200])

        self._ok()
        if not ignore_pause:                      # selvtestens svar skal ikke fylde i cachen
            self._cache_put(ckey, result)
        return result

    def status(self) -> dict:
        """Til /api/status og log. Ingen nøgler, intet indhold."""
        u = self._usage()
        return {"provider": self.s.provider, "model": self.s.model, "day": u.get("day"),
                "requests_today": u.get("requests", 0), "daily_cap": self.s.daily_cap,
                "backoff_until": u.get("backoff_until"), "last_error": u.get("last_error"), "last_ok": u.get("last_ok")}

    # ----- budget og pause
    def _usage(self) -> dict:
        u = _read(self.usage_path)
        today = self.clock().astimezone(PACIFIC).date().isoformat()
        if u.get("day") != today:                 # ny dag hos Google: tælleren starter forfra
            u = {**u, "day": today, "requests": 0}
        return u

    def _admit(self, ignore_pause: bool = False) -> None:
        now = self.clock()
        u = self._usage()
        until = u.get("backoff_until")
        if until and not ignore_pause and dt.datetime.fromisoformat(until) > now:
            raise AIUnavailable("pause", f"til {until}")
        if u.get("requests", 0) >= self.s.daily_cap:
            raise AIUnavailable("dagsbudget", f"{u.get('requests')}/{self.s.daily_cap}")
        recent = [t for t in u.get("recent", []) if now.timestamp() - t < 60]
        if self.s.rpm > 0 and len(recent) >= self.s.rpm:
            wait = 60 - (now.timestamp() - min(recent)) + 0.5
            if wait > self.s.max_wait_seconds:
                raise AIUnavailable("minutgraense", f"{len(recent)}/{self.s.rpm}")
            log.info("AI: venter %.0f s på minutgrænsen", wait)
            self.sleep(wait)

    def _count_request(self) -> None:
        now = self.clock()
        u = self._usage()
        u["requests"] = u.get("requests", 0) + 1
        u["recent"] = [t for t in u.get("recent", []) if now.timestamp() - t < 60] + [now.timestamp()]
        _write(self.usage_path, u)

    def _ok(self) -> None:
        u = self._usage()
        u.update(failures=0, backoff_until=None, last_ok=self.clock().isoformat(timespec="seconds"))
        _write(self.usage_path, u)

    def _fail(self, reason: str, detail: str = "", wait: float | None = None, until: dt.datetime | None = None):
        now = self.clock()
        u = self._usage()
        n = u.get("failures", 0) + 1
        if until is None:
            first, cap = BACKOFF.get(reason, (300, 3600))
            secs = min(cap, max(first * 2 ** (n - 1), wait or 0))
            until = now + dt.timedelta(seconds=secs)
        u.update(failures=n, backoff_until=until.isoformat(timespec="seconds"),
                 last_error={"reason": reason, "detail": detail, "at": now.isoformat(timespec="seconds")})
        _write(self.usage_path, u)
        log.warning("AI ikke tilgængelig: %s (%s) – pause til %s", REASONS.get(reason, reason), detail, u["backoff_until"])
        raise AIUnavailable(reason, detail)

    def _http_error(self, resp: httpx.Response, body: dict):
        code = resp.status_code
        msg = str((body.get("error") or {}).get("message") or (body.get("error") or {}).get("type") or "")[:200]
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
