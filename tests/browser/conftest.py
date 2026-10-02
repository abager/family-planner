"""Browsertests: Chromium via Playwright mod en ren kopi af web/ og en simuleret server. Springes over, hvis Chromium ikke er installeret."""
from __future__ import annotations

import datetime as dt
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from fake_server import FakeServer  # noqa: E402
from helpers import StaticDir  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(items):
    for it in items:
        if "browser" in str(it.fspath).split("/"):
            it.add_marker(pytest.mark.browser)


@pytest.fixture(scope="session")
def pw():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(pw):
    try:
        b = pw.chromium.launch()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Chromium er ikke installeret (python -m playwright install chromium): {str(e)[:80]}")
    yield b
    b.close()


@pytest.fixture(scope="session")
def site(tmp_path_factory):
    """En ren kopi af appen uden nogen family.json – så testene aldrig læser rigtige data."""
    d = tmp_path_factory.mktemp("site")
    for f in (ROOT / "web").iterdir():
        if f.is_file() and f.name != "family.json":
            shutil.copy(f, d / f.name)
    s = StaticDir(d)
    yield s
    s.close()


class Opened:
    """Samler fejl og de adresser, appen forsøgte at åbne (fx Google Kalender-linket)."""

    def __init__(self, page):
        self.page, self.errors = page, []
        page.on("pageerror", lambda e: self.errors.append(str(e)))

    @property
    def links(self) -> list[str]:
        return self.page.evaluate("window.__opened || []")


@pytest.fixture
def make_page(browser, site, pw):
    """make_page(server=None, now=None, device=None, **viewport) → (page, fake, opened). Alt lukkes efter testen."""
    made = []

    def make(fake: FakeServer | None = None, now: dt.datetime | None = None, device: str | None = None, fixed: bool = False, width=1280, height=900,
             touch=False, goto=True, url="/index.html"):
        fake = fake or FakeServer()
        kw = pw.devices[device] if device else {"viewport": {"width": width, "height": height}, "has_touch": touch}
        ctx = browser.new_context(timezone_id="Europe/Copenhagen", **kw)
        page = ctx.new_page()
        page.add_init_script("window.__opened=[];window.open=(u)=>{window.__opened.push(u);return null}")
        if now:
            (page.clock.set_fixed_time if fixed else page.clock.install)(time=now)
        fake.install(page)
        made.append(ctx)
        opened = Opened(page)
        if goto:
            page.goto(site.url + url)
            page.wait_for_timeout(500)
        return page, fake, opened

    yield make
    for c in made:
        c.close()
