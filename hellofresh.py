"""HelloFresh integration – ugens meny til familieoverblikket.

Bruger HelloFresh's offentlige API (gw.hellofresh.com) med published credentials.
Ingen bruger-tokens, ingen kompleksitet – bare APIet som det skal bruges.
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

CACHE_FILE = "hellofresh_cache.json"
TOKEN_CACHE_FILE = "hellofresh_token_cache.json"
FETCH_EVERY = dt.timedelta(days=7)  # Hent kun én gang om ugen
MAX_CACHE_AGE = dt.timedelta(days=8)

# HelloFresh offentlige credentials (fra deres API docs)
HF_CLIENT_ID = "hellofresh-dev-test"
HF_CLIENT_SECRET = "g4c25EzG4#%Afeh07Bb#anbH5BQQ67bJ7!G6QZOA"
HF_API_BASE = "https://gw.hellofresh.com/api"


class HelloFreshUnavailable(Exception):
    pass


def _write_json(path: Path, data: dict) -> None:
    """Skriv JSON-fil."""
    tmp = path.with_suffix(".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict:
    """Læs JSON-fil, returner tom dict hvis den ikke findes."""
    try:
        d = json.loads(path.read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _get_access_token(state_dir: Path) -> str:
    """Få access token fra HelloFresh API (caches i 30 minutter)."""
    cache_path = Path(state_dir) / TOKEN_CACHE_FILE
    cache = _read_json(cache_path)
    
    # Check hvis token er frisk
    if cache.get("access_token") and cache.get("expires_at"):
        expires_at = dt.datetime.fromisoformat(cache["expires_at"])
        if dt.datetime.now(TZ) < expires_at:
            log.debug("HelloFresh token fra cache")
            return cache["access_token"]
    
    # Hent nyt token
    try:
        import requests
        r = requests.post(
            "https://gw.hellofresh.com/auth/token",
            json={
                "client_id": HF_CLIENT_ID,
                "client_secret": HF_CLIENT_SECRET,
                "grant_type": "client_credentials",
                "scope": "public"
            },
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        
        token = data.get("access_token")
        expires_in = data.get("expires_in", 1800)  # Default 30 min
        
        # Gem token med expiry
        expires_at = dt.datetime.now(TZ) + dt.timedelta(seconds=expires_in)
        _write_json(cache_path, {
            "access_token": token,
            "expires_at": expires_at.isoformat(),
            "fetched": dt.datetime.now(TZ).isoformat()
        })
        
        log.info("HelloFresh access token hentet (udløber om %d min)", expires_in // 60)
        return token
    
    except Exception as e:
        raise HelloFreshUnavailable(f"Kunne ikke hente HelloFresh token: {e}") from e


def _fetch_recipes(token: str, country: str = "DK") -> list:
    """Hent recepter fra HelloFresh API."""
    try:
        import requests
    except ImportError:
        raise HelloFreshUnavailable("requests biblioteket er ikke installeret") from None
    
    try:
        headers = {"Authorization": f"Bearer {token}"}
        
        # Hent recepter – tag de 3 første (ugens menu)
        r = requests.get(
            f"{HF_API_BASE}/recipes/search",
            params={
                "country": country.lower(),
                "locale": "da-DK",
                "limit": 3,
                "order": "-date"
            },
            headers=headers,
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        
        recipes = data.get("hits", [])
        if not recipes:
            raise HelloFreshUnavailable("Ingen recepter returneret")
        
        log.info("HelloFresh recepter hentet (%d stk)", len(recipes))
        return recipes
    
    except requests.exceptions.RequestException as e:
        raise HelloFreshUnavailable(f"HelloFresh API fejl: {e}") from e
    except Exception as e:
        raise HelloFreshUnavailable(f"Fejl ved hentning af recepter: {e}") from e


def _format_recipe(recipe: dict) -> dict:
    """Formatter HelloFresh recept til family.json format."""
    return {
        "id": recipe.get("id", "unknown"),
        "titel": recipe.get("title", recipe.get("name", "Ukendt måltid")),
        "billede": recipe.get("image_url", recipe.get("image", "")),
        "servinger": recipe.get("servings", 2),
        "tid_minutter": (recipe.get("prep_time", 0) or 0) + (recipe.get("cook_time", 0) or 0),
        "ingredienser": [
            {"navn": ing.get("name", ""), "mængde": ing.get("amount", ""), "enhed": ing.get("unit", "")}
            for ing in recipe.get("ingredients", [])
        ],
        "trin": [s.get("instruction", "") if isinstance(s, dict) else str(s) for s in recipe.get("steps", [])],
    }


def fetch_menu(state_dir: Path) -> dict | None:
    """Henter ugens menu fra cache eller HelloFresh API."""
    state_path = Path(state_dir)
    cache_path = state_path / CACHE_FILE
    
    # Læs eksisterende cache
    cache = _read_json(cache_path)
    fetched = dt.datetime.fromisoformat(cache["fetched"]) if cache.get("fetched") else None
    
    # Hvis cache er frisk nok: brug den
    if fetched and dt.datetime.now(TZ) - fetched < FETCH_EVERY and cache.get("recipes"):
        age_hours = (dt.datetime.now(TZ) - fetched).total_seconds() / 3600
        log.debug("HelloFresh menu fra cache (%.1f timer siden)", age_hours)
        return cache
    
    # Prøv at hente nyt
    try:
        token = _get_access_token(state_dir)
        recipes = _fetch_recipes(token)
        
        if recipes:
            new_data = {"recipes": recipes, "fetched": dt.datetime.now(TZ).isoformat()}
            _write_json(cache_path, new_data)
            return new_data
    
    except HelloFreshUnavailable as e:
        log.warning("HelloFresh fetch fejlede: %s", e)
    
    # Fallback: brug cache hvis det ikke er for gammelt
    if cache.get("recipes"):
        cache_age = dt.datetime.now(TZ) - fetched if fetched else None
        if cache_age and cache_age < MAX_CACHE_AGE:
            age_hours = cache_age.total_seconds() / 3600
            log.info("HelloFresh menu fra cache (%.1f timer gammel – fetch fejlede)", age_hours)
            return cache
    
    log.info("HelloFresh: ingen brugbar menu")
    return None


def for_family(cfg: dict, now: dt.datetime) -> dict | None:
    """Til family.json: denne uges HelloFresh meny."""
    if not enabled(cfg):
        log.debug("HelloFresh: slået fra i config.toml")
        return None
    
    state_dir = Path(cfg.get("output", "web/family.json")).parent
    
    try:
        data = fetch_menu(state_dir)
        if not data:
            return None
        
        recipes = data.get("recipes", [])[:3]  # Tag kun de første 3
        meals = [_format_recipe(r) for r in recipes]
        
        return {"kilde": "HelloFresh", "måltider": meals}
    
    except Exception as e:  # noqa: BLE001
        log.warning("HelloFresh sprunget over: %s", e)
        return None


def enabled(cfg: dict) -> bool:
    """Er HelloFresh slået til i config?"""
    return bool(cfg.get("hellofresh", {}).get("enabled", True))


def status(cfg: dict, state_dir: Path) -> dict:
    """Til /api/status: HelloFresh status."""
    return {"enabled": enabled(cfg), "token_set": True}  # Token sættes automatisk
