"""Vejret hjemme fra DMI (Forecast EDR API, vejrmodellen HARMONIE) – til overblikket og kioskskærmen.

- Ingen nøgle (DMI kræver ikke længere en siden 2. december 2025). Fair brug: hentes højst én gang i timen,
  og efter en fejl ventes en halv time. Et svar op til 6 timer gammelt bruges, hvis DMI er nede.
- Kun hjemmets placering, afrundet til ca. 1 km, sendes til DMI. Den gemmes i `home_location.json`, sættes
  med knappen "Brug min placering som hjem" i appen og står aldrig i family.json eller config.toml.
- Resultatet er GROFT med vilje (hele grader, regn i kategorier, del af dagen), så små ændringer i
  prognosen ikke laver et nyt AI-overblik hver time.
- HARMONIE rækker kun et par døgn frem. Dage, hvor dagtimerne (kl. 7–19) ikke er dækket, udelades.

Enheder fra DMI: temperatur i Kelvin, nedbør i kg/m² (= mm, summeret fra modellens start), vind i m/s,
skydække som andel 0–1.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger("familieplanner.weather")
TZ = ZoneInfo("Europe/Copenhagen")
UTC = dt.timezone.utc

URL = "https://opendataapi.dmi.dk/v1/forecastedr/collections/harmonie_dini_sf/position"
PARAMS = ["temperature-2m", "total-precipitation", "wind-speed-10m", "gust-wind-speed-10m", "cloudcover"]
HOME_FILE = "home_location.json"
CACHE_FILE = "weather_cache.json"
FETCH_EVERY = dt.timedelta(minutes=60)
WAIT_AFTER_ERROR = dt.timedelta(minutes=30)
MAX_AGE = dt.timedelta(hours=6)
DAY_START, DAY_END = 7, 19                     # dagtimerne, vejret gælder for (skole, fritid, hjem igen)

# Danmark med Bornholm og lidt luft. HARMONIE DINI dækker mere, men "hjem" skal ligge i Danmark.
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


def parse(geojson: dict) -> list[dict]:
    """DMI's GeoJSON → timer med temperatur (°C), regn (mm i timen), vind, vindstød og skydække."""
    rows = []
    for f in geojson.get("features") or []:
        p = f.get("properties") or {}
        if "step" not in p or p.get("temperature-2m") is None:
            continue
        rows.append({"t": dt.datetime.fromisoformat(p["step"].replace("Z", "+00:00")),
                     "temp": p["temperature-2m"] - 273.15, "acc": p.get("total-precipitation"),
                     "wind": p.get("wind-speed-10m") or 0.0, "gust": p.get("gust-wind-speed-10m") or 0.0,
                     "cloud": p.get("cloudcover")})
    rows.sort(key=lambda r: r["t"])
    if not rows:
        raise WeatherUnavailable("ingen timer i svaret")
    acc = [r["acc"] or 0.0 for r in rows]
    # total-precipitation er summeret fra modellens start; falder tallet nogensinde, er det i stedet pr. time
    accumulated = all(b >= a - 0.01 for a, b in zip(acc, acc[1:]))
    for i, r in enumerate(rows):
        r["rain"] = max(0.0, acc[i] - acc[i - 1]) if accumulated and i else (0.0 if accumulated else max(0.0, acc[i]))
        del r["acc"]
    return rows


def fetch(lat: float, lon: float, transport: httpx.BaseTransport | None = None, timeout: float = 20) -> list[dict]:
    params = {"coords": f"POINT({lon} {lat})", "crs": "crs84", "parameter-name": ",".join(PARAMS), "f": "GeoJSON"}
    try:
        with httpx.Client(transport=transport, timeout=timeout, headers={"user-agent": "Familieplan (privat familieapp)"}) as c:
            r = c.get(URL, params=params)
    except httpx.HTTPError as e:
        raise WeatherUnavailable(f"kunne ikke nå DMI: {type(e).__name__}") from e
    if r.status_code != 200:
        raise WeatherUnavailable(f"DMI svarede {r.status_code}")
    try:
        return parse(r.json())
    except (ValueError, KeyError, TypeError) as e:
        raise WeatherUnavailable(f"uventet svar fra DMI: {e}") from e


def hours(state_dir: Path, now: dt.datetime, transport: httpx.BaseTransport | None = None) -> list[dict] | None:
    """Timer fra cache eller DMI. None, hvis hjemmet ikke er sat, eller der intet brugbart vejr er."""
    home = load_home(state_dir)
    if not home:
        return None
    cpath = Path(state_dir) / CACHE_FILE
    c = _read(cpath)
    same_place = c.get("lat") == home["lat"] and c.get("lon") == home["lon"]
    fetched = dt.datetime.fromisoformat(c["fetched"]) if same_place and c.get("fetched") else None
    failed = dt.datetime.fromisoformat(c["failed"]) if same_place and c.get("failed") else None

    def cached():
        if fetched and now - fetched <= MAX_AGE and c.get("hours"):
            return [{**h, "t": dt.datetime.fromisoformat(h["t"])} for h in c["hours"]]
        return None

    if fetched and now - fetched < FETCH_EVERY:
        return cached()
    if failed and now - failed < WAIT_AFTER_ERROR:
        return cached()
    try:
        rows = fetch(home["lat"], home["lon"], transport)
    except WeatherUnavailable as e:
        log.warning("Vejret kunne ikke hentes: %s", e)
        _write_private(cpath, {**(c if same_place else {}), "lat": home["lat"], "lon": home["lon"],
                               "failed": now.isoformat(timespec="seconds"), "error": str(e)[:200]})
        return cached()
    _write_private(cpath, {"lat": home["lat"], "lon": home["lon"], "fetched": now.isoformat(timespec="seconds"),
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
    """Én post pr. dag, hvor DMI dækker dagtimerne. I dag tæller kun de timer, der er tilbage."""
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
        return "🌧"
    if d["frost"] or d["max"] <= 2:
        return "❄"
    return {"sol": "☀", "skyet": "⛅", "overskyet": "☁"}[d["himmel"]]


def for_family(cfg: dict, now: dt.datetime, transport: httpx.BaseTransport | None = None) -> dict | None:
    """Til family.json: kun det grove dagsresumé – aldrig placeringen. None uden hjem eller vejr."""
    if not cfg.get("weather", {}).get("enabled", True):
        return None
    state_dir = Path(cfg.get("output", "web/family.json")).parent
    try:
        rows = hours(state_dir, now, transport)
    except Exception as e:  # noqa: BLE001 – vejret er et ekstra og må aldrig vælte hentningen
        log.warning("Vejret sprunget over: %s", e)
        return None
    if not rows:
        return None
    days = summarize(rows, now)
    for d in days:
        d["ikon"] = icon(d)
    c = _read(state_dir / CACHE_FILE)
    return {"kilde": "DMI", "hentet": c.get("fetched"), "dage": days} if days else None
