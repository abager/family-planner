"""AI-fortællingen i appen og på kioskskærmen. Reglernes liste vises stadig, når AI ikke er brugt."""
import datetime as dt

NOW = dt.datetime(2026, 10, 1, 7, 30)
STORY = ["I skal huske regntøj og gummistøvler til Carla og Hugo, for det regner fra middag.",
         "Hugo har vikar i matematik kl. 10.00, og Carla har fodbold kl. 16.30 på Kunstgræsbanen.",
         "Aftenen er rolig – der er ingen aftaler efter fodbold."]


def brief(**extra):
    return {"generated": "2026-10-01T07:00:00+02:00", "mode": "day", "method": "ai", "provider": "gemini",
            "headline_label": "i dag", "period": ["2026-10-01", "2026-10-01"], "fortaelling": STORY, "kilde_ids": ["A1"], **extra}


def open_app(make_page, site, b, kiosk=False, viewport=None):
    kw = viewport or {}
    page, fake, _ = make_page(now=NOW, fixed=True, goto=False, **kw)
    page.goto(site.url + "/index.html")
    fake.use_demo(page)
    fake.briefing = b
    page.goto(site.url + ("/index.html?kiosk=1" if kiosk else "/index.html"))
    page.wait_for_timeout(600)
    return page


def test_the_app_shows_the_narrative_as_paragraphs(make_page, site):
    page = open_app(make_page, site, brief())
    paras = page.eval_on_selector_all("#briefDay .story p", "ps => ps.map(p => p.textContent)")
    assert paras == STORY
    assert page.locator("#briefDay .bsec").count() == 0
    assert "Skrevet af familieassistenten" in page.inner_text("#briefDay .bfoot")


def test_the_rules_list_is_still_shown_without_ai(make_page, site):
    b = brief(method="offline", afsnit=[{"titel": "Husk", "punkter": [{"tekst": "Gymnastiktøj", "hvem": ["Hugo"], "kilder": []}]}])
    del b["fortaelling"]
    page = open_app(make_page, site, b)
    assert page.locator("#briefDay .story").count() == 0 and "Gymnastiktøj" in page.inner_text("#briefDay .bsecs")


def test_narrative_text_is_escaped(make_page, site):
    page = open_app(make_page, site, brief(fortaelling=["<img src=x onerror=alert(1)> Carla har fodbold."]))
    assert page.locator("#briefDay .story img").count() == 0
    assert "<img" in page.inner_text("#briefDay .story")


def test_the_kiosk_shows_the_narrative_under_the_heading_dagen_and_fits_one_screen(make_page, site):
    for vp in ({"width": 1280, "height": 800}, {"width": 1920, "height": 1080}, {"width": 1024, "height": 768}):
        page = open_app(make_page, site, brief(), kiosk=True, viewport=vp)
        assert page.inner_text("#kRememberHead").startswith("Overblik ")
        assert page.eval_on_selector_all("#kRemember p", "ps => ps.map(p => p.textContent)") == STORY
        assert page.evaluate("document.documentElement.scrollHeight<=innerHeight+1"), vp


def test_a_long_narrative_still_fits_the_kiosk(make_page, site):
    long = [" ".join(["Carla og Hugo skal huske regntøj, og der er fodbold om eftermiddagen."] * 4)] * 4   # ~180 ord
    page = open_app(make_page, site, brief(fortaelling=long), kiosk=True, viewport={"width": 1280, "height": 800})
    assert page.evaluate("document.documentElement.scrollHeight<=innerHeight+1")


def test_the_kiosk_keeps_the_list_heading_without_a_narrative(make_page, site):
    b = brief(method="offline", afsnit=[{"titel": "Husk", "punkter": [{"tekst": "Gymnastiktøj", "hvem": ["Hugo"], "kilder": []}]}])
    del b["fortaelling"]
    page = open_app(make_page, site, b, kiosk=True)
    assert page.inner_text("#kRememberHead").startswith("Overblik ") and "Gymnastiktøj" in page.inner_text("#kRemember")
