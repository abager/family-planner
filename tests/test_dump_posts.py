"""Diagnose af læst-status på opslag (--dump-posts): rå data gemmes lokalt, struktur-filen må aldrig indeholde familiedata."""
import asyncio
from pathlib import Path

import httpx

import fetch_family as F
from tests.test_state_files import _hook_pattern

ROOT = Path(__file__).resolve().parents[1]

POST = {"id": 101, "title": "Tur til skoven", "content": {"html": "<p>Husk madpakke, Carla</p>"},
        "ownerProfile": {"fullName": "Lærer Anne"}, "isImportant": False, "isRead": True,
        "sharedWithGroups": [{"id": 7, "name": "3.B"}], "attachments": []}
NOTIF = {"notificationId": "n1", "notificationEventType": "PostSharedWithMe", "postId": 101, "isRead": False,
         "relatedChild": {"name": "Carla Bager"}}


class Child:
    def __init__(self, cid, code):
        self.id, self._raw = cid, {"institutionProfile": {"institutionCode": code}}


class Profile:
    children = [Child(11, "280123"), Child(12, "280123")]
    institution_profile_ids = [1, 11, 12]


class Client:
    api_url = "https://aula/api"

    def __init__(self, fail_notifications=False):
        self.fail, self.urls = fail_notifications, []

    async def get_profile(self):
        return Profile()

    async def _request_with_version_retry(self, method, url):
        assert method == "get"                                   # diagnosen læser kun
        self.urls.append(url)
        if "notifications" in url:
            if self.fail:
                return httpx.Response(500, request=httpx.Request("GET", url))
            body = {"data": [NOTIF]}
        else:
            body = {"data": {"posts": [POST, {**POST, "id": 102, "isRead": False}]}}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))


def run(monkeypatch, tmp_path, client):
    class Ctx:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *a):
            return False

    async def open_client(cfg):
        return Ctx()
    monkeypatch.setattr(F, "open_aula_client", open_client)
    return asyncio.run(F.dump_posts({"aula": {}}, limit=5, out_dir=tmp_path))


def test_dump_keeps_raw_data_and_the_shape_file_has_no_family_data(monkeypatch, tmp_path):
    client = Client()
    dump, shape = run(monkeypatch, tmp_path, client)
    assert "Husk madpakke" in dump.read_text("utf-8")              # rå data er der til os selv
    text = shape.read_text("utf-8")
    for secret in ("Tur til skoven", "madpakke", "Carla", "Lærer Anne", "3.B", "280123", "101", "PostSharedWithMe"):
        assert secret not in text, f"{secret!r} slap med i struktur-filen"
    assert "data.posts[].isRead  bool   (sand 1 / falsk 1)" in text  # bool-felter tælles – her ville læst-status vise sig
    assert "data.posts[].title  tekst" in text and "data[].postId  tal" in text
    posts_url, notif_url = client.urls
    assert "limit=5" in posts_url and posts_url.count("institutionProfileIds%5B%5D") == 3
    assert notif_url.count("activeChildrenIds%5B%5D") == 2 and notif_url.count("activeInstitutionCodes%5B%5D") == 1


def test_a_failing_call_is_recorded_and_does_not_hide_the_other(monkeypatch, tmp_path):
    dump, shape = run(monkeypatch, tmp_path, Client(fail_notifications=True))
    text = shape.read_text("utf-8")
    assert "fejl: HTTPStatusError" in text and "data.posts[].isRead" in text


def test_id_keys_are_not_leaked_in_paths():
    shape = F._json_shape({"byId": {"4711": {"seen": True}}})
    assert list(shape) == ["byId.<id>.seen"]


def test_dump_files_never_reach_git_or_docker():
    for name in ("aula_posts_dump.json", "aula_posts_struktur.txt"):
        for ignore in (".gitignore", ".dockerignore"):
            assert name in (ROOT / ignore).read_text("utf-8").splitlines()
        assert _hook_pattern().match(name)
