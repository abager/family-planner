"""I dag: én fælles tidsakse med en bane pr. person (børn først), en kolonne side om side, når ting overlapper for samme
person, aftaler for hele familien som én blok på tværs, aksen 07–21 der strækkes efter behov, "nu"-linjen kun i dag,
og under 700 px én liste i tidsorden. Syntetiske data oven på appens demodata (ugen starter mandag 28/9 2026)."""
import datetime as dt
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Copenhagen")


def on(day, h, m=0):
    """Dag nr. `day` i demougen (0 = mandag 28/9) kl. h.m."""
    return dt.datetime(2026, 9, 28, h, m, tzinfo=TZ) + dt.timedelta(days=day)


def open_day(make_page, now, add="", **kw):
    """`add` er JS, der lægger aftaler i state.data, før der tegnes igen (E(id,titel,start,slut,personer))."""
    page, *_ = make_page(now=now, fixed=True, **kw)
    if add:
        page.evaluate("""(()=>{ const d=new Date();
            const at=(h,m)=>{const x=new Date(d); x.setHours(h,m||0,0,0); return x.toISOString();};
            const E=(id,t,s,e,p)=>({id,title:t,start:s,end:e,people:p,source:'google',allDay:false});
            %s; render(); })()""" % add)
    return page


def lanes(page):
    return page.eval_on_selector_all("#dayLanes .dlane", "ls => ls.map(l => l.dataset.person)")


def box(page, sel):
    return page.eval_on_selector(sel, "e => { const r=e.getBoundingClientRect(); return {l:r.left, r:r.right, t:r.top, b:r.bottom}; }")


def test_one_lane_per_visible_person_children_first(make_page):
    page = open_day(make_page, on(3, 10))
    assert lanes(page) == ["hugo", "carla", "leo", "andreas", "monica"]
    page.click("#people .chip:has-text('Leo')")
    assert lanes(page) == ["hugo", "carla", "andreas", "monica"]


def test_overlapping_things_for_one_person_stand_side_by_side(make_page):
    page = open_day(make_page, on(2, 10))                                   # onsdag: Carlas emneuge 8–13 oven i lektionerne
    ev = box(page, '.dlane[data-person="carla"] .blk:has-text("Emneuge")')
    les = box(page, '.dlane[data-person="carla"] .blk.lesson:has-text("Dansk")')
    assert ev["r"] <= les["l"] + 1 or les["r"] <= ev["l"] + 1                # ved siden af hinanden, ikke oven i
    assert les["t"] < ev["b"] and ev["t"] < les["b"]                         # og samtidig
    hugo = box(page, '.dlane[data-person="hugo"] .blk.lesson:has-text("Dansk")')
    lane = box(page, '.dlane[data-person="hugo"]')
    assert hugo["r"] - hugo["l"] > 0.9 * (lane["r"] - lane["l"])             # alene: hele banens bredde


def test_a_family_event_is_one_block_across_all_lanes_unless_something_overlaps_it(make_page):
    page = open_day(make_page, on(4, 10))                                   # fredag: aftensmad 17.30–20.30 for hele familien
    assert page.locator(".dspan .blk.fam").count() == 1
    assert page.locator(".dlane .blk.fam").count() == 0
    span, cols = box(page, ".dspan .blk.fam"), box(page, ".dcols")
    assert span["r"] - span["l"] > 0.9 * (cols["r"] - cols["l"])
    page = open_day(make_page, on(4, 10), add="state.data.events.push(E('x1','Yoga',at(19),at(20),['monica']))")
    assert page.locator(".dspan .blk").count() == 0
    assert page.locator(".dlane .blk.fam").count() == 5                      # hver bane sin egen, så yoga kan ses


def test_the_axis_is_7_to_21_and_stretches_when_needed(make_page):
    hours = lambda p: p.eval_on_selector_all(".daxis .hl time", "ts => ts.map(t => t.textContent)")
    page = open_day(make_page, on(3, 10))
    assert hours(page)[0] == "07" and hours(page)[-1] == "20"
    page = open_day(make_page, on(3, 10), add="state.data.events.push(E('x1','Fly',at(5,30),at(6,15),['andreas']),E('x2','Koncert',at(21,30),at(22,45),['monica']))")
    assert hours(page)[0] == "05" and hours(page)[-1] == "22"


def test_the_now_line_is_only_shown_for_today(make_page):
    page = open_day(make_page, on(3, 10, 20))
    assert page.inner_text("#dayTitle") == "I dag" and page.locator(".dnow").count() == 1
    assert "10.20" in page.inner_text(".dnow")
    page = open_day(make_page, on(3, 18, 30))                              # efter kl. 18 handler siden om i morgen
    assert page.inner_text("#dayTitle") == "I morgen" and page.locator(".dnow").count() == 0


def test_finished_things_are_dimmed_and_the_current_lesson_is_marked(make_page):
    page = open_day(make_page, on(3, 10, 20))
    assert page.locator('.dlane[data-person="hugo"] .blk.lesson.past:has-text("Dansk")').count() == 2
    assert page.locator('.dlane[data-person="hugo"] .blk.lesson.now:has-text("Matematik")').count() == 1


def test_a_tap_opens_the_details(make_page):
    page = open_day(make_page, on(3, 10))
    page.click('.dlane[data-person="leo"] .blk:has-text("Bedsteforældredag")')
    assert page.evaluate("detail.open") and "09.00–11.00" in page.inner_text("#dBody")
    page.evaluate("detail.close()")
    page.click('.dlane[data-person="hugo"] .blk.lesson:has-text("Idræt")')
    assert "Hallen" in page.inner_text("#dBody")


def test_on_a_phone_the_day_is_one_list_with_each_event_once(make_page):
    page = open_day(make_page, on(2, 10), width=390, height=844)            # onsdag: svømning for Carla og Leo
    assert page.is_hidden("#dayLanes .dgrid") and page.is_visible("#dayLanes .dlist")
    rows = page.locator(".lrow", has_text="Svømning")
    assert rows.count() == 1 and rows.locator(".pav").count() == 2
    assert page.locator(".lrow", has_text="Skole").count() == 2               # én linje pr. barn med lektioner
    assert page.locator(".lnow").count() == 1


def test_an_ipad_in_portrait_keeps_the_lanes(make_page):
    page = open_day(make_page, on(2, 10), width=820, height=1180)
    assert page.is_visible("#dayLanes .dgrid") and page.is_hidden("#dayLanes .dlist")
    assert len(lanes(page)) == 5
