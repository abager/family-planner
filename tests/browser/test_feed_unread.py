"""Ulæste opslag i feedet: blå prik og kant, tal på Feed-knappen, filteret "Ulæste opslag" og "Markér som læst",
som gemmes på serveren for hele husstanden. Kun manuelt – at vise eller åbne et opslag markerer det ikke."""
import datetime as dt

from data import family
from fake_server import FakeServer

NOW = dt.datetime(2026, 10, 15, 12, 0)


def post(n, unread, hours_ago=1):
    ts = (NOW - dt.timedelta(hours=hours_ago)).astimezone().isoformat()
    return {"id": f"post:{n}", "title": f"Opslag {n}", "author": "Lærer", "timestamp": ts, "important": False,
            "text": "Kort besked fra skolen.", "images": [], "attachments": 0, "people": ["family"], "unread": unread}


def server_with(posts):
    f = FakeServer()
    f.family, f.server = family([], posts=posts), True
    return f


def open_feed(make_page, fake):
    page, fake, opened = make_page(fake, now=NOW)
    page.click("#v-aula")
    page.wait_for_timeout(200)
    return page, fake, opened


def unread_titles(page):
    return page.evaluate("[...document.querySelectorAll('#feed .card.post.unread h3')].map(h=>h.innerText)")


def test_unread_posts_are_marked_and_counted_and_showing_them_marks_nothing(make_page):
    page, fake, opened = open_feed(make_page, server_with([post(3, True, 1), post(2, True, 2), post(1, False, 3)]))
    assert unread_titles(page) == ["Opslag 3", "Opslag 2"]
    assert page.inner_text("#feedCount") == "2"
    page.click("#feed .card h3")                                                                # at trykke på opslaget er ikke at markere det
    page.wait_for_timeout(3500)
    assert fake.posts("/api/posts/read") == [] and page.inner_text("#feedCount") == "2"     # kun manuel markering
    assert not opened.errors


def test_mark_as_read_saves_on_the_server_and_updates_the_card_and_badge(make_page):
    page, fake, opened = open_feed(make_page, server_with([post(2, True), post(1, True, 2)]))
    page.locator("#feed [data-pread]").first.click()
    page.wait_for_timeout(400)
    assert [b["ids"] for b in fake.posts("/api/posts/read")] == [["post:2"]]
    assert unread_titles(page) == ["Opslag 1"] and page.inner_text("#feedCount") == "1"
    assert page.locator('#feed [data-pid="post:2"] [data-pread]').count() == 0                 # knappen er væk – kan ikke fortrydes
    page.locator("#feed [data-pread]").first.click()
    page.wait_for_timeout(400)
    assert page.locator("#feedCount").is_hidden()
    assert not opened.errors


def test_the_unread_filter_shows_only_unread_posts_and_empties_as_they_are_read(make_page):
    page, fake, _ = open_feed(make_page, server_with([post(2, True), post(1, False, 2)]))
    page.click('[data-ft="unread"]')
    assert page.evaluate("[...document.querySelectorAll('#feed .card h3')].map(h=>h.innerText)") == ["Opslag 2"]
    page.locator("#feed [data-pread]").click()
    page.wait_for_timeout(500)
    assert "Ingen ulæste opslag" in page.inner_text("#feed")


def test_a_failed_save_puts_the_post_back_as_unread_and_says_so(make_page):
    fake = server_with([post(1, True)])
    fake.fail_posts_read = True
    page, fake, _ = open_feed(make_page, fake)
    page.locator("#feed [data-pread]").click()
    page.wait_for_timeout(500)
    assert unread_titles(page) == ["Opslag 1"] and page.inner_text("#feedCount") == "1"
    assert "står stadig som ulæst" in page.inner_text("#toast")
