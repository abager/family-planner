"""Læst/ulæst på opslag (posts_read.py): appens egen, fælles status. Alt eksisterende er læst, når funktionen slås til;
nye opslag er ulæste, til nogen markerer dem. Intet skrives til Aula."""
import datetime as dt
import json

import posts_read as R

T0 = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.timezone.utc)


def post(n, hours):
    return {"id": f"post:{n}", "timestamp": (T0 + dt.timedelta(hours=hours)).isoformat()}


def test_everything_already_in_the_feed_is_read_when_the_feature_starts(tmp_path):
    store, posts = R.Store(tmp_path / R.FILE), [post(1, -5), post(2, -1)]
    assert store.apply(posts, now=T0) == 0
    assert [p["unread"] for p in posts] == [False, False]
    assert json.loads((tmp_path / R.FILE).read_text("utf-8"))["since"] == T0.isoformat(timespec="seconds")


def test_new_posts_are_unread_until_someone_marks_them(tmp_path):
    store = R.Store(tmp_path / R.FILE)
    store.apply([post(1, -5)], now=T0)
    posts = [post(3, 2), post(2, 1), post(1, -5)]
    assert store.apply(posts) == 2 and [p["unread"] for p in posts] == [True, True, False]
    assert store.mark(["post:2"], known={"post:1", "post:2", "post:3"}) == ["post:2"]
    assert store.apply(posts) == 1 and [p["unread"] for p in posts] == [True, False, False]


def test_an_older_post_that_shows_up_later_is_not_suddenly_new(tmp_path):
    store = R.Store(tmp_path / R.FILE)
    store.apply([post(5, -1)], now=T0)
    older = [post(4, -48)]                     # fx når der hentes flere opslag end før
    assert store.apply(older) == 0 and older[0]["unread"] is False


def test_no_baseline_is_set_while_there_are_no_posts(tmp_path):
    store = R.Store(tmp_path / R.FILE)
    assert store.apply([], now=T0) == 0 and not (tmp_path / R.FILE).exists()   # fx Aula nede ved første kørsel
    later = [post(1, -3)]
    store.apply(later, now=T0)
    assert later[0]["unread"] is False


def test_only_posts_we_have_fetched_can_be_marked_and_marks_are_not_duplicated(tmp_path):
    store = R.Store(tmp_path / R.FILE)
    store.apply([post(1, -1)], now=T0)
    assert store.mark(["post:1", "post:999", "post:1"], known={"post:1"}) == ["post:1"]
    assert store.mark(["post:1"], known={"post:1"}) == []
    assert json.loads((tmp_path / R.FILE).read_text("utf-8"))["read"] == ["post:1"]


def test_the_list_of_read_ids_stays_bounded_and_keeps_the_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "MAX_IDS", 3)
    store = R.Store(tmp_path / R.FILE)
    store.apply([post(1, -1)], now=T0)
    ids = [f"post:{n}" for n in (10, 2, 33, 4)]
    store.mark(ids, known=set(ids))
    assert store.load()["read"] == ["post:4", "post:10", "post:33"]


def test_a_broken_state_file_starts_over_instead_of_failing(tmp_path):
    (tmp_path / R.FILE).write_text("{ikke json")
    posts = [post(1, -1)]
    assert R.Store(tmp_path / R.FILE).apply(posts, now=T0) == 0 and posts[0]["unread"] is False


def test_patch_family_marks_posts_read_right_away(tmp_path):
    fam = tmp_path / "family.json"
    fam.write_text(json.dumps({"posts": [{"id": "post:1", "unread": True}, {"id": "post:2", "unread": True}], "messages": []}))
    R.patch_family(fam, ["post:2"])
    assert [p["unread"] for p in json.loads(fam.read_text("utf-8"))["posts"]] == [True, False]
