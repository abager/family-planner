"""HelloFresh integration – ugens meny til familieoverblikket.

Login med email + password mod /gw/login endpoint.
Tokens refreshes automatisk når de udløber.
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

CREDENTIALS_FILE = "secrets/hellofresh_credentials.json"
ACCESS_TOKEN_FILE = "hellofresh_access_token_cache.json"
CACHE_FILE = "hellofresh_cache.json"
FETCH_EVERY = dt.timedelta(days=7)
MAX_CACHE_AGE = dt.timedelta(days=8)

HF_LOGIN_URL = "https://gw.hellofresh.com/auth/login"
HF_API_BASE = "https://www.hellofresh.com/api/v1"


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


def _read_json(path: Path) -> dict:
    """Læs JSON-fil, returner tom dict hvis den ikke findes."""
    try:
        d = json.loads(path.read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_credentials(state_dir: Path, email: str, password: str) -> None:
    """Gemmer HelloFresh credentials sikkert."""
    if not email or not password:
        raise ValueError("Email og password må ikke være tomme")
    _write_secure(
        Path(state_dir) / CREDENTIALS_FILE,
        {"email": email, "password": password, "saved": dt.datetime.now(TZ).isoformat()}
    )
    log.info("HelloFresh credentials gemt")


def load_credentials(state_dir: Path) -> tuple[str, str] | None:
    """Henter credentials. None hvis ikke sat."""
    data = _read_json(Path(state_dir) / CREDENTIALS_FILE)
    if data and data.get("email") and data.get("password"):
        return (data["email"], data["password"])
    return None


def _login_and_get_token(email: str, password: str) -> str:
    """Login med email + password, få access_token."""
    try:
        import requests
    except ImportError:
        raise HelloFreshUnavailable("requests biblioteket er ikke installeret") from None
    
    try:
        r = requests.post(
            HF_LOGIN_URL,
            json={"email": email, "password": password},
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        
        access_token = data.get("access_token")
        if not access_token:
            raise HelloFreshUnavailable("Ingen access_token i login response")
        
        log.info("HelloFresh login vellykket")
        return access_token
    
    except requests.exceptions.RequestException as e:
        raise HelloFreshUnavailable(f"HelloFresh login fejlede: {e}") from e


def _get_access_token(state_dir: Path) -> str:
    """Få access_token – hent nyt hvis cache er udløbet."""
    cache_path = Path(state_dir) / ACCESS_TOKEN_FILE
    cache = _read_json(cache_path)
    
    # Check hvis access_token er frisk
    if cache.get("access_token") and cache.get("expires_at"):
        expires_at = dt.datetime.fromisoformat(cache["expires_at"])
        if dt.datetime.now(TZ) < expires_at:
            log.debug("HelloFresh access_token fra cache")
            return cache["access_token"]
    
    # Hent nyt token med credentials
    creds = load_credentials(state_dir)
    if not creds:
        raise HelloFreshUnavailable("Ingen HelloFresh credentials sat")
    
    email, password = creds
    access_token = _login_and_get_token(email, password)
    
    # Gem token (typisk 1800 sekunder = 30 min)
    expires_at = dt.datetime.now(TZ) + dt.timedelta(seconds=1800)
    _write_secure(cache_path, {
        "access_token": access_token,
        "expires_at": expires_at.isoformat(),
        "fetched": dt.datetime.now(TZ).isoformat()
    })
    
    return access_token


def _fetch_recipes(token: str, country: str = "DK") -> list:
    """Hent recepter fra HelloFresh API."""
    try:
        import requests
    except ImportError:
        raise HelloFreshUnavailable("requests biblioteket er ikke installeret") from None
    
    try:
        headers = {"Authorization": f"Bearer {token}"}
        
        # Hent denne uges meny
        r = requests.get(
            f"{HF_API_BASE}/recurring_plan?country={country}",
            headers=headers,
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        
        recipes = data.get("recipes", [])
        if not recipes:
            raise HelloFreshUnavailable("Ingen recepter i menuen")
        
        log.info("HelloFresh menu hentet (%d måltider)", len(recipes))
        return recipes[:3]  # Tag kun de første 3
    
    except requests.exceptions.RequestException as e:
        raise HelloFreshUnavailable(f"HelloFresh API fejl: {e}") from e
    except Exception as e:
        raise HelloFreshUnavailable(f"Fejl ved hentning af menu: {e}") from e


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
        access_token = _get_access_token(state_dir)
        recipes = _fetch_recipes(access_token)
        
        if recipes:
            new_data = {"recipes": recipes, "fetched": dt.datetime.now(TZ).isoformat()}
            _write_secure(cache_path, new_data)
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
        
        recipes = data.get("recipes", [])[:3]
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
    return {"enabled": enabled(cfg), "token_set": load_credentials(state_dir) is not None}
