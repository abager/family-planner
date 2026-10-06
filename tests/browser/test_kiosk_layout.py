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
    page = open_kiosk(make_page, add="""for(let i=0;i<18;i++){ state.data.events.push(E('s'+i,'Ekstra aftale '+i,at(15,i*3),at(16),['hugo']));
        state.data.tasks.push(T('t'+i,'Ekstra ting '+i,k,'carla')); }""")
    assert page.evaluate("document.documentElement.scrollHeight<=innerHeight+1")
    assert page.evaluate("[...document.querySelectorAll('.k-person,.k-husk')].every(b=>b.scrollHeight<=b.clientHeight+1)")
    more = page.locator(".k-person[data-person=hugo] .k-more")
    assert more.is_visible() and more.inner_text().startswith("+")
    assert page.locator("#kHusk .k-more").is_visible()
    assert page.evaluate("parseFloat(getComputedStyle(kioskView).fontSize)") >= 14
    more.click()
    assert page.evaluate("detail.open") and page.locator("#dBody li", has_text="Ekstra aftale 17").count() == 1


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


def test_the_kiosk_is_dark_even_when_the_device_is_light(make_page):
    page = open_kiosk(make_page)
    assert page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches") is False
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(15, 20, 29)"
    page.keyboard.press("Escape")
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") != "rgb(15, 20, 29)"   # resten af appen følger enheden
