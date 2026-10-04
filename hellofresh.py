"""HelloFresh integrasjon – ugens meny til familieoverblikket.

- Token gemmes sikkert i secrets/hellofresh_token.json (read-only fil)
- Menu cachas i hellofresh_cache.json – opdateres én gang om ugen (tirsdag når ny menu kommer)
- Viser 3 måltider + billeder + ingredienser + opskrift
- Degrade gracefully: ingen menu = ingen HelloFresh i appen (aldrig fejl)
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

# Tillad nested event loops (FastAPI serveren kører allerede en loop)
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

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


async def start_passwordless_login_async(email: str, country: str = "DK", locale: str = "da-DK") -> dict:
    """Start passwordless login – sender magic link til email."""
    try:
        from pyhellofresh import HelloFreshClient
    except ImportError:
        raise HelloFreshUnavailable("pyhellofresh biblioteket er ikke installeret") from None

    try:
        client = HelloFreshClient(country=country, locale=locale)
        result = await client.start_passwordless_login(email)
        log.info("Passwordless login startet for %s", email)
        return result
    except Exception as e:
        raise HelloFreshUnavailable(f"Kunne ikke starte passwordless login: {type(e).__name__}: {e}") from e


def start_passwordless_login(email: str) -> dict:
    """Synkron wrapper omkring async passwordless login start (brug eksisterende event loop)."""
    try:
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(start_passwordless_login_async(email))
        return result
    except HelloFreshUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        raise HelloFreshUnavailable(f"Passwordless login fejlede: {e}") from e


def load_token(state_dir: Path) -> str | None:
    """Henter HelloFresh token. None hvis ikke sat."""
    data = _read(Path(state_dir) / TOKEN_FILE)
    return data.get("token") if data else None


async def _fetch_menu_async(token: str, country: str = "DK", locale: str = "da-DK") -> dict:
    """Henter ugens meny fra HelloFresh API (async)."""
    try:
        from pyhellofresh import HelloFreshClient
    except ImportError:
        raise HelloFreshUnavailable("pyhellofresh biblioteket er ikke installeret") from None

    try:
        client = HelloFreshClient(access_token=token, country=country, locale=locale)

        # Hent denne uges meny
        menu = await client.get_menu()
        if not menu or not menu.get("recipes"):
            raise HelloFreshUnavailable("Ingen menu returneret fra HelloFresh")

        # Hent fuld opskrift data for hver recipe
        recipes_data = []
        for recipe in menu["recipes"]:
            recipe_id = recipe.get("id")
            if recipe_id:
                try:
                    full_recipe = await client.get_recipe(recipe_id)
                    recipes_data.append(full_recipe)
                except Exception as e:
                    log.warning("Kunne ikke hente opskrift %s: %s", recipe_id, e)
                    recipes_data.append(recipe)

        return {"recipes": recipes_data, "fetched": dt.datetime.now(TZ).isoformat()}

    except HelloFreshUnavailable:
        raise
    except Exception as e:
        raise HelloFreshUnavailable(f"HelloFresh API fejl: {type(e).__name__}: {e}") from e


def fetch_menu(state_dir: Path) -> dict | None:
    """Synkron wrapper omkring async fetch (brug eksisterende event loop)."""
    token = load_token(state_dir)
    if not token:
        return None

    try:
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(_fetch_menu_async(token))
        return result
    except HelloFreshUnavailable as e:
        log.warning("HelloFresh menu kunne ikke hentes: %s", e)
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("HelloFresh fetch kritisk fejl: %s", e)
        return None


def _format_recipe(recipe: dict) -> dict:
    """Formatter en HelloFresh opskrift til familie.json format."""
    return {
        "id": recipe.get("id", "unknown"),
        "titel": recipe.get("title", "Ukendt måltid"),
        "billede": recipe.get("image", ""),
        "servinger": recipe.get("servings", 2),
        "tid_minutter": recipe.get("prepTime", 0) + recipe.get("cookTime", 0),
        "ingredienser": [
            {"navn": ing.get("name", ""), "mængde": ing.get("quantity", ""), "enhed": ing.get("unit", "")}
            for ing in recipe.get("ingredients", [])
        ],
        "trin": recipe.get("instructions", []),
        "ernæring": recipe.get("nutrition", {}),
    }


def get_menu(state_dir: Path, now: dt.datetime) -> list[dict] | None:
    """Henter ugens menu fra cache eller HelloFresh API.

    Cachebehaviour:
    - Hvis cache er mindre end FETCH_EVERY siden hentning: brug cache
    - Ellers hent nyt fra API
    - Hvis API fejler og cache er mindre end MAX_CACHE_AGE: brug cache
    - None hvis cache er tom eller for gammel
    """
    state_path = Path(state_dir)
    cache_path = state_path / CACHE_FILE

    # Læs eksisterende cache
    cache = _read(cache_path)
    fetched = dt.datetime.fromisoformat(cache["fetched"]) if cache.get("fetched") else None

    # Hvis cache er frisk nok: brug den
    if fetched and now - fetched < FETCH_EVERY and cache.get("recipes"):
        log.debug("HelloFresh menu fra cache (%.1f timer siden)", (now - fetched).total_seconds() / 3600)
        return [_format_recipe(r) for r in cache.get("recipes", [])]

    # Prøv at hente nyt
    try:
        new_data = fetch_menu(state_dir)
        if new_data and new_data.get("recipes"):
            # Gem nyt i cache
            _write_secure(cache_path, new_data)
            log.info("HelloFresh menu hentet og cachet (%d måltider)", len(new_data["recipes"]))
            return [_format_recipe(r) for r in new_data.get("recipes", [])]
    except Exception as e:
        log.warning("HelloFresh fetch fejlede: %s", e)

    # Fallback: brug cache hvis det ikke er for gammelt
    if cache.get("recipes"):
        cache_age = now - fetched if fetched else None
        if cache_age and cache_age < MAX_CACHE_AGE:
            log.info("HelloFresh menu fra cache (%.1f timer gamle – fetch fejlede)", cache_age.total_seconds() / 3600)
            return [_format_recipe(r) for r in cache.get("recipes", [])]
        else:
            log.warning("HelloFresh cache er for gamle (%.1f dage) – ingen menu", cache_age.total_seconds() / 86400)

    log.info("HelloFresh: ingen brugbar menu")
    return None


def for_family(cfg: dict, now: dt.datetime) -> dict | None:
    """Til family.json: denne uges HelloFresh meny. None hvis disabled/ingen token/fejl."""
    if not enabled(cfg):
        log.debug("HelloFresh: slået fra i config.toml")
        return None

    state_dir = Path(cfg.get("output", "web/family.json")).parent

    try:
        recipes = get_menu(state_dir, now)
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
