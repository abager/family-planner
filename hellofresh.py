"""HelloFresh integration – ugens meny til familieoverblikket.

- Token gemmes sikkert i secrets/hellofresh_token.json (read-only fil)
- Menu cachas i hellofresh_cache.json – opdateres én gang om ugen (tirsdag når ny menu kommer)
- Viser 3 måltider + billeder + ingredienser + opskrift
- Degrade gracefully: ingen menu = ingen HelloFresh i appen (aldrig fejl)
- Bruger synkrone HTTP requests (requests bibliotek) – ingen async kompleksitet
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("familieplanner.hellofresh")
TZ = ZoneInfo("Europe/Copenhagen")

TOKEN_FILE = "secrets/hellofresh_token.json"
CACHE_FILE = "hellofresh_cache.json"
FETCH_EVERY = dt.timedelta(days=7)  # Hent kun én gang om ugen
MAX_CACHE_AGE = dt.timedelta(days=8)  # Brug cache max 8 dage (hvis fetch fejler)


class HelloFreshUnavailable(Exception):
    pass


def _write_secure(path: Path, data: dict) -> None:
    """Skriv JSON-fil med begrænsede rettigheder (secrets)."""
    tmp = path.with_suffix(".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _read(path: Path) -> dict:
    """Læs JSON-fil, returner tom dict hvis den ikke findes."""
    try:
        d = json.loads(path.read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_token(state_dir: Path, token: str) -> None:
    """Gemmer HelloFresh bearer token sikkert."""
    if not token or not isinstance(token, str):
        raise ValueError("Token må ikke være tomt")
    _write_secure(Path(state_dir) / TOKEN_FILE, {"token": token, "saved": dt.datetime.now(TZ).isoformat()})
    log.info("HelloFresh token gemt")


def load_token(state_dir: Path) -> str | None:
    """Henter HelloFresh token. None hvis ikke sat."""
    data = _read(Path(state_dir) / TOKEN_FILE)
    return data.get("token") if data else None


def _fetch_menu_sync(token: str, country: str = "DK") -> dict:
    """Henter ugens meny fra HelloFresh API (synkront via requests)."""
    try:
        import requests
    except ImportError:
        raise HelloFreshUnavailable("requests biblioteket er ikke installeret") from None

    try:
        # HelloFresh API base URL
        base_url = "https://www.hellofresh.com/api/v1"
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "familieplan/1.0",
            "Accept": "application/json",
        }

        # Hent denne uges meny
        r = requests.get(f"{base_url}/recurring_plan?country={country}", headers=headers, timeout=10)
        r.raise_for_status()
        menu_data = r.json()

        if not menu_data or not menu_data.get("recipes"):
            raise HelloFreshUnavailable("Ingen menu returneret fra HelloFresh")

        # Formater recepter (tag fuld data fra menu response)
        recipes_data = []
        for recipe in menu_data.get("recipes", [])[:3]:  # Tag kun de første 3
            recipes_data.append(recipe)

        return {"recipes": recipes_data, "fetched": dt.datetime.now(TZ).isoformat()}

    except HelloFreshUnavailable:
        raise
    except requests.exceptions.RequestException as e:
        raise HelloFreshUnavailable(f"HelloFresh API fejl: {type(e).__name__}: {e}") from e
    except Exception as e:
        raise HelloFreshUnavailable(f"HelloFresh fejl: {type(e).__name__}: {e}") from e


def fetch_menu(state_dir: Path) -> dict | None:
    """Henter ugens menu fra cache eller HelloFresh API (synkront)."""
    state_path = Path(state_dir)
    cache_path = state_path / CACHE_FILE

    # Læs eksisterende cache
    cache = _read(cache_path)
    fetched = dt.datetime.fromisoformat(cache["fetched"]) if cache.get("fetched") else None

    # Hvis cache er frisk nok: brug den
    if fetched and dt.datetime.now(TZ) - fetched < FETCH_EVERY and cache.get("recipes"):
        log.debug("HelloFresh menu fra cache (%.1f timer siden)", (dt.datetime.now(TZ) - fetched).total_seconds() / 3600)
        return [_format_recipe(r) for r in cache.get("recipes", [])]

    token = load_token(state_dir)
    if not token:
        log.debug("HelloFresh: ingen token sat")
        return None

    # Prøv at hente nyt
    try:
        new_data = _fetch_menu_sync(token)
        if new_data and new_data.get("recipes"):
            # Gem nyt i cache
            _write_secure(cache_path, new_data)
            log.info("HelloFresh menu hentet og cachet (%d måltider)", len(new_data["recipes"]))
            return [_format_recipe(r) for r in new_data.get("recipes", [])]
    except HelloFreshUnavailable as e:
        log.warning("HelloFresh fetch fejlede: %s", e)

    # Fallback: brug cache hvis det ikke er for gammelt
    if cache.get("recipes"):
        cache_age = dt.datetime.now(TZ) - fetched if fetched else None
        if cache_age and cache_age < MAX_CACHE_AGE:
            log.info("HelloFresh menu fra cache (%.1f timer gamle – fetch fejlede)", cache_age.total_seconds() / 3600)
            return [_format_recipe(r) for r in cache.get("recipes", [])]
        else:
            age_days = cache_age.total_seconds() / 86400 if cache_age else 999
            log.warning("HelloFresh cache er for gamle (%.1f dage) – ingen menu", age_days)

    log.info("HelloFresh: ingen brugbar menu")
    return None


def _format_recipe(recipe: dict) -> dict:
    """Formatter en HelloFresh opskrift til familie.json format."""
    return {
        "id": recipe.get("id", "unknown"),
        "titel": recipe.get("title") or recipe.get("name", "Ukendt måltid"),
        "billede": recipe.get("image_url") or recipe.get("image", ""),
        "servinger": recipe.get("servings", 2),
        "tid_minutter": (recipe.get("prep_time", 0) or 0) + (recipe.get("cook_time", 0) or 0),
        "ingredienser": [
            {"navn": ing.get("name", ""), "mængde": ing.get("amount", ""), "enhed": ing.get("unit", "")}
            for ing in recipe.get("ingredients", [])
        ],
        "trin": [s.get("instruction", "") if isinstance(s, dict) else str(s) for s in recipe.get("steps", [])],
        "ernæring": recipe.get("nutrition", {}),
    }


def for_family(cfg: dict, now: dt.datetime) -> dict | None:
    """Til family.json: denne uges HelloFresh meny. None hvis disabled/ingen token/fejl."""
    if not enabled(cfg):
        log.debug("HelloFresh: slået fra i config.toml")
        return None

    state_dir = Path(cfg.get("output", "web/family.json")).parent

    try:
        recipes = fetch_menu(state_dir)
    except Exception as e:  # noqa: BLE001
        log.warning("HelloFresh sprunget over: %s", e)
        return None

    if not recipes:
        return None

    # Tag kun de første 3 måltider
    meals = recipes[:3]
    return {"kilde": "HelloFresh", "måltider": meals}


def enabled(cfg: dict) -> bool:
    """Er HelloFresh slået til i config?"""
    return bool(cfg.get("hellofresh", {}).get("enabled", True))


def status(cfg: dict, state_dir: Path) -> dict:
    """Til /api/status: er HelloFresh slået til, og er token sat?"""
    return {"enabled": enabled(cfg), "token_set": load_token(state_dir) is not None}
