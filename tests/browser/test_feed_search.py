"""Søgning i feedet (opslag og billeder): filtrerer mens man skriver, alle ord skal findes, træf markeres, Esc rydder."""
import datetime as dt

NOW = dt.datetime(2026, 10, 1, 10, 0)


def titles(page):
    return [page.locator("#feed .card h3").nth(i).inner_text() for i in range(page.locator("#feed .card").count())]


def feed(make_page):
    page, *_ = make_page(now=NOW, fixed=True)
    page.click("#v-aula")
    page.wait_for_timeout(200)
    return page


def test_typing_filters_posts_and_albums_and_marks_the_hits(make_page):
    page = feed(make_page)
    everything = titles(page)
    assert len(everything) == 4
    page.fill("#fsearch", "madpakke")
    assert titles(page) == ["Motionsdag fredag"]
    assert page.locator("#feed mark").first.inner_text().lower() == "madpakke"
    assert page.inner_text("#fcount") == "1 af 4"
    page.fill("#fsearch", "")
    assert titles(page) == everything and page.inner_text("#fcount") == ""


def test_all_words_must_match_in_any_order_and_case(make_page):
    page = feed(make_page)
    page.fill("#fsearch", "SNOBRØD æblemost")                                     # albummets tekst
    assert titles(page) == ["Høstfest i børnehaven"]
    page.fill("#fsearch", "snobrød krabber")                                      # to forskellige kort: intet har begge
    assert titles(page) == []


def test_the_author_is_searched_too(make_page):
    page = feed(make_page)
    page.fill("#fsearch", "klasselæreren")
    assert titles(page) == ["Forældremøde i 6. klasse"] and page.locator("#feed .who mark").count() == 1


def test_search_respects_the_type_tabs_and_the_person_filter(make_page):
    page = feed(make_page)
    page.fill("#fsearch", "børnehaven")
    assert titles(page) == ["Høstfest i børnehaven"]
    page.click('[data-ft="post"]')
    assert titles(page) == [] and "Intet opslag eller billede matcher «børnehaven»" in page.inner_text("#feed")
    page.click('[data-ft="all"]')
    page.locator("#people button", has_text="Leo").click()                       # skjul Leo: albummet er hans
    assert titles(page) == []


def test_escape_clears_and_long_posts_are_expanded_while_searching(make_page):
    page = feed(make_page)
    page.evaluate("""state.data.posts[0].text = 'Fredag er det motionsdag. ' + 'Vi løber rundt om søen og spiser frugt bagefter. '.repeat(8) + 'Husk drikkedunk.'; renderAula()""")
    card = page.locator("#feed .card", has_text="Motionsdag fredag")
    assert card.locator(".txt.clamp").count() == 1 and card.locator(".more").count() == 1      # langt opslag: forkortet
    page.fill("#fsearch", "drikkedunk")
    assert card.locator(".txt.clamp").count() == 0 and card.locator(".more").count() == 0      # under søgning: hele teksten
    assert card.locator("mark", has_text="drikkedunk").count() == 1
    page.press("#fsearch", "Escape")
    assert page.input_value("#fsearch") == "" and len(titles(page)) == 4


def test_the_empty_result_says_what_is_searched(make_page):
    page = feed(make_page)
    page.fill("#fsearch", "zebra")
    t = page.inner_text("#feed")
    assert "matcher «zebra»" in t and "de 4 nyeste" in t and "ældre findes i Aula" in t
