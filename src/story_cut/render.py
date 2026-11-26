"""Render an openreel-edit JSON straight to MP4 on the PC with ffmpeg.

Handy from a phone: review/trim in the browser, then let the PC make the full-quality file.
Supports what story-cut produces (one source video, cuts, subtitles, 16:9 or 9:16 crop).
Effects, transitions and extra tracks made later in OpenReel are ignored; use OpenReel's
own export for those.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from story_cut.fftool import FfmpegError, probe


@dataclass
class Cut:
    start: float  # source seconds
    end: float


def cuts_from_edit(doc: dict[str, Any]) -> tuple[list[Cut], list[dict[str, Any]], tuple[int, int], str]:
    project = doc.get("project") or {}
    settings = project.get("settings") or {}
    tracks = (project.get("timeline") or {}).get("tracks") or []
    video_tracks = [t for t in tracks if t.get("type") == "video" and not t.get("hidden")]
    if not video_tracks:
        raise ValueError("영상 트랙이 없습니다.")
    clips = sorted(video_tracks[0].get("clips") or [], key=lambda c: float(c.get("startTime") or 0))
    cuts: list[Cut] = []
    fit = "contain"
    for clip in clips:
        start = float(clip.get("inPoint") or 0)
        end = float(clip.get("outPoint") or start + float(clip.get("duration") or 0))
        if end - start > 0.04:
            cuts.append(Cut(start, end))
        fit = ((clip.get("transform") or {}).get("fitMode")) or fit
    if not cuts:
        raise ValueError("남긴 구간이 없습니다.")
    subs = list((project.get("timeline") or {}).get("subtitles") or [])
    size = (int(settings.get("width") or 1920), int(settings.get("height") or 1080))
    return cuts, subs, size, fit


def _srt_time(t: float) -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(subs: list[dict[str, Any]], path: Path) -> Path:
    lines = []
    n = 0
    for sub in sorted(subs, key=lambda s: float(s.get("startTime") or 0)):
        text = str(sub.get("text") or "").strip()
        if not text:
            continue
        n += 1
        lines += [str(n), f"{_srt_time(float(sub['startTime']))} --> {_srt_time(float(sub['endTime']))}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def build_filter(cuts: list[Cut], size: tuple[int, int], fit: str, *, has_audio: bool, subtitles: str | None) -> str:
    w, h = size
    w -= w % 2
    h -= h % 2
    parts = []
    for i, c in enumerate(cuts):
        parts.append(f"[0:v]trim=start={c.start:.3f}:end={c.end:.3f},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            parts.append(f"[0:a]atrim=start={c.start:.3f}:end={c.end:.3f},asetpts=PTS-STARTPTS[a{i}]")
    joined = "".join(f"[v{i}]" + (f"[a{i}]" if has_audio else "") for i in range(len(cuts)))
    parts.append(f"{joined}concat=n={len(cuts)}:v=1:a={1 if has_audio else 0}[vc]" + ("[ac]" if has_audio else ""))
    if fit == "cover":
        scale = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    else:
        scale = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
    chain = f"[vc]{scale},setsar=1"
    if subtitles:
        font = "Malgun Gothic" if os.name == "nt" else "Noto Sans CJK KR"
        size_pt = 14 if h > w else 20
        chain += f",subtitles={subtitles}:force_style='FontName={font},FontSize={size_pt},Outline=2,MarginV=40'"
    parts.append(chain + "[vout]")
    return ";".join(parts)


def render_edit(
    edit: Path | dict[str, Any],
    video: Path,
    out: Path,
    *,
    burn_subtitles: bool = False,
    encoder: str = "libx264",
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    doc = edit if isinstance(edit, dict) else json.loads(Path(edit).read_text(encoding="utf-8"))
    cuts, subs, size, fit = cuts_from_edit(doc)
    media = probe(video)
    out = out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    total = sum(c.end - c.start for c in cuts)
    srt_name = None
    srt = out.with_suffix(".srt")
    if subs:
        write_srt(subs, srt)
        if burn_subtitles:
            srt_name = srt.name  # ffmpeg runs in out.parent, so no Windows drive-letter escaping is needed
    script = out.with_suffix(".filter.txt")
    script.write_text(build_filter(cuts, size, fit, has_audio=media.has_audio, subtitles=srt_name), encoding="utf-8")
    venc = [encoder]
    if encoder == "libx264":
        venc += ["-preset", "veryfast", "-crf", "20"]
    elif encoder in {"h264_amf", "hevc_amf"}:
        venc += ["-quality", "balanced", "-rc", "cqp", "-qp_i", "20", "-qp_p", "22"]
    args = [
        "ffmpeg", "-y", "-hide_banner", "-nostats", "-progress", "pipe:1",
        "-i", str(video),
        *_filter_script_flag(), script.name,
        "-map", "[vout]",
    ]
    if media.has_audio:
        args += ["-map", "[ac]", "-c:a", "aac", "-b:a", "192k"]
    args += ["-c:v", *venc, "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    try:
        proc = subprocess.Popen(
            args, cwd=str(out.parent), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        raise FfmpegError("ffmpeg를 찾지 못했습니다.") from exc
    assert proc.stdout is not None
    for line in proc.stdout:
        m = re.match(r"out_time_(?:us|ms)=(\d+)", line.strip())
        if m and on_progress and total > 0:
            on_progress(min(1.0, int(m.group(1)) / 1_000_000 / total))
    err = proc.stderr.read() if proc.stderr else ""
    code = proc.wait()
    try:
        script.unlink()
    except OSError:
        pass
    if code != 0:
        raise FfmpegError(f"영상 만들기에 실패했습니다.\n{err[-1500:]}")
    if on_progress:
        on_progress(1.0)
    return out


_FLAG: list[str] | None = None


def _filter_script_flag() -> list[str]:
    """ffmpeg 7+ reads option values from a file with "-/option"; older builds use -filter_complex_script."""
    global _FLAG
    if _FLAG is None:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True).stdout
            m = re.search(r"ffmpeg version n?(\d+)", out)
            major = int(m.group(1)) if m else 0
        except (OSError, ValueError):
            major = 0
        _FLAG = ["-/filter_complex"] if major >= 7 else ["-filter_complex_script"]
    return _FLAG


def make_proxy(video: Path, out: Path, *, height: int = 540) -> Path:
    """Light copy for phones (same length, so an edit made on it still fits the original)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
        "-vf", f"scale=-2:{height}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out),
    ]
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FfmpegError(f"가벼운 영상 만들기에 실패했습니다.\n{proc.stderr[-800:]}")
    return out
