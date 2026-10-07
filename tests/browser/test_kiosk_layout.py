"""Kioskskærmen: én kolonne pr. person (børn først), aftaler for hele familien hos alle, husk for tre dage,
"+N flere" når der ikke er plads, detaljer ved tryk og mørkt tema. Syntetiske data oven på appens demodata."""
import datetime as dt
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Copenhagen")


def at(h, m=0):
    return dt.datetime(2026, 10, 1, h, m, tzinfo=TZ)


def open_kiosk(make_page, now=None, add=""):
    """Åbn kiosken med demodata; `add` er JS, der lægger flere aftaler/opgaver i state.data, før der tegnes igen."""
    page, *_ = make_page(now=now or at(10, 20), fixed=True, width=1180, height=820, url="/index.html?kiosk=1")
    if add:
        page.evaluate("""(()=>{ const d=new Date(), k=ymd(d), k1=ymd(addDays(d,1)), k2=ymd(addDays(d,2)), k3=ymd(addDays(d,3));
            const at=(h,m)=>{const x=new Date(d); x.setHours(h,m||0,0,0); return x.toISOString();};
            const E=(id,t,s,e,p)=>({id,title:t,start:s,end:e,people:p,source:'google',allDay:false});
            const T=(id,t,due,p,extra)=>({id,title:t,due,kind:'husk',people:[p],person:p,...(extra||{})});
            %s; renderKiosk(); })()""" % add)
    return page


def cols(page):
    return page.eval_on_selector_all(".k-person", "cs => cs.map(c => c.dataset.person)")


def test_one_column_per_person_children_first(make_page):
    page = open_kiosk(make_page)
    assert cols(page) == ["hugo", "carla", "leo", "andreas", "monica"]
    w = page.evaluate("['kPeople','kHusk'].map(id => document.getElementById(id).getBoundingClientRect().width)")
    assert w[0] > 3 * w[1]                                                  # skemaerne får det meste af bredden


def test_a_family_event_is_shown_in_every_column(make_page):
    page = open_kiosk(make_page, add="state.data.events.push(E('f1','Middag hos bedsteforældre',at(18,30),at(21),['family']))")
    for p in cols(page):
        assert "Middag hos bedsteforældre" in page.inner_text(f".k-person[data-person={p}]")


def test_an_adults_event_is_only_in_that_column(make_page):
    page = open_kiosk(make_page, add="state.data.events.push(E('m1','Tandlæge kontrol',at(17),at(17,45),['monica']))")
    assert "Tandlæge kontrol" in page.inner_text(".k-person[data-person=monica]")
    assert "Tandlæge kontrol" not in page.inner_text(".k-person[data-person=andreas]")


def test_the_remember_column_covers_three_days_and_lists_a_task_once(make_page):
    page = open_kiosk(make_page, add="""state.data.tasks=[T('a','Idrætstøj',k,'hugo'),T('b','Madpakke til tur',k1,'carla'),T('c','Bibliotekbøger',k2,'hugo'),
        T('d','Læs 20 minutter',k,'carla',{recurring:'daily',from:k}),T('e','For langt ude',k3,'leo')]""")
    heads = page.eval_on_selector_all("#kRemember .k-hday", "hs => hs.map(h => h.textContent)")
    assert heads == ["I dag", "I morgen", "Lørdag 3/10"]
    t = page.inner_text("#kRemember")
    assert t.count("Læs 20 minutter") == 1 and "Madpakke til tur" in t and "Bibliotekbøger" in t and "For langt ude" not in t


def test_after_the_evening_hour_the_remember_column_starts_tomorrow(make_page):
    page = open_kiosk(make_page, now=at(18, 30), add="state.data.tasks=[T('a','Idrætstøj',k,'hugo'),T('e','Søndagsting',k3,'leo')]")
    heads = page.eval_on_selector_all("#kRemember .k-hday", "hs => hs.map(h => h.textContent)")
    assert heads == ["I morgen", "Lørdag 3/10", "Søndag 4/10"]
    t = page.inner_text("#kRemember")
    assert "Idrætstøj" not in t and "Søndagsting" in t


def test_too_much_ends_with_more_and_the_full_list_on_a_tap(make_page):
    page = open_kiosk(make_page, add="""for(let i=0;i<30;i++){ state.data.events.push(E('s'+i,'Ekstra aftale '+i,at(15,i),at(16),['hugo']));
        state.data.tasks.push(T('t'+i,'Ekstra ting '+i,k,'carla')); }""")
    assert page.evaluate("document.documentElement.scrollHeight<=innerHeight+1")
    assert page.evaluate("[...document.querySelectorAll('.k-person,.k-husk')].every(b=>b.scrollHeight<=b.clientHeight+1)")
    more = page.locator(".k-person[data-person=hugo] .k-more")
    assert more.is_visible() and more.inner_text().startswith("+")
    assert page.locator("#kHusk .k-more").is_visible()
    assert page.evaluate("parseFloat(getComputedStyle(kioskView).fontSize)") >= 12
    more.click()
    assert page.evaluate("detail.open") and page.locator("#dBody li", has_text="Ekstra aftale 29").count() == 1


def test_a_task_detail_never_shows_message_text(make_page):
    page = open_kiosk(make_page, add="state.data.tasks=[T('a','Underskriv seddel',k,'hugo',{text:'Fortroligt indhold fra beskeden',source:'besked'})]")
    page.locator("#kRemember .k-row", has_text="Underskriv seddel").click()
    assert page.evaluate("detail.open") and "Underskriv seddel" in page.inner_text("#dTitle")
    assert "Fortroligt" not in page.inner_text("#dBody")


def test_escape_closes_the_details_first_and_then_the_kiosk(make_page):
    page = open_kiosk(make_page)
    page.locator(".k-row").first.click()
    assert page.evaluate("detail.open")
    page.keyboard.press("Escape")
    page.wait_for_timeout(100)
    assert not page.evaluate("detail.open") and page.evaluate("state.kiosk")
    page.keyboard.press("Escape")
    assert not page.evaluate("state.kiosk")


WX = "state.data.weather={kilde:'MET Norway',dage:[{dato:k,min:8,max:11,ikon:%r,tekst:'8–11°',raad:[],timer:[]}]}"


def sky(page):
    return page.evaluate("document.body.dataset.sky || null")


def test_by_day_the_kiosk_is_light_and_the_background_is_the_days_weather(make_page):
    for icon, want in (("☀️", "sol"), ("⛅", "skyet"), ("☁️", "overskyet"), ("🌧️", "regn"), ("❄️", "frost"), ("⛈️", "regn")):
        page = open_kiosk(make_page, add=WX % icon)
        assert sky(page) == want, icon
        assert page.evaluate("document.body.classList.contains('night')") is False
        assert "gradient" in page.evaluate("getComputedStyle(document.body).backgroundImage")
        page.close()


def test_without_weather_the_day_background_is_sage(make_page):
    page = open_kiosk(make_page, add="state.data.weather=null")
    assert sky(page) is None
    assert page.evaluate("getComputedStyle(document.body).getPropertyValue('--sky1').trim()").upper() == "#DCE5D3"


def test_the_background_follows_the_focus_day_after_the_evening_hour(make_page):
    page = open_kiosk(make_page, now=at(18, 30), add="""state.data.weather={kilde:'MET Norway',dage:[
        {dato:k,min:8,max:11,ikon:'☀️',tekst:'sol',raad:[],timer:[]},{dato:k1,min:6,max:9,ikon:'🌧️',tekst:'regn',raad:[],timer:[]}]}""")
    assert sky(page) == "regn"                                              # efter kl. 18 handler skærmen om i morgen


def test_from_21_to_6_the_kiosk_is_dark(make_page):
    for h, night in ((20, False), (21, True), (23, True), (5, True), (6, False)):
        page = open_kiosk(make_page, now=at(h, 30), add=WX % "☀️")
        assert page.evaluate("document.body.classList.contains('night')") is night, h
        bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        assert (bg == "rgb(15, 20, 29)") is night, h
        page.close()


def _lum(c):
    c = [int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _contrast(a, b):
    a, b = _lum(a), _lum(b)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def test_text_keeps_wcag_aa_contrast_on_every_sky(make_page):
    page = open_kiosk(make_page)
    for w in (None, "sol", "skyet", "overskyet", "regn", "frost"):
        v = page.evaluate("""(w)=>{ if(w) document.body.dataset.sky=w; else delete document.body.dataset.sky;
            const cs=getComputedStyle(document.body), g=n=>cs.getPropertyValue(n).trim();
            return {ink:g('--ink'), muted:g('--muted'), accent:g('--accent'), warn:g('--warn'), rain:g('--rain'), sky1:g('--sky1'), sky3:g('--sky3')}; }""", w)
        for bg in (v["sky1"], v["sky3"]):
            assert _contrast(v["ink"], bg) >= 7, (w, v)
            for k in ("muted", "accent", "warn", "rain"):
                assert _contrast(v[k], bg) >= 4.5, (w, k, v)


def test_leaving_the_kiosk_keeps_the_theme_because_the_whole_app_has_it(make_page):
    page = open_kiosk(make_page, now=at(22), add=(WX % "🌧️").replace("dato:k,", "dato:k1,"))   # efter kl. 18: morgendagens vejr
    assert sky(page) == "regn"
    page.keyboard.press("Escape")
    assert not page.evaluate("state.kiosk")
    assert sky(page) == "regn" and page.evaluate("document.body.classList.contains('night')") is True


def theme_colors(page):
    return page.eval_on_selector_all('meta[name="theme-color"]', "ms => ms.map(m => m.content.toUpperCase())")


def test_the_status_bar_colour_follows_the_top_of_the_screen_in_and_out_of_the_kiosk(make_page):
    page = open_kiosk(make_page, add=WX % "☁️")
    assert theme_colors(page) == ["#D3D7DB"]                                # overskyet: toppen af himlen
    page.close()
    page = open_kiosk(make_page, now=at(22), add=WX % "☁️")
    assert theme_colors(page) == ["#0F141D"]                                # om natten: den mørke farve
    page.keyboard.press("Escape")
    assert theme_colors(page) == ["#0F141D"]                                # appen har samme tema
