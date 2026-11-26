import json
from pathlib import Path

from story_cut.export_openreel import build_openreel_edit
from story_cut.portable import fix_openreel_edit_file, sanitize_portable_edit

from test_export import _media
from story_cut.classify import Classification
from story_cut.merge import AnalyzedShot, apply_keep, merge_kept
from story_cut.scenes import Shot
from story_cut.transcribe import Transcript


def _sample_doc():
    shots = [AnalyzedShot(Shot(0, 0, 3), Classification("cutscene", 0.9, "c"), "hello")]
    apply_keep(shots, {"cutscene"}, 0.0)
    ranges = merge_kept(shots, pad=0.0, media_duration=12.0)
    return build_openreel_edit(_media(), ranges, Transcript(language="en", text="", words=[]), project_name="Test")


def test_sanitize_strips_source_color_and_other_runtime_keys():
    doc = _sample_doc()
    doc["project"]["media"][0]["metadata"]["sourceColor"] = {
        "primaries": "bt709",
        "transfer": "bt709",
        "matrix": "bt709",
        "fullRange": False,
    }
    doc["project"]["media"][0]["metadata"]["pixelAspectRatio"] = 1
    doc["project"]["media"][0]["metadata"]["startTimecode"] = 0
    cleaned = sanitize_portable_edit(doc)
    meta = cleaned["project"]["media"][0]["metadata"]
    assert "sourceColor" not in meta
    assert "pixelAspectRatio" not in meta
    assert "startTimecode" not in meta
    for key in ("duration", "width", "height", "frameRate", "codec", "sampleRate", "channels", "fileSize", "hasVideo", "hasAudio"):
        assert key in meta


def test_build_openreel_edit_metadata_stays_in_portable_schema():
    meta = _sample_doc()["project"]["media"][0]["metadata"]
    allowed = {
        "duration", "width", "height", "frameRate", "codec", "sampleRate",
        "channels", "fileSize", "audioTrackCount", "audioStreamChannels",
        "hasVideo", "hasAudio",
    }
    assert set(meta) <= allowed


def test_fix_json_cli_roundtrip(tmp_path: Path):
    src = tmp_path / "in.json"
    dest = tmp_path / "out.json"
    dirty = _sample_doc()
    dirty["project"]["media"][0]["metadata"]["sourceColor"] = {"primaries": "bt709"}
    src.write_text(json.dumps(dirty), encoding="utf-8")
    written = fix_openreel_edit_file(src, dest)
    assert written == dest.resolve()
    out = json.loads(dest.read_text(encoding="utf-8"))
    assert "sourceColor" not in out["project"]["media"][0]["metadata"]
    assert out["format"] == "openreel-edit"
    assert out["project"]["media"][0]["metadata"]["codec"] == "h264"
