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


def test_the_kiosk_shows_the_narrative_in_full_width_and_fits_one_screen(make_page, site):
    for vp in ({"width": 1180, "height": 820}, {"width": 1920, "height": 1080}, {"width": 1024, "height": 768}):
        page = open_app(make_page, site, brief(), kiosk=True, viewport=vp)
        assert page.inner_text("#kBriefHead") == "Overblik i dag"
        assert page.inner_text("#kBriefText").startswith(STORY[0])
        assert page.evaluate("document.documentElement.scrollHeight<=innerHeight+1"), vp
        w = page.evaluate("['kBrief','kPeople'].map(id => document.getElementById(id).getBoundingClientRect().width)")
        assert w[0] > w[1]                                               # fuld bredde over skemaerne


def test_a_long_narrative_is_cut_at_a_sentence_with_at_most_four_lines(make_page, site):
    long = [" ".join([f"Carla og Hugo skal huske regntøj nummer {i}, og der er fodbold om eftermiddagen." for i in range(4)])] * 4
    page = open_app(make_page, site, brief(fortaelling=long), kiosk=True, viewport={"width": 1180, "height": 820})
    t = page.inner_text("#kBriefText")
    assert t.endswith(". …") and len(t) < len(" ".join(long))
    lines = page.evaluate("(()=>{ const b=kBriefText; return Math.round(b.scrollHeight/parseFloat(getComputedStyle(b).lineHeight)); })()")
    assert lines <= 4
    assert page.evaluate("document.documentElement.scrollHeight<=innerHeight+1")
    page.click("#kBrief")                                                # hele fortællingen ved et tryk
    assert page.eval_on_selector_all("#dBody .story p", "ps => ps.map(p => p.textContent)") == long


def test_the_kiosk_shows_the_rules_points_without_a_narrative(make_page, site):
    b = brief(method="offline", afsnit=[{"titel": "Husk", "punkter": [{"tekst": "Gymnastiktøj", "hvem": ["Hugo"], "kilder": []}]}])
    del b["fortaelling"]
    page = open_app(make_page, site, b, kiosk=True)
    assert page.inner_text("#kBriefHead") == "Overblik i dag" and "Gymnastiktøj" in page.inner_text("#kBriefText")


def test_without_an_overview_the_kiosk_says_so(make_page, site):
    page = open_app(make_page, site, None, kiosk=True)
    assert "har ikke lavet et overblik for i dag endnu" in page.inner_text("#kBriefText")
