"""Sidecar file next to edit.json so a result can be reviewed, trimmed and rebuilt later
(used by `story-cut serve` and `story-cut render`)."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from story_cut.fftool import MediaInfo
from story_cut.merge import AnalyzedShot, KeptRange
from story_cut.modes import ModePreset, PromptPlan

SIDECAR_VERSION = 1


def sidecar_path(edit_path: Path) -> Path:
    return edit_path.with_suffix(".story-cut.json")


def write_sidecar(
    edit_path: Path,
    *,
    video: Path,
    cache_root: Path,
    preset: ModePreset,
    plan: PromptPlan | None,
    ranges: list[KeptRange],
    shots: list[AnalyzedShot],
    media: MediaInfo,
) -> Path:
    data = {
        "format": "story-cut-result",
        "version": SIDECAR_VERSION,
        "video": str(video),
        "cache_dir": str(cache_root),
        "edit": str(edit_path),
        "mode": preset.name,
        "mode_title": preset.title,
        "aspect": preset.aspect,
        "max_total": preset.max_total,
        "plan": asdict(plan) if plan else None,
        "media": {"duration": media.duration, "width": media.width, "height": media.height},
        "ranges": [
            {
                "start": round(r.start, 3),
                "end": round(r.end, 3),
                "label": r.primary_label,
                "confidence": round(r.confidence, 3),
                "reason": r.reason[:300],
            }
            for r in ranges
        ],
        "shots": [
            {
                "index": s.shot.index,
                "start": round(s.shot.start, 3),
                "end": round(s.shot.end, 3),
                "label": s.classification.label,
                "confidence": round(s.classification.confidence, 3),
                "keep": bool(s.keep),
                "reason": s.classification.reason[:200],
                "loudness": None if s.loudness is None else round(s.loudness, 1),
            }
            for s in shots
        ],
    }
    path = sidecar_path(edit_path)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_sidecar(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != "story-cut-result":
        raise ValueError("story-cut 결과 파일이 아닙니다.")
    return data


def clean_ranges(items: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    """Validate ranges coming from the phone UI: clamp, sort, drop empty, merge overlaps."""
    cleaned: list[dict[str, Any]] = []
    for item in items:
        try:
            start = max(0.0, float(item["start"]))
            end = min(float(duration), float(item["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if end - start < 0.1:
            continue
        cleaned.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "label": str(item.get("label") or "manual")[:40],
            "confidence": float(item.get("confidence") or 1.0),
            "reason": str(item.get("reason") or "")[:300],
        })
    cleaned.sort(key=lambda r: r["start"])
    merged: list[dict[str, Any]] = []
    for item in cleaned:
        if merged and item["start"] < merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], item["end"])
        else:
            merged.append(item)
    return merged


def rebuild_edit(sidecar_file: Path, ranges: list[dict[str, Any]], *, aspect: str | None = None) -> Path:
    """Write edit.json again from edited ranges (subtitles are re-mapped from the cached transcript)."""
    from story_cut.export_openreel import build_openreel_edit
    from story_cut.fftool import probe
    from story_cut.transcribe import empty_transcript, transcript_from_json

    data = read_sidecar(sidecar_file)
    video = Path(data["video"])
    media = probe(video)
    ranges = clean_ranges(ranges, media.duration)
    tr_path = Path(data.get("cache_dir") or "") / "transcript.json"
    transcript = empty_transcript()
    if tr_path.is_file():
        transcript = transcript_from_json(json.loads(tr_path.read_text(encoding="utf-8")))
    kept = [
        KeptRange(r["start"], r["end"], [], [r["label"]], fixed_label=r["label"], fixed_reason=r["reason"])
        for r in ranges
    ]
    aspect = aspect or data.get("aspect") or "source"
    edit_path = Path(data["edit"])
    doc = build_openreel_edit(media, kept, transcript, aspect=aspect, mode=data.get("mode") or "story")
    edit_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    data["ranges"] = ranges
    data["aspect"] = aspect
    data["edited"] = True
    Path(sidecar_file).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return edit_path
