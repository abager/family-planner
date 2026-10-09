"""Layout: feedet er én kolonne i sidens fulde bredde, billeder i en stribe, Praktisk info har ingen skjul/vis-knap, intet løber ud over siden.

Dagens baner: på iPad på højkant (og bredere) en bane pr. person, under 700 px én liste i tidsorden."""
import datetime as dt

import pytest

NOW = dt.datetime(2026, 10, 1, 10, 0)
VIEWS = [("v-today", "I dag"), ("v-week", "Ugen"), ("v-mail", "Beskeder"), ("v-aula", "Feed")]


@pytest.mark.parametrize("w,h,expected", [(390, 844, None), (810, 1080, None), (1080, 810, None), (1440, 900, 1248), (1920, 1080, 1248)])
def test_feed_is_one_column_and_uses_the_page_width(make_page, w, h, expected):
    page, *_ = make_page(now=NOW, fixed=True, width=w, height=h)
    page.click("#v-aula")
    page.wait_for_timeout(300)
    m = page.evaluate("""(()=>{const cs=[...document.querySelectorAll('#feed .card')];
        const wr=document.querySelector('.wrap'), ws=getComputedStyle(wr);
        return {n:cs.length,fw:Math.round(document.querySelector('.feedwrap').getBoundingClientRect().width),
                inner:Math.round(wr.clientWidth-parseFloat(ws.paddingLeft)-parseFloat(ws.paddingRight)),
                lefts:[...new Set(cs.map(c=>Math.round(c.getBoundingClientRect().left)))],ph:Math.round(document.querySelector('#feed .photos button').getBoundingClientRect().height),vh:innerHeight}})()""")
    assert m["n"] >= 3 and len(m["lefts"]) == 1                                  # alle kort står under hinanden
    assert m["fw"] == m["inner"]                                                  # samme bredde som indholdet på de andre sider
    if expected:
        assert m["fw"] == expected
    assert m["ph"] <= max(760, 0.7 * m["vh"]) + 2                                # et billede fylder aldrig mere end ca. 70 % af skærmhøjden


def test_important_info_has_no_show_hide_button_for_regular_lessons(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    assert page.locator("#planMore").count() == 0 and page.locator("#dayLanes .more").count() == 0
    assert page.locator('#dayLanes [data-plan]').count() >= 1                       # Praktisk info står i "Hele dagen"-rækken
    assert "almindelig undervisning" not in page.inner_text("#dayLanes").lower()
    assert page.evaluate("typeof state.showAllPlan") == "undefined"


@pytest.mark.parametrize("w,h", [(390, 844), (820, 1180), (1280, 800), (1920, 1080)])
def test_no_view_scrolls_sideways(make_page, w, h):
    page, *_ = make_page(now=NOW, fixed=True, width=w, height=h)
    for vid, name in VIEWS:
        page.click("#" + vid)
        page.wait_for_timeout(200)
        assert page.evaluate("document.documentElement.scrollWidth-document.documentElement.clientWidth") <= 0, f"{name} ved {w} px"


def test_message_list_uses_plain_list_semantics(make_page):
    """Axe fandt en gang listbox/option med <li> imellem. En almindelig liste af knapper med aria-current er korrekt."""
    page, *_ = make_page(now=NOW, fixed=True)
    page.click("#v-mail")
    page.wait_for_timeout(300)
    assert page.locator('#mitems[role="listbox"], #mitems [role="option"]').count() == 0
    assert page.locator('#mitems .mrow[aria-current="true"]').count() == 1


def test_google_outage_without_a_server_is_shown_under_the_warning_at_the_title(make_page):
    # Uden server (statisk brug) er family.json den eneste kilde – fejlen står under ⚠ ved titlen, ikke i statuslinjen
    from data import family
    from fake_server import FakeServer
    fake = FakeServer()
    fake.family = family(health={"google": {"ok": False, "failed": ["Familiekalender"], "last_ok": "2026-10-01T08:15:00+02:00"}, "aula": {"state": "ok"}})
    page, *_ = make_page(fake)
    assert "kunne ikke hentes" not in page.inner_text("#status")
    page.click("#probBtn")
    assert "Familiekalenderen kunne ikke hentes" in page.inner_text("#probDlg")


def _album_with(page, n):
    page.click("#v-aula")
    page.wait_for_timeout(300)
    page.evaluate(f"""(()=>{{const a=state.data.albums[0]; a.images=Array.from({{length:{n}}},(_,i)=>a.images[i%a.images.length]); renderAula();}})()""")
    page.wait_for_timeout(300)


STRIP = """(()=>{const c=document.querySelector('#feed .car'), p=c.querySelector('.photos'), b=p.children[0].getBoundingClientRect();
    return {w:Math.round(b.width),h:Math.round(b.height),pw:Math.round(p.clientWidth),ctr:c.querySelector('.ctr').textContent,
            ctrHidden:c.querySelector('.ctr').hidden,prev:c.querySelector('.arr.prev').hidden,next:c.querySelector('.arr.next').hidden}})()"""


@pytest.mark.parametrize("w,h,per", [(1440, 900, 3), (820, 1180, 2), (390, 844, 1)])
def test_an_album_shows_several_portrait_photos_side_by_side_on_wide_screens(make_page, w, h, per):
    page, *_ = make_page(now=NOW, fixed=True, width=w, height=h)
    _album_with(page, 8)
    m = page.evaluate(STRIP)
    assert abs(m["w"] * per + (per - 1) * 2 - m["pw"]) <= per                       # præcis 'per' billeder fylder striben
    if per > 1:
        assert m["h"] > m["w"] and m["ctr"] == f"1–{per}/8"                         # portræt, og tælleren viser de synlige
    else:
        assert abs(m["h"] - m["w"]) <= 1 and m["ctr"] == "1/8"                      # telefon: ét kvadratisk billede som før
    assert m["prev"] and not m["next"]


def test_the_arrows_jump_a_screenful_and_stop_at_the_end(make_page):
    page, *_ = make_page(now=NOW, fixed=True, width=1440, height=900)
    _album_with(page, 8)
    page.click("#feed .car .arr.next", force=True)
    page.wait_for_timeout(700)
    assert page.evaluate(STRIP)["ctr"] == "4–6/8"
    page.click("#feed .car .arr.next", force=True)
    page.wait_for_timeout(700)
    m = page.evaluate(STRIP)
    assert m["ctr"] == "6–8/8" and m["next"] and not m["prev"]


def test_fewer_photos_than_room_share_the_width_without_counter(make_page):
    page, *_ = make_page(now=NOW, fixed=True, width=1440, height=900)
    _album_with(page, 2)
    m = page.evaluate(STRIP)
    assert abs(m["w"] * 2 + 2 - m["pw"]) <= 2 and m["ctrHidden"] and m["next"]


def test_a_single_photo_is_full_width_with_limited_height_and_not_cropped(make_page):
    page, *_ = make_page(now=NOW, fixed=True, width=1440, height=900)
    _album_with(page, 1)
    m = page.evaluate("""(()=>{const p=document.querySelector('#feed .photos'), img=p.querySelector('img:not(.bg)');
        return {one:p.classList.contains('one'),w:Math.round(p.clientWidth),h:Math.round(p.getBoundingClientRect().height),fit:getComputedStyle(img).objectFit,vh:innerHeight}})()""")
    assert m["one"] and m["w"] >= 1240 and m["h"] <= min(0.6 * m["vh"], 640) + 1 and m["fit"] == "contain"


def test_tapping_a_photo_in_the_strip_opens_it_in_full_screen(make_page):
    page, *_ = make_page(now=NOW, fixed=True, width=1440, height=900)
    _album_with(page, 8)
    page.locator("#feed .car").first.locator(".photos button").nth(2).click()
    page.wait_for_timeout(400)
    assert page.evaluate("document.getElementById('lb').open") and page.inner_text("#lbCtr") == "3/8"


def test_the_page_keeps_room_for_the_scrollbar_so_views_line_up(make_page):
    page, *_ = make_page(now=NOW, fixed=True, width=1440, height=900)
    assert page.evaluate("getComputedStyle(document.documentElement).scrollbarGutter") == "stable"
    lefts = set()
    for vid, _ in VIEWS:
        page.click("#" + vid)
        page.wait_for_timeout(200)
        lefts.add(page.evaluate("Math.round(document.querySelector('.wrap').getBoundingClientRect().left)"))
    assert len(lefts) == 1                                                          # Beskeder står samme sted som resten
