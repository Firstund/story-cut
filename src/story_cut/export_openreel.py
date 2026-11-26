from __future__ import annotations

from pathlib import Path

from story_cut.fftool import MediaInfo
from story_cut.ids import make_id
from story_cut.portable import sanitize_portable_edit
from story_cut.merge import KeptRange, cue_groups, words_in_ranges
from story_cut.transcribe import Transcript

LABEL_COLORS = {
    "cutscene": "#F59E0B",
    "dialogue": "#38BDF8",
    "combat": "#F43F5E",
    "exploration": "#34D399",
    "menu": "#A78BFA",
    "other": "#94A3B8",
    "speech": "#22D3EE",
}

ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}

DEFAULT_TRANSFORM = {
    "position": {"x": 0.5, "y": 0.5},
    "scale": {"x": 1, "y": 1},
    "rotation": 0,
    "anchor": {"x": 0.5, "y": 0.5},
    "opacity": 1,
    "fitMode": "contain",
}


def _round(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def build_openreel_edit(
    media: MediaInfo,
    ranges: list[KeptRange],
    transcript: Transcript,
    *,
    project_name: str | None = None,
    aspect: str = "source",
    mode: str = "story",
) -> dict:
    """Build an openreel-edit v3 document. aspect "9:16" makes a vertical project with a centre crop."""
    media_id = make_id("media")
    track_id = make_id("track")
    project_id = make_id("project")

    transform = dict(DEFAULT_TRANSFORM)
    if aspect in ASPECTS and aspect != "16:9":
        transform["fitMode"] = "cover"
    clips = []
    markers = []
    cursor = 0.0
    for index, item in enumerate(ranges):
        duration = _round(item.duration)
        if duration < 0.001:
            continue
        in_point = _round(item.start)
        out_point = _round(item.start + duration)
        # Keep duration in sync with the source range (speed = 1).
        if abs((out_point - in_point) - duration) > 0.0001:
            duration = _round(out_point - in_point)
        clip_id = make_id("clip")
        start_time = _round(cursor)
        clips.append({
            "id": clip_id,
            "mediaId": media_id,
            "trackId": track_id,
            "startTime": start_time,
            "duration": duration,
            "inPoint": in_point,
            "outPoint": out_point,
            "effects": [],
            "audioEffects": [],
            "transform": transform,
            "volume": 1,
            "keyframes": [],
        })
        label = item.primary_label
        color = LABEL_COLORS.get(label.split("+")[0], "#94A3B8")
        markers.append({
            "id": make_id("mk"),
            "time": start_time,
            "label": f"{label} ({item.confidence:.2f})",
            "color": color,
            "duration": duration,
            "comment": f"source {item.start:.2f}-{item.end:.2f}s; {item.reason}",
            "kind": "segmentation",
        })
        cursor += duration

    mapped = words_in_ranges(transcript, ranges)
    subtitles = []
    for text, start, end in cue_groups(mapped):
        if end - start < 0.05 or not text.strip():
            continue
        subtitles.append({
            "id": make_id("sub"),
            "text": text.strip(),
            "startTime": _round(start),
            "endTime": _round(max(start + 0.05, end)),
        })

    timeline_duration = _round(cursor)
    src_width = max(16, min(8192, media.width or 1920))
    src_height = max(16, min(8192, media.height or 1080))
    width, height = ASPECTS.get(aspect, (src_width, src_height))
    frame_rate = media.frame_rate if 1 <= media.frame_rate <= 120 else 30
    sample_rate = media.sample_rate if 8000 <= media.sample_rate <= 192000 else 48000
    channels = media.channels if 1 <= media.channels <= 8 else (2 if media.has_audio else 2)

    doc = {
        "format": "openreel-edit",
        "version": 3,
        "project": {
            "id": project_id,
            "name": project_name or f"{media.path.stem} {mode} cut",
            "settings": {
                "width": width,
                "height": height,
                "frameRate": frame_rate,
                "sampleRate": sample_rate,
                "channels": channels,
            },
            "media": [{
                "id": media_id,
                "name": media.filename,
                "type": "video",
                "metadata": {
                    "duration": _round(media.duration),
                    "width": src_width,
                    "height": src_height,
                    "frameRate": frame_rate,
                    "codec": media.video_codec or "unknown",
                    "sampleRate": sample_rate,
                    "channels": channels,
                    "fileSize": media.file_size,
                    "hasVideo": True,
                    "hasAudio": media.has_audio,
                },
                "sourceFile": {
                    "name": media.filename,
                    "size": media.file_size,
                    "lastModified": media.last_modified_ms,
                },
            }],
            "timeline": {
                "tracks": [{
                    "id": track_id,
                    "name": "Main",
                    "type": "video",
                    "locked": False,
                    "hidden": False,
                    "muted": False,
                    "solo": False,
                    "clips": clips,
                    "transitions": [],
                }],
                "subtitles": subtitles,
                "markers": markers,
                "duration": timeline_duration,
            },
            "textClips": [],
        },
    }
    return sanitize_portable_edit(doc)
