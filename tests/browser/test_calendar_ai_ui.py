"""Forslag fundet af AI udfylder den eksisterende "Føj til kalender"-formular – ingen ny skærm."""
import datetime as dt
import json

NOW = dt.datetime(2026, 10, 1, 10, 0)
AI_OPT = {"id": "ev_ai000000001", "title": "Motionsdag", "calendar_title": "Carla: Motionsdag", "start": "2026-10-09",
          "end": "2026-10-09", "start_time": None, "end_time": None, "all_day": True, "location": "Skolens bane",
          "people": ["carla"], "description": "Fra Aula-besked «Motionsdag»\n\nMotionsdag fredag\n\nOprettet fra Familieplan",
          "exists": False, "by": "ai", "source": {"type": "message", "id": "m1", "title": "Motionsdag", "label": "Aula-besked"}}


def open_with(make_page, cal):
    page, fake, _ = make_page(now=NOW, fixed=True)
    demo = json.loads(page.evaluate("JSON.stringify(demoData())"))
    demo["messages"][0]["cal"] = cal
    fake.use_demo(page, messages=demo["messages"])
    page.reload()
    page.wait_for_timeout(600)
    page.click("#v-mail")
    page.wait_for_timeout(250)
    page.locator('.mrow[data-mid="m1"]').click()
    page.wait_for_timeout(300)
    return page


def test_an_ai_all_day_suggestion_prefills_the_form_with_hele_dagen(make_page):
    page = open_with(make_page, [AI_OPT])
    page.locator(f'[data-addcal="{AI_OPT["id"]}"]').click()
    assert page.input_value("#evTitle") == "Carla: Motionsdag" and page.input_value("#evDate") == "09/10/2026"
    assert page.is_checked("#evAll")
    assert page.input_value("#evLoc") == "Skolens bane"


def test_an_item_without_suggestions_has_no_add_button(make_page):
    page = open_with(make_page, [])
    assert page.locator("#detail [data-addcal], .mdetail [data-addcal], [data-addcal]").count() == 0
