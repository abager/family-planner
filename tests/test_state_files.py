"""Alle tilstandsfiler med familiens data skal være dækket fire steder: .gitignore, .dockerignore,
no-family-data-hooket i .pre-commit-config.yaml og server.DENY_NAMES. Glemmer man ét sted, fejler testen."""
import re
from pathlib import Path

import pytest

import server

ROOT = Path(__file__).resolve().parents[1]
STATE_FILES = ["web/server_state.json", "web/suggestions_state.json", "web/learned_rules.json",
               "web/ai_cache.json", "web/ai_usage.json",
               "web/home_location.json", "web/weather_cache.json"]


def _hook_pattern() -> re.Pattern:
    text = (ROOT / ".pre-commit-config.yaml").read_text("utf-8")
    block = text.split("id: no-family-data", 1)[1].split("files: >-", 1)[1]
    body = block.split("(?x)^(", 1)[1].split(")$", 1)[0]
    alternatives = [ln.strip().rstrip("|") for ln in body.splitlines() if ln.strip()]
    return re.compile("^(" + "|".join(alternatives) + ")$")


@pytest.mark.parametrize("path", STATE_FILES)
def test_state_file_is_ignored_blocked_and_never_served(path):
    for ignore in (".gitignore", ".dockerignore"):
        lines = (ROOT / ignore).read_text("utf-8").splitlines()
        assert path in lines, f"{path} mangler i {ignore}"
    assert _hook_pattern().match(path), f"{path} fanges ikke af no-family-data-hooket"
    assert Path(path).name in server.DENY_NAMES, f"{Path(path).name} mangler i DENY_NAMES"
