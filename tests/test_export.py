import json
from pathlib import Path

from story_cut.classify import Classification, parse_classification
from story_cut.export_openreel import build_openreel_edit
from story_cut.fftool import MediaInfo
from story_cut.merge import AnalyzedShot, apply_keep, merge_kept
from story_cut.scenes import Shot
from story_cut.transcribe import Transcript, Word


def _media() -> MediaInfo:
    return MediaInfo(
        path=Path("gameplay.mp4"),
        duration=12.0,
        width=1920,
        height=1080,
        frame_rate=30.0,
        video_codec="h264",
        audio_codec="aac",
        sample_rate=48000,
        channels=2,
        file_size=123456,
        has_video=True,
        has_audio=True,
        last_modified_ms=1_700_000_000_000,
    )


def test_openreel_edit_shape_and_duration_sync():
    shots = [
        AnalyzedShot(Shot(0, 0, 3), Classification("cutscene", 0.9, "c"), "hello there"),
        AnalyzedShot(Shot(1, 3, 6), Classification("combat", 0.8, "fight"), ""),
        AnalyzedShot(Shot(2, 6, 9), Classification("dialogue", 0.95, "talk"), "story"),
    ]
    apply_keep(shots, {"cutscene", "dialogue"}, 0.0)
    ranges = merge_kept(shots, pad=0.0, media_duration=12.0)
    tr = Transcript(language="en", text="hello there story", words=[
        Word("hello", 0.2, 0.6),
        Word("there", 0.7, 1.1),
        Word("story", 6.2, 6.8),
    ])
    doc = build_openreel_edit(_media(), ranges, tr, project_name="Test Cut")
    assert doc["format"] == "openreel-edit"
    assert doc["version"] == 3
    project = doc["project"]
    assert project["name"] == "Test Cut"
    assert set(project["settings"]) == {"width", "height", "frameRate", "sampleRate", "channels"}
    media = project["media"][0]
    assert media["sourceFile"]["name"] == "gameplay.mp4"
    assert media["metadata"]["duration"] == 12.0
    clips = project["timeline"]["tracks"][0]["clips"]
    assert len(clips) == 2
    # clip 0: source 0-3, output 0-3; clip 1: source 6-9, output 3-6
    assert clips[0]["startTime"] == 0
    assert clips[0]["inPoint"] == 0
    assert clips[0]["outPoint"] == 3
    assert abs(clips[0]["duration"] - (clips[0]["outPoint"] - clips[0]["inPoint"])) < 0.05
    assert clips[1]["startTime"] == 3
    assert clips[1]["inPoint"] == 6
    assert clips[1]["outPoint"] == 9
    assert abs(clips[1]["duration"] - 3) < 0.05
    for clip in clips:
        t = clip["transform"]
        assert {"position", "scale", "rotation", "anchor", "opacity"} <= set(t)
        assert clip["effects"] == []
        assert clip["audioEffects"] == []
        assert clip["keyframes"] == []
    assert project["textClips"] == []
    assert project["timeline"]["duration"] == 6
    markers = project["timeline"]["markers"]
    assert len(markers) == 2
    assert markers[0]["kind"] == "segmentation"
    subs = project["timeline"]["subtitles"]
    texts = " ".join(s["text"] for s in subs)
    assert "hello" in texts and "story" in texts
    ids = [media["id"], project["timeline"]["tracks"][0]["id"], project["id"]]
    ids += [c["id"] for c in clips]
    ids += [m["id"] for m in markers]
    ids += [s["id"] for s in subs]
    assert len(ids) == len(set(ids))
    # Write and ensure JSON is serializable
    raw = json.dumps(doc)
    assert "openreel-edit" in raw


def test_parse_classification_from_fenced_json():
    text = """```json
    {"label": "Cutscene", "confidence": 1.2, "reason": "cinematic"}
    ```"""
    result = parse_classification(text)
    assert result.label == "cutscene"
    assert result.confidence == 1.0
