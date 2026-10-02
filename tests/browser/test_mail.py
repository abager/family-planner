"""Beskeder: læst-markering efter 3 sekunder, stryg for at markere, og private tråde bag en kode."""
from data import family, message
from fake_server import FakeServer


def server_with(messages, **kw):
    f = FakeServer()
    f.family, f.server = family(messages), True
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def unread(page):
    return page.evaluate("[...document.querySelectorAll('.mrow.unread')].length")


def reads(fake):
    return [b["ids"] for b in fake.posts("/api/messages/read")]


def swipe(cdp, pts):
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": pts[0][0], "y": pts[0][1]}]})
    for x, y in pts[1:]:
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y}]})
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})


def unread_inbox(n=14, unread_n=8):
    return [message(i, unread=i < unread_n) for i in range(n)]


# ---------------------------------------------------------------- læst efter 3 sekunder
def test_a_message_is_read_only_after_three_seconds_of_actually_reading_it(make_page):
    page, fake, _ = make_page(server_with(unread_inbox()), touch=True)
    page.click("#v-mail")
    page.wait_for_timeout(3500)
    assert unread(page) == 8 and reads(fake) == []                  # den automatisk valgte første besked tæller ikke
    rows = page.locator(".mrow")
    rows.nth(2).click()
    page.wait_for_timeout(1500)
    assert unread(page) == 8 and reads(fake) == []                  # 1,5 s er ikke nok
    page.wait_for_timeout(2200)
    assert unread(page) == 7 and len(reads(fake)) == 1
    assert page.inner_text("#unreadCount") == "7"


def test_switching_message_quickly_marks_only_the_last_one(make_page):
    page, fake, _ = make_page(server_with(unread_inbox()))
    page.click("#v-mail")
    rows = page.locator(".mrow")
    rows.nth(3).click()
    page.wait_for_timeout(1200)
    rows.nth(4).click()
    page.wait_for_timeout(3600)
    assert reads(fake) == [["msg:4"]] and unread(page) == 7


def test_the_mark_read_button_marks_immediately(make_page):
    page, fake, _ = make_page(server_with(unread_inbox()))
    page.click("#v-mail")
    page.locator(".mrow").nth(5).click()
    page.wait_for_timeout(300)
    assert page.locator("[data-markread]").count() == 1 and page.locator(".upill").count() == 1
    page.click("[data-markread]")
    page.wait_for_timeout(400)
    assert reads(fake)[-1] == ["msg:5"] and page.locator(".upill").count() == 0 and unread(page) == 7


def test_swiping_a_row_reveals_read_and_a_long_swipe_marks_it(make_page):
    page, fake, _ = make_page(server_with(unread_inbox()), touch=True, height=820)
    page.click("#v-mail")
    page.wait_for_timeout(300)
    cdp = page.context.new_cdp_session(page)
    rows = page.locator(".mrow")
    sel = page.evaluate("state.mail.sel")

    def box(i):
        b = rows.nth(i).bounding_box()
        return b["x"] + b["width"] - 20, b["y"] + b["height"] / 2

    x, y = box(6)
    swipe(cdp, [(x, y)] + [(x - d, y) for d in range(8, 121, 8)])
    page.wait_for_timeout(400)
    li = page.locator(".mswipe").nth(6)
    assert li.evaluate("e=>e.classList.contains('open')") and li.evaluate("e=>e.dataset.unread") == "1" and page.evaluate("state.mail.sel") == sel
    li.locator(".mactbtn").click()
    page.wait_for_timeout(500)
    assert li.evaluate("e=>e.dataset.unread") == "0" and reads(fake)[-1] == ["msg:6"]
    x, y = box(7)
    swipe(cdp, [(x, y)] + [(x - d, y) for d in range(10, 261, 10)])
    page.wait_for_timeout(800)
    assert reads(fake)[-1] == ["msg:7"] and page.evaluate("state.mail.sel") == sel
    # lodret rulning åbner ingen række
    x, y = box(1)
    swipe(cdp, [(x, y)] + [(x - 3, y - d) for d in range(10, 121, 10)])
    page.wait_for_timeout(300)
    assert page.locator(".mswipe.open").count() == 0


def test_on_a_phone_the_reader_covers_the_list_and_back_does_not_mark_read(make_page):
    page, fake, _ = make_page(server_with(unread_inbox()), device="iPhone 13")
    page.locator("#v-mail").tap()
    page.wait_for_timeout(300)
    reading = lambda: page.evaluate("document.getElementById('mail').classList.contains('reading')")
    assert not reading()
    page.locator(".mrow").nth(3).tap()
    page.wait_for_timeout(300)
    assert reading() and unread(page) == 8
    page.locator("[data-back]").tap()                                # tilbage efter 0,3 s: ikke læst
    page.wait_for_timeout(3600)
    assert reads(fake) == [] and unread(page) == 8


# ---------------------------------------------------------------- private tråde
FULL = message(3, private=True, subject="Samtale om Hugo", text="Utryghed i 6B. Ring på 12345678.", sender="Lærer Anders")


def private_server(**kw):
    msgs = [message(i) for i in range(3)] + [message(3, redacted=True, unread=True)] + [message(i) for i in range(4, 8)]
    return server_with(msgs, private_full=[FULL], **kw)


def open_private(page):
    page.click("#v-mail")
    page.wait_for_timeout(300)


def dom_text(page):
    return page.evaluate("document.body.innerText + document.documentElement.innerHTML")


def test_locked_private_threads_show_no_content_anywhere_in_the_page(make_page):
    page, fake, _ = make_page(private_server())
    page.click("#v-mail")
    page.wait_for_timeout(300)
    html = dom_text(page)
    assert "Utryghed" not in html and "12345678" not in html and "Samtale om Hugo" not in html
    assert "Privat samtale" in page.inner_text("#mitems") and "Låst" in page.inner_text("#mitems")
    assert not any(p == "/api/private" for (_, p, _) in fake.calls if _ != "POST")      # indholdet er ikke hentet


def test_wrong_code_is_refused_and_the_right_one_shows_the_thread(make_page):
    page, fake, _ = make_page(private_server())
    open_private(page)
    page.locator('.mrow[data-mid="msg:3"]').click()
    page.click("[data-reveal-mail]")
    page.wait_for_timeout(300)
    assert page.evaluate("privDlg.open")
    page.fill("#privCode", "0000")
    page.click("#privOk")
    page.wait_for_timeout(300)
    assert page.inner_text("#privErr") == "Forkert kode." and page.evaluate("privDlg.open") and "Utryghed" not in dom_text(page)
    page.fill("#privCode", "4711")
    page.click("#privOk")
    page.wait_for_timeout(500)
    assert not page.evaluate("privDlg.open")
    assert "Utryghed i 6B" in page.inner_text("#reader") and "Samtale om Hugo" in page.inner_text("#mitems")


def test_private_threads_lock_again_when_you_leave_messages(make_page):
    page, fake, _ = make_page(private_server())
    open_private(page)
    page.locator('.mrow[data-mid="msg:3"]').click()
    page.click("[data-reveal-mail]")
    page.fill("#privCode", "4711")
    page.click("#privOk")
    page.wait_for_timeout(500)
    assert "Utryghed" in dom_text(page)
    page.click("#v-today")
    page.wait_for_timeout(300)
    assert "Utryghed" not in dom_text(page) and fake.unlocked is False                 # låst i hukommelsen og på serveren
    assert fake.posts("/api/private/lock")
    page.click("#v-mail")
    page.wait_for_timeout(300)
    assert "Utryghed" not in dom_text(page)


def test_private_threads_lock_by_themselves_when_the_time_runs_out(make_page):
    page, fake, _ = make_page(private_server(expires=1))
    open_private(page)
    page.locator('.mrow[data-mid="msg:3"]').click()
    page.click("[data-reveal-mail]")
    page.fill("#privCode", "4711")
    page.click("#privOk")
    page.wait_for_timeout(400)
    assert "Utryghed" in dom_text(page)
    page.wait_for_timeout(1400)
    assert "Utryghed" not in dom_text(page) and "låst igen" in page.inner_text("#toast")


def test_private_threads_lock_when_the_screen_goes_away(make_page):
    page, fake, _ = make_page(private_server())
    open_private(page)
    page.locator('.mrow[data-mid="msg:3"]').click()
    page.click("[data-reveal-mail]")
    page.fill("#privCode", "4711")
    page.click("#privOk")
    page.wait_for_timeout(400)
    page.evaluate("Object.defineProperty(document,'visibilityState',{value:'hidden',configurable:true});document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(200)
    assert "Utryghed" not in dom_text(page) and fake.unlocked is False


def test_without_a_code_on_the_server_the_dialog_explains_what_to_set(make_page):
    page, fake, _ = make_page(private_server(private_configured=False))
    open_private(page)
    page.locator('.mrow[data-mid="msg:3"]').click()
    page.click("[data-reveal-mail]")
    page.fill("#privCode", "hvad som helst")
    page.click("#privOk")
    page.wait_for_timeout(300)
    assert "FAMILIEPLAN_PRIVATE_CODE" in page.inner_text("#privErr")


def test_new_data_while_unlocked_keeps_the_thread_open_instead_of_hiding_it_again(make_page):
    page, fake, _ = make_page(private_server())
    open_private(page)
    page.locator('.mrow[data-mid="msg:3"]').click()
    page.click("[data-reveal-mail]")
    page.fill("#privCode", "4711")
    page.click("#privOk")
    page.wait_for_timeout(400)
    page.evaluate("load()")                                                           # den regelmæssige hentning, som family.json er udtømt igen
    page.wait_for_timeout(600)
    assert "Utryghed" in dom_text(page)


def test_without_a_server_private_threads_in_plain_data_still_work_as_before(make_page):
    msgs = [message(i) for i in range(3)] + [message(3, private=True, subject="Samtale", text="Fortroligt indhold", sender="Lærer Anders")]
    f = FakeServer()
    f.family = family(msgs)                                          # statisk brug, ingen /api/status, ingen redacted
    page, *_ = make_page(f)
    open_private(page)
    page.locator('.mrow[data-mid="msg:3"]').click()
    page.click("[data-reveal-mail]")
    page.wait_for_timeout(300)
    assert not page.evaluate("privDlg.open") and "Fortroligt indhold" in page.inner_text("#reader")
