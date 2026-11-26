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


def test_scaled_size_for_phone_render():
    from story_cut.render import scaled_size
    assert scaled_size((1920, 1080), 720) == (1280, 720)
    assert scaled_size((1080, 1920), 720) == (720, 1280)
    assert scaled_size((1280, 720), 720) == (1280, 720)  # already small
    assert scaled_size((1921, 1081), None) == (1920, 1080)


def test_ip_kind_labels():
    from story_cut.server import ip_kind
    assert ip_kind("100.101.102.103") == "Tailscale"
    assert ip_kind("192.168.0.12") == "같은 와이파이"
    assert ip_kind("59.9.73.108") == "공인 IP"


def test_output_download_headers_and_range(client):
    import time
    from story_cut.server import Job
    c, _ = client
    h = {"Authorization": "Bearer secret123"}
    m = c.app.state.manager
    job = Job(id="20260101-000000-abcd", video="x", video_name="방송.mp4", mode="story", prompt=None,
              max_total=None, created=time.time(), status="done")
    m.jobs[job.id] = job
    assert c.get(f"/api/jobs/{job.id}/output", headers=h).status_code == 404  # not rendered yet
    d = m.job_dir(job.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "방송-edit-small.mp4").write_bytes(bytes(range(256)) * 40)
    job.render = {"status": "done", "file": "방송-edit-small.mp4", "size": 10240}
    r = c.get(f"/api/jobs/{job.id}/output", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    assert r.headers["content-disposition"].startswith("attachment;")
    assert "%EB%B0%A9%EC%86%A1" in r.headers["content-disposition"]  # Korean name survives
    assert r.headers["accept-ranges"] == "bytes"
    r = c.get(f"/api/jobs/{job.id}/output?inline=1", headers=h)
    assert r.headers["content-disposition"].startswith("inline;")
    r = c.get(f"/api/jobs/{job.id}/output", headers={**h, "Range": "bytes=256-511"})
    assert r.status_code == 206 and r.content == bytes(range(256))
    assert c.get(f"/api/jobs/{job.id}/output").status_code == 401
