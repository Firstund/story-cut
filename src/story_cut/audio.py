"""Audio helpers: loudness per shot (for highlights) and silence detection (talk mode)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from story_cut.fftool import FfmpegError


def ensure_wav(video: Path, work_dir: Path) -> Path:
    """16 kHz mono wav next to the cache (the transcriber writes the same file)."""
    from story_cut.transcribe import _extract_mono16k
    wav = Path(work_dir) / f"{video.stem}.16k.wav"
    if not wav.exists() or wav.stat().st_size < 1000:
        _extract_mono16k(video, wav)
    return wav


def load_wav(wav: Path) -> np.ndarray:
    from story_cut.transcribe import _load_wav_f32
    return _load_wav_f32(wav)


def loudness_db(samples: np.ndarray, start: float, end: float, rate: int = 16000) -> float:
    """Upper-percentile RMS level (dB) of a time range. Short peaks such as shouting count."""
    a = max(0, int(start * rate))
    b = min(len(samples), int(end * rate))
    if b - a < rate // 10:
        return -90.0
    chunk = samples[a:b]
    win = rate // 10  # 100 ms windows
    n = len(chunk) // win
    if n == 0:
        rms = float(np.sqrt(np.mean(chunk ** 2)) + 1e-9)
        return 20 * np.log10(rms)
    frames = chunk[: n * win].reshape(n, win)
    rms = np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-9
    return float(20 * np.log10(np.percentile(rms, 90)))


def parse_silencedetect(stderr: str, duration: float) -> list[tuple[float, float]]:
    """Return silent intervals from ffmpeg silencedetect output."""
    silences: list[tuple[float, float]] = []
    start: float | None = None
    for line in stderr.splitlines():
        m = re.search(r"silence_start:\s*(-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"silence_end:\s*([\d.]+)", line)
        if m:
            end = float(m.group(1))
            silences.append((start if start is not None else 0.0, end))
            start = None
    if start is not None:
        silences.append((start, duration))
    return silences


def detect_silence(video: Path, *, noise_db: float = -35.0, min_silence: float = 0.6, duration: float) -> list[tuple[float, float]]:
    args = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vn",
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise FfmpegError("ffmpeg를 찾지 못했습니다.") from exc
    if proc.returncode != 0:
        raise FfmpegError(f"무음 찾기에 실패했습니다.\n{proc.stderr[-800:]}")
    return parse_silencedetect(proc.stderr, duration)


def speech_ranges(
    silences: list[tuple[float, float]],
    duration: float,
    *,
    pad: float = 0.2,
    min_keep: float = 0.4,
    min_sound: float = 0.15,
) -> list[tuple[float, float]]:
    """Invert silences into sounding ranges, pad them, merge overlaps, drop tiny blips."""
    sounding: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in sorted(silences):
        if s > cursor:
            sounding.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        sounding.append((cursor, duration))
    padded = [(max(0.0, s - pad), min(duration, e + pad)) for s, e in sounding if e - s >= min_sound]
    merged: list[list[float]] = []
    for s, e in padded:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged if e - s >= min_keep]
