from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FfmpegError(RuntimeError):
    pass


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FfmpegError(
            "ffmpeg를 찾지 못했습니다. PATH에 ffmpeg가 있는지 확인하세요. "
            "설치 방법은 README를 보세요."
        )
    if not shutil.which("ffprobe"):
        raise FfmpegError("ffprobe를 찾지 못했습니다. ffmpeg를 다시 설치해 주세요.")
    return path


def run(args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            check=True,
            capture_output=capture,
            text=True,
        )
    except FileNotFoundError as exc:
        raise FfmpegError(f"명령을 실행할 수 없습니다: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or "").strip()
        raise FfmpegError(f"{args[0]} 실행에 실패했습니다.\n{err}") from exc


def _frac(value: str | None) -> float:
    if not value:
        return 0.0
    if "/" in value:
        num, den = value.split("/", 1)
        d = float(den)
        return float(num) / d if d else 0.0
    return float(value)


@dataclass
class MediaInfo:
    path: Path
    duration: float
    width: int
    height: int
    frame_rate: float
    video_codec: str
    audio_codec: str
    sample_rate: int
    channels: int
    file_size: int
    has_video: bool
    has_audio: bool
    last_modified_ms: int

    @property
    def filename(self) -> str:
        return self.path.name


def probe(path: Path) -> MediaInfo:
    require_ffmpeg()
    path = path.resolve()
    if not path.is_file():
        raise FfmpegError(f"파일을 찾을 수 없습니다: {path}")
    raw = run([
        "ffprobe", "-v", "error",
        "-show_streams", "-show_format",
        "-of", "json",
        str(path),
    ]).stdout
    data = json.loads(raw)
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise FfmpegError("이 파일에서 영상 트랙을 찾지 못했습니다.")
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise FfmpegError("영상 길이를 읽을 수 없습니다.")
    fps = _frac(video.get("avg_frame_rate")) or _frac(video.get("r_frame_rate")) or 30.0
    st = path.stat()
    return MediaInfo(
        path=path,
        duration=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        frame_rate=fps,
        video_codec=str(video.get("codec_name") or ""),
        audio_codec=str(audio.get("codec_name") or "") if audio else "",
        sample_rate=int(audio.get("sample_rate") or 0) if audio else 0,
        channels=int(audio.get("channels") or 0) if audio else 0,
        file_size=int(fmt.get("size") or st.st_size),
        has_video=True,
        has_audio=audio is not None,
        last_modified_ms=int(st.st_mtime * 1000),
    )


def extract_jpeg(path: Path, time_sec: float, dest: Path, *, width: int = 640) -> Path:
    require_ffmpeg()
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Fast seek then decode one frame; scale down for the vision model.
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, time_sec):.3f}",
        "-i", str(path),
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-q:v", "3",
        str(dest),
    ], capture=True)
    if not dest.is_file() or dest.stat().st_size == 0:
        raise FfmpegError(f"프레임을 뽑지 못했습니다 ({time_sec:.2f}s).")
    return dest


def make_sample_video(dest: Path, *, scene_seconds: float = 3.0, scenes: int = 4) -> Path:
    """Generate a short test clip with hard color cuts and a sine-wave track."""
    require_ffmpeg()
    dest.parent.mkdir(parents=True, exist_ok=True)
    colors = ["red", "green", "blue", "yellow", "magenta", "cyan"]
    n = max(2, scenes)
    duration = scene_seconds * n
    inputs: list[str] = []
    for i in range(n):
        inputs += [
            "-f", "lavfi",
            "-i", f"color=c={colors[i % len(colors)]}:s=640x360:d={scene_seconds}:r=30",
        ]
    inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}"]
    vlabels = "".join(f"[{i}:v]" for i in range(n))
    filt = f"{vlabels}concat=n={n}:v=1:a=0,format=yuv420p[v]"
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", filt,
        "-map", "[v]", "-map", f"{n}:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(dest),
    ], capture=True)
    return dest
