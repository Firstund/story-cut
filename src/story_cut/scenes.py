from __future__ import annotations

from dataclasses import asdict, dataclass

from scenedetect import ContentDetector, SceneManager, open_video


@dataclass
class Shot:
    index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def detect_shots(
    video_path,
    *,
    min_shot: float = 0.5,
    max_segment: float = 20.0,
    threshold: float = 27.0,
    duration: float,
) -> list[Shot]:
    video = open_video(str(video_path))
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))
    manager.detect_scenes(video)
    raw = manager.get_scene_list()
    spans: list[tuple[float, float]] = []
    if not raw:
        spans.append((0.0, duration))
    else:
        for start, end in raw:
            spans.append((max(0.0, start.get_seconds()), min(duration, end.get_seconds())))
        # Cover leading/trailing leftovers, if any.
        if spans[0][0] > 0.05:
            spans.insert(0, (0.0, spans[0][0]))
        if duration - spans[-1][1] > 0.05:
            spans.append((spans[-1][1], duration))

    merged = _merge_short(spans, min_shot)
    capped = _cap_length(merged, max_segment)
    return [Shot(index=i, start=s, end=e) for i, (s, e) in enumerate(capped) if e - s > 0.01]


def _merge_short(spans: list[tuple[float, float]], min_shot: float) -> list[tuple[float, float]]:
    if not spans:
        return []
    out: list[tuple[float, float]] = [spans[0]]
    for start, end in spans[1:]:
        prev_s, prev_e = out[-1]
        if (end - start) < min_shot or (prev_e - prev_s) < min_shot:
            out[-1] = (prev_s, end)
        else:
            out.append((start, end))
    if len(out) >= 2 and (out[0][1] - out[0][0]) < min_shot:
        out[1] = (out[0][0], out[1][1])
        out.pop(0)
    return out


def _cap_length(spans: list[tuple[float, float]], max_segment: float) -> list[tuple[float, float]]:
    if max_segment <= 0:
        return spans
    out: list[tuple[float, float]] = []
    for start, end in spans:
        t = start
        while end - t > max_segment + 0.05:
            out.append((t, t + max_segment))
            t += max_segment
        if end - t > 0.01:
            out.append((t, end))
    return out


def shots_to_json(shots: list[Shot]) -> list[dict]:
    return [asdict(s) for s in shots]
