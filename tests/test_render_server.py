import json
from pathlib import Path

import pytest

from story_cut.export_openreel import build_openreel_edit
from story_cut.merge import KeptRange
from story_cut.project import clean_ranges
from story_cut.render import build_filter, cuts_from_edit, write_srt
from story_cut.transcribe import Transcript, Word
from tests.test_export import _media


def _doc(aspect="source"):
    ranges = [KeptRange(1, 3, [], ["speech"], fixed_label="speech"), KeptRange(6, 9, [], ["speech"], fixed_label="speech")]
    tr = Transcript("ko", "안녕 하세요", [Word("안녕", 1.2, 1.6), Word("하세요", 6.1, 6.5)])
    return build_openreel_edit(_media(), ranges, tr, aspect=aspect, mode="talk")


def test_vertical_export_and_cuts():
    doc = _doc("9:16")
    s = doc["project"]["settings"]
    assert (s["width"], s["height"]) == (1080, 1920)
    assert doc["project"]["media"][0]["metadata"]["width"] == 1920
    assert doc["project"]["name"].endswith("talk cut")
    cuts, subs, size, fit = cuts_from_edit(doc)
    assert [(c.start, c.end) for c in cuts] == [(1, 3), (6, 9)]
    assert size == (1080, 1920) and fit == "cover" and len(subs) >= 1
    f = build_filter(cuts, size, fit, has_audio=True, subtitles="x.srt")
    assert "concat=n=2:v=1:a=1" in f and "crop=1080:1920" in f and "subtitles=x.srt" in f
    assert doc["project"]["timeline"]["markers"][0]["label"].startswith("speech")


def test_landscape_filter_pads_and_no_audio():
    cuts, subs, size, fit = cuts_from_edit(_doc())
    f = build_filter(cuts, size, fit, has_audio=False, subtitles=None)
    assert "pad=1920:1080" in f and "atrim" not in f and "a=0" in f


def test_srt(tmp_path):
    p = write_srt([{"text": "b", "startTime": 3.5, "endTime": 4}, {"text": "a", "startTime": 0, "endTime": 1.25}], tmp_path / "s.srt")
    text = p.read_text(encoding="utf-8")
    assert text.startswith("1\n00:00:00,000 --> 00:00:01,250\na")


def test_clean_ranges():
    out = clean_ranges([{"start": 5, "end": 8}, {"start": -1, "end": 2}, {"start": 7, "end": 30}, {"start": 3, "end": 3.05}, {"x": 1}], 20)
    assert [(r["start"], r["end"]) for r in out] == [(0, 2), (5, 20)]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from story_cut.server import ServeOptions, create_app
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    vids = tmp_path / "videos"
    vids.mkdir()
    (vids / "a.mp4").write_bytes(b"x" * 2000)
    (vids / "notes.txt").write_text("no")
    app = create_app(ServeOptions(videos=vids, token="secret123", backend="mock"))
    return TestClient(app), vids


def test_server_requires_token(client):
    c, _ = client
    assert c.get("/").status_code == 200  # login page is public
    assert c.get("/api/videos").status_code == 401
    assert c.post("/api/login", json={"token": "wrong"}).status_code == 401
    assert c.post("/api/login", json={"token": "secret123"}).status_code == 200
    data = c.get("/api/videos").json()  # cookie set by login
    assert [v["name"] for v in data["videos"]] == ["a.mp4"]
    assert c.get("/api/info").json()["modes"][0]["name"] == "story"


def test_server_bearer_and_path_traversal(client):
    import base64
    c, vids = client
    h = {"Authorization": "Bearer secret123"}
    bad = base64.urlsafe_b64encode(b"../outside.mp4").decode().rstrip("=")
    (vids.parent / "outside.mp4").write_bytes(b"y")
    assert c.get(f"/api/videos/{bad}/file", headers=h).status_code == 404
    r = c.post("/api/upload?name=..%2F..%2Fhack.exe", content=b"z" * 100, headers=h)
    assert r.status_code == 200
    assert r.json()["path"] == "uploads/hack.mp4"
    assert (vids / "uploads" / "hack.mp4").is_file()
    r = c.post("/api/jobs", json={"video": "bm9wZQ", "mode": "story"}, headers=h)
    assert r.status_code == 404
    first = c.get("/api/videos", headers=h).json()["videos"][0]["id"]
    r = c.post("/api/jobs", json={"video": first, "mode": "bogus"}, headers=h)
    assert r.status_code == 400


def test_token_query_sets_cookie_and_redirects(client):
    c, _ = client
    r = c.get("/?token=secret123", follow_redirects=False)
    assert r.status_code == 303 and "sc_token" in r.headers.get("set-cookie", "")
