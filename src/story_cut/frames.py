from __future__ import annotations

from pathlib import Path

from story_cut.fftool import extract_jpeg
from story_cut.scenes import Shot


def sample_times(shot: Shot, count: int) -> list[float]:
    n = max(2, min(4, count))
    dur = shot.duration
    if dur <= 0.05:
        return [shot.start]
    # Stay slightly inside the shot so we don't land on the cut frame.
    edge = min(0.12, dur * 0.1)
    start = shot.start + edge
    end = shot.end - edge
    if end <= start:
        return [(shot.start + shot.end) / 2]
    if n == 2:
        return [start, end]
    step = (end - start) / (n - 1)
    return [start + i * step for i in range(n)]


def extract_frames(video: Path, shot: Shot, dest_dir: Path, *, count: int = 3, width: int = 640) -> list[Path]:
    times = sample_times(shot, count)
    frames: list[Path] = []
    for i, t in enumerate(times):
        name = f"shot{shot.index:04d}_{i}_{t:.3f}.jpg"
        dest = dest_dir / name
        if dest.is_file() and dest.stat().st_size > 0:
            frames.append(dest)
            continue
        extract_jpeg(video, t, dest, width=width)
        frames.append(dest)
    return frames
