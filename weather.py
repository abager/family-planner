"""Vejret hjemme fra MET Norway (yr.no, Locationforecast 2.0) – til overblikket og kioskskærmen.

- Ingen nøgle, men MET kræver en User-Agent med kontaktoplysninger (her et link til repoet; kan ændres med
  [weather] contact i config.toml). Uden den svarer MET 403.
- Fair brug efter MET's vilkår: hentes højst én gang i timen og aldrig før svarets `Expires`; næste gang med
  `If-Modified-Since` (304 = uændret). Efter en fejl ventes en halv time. Et svar op til 6 timer gammelt bruges,
  hvis MET er nede.
- Kun hjemmets placering, afrundet til ca. 1 km (2 decimaler), sendes til MET. Den gemmes i `home_location.json`,
  sættes med knappen "Brug min placering som hjem" i appen og står aldrig i family.json eller config.toml.
- Resultatet er GROFT med vilje (hele grader, regn i kategorier, del af dagen), så små ændringer i
  prognosen ikke laver et nyt AI-overblik hver time.
- Prognosen har time-for-time-data ca. 2½ døgn frem; derefter kun 6-timers-intervaller, som ikke bruges.
  Dage, hvor dagtimerne (kl. 7–19) ikke er dækket time for time, udelades.

Enheder fra MET: temperatur i °C, nedbør i mm for den kommende time, vind og vindstød i m/s, skydække i %.
Data: MET Norway, licens CC BY 4.0 – krediteres i appen.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger("familieplanner.weather")
TZ = ZoneInfo("Europe/Copenhagen")
UTC = dt.timezone.utc

URL = "https://api.met.no/weatherapi/locationforecast/2.0/complete"   # "complete" har vindstød, "compact" har ikke
SOURCE = "MET Norway"
CONTACT = "https://github.com/abager/family-planner"                    # standard-kontakt i User-Agent
HOME_FILE = "home_location.json"
CACHE_FILE = "weather_cache.json"
FETCH_EVERY = dt.timedelta(minutes=60)
WAIT_AFTER_ERROR = dt.timedelta(minutes=30)
MAX_AGE = dt.timedelta(hours=6)
DAY_START, DAY_END = 7, 19                     # dagtimerne, vejret gælder for (skole, fritid, hjem igen)
HOUR_START, HOUR_END = 6, 22                   # timerne, der kan foldes ud i appen (time for time)
# Emoji med \ufe0f, så Windows og Android tegner dem i farver og ikke som sort tekst
SUN, SUN_CLOUD, CLOUD, RAIN, SNOW, FROST, MOON = "\u2600\ufe0f", "\u26c5", "\u2601\ufe0f", "\U0001F327\ufe0f", "\U0001F328\ufe0f", "\u2744\ufe0f", "\U0001F319"
FAIR, FOG, THUNDER = "\U0001F324\ufe0f", "\U0001F32B\ufe0f", "\u26c8\ufe0f"

# Danmark med Bornholm og lidt luft. MET dækker hele verden, men "hjem" skal ligge i Danmark.
BOUNDS = {"lat": (54.4, 57.9), "lon": (7.9, 15.3)}
WEEKDAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]


# ---------------------------------------------------------------- hjemmets placering
def round_location(lat: float, lon: float) -> tuple[float, float]:
    """To decimaler ≈ 1 km nord-syd og ≈ 0,6 km øst-vest i Danmark."""
    return round(float(lat), 2), round(float(lon), 2)


def valid_location(lat, lon) -> bool:
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return BOUNDS["lat"][0] <= lat <= BOUNDS["lat"][1] and BOUNDS["lon"][0] <= lon <= BOUNDS["lon"][1]


def _write_private(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_home(state_dir: Path, lat: float, lon: float, now: dt.datetime) -> dict:
    """Gemmer hjemmet afrundet. Rejser ValueError, hvis placeringen ikke ligger i Danmark."""
    if not valid_location(lat, lon):
        raise ValueError("Placeringen ligger ikke i Danmark")
    rlat, rlon = round_location(lat, lon)
    home = {"lat": rlat, "lon": rlon, "set": now.isoformat(timespec="minutes")}
    _write_private(Path(state_dir) / HOME_FILE, home)
    (Path(state_dir) / CACHE_FILE).unlink(missing_ok=True)       # ny placering: hent vejret forfra
    return home


def load_home(state_dir: Path) -> dict | None:
    h = _read(Path(state_dir) / HOME_FILE)
    if valid_location(h.get("lat"), h.get("lon")):
        rlat, rlon = round_location(h["lat"], h["lon"])          # også hvis nogen har skrevet flere decimaler i filen
        return {**h, "lat": rlat, "lon": rlon}
    return None


# ---------------------------------------------------------------- hentning
class WeatherUnavailable(Exception):
    pass


def parse(doc: dict) -> list[dict]:
    """MET's GeoJSON → timer med temperatur (°C), regn (mm i timen), vind, vindstød, skydække (0–1) og MET's symbol.

    Kun tidspunkter med en `next_1_hours`-blok bruges – længere ude har MET kun 6-timers-intervaller."""
    rows = []
    for ts in (doc.get("properties") or {}).get("timeseries") or []:
        data = ts.get("data") or {}
        inst = (data.get("instant") or {}).get("details") or {}
        nxt = data.get("next_1_hours")
        if "time" not in ts or inst.get("air_temperature") is None or not nxt:
            continue
        cloud = inst.get("cloud_area_fraction")
        rows.append({"t": dt.datetime.fromisoformat(ts["time"].replace("Z", "+00:00")),
                     "temp": float(inst["air_temperature"]),
                     "rain": max(0.0, float((nxt.get("details") or {}).get("precipitation_amount") or 0.0)),
                     "wind": float(inst.get("wind_speed") or 0.0),
                     "gust": float(inst.get("wind_speed_of_gust") or inst.get("wind_speed") or 0.0),
                     "cloud": None if cloud is None else float(cloud) / 100,
                     "sym": (nxt.get("summary") or {}).get("symbol_code")})
    rows.sort(key=lambda r: r["t"])
    if not rows:
        raise WeatherUnavailable("ingen timer i svaret")
    return rows


def _detail(r: httpx.Response) -> str:
    """MET's egen forklaring på en afvisning, kort – og uden decimaltal, så koordinater aldrig ender i loggen."""
    try:
        j = r.json()
        msg = (j.get("description") or j.get("detail") or j.get("message") or j.get("title") or "") if isinstance(j, dict) else ""
    except ValueError:
        msg = re.sub(r"<[^>]+>", " ", r.text or "")
    msg = re.sub(r"-?\d+\.\d+", "…", " ".join(str(msg).split()))[:160]
    return f" ({msg})" if msg else ""


def user_agent(cfg: dict | None = None) -> str:
    contact = ((cfg or {}).get("weather", {}).get("contact") or CONTACT).strip()
    return f"Familieplan/1.0 {contact}"


def _http_date(t: dt.datetime) -> str:
    return t.astimezone(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")


def _parse_http_date(v: str | None) -> dt.datetime | None:
    if not v:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(v).astimezone(UTC)
    except (TypeError, ValueError, IndexError):
        return None


class Fetched:
    """Et svar fra MET: timer (None ved 304 = uændret) og cache-felterne, MET beder os respektere."""
    def __init__(self, rows, expires, last_modified):
        self.rows, self.expires, self.last_modified = rows, expires, last_modified


def fetch_raw(lat: float, lon: float, transport: httpx.BaseTransport | None = None, timeout: float = 20,
              cfg: dict | None = None, if_modified_since: str | None = None) -> Fetched:
    params = {"lat": f"{lat:.2f}", "lon": f"{lon:.2f}"}                 # højst 2 decimaler: privatliv og MET's cache
    headers = {"user-agent": user_agent(cfg)}
    if if_modified_since:
        headers["if-modified-since"] = if_modified_since
    try:
        with httpx.Client(transport=transport, timeout=timeout, headers=headers) as c:
            r = c.get(URL, params=params)
    except httpx.HTTPError as e:
        raise WeatherUnavailable(f"kunne ikke nå {SOURCE}: {type(e).__name__}") from e
    expires, lm = r.headers.get("expires"), r.headers.get("last-modified")
    if r.status_code == 304:
        return Fetched(None, expires, lm or if_modified_since)
    if r.status_code == 203:                                           # MET: denne version udfases snart
        log.warning("Vejr: %s melder, at API-versionen snart udfases – opdatér weather.py", SOURCE)
    elif r.status_code != 200:
        raise WeatherUnavailable(f"{SOURCE} svarede {r.status_code}{_detail(r)}")
    try:
        return Fetched(parse(r.json()), expires, lm)
    except (ValueError, KeyError, TypeError) as e:
        raise WeatherUnavailable(f"uventet svar fra {SOURCE}: {e}") from e


def fetch(lat: float, lon: float, transport: httpx.BaseTransport | None = None, timeout: float = 20,
          cfg: dict | None = None) -> list[dict]:
    """Frisk hentning uden cache (selvtesten)."""
    return fetch_raw(lat, lon, transport, timeout, cfg).rows


def _rows_from_cache(c: dict) -> list[dict]:
    return [{**h, "t": dt.datetime.fromisoformat(h["t"])} for h in c.get("hours") or []]


def hours(state_dir: Path, now: dt.datetime, transport: httpx.BaseTransport | None = None,
          cfg: dict | None = None) -> list[dict] | None:
    """Timer fra cache eller MET. None, hvis hjemmet ikke er sat, eller der intet brugbart vejr er."""
    home = load_home(state_dir)
    if not home:
        return None
    cpath = Path(state_dir) / CACHE_FILE
    c = _read(cpath)
    same_place = c.get("lat") == home["lat"] and c.get("lon") == home["lon"] and c.get("source") == SOURCE
    fetched = dt.datetime.fromisoformat(c["fetched"]) if same_place and c.get("fetched") else None
    failed = dt.datetime.fromisoformat(c["failed"]) if same_place and c.get("failed") else None
    expires = _parse_http_date(c.get("expires")) if same_place else None

    def cached():
        if fetched and now - fetched <= MAX_AGE and c.get("hours"):
            return _rows_from_cache(c)
        return None

    if fetched and now - fetched < FETCH_EVERY:
        return cached()
    if expires and now.astimezone(UTC) < expires and cached():
        return cached()                                                # MET: spørg ikke før Expires
    if failed and now - failed < WAIT_AFTER_ERROR:
        return cached()
    ims = c.get("last_modified") if same_place and c.get("hours") else None
    try:
        got = fetch_raw(home["lat"], home["lon"], transport, cfg=cfg, if_modified_since=ims)
    except WeatherUnavailable as e:
        log.warning("Vejret kunne ikke hentes: %s", e)
        _write_private(cpath, {**(c if same_place else {}), "source": SOURCE, "lat": home["lat"], "lon": home["lon"],
                               "failed": now.isoformat(timespec="seconds"), "error": str(e)[:200]})
        return cached()
    rows = got.rows if got.rows is not None else _rows_from_cache(c)   # 304: prognosen er uændret
    _write_private(cpath, {"source": SOURCE, "lat": home["lat"], "lon": home["lon"],
                           "fetched": now.isoformat(timespec="seconds"), "expires": got.expires,
                           "last_modified": got.last_modified,
                           "hours": [{**h, "t": h["t"].isoformat()} for h in rows]})
    return rows


# ---------------------------------------------------------------- groft dagsresumé og råd
def _part_of_day(h: int) -> str:
    return "om morgenen" if h < 10 else "om formiddagen" if h < 12 else "om eftermiddagen" if h < 17 else "om aftenen"


def _rain_class(mm: float) -> str:
    return "ingen" if mm < 0.5 else "lidt" if mm < 3 else "regn" if mm < 10 else "meget"


def advice(day: dict) -> list[str]:
    """Praktiske råd ud fra dagen. Bruges af reserven (uden AI) og vises på kiosken."""
    out = []
    if day["regn"] in ("regn", "meget"):
        out.append("regntøj og gummistøvler")
    elif day["regn"] == "lidt":
        out.append("regnjakke")
    if day["max"] <= 5 or day["frost"]:
        out.append("vanter og hue")
    if day["vind"] == "hård":
        out.append("hård vind – pas på på cyklen")
    if day["max"] >= 22 and day["himmel"] == "sol":
        out.append("solcreme og kasket")
    if day["max"] >= 25:
        out.append("ekstra drikkevand")
    return out


def summarize(rows: list[dict], now: dt.datetime) -> list[dict]:
    """Én post pr. dag, hvor MET dækker dagtimerne time for time. I dag tæller kun de timer, der er tilbage."""
    local = [{**r, "lt": r["t"].astimezone(TZ)} for r in rows]
    today = now.astimezone(TZ).date()
    days = []
    for date in sorted({r["lt"].date() for r in local}):
        if date < today:
            continue
        first = max(DAY_START, now.astimezone(TZ).hour) if date == today else DAY_START
        if first > DAY_END:
            continue                                                 # i dag er dagen forbi
        span = [r for r in local if r["lt"].date() == date and first <= r["lt"].hour <= DAY_END]
        if len({r["lt"].hour for r in span}) < (DAY_END - first + 1) - 1:   # mangler mere end én time: ikke dækket
            continue
        morning = [r for r in local if r["lt"].date() == date and r["lt"].hour <= 9]
        temps = [r["temp"] for r in span]
        rain = sum(r["rain"] for r in span)
        wet = next((r["lt"].hour for r in span if r["rain"] >= 0.2), None)
        clouds = [r["cloud"] for r in span if r["cloud"] is not None]
        cl = sum(clouds) / len(clouds) if clouds else 0.5
        wind = max(r["wind"] for r in span)
        gust = max(r["gust"] for r in span)
        d = {"dato": date.isoformat(), "ugedag": WEEKDAYS[date.weekday()],
             "min": round(min(temps)), "max": round(max(temps)),
             "regn": _rain_class(rain),
             "himmel": "sol" if cl < 0.3 else "skyet" if cl <= 0.7 else "overskyet",
             "vind": "hård" if wind >= 14 or gust >= 20 else "frisk" if wind >= 8 else None,
             "frost": any(r["temp"] <= 0 for r in (morning or span))}
        if d["regn"] != "ingen" and wet is not None:
            d["regn_hvornaar"] = _part_of_day(wet)
        d["tekst"] = short_text(d)
        d["raad"] = advice(d)
        days.append(d)
    return days


# ---------------------------------------------------------------- time for time
def sun_up(t: dt.datetime, lat: float, lon: float) -> bool:
    """Er solen over horisonten? NOAA's tilnærmelse – rigeligt præcis til at vælge sol eller måne."""
    u = t.astimezone(UTC)
    g = 2 * math.pi / 365 * (u.timetuple().tm_yday - 1 + (u.hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    minutes = u.hour * 60 + u.minute + eqtime + 4 * lon
    ha = math.radians(minutes / 4 - 180)
    la = math.radians(lat)
    elev = math.degrees(math.asin(math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)))
    return elev > -0.833


def symbol_icon(code: str | None) -> str | None:
    """MET's symbolkode (fx "lightrainshowers_day") → emoji. None, hvis koden er ukendt."""
    if not code:
        return None
    base, _, phase = code.partition("_")
    night = phase in ("night", "polartwilight")
    if "thunder" in base:
        return THUNDER
    if "snow" in base or "sleet" in base:
        return SNOW
    if "rain" in base:
        return RAIN
    return {"clearsky": MOON if night else SUN, "fair": MOON if night else FAIR,
            "partlycloudy": CLOUD if night else SUN_CLOUD, "cloudy": CLOUD, "fog": FOG}.get(base)


def hour_icon(h: dict, light: bool) -> str:
    sym = symbol_icon(h.get("sym"))
    if sym:
        return sym
    if h["rain"] >= 0.2:
        return SNOW if h["temp"] <= 0.5 else RAIN
    cl = h["cloud"] if h.get("cloud") is not None else 0.5
    if not light:
        return MOON if cl < 0.3 else CLOUD
    return SUN if cl < 0.3 else SUN_CLOUD if cl <= 0.7 else CLOUD


def hourly(rows: list[dict], date: str, lat: float, lon: float) -> list[dict]:
    """Timerne kl. 6–22 for en dag, til visningen i appen. Indgår IKKE i AI-overblikket (det bruger kun resuméet)."""
    out = []
    for r in rows:
        lt = r["t"].astimezone(TZ)
        if lt.date().isoformat() != date or not HOUR_START <= lt.hour <= HOUR_END:
            continue
        out.append({"kl": lt.hour, "ikon": hour_icon(r, sun_up(r["t"], lat, lon)), "temp": round(r["temp"]),
                    "regn": round(r["rain"], 1), "vind": round(r["wind"])})
    return out


def short_text(d: dict) -> str:
    parts = [f"{d['min']}–{d['max']}°" if d["min"] != d["max"] else f"{d['max']}°"]
    if d["regn"] != "ingen":
        word = {"lidt": "lidt regn", "regn": "regn", "meget": "meget regn"}[d["regn"]]
        parts.append(f"{word} {d.get('regn_hvornaar', '')}".strip())
    else:
        parts.append({"sol": "sol", "skyet": "skyet", "overskyet": "overskyet"}[d["himmel"]])
    if d["vind"]:
        parts.append(f"{d['vind']} vind")
    if d["frost"]:
        parts.append("frost om morgenen")
    return ", ".join(parts)


def icon(d: dict) -> str:
    if d["regn"] != "ingen":
        return RAIN
    if d["frost"] or d["max"] <= 2:
        return FROST
    return {"sol": SUN, "skyet": SUN_CLOUD, "overskyet": CLOUD}[d["himmel"]]


def for_family(cfg: dict, now: dt.datetime, transport: httpx.BaseTransport | None = None) -> dict | None:
    """Til family.json: groft dagsresumé og timerne – aldrig placeringen. None uden hjem eller vejr.

    Skriver altid én linje i loggen, så man kan se, om vejret er med – og hvorfor ikke (aldrig koordinaterne)."""
    if not enabled(cfg):
        log.info("Vejr: slået fra i config.toml ([weather] enabled = false)")
        return None
    state_dir = Path(cfg.get("output", "web/family.json")).parent
    home = load_home(state_dir)
    if not home:
        log.info('Vejr: hjemmets placering er ikke sat – åbn appen på pc\'en, der kører den '
                 '(normalt http://localhost:8080), og tryk på "Vejr: hjem"')
        return None
    try:
        rows = hours(state_dir, now, transport, cfg)
    except Exception as e:  # noqa: BLE001 – vejret er et ekstra og må aldrig vælte hentningen
        log.warning("Vejret sprunget over: %s", e)
        return None
    if not rows:
        log.info("Vejr: ingen brugbar prognose fra %s – overblikket er uden vejr", SOURCE)
        return None
    days = summarize(rows, now)
    if not days:
        log.info("Vejr: %s' prognose dækker ikke dagtimerne – overblikket er uden vejr", SOURCE)
        return None
    for d in days:
        d["ikon"] = icon(d)
        d["timer"] = hourly(rows, d["dato"], home["lat"], home["lon"])
    c = _read(state_dir / CACHE_FILE)
    fetched = c.get("fetched")
    at = dt.datetime.fromisoformat(fetched).astimezone(TZ).strftime("%H:%M") if fetched else "?"
    log.info("Vejr: %d dag%s fra %s (prognose hentet kl. %s)", len(days), "e" if len(days) != 1 else "", SOURCE, at)
    return {"kilde": SOURCE, "hentet": fetched, "dage": days}


def enabled(cfg: dict) -> bool:
    return bool(cfg.get("weather", {}).get("enabled", True))


def status(cfg: dict, state_dir: Path) -> dict:
    """Til /api/status: er vejret slået til, og er hjemmet sat? Kun ja/nej – aldrig placeringen."""
    return {"enabled": enabled(cfg), "home": load_home(state_dir) is not None}
