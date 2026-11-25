from __future__ import annotations

from dataclasses import dataclass, field

from story_cut.classify import Classification
from story_cut.scenes import Shot
from story_cut.transcribe import Transcript, Word


@dataclass
class AnalyzedShot:
    shot: Shot
    classification: Classification
    transcript: str
    ocr_text: str = ""
    frame_paths: list[str] = field(default_factory=list)

    @property
    def keep(self) -> bool:
        return self._keep

    # filled by apply_keep()
    _keep: bool = False


@dataclass
class KeptRange:
    """A merged range on the source timeline that will become one output clip."""
    start: float
    end: float
    shots: list[AnalyzedShot]
    labels: list[str]

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def primary_label(self) -> str:
        # Prefer the first kept label; if mixed, join unique in order.
        seen: list[str] = []
        for label in self.labels:
            if label not in seen:
                seen.append(label)
        return "+".join(seen) if seen else "other"

    @property
    def reason(self) -> str:
        reasons = [s.classification.reason for s in self.shots if s.classification.reason]
        return " | ".join(reasons[:4])

    @property
    def confidence(self) -> float:
        if not self.shots:
            return 0.0
        return sum(s.classification.confidence for s in self.shots) / len(self.shots)


def apply_keep(shots: list[AnalyzedShot], keep_labels: set[str], min_confidence: float) -> list[AnalyzedShot]:
    for item in shots:
        ok = item.classification.label in keep_labels
        if item.classification.confidence < min_confidence:
            ok = False
        item._keep = ok
    return shots


def merge_kept(
    shots: list[AnalyzedShot],
    *,
    pad: float,
    media_duration: float,
    max_gap: float | None = None,
) -> list[KeptRange]:
    """Pad kept shots slightly, then merge overlaps / small gaps."""
    gap = pad if max_gap is None else max_gap
    kept = [s for s in shots if s.keep]
    if not kept:
        return []
    padded: list[KeptRange] = []
    for item in kept:
        start = max(0.0, item.shot.start - pad)
        end = min(media_duration, item.shot.end + pad)
        if end - start < 0.05:
            continue
        padded.append(KeptRange(start=start, end=end, shots=[item], labels=[item.classification.label]))

    merged: list[KeptRange] = [padded[0]]
    for item in padded[1:]:
        prev = merged[-1]
        if item.start <= prev.end + gap:
            prev.end = max(prev.end, item.end)
            prev.shots.extend(item.shots)
            prev.labels.extend(item.labels)
        else:
            merged.append(item)
    return merged


def remap_time(source_time: float, ranges: list[KeptRange]) -> float | None:
    """Map a source timestamp into the concatenated output timeline."""
    out = 0.0
    for item in ranges:
        if source_time < item.start:
            return out
        if source_time <= item.end:
            return out + (source_time - item.start)
        out += item.duration
    return None


def words_in_ranges(transcript: Transcript, ranges: list[KeptRange]) -> list[tuple[Word, float, float]]:
    """Return words that overlap a kept range, with output start/end times."""
    result: list[tuple[Word, float, float]] = []
    for word in transcript.words:
        for item in ranges:
            overlap_s = max(word.start, item.start)
            overlap_e = min(word.end, item.end)
            if overlap_e - overlap_s <= 0.02:
                continue
            out_s = remap_time(overlap_s, ranges)
            out_e = remap_time(overlap_e, ranges)
            if out_s is None or out_e is None or out_e <= out_s:
                continue
            result.append((word, out_s, out_e))
            break
    return result


def cue_groups(
    mapped: list[tuple[Word, float, float]],
    *,
    max_chars: int = 42,
    max_duration: float = 4.0,
) -> list[tuple[str, float, float]]:
    cues: list[tuple[str, float, float]] = []
    buf: list[str] = []
    start: float | None = None
    end = 0.0
    for word, s, e in mapped:
        if start is None:
            start, end, buf = s, e, [word.text]
            continue
        text = " ".join(buf + [word.text])
        if (e - start) > max_duration or len(text) > max_chars:
            cues.append((" ".join(buf), start, end))
            start, end, buf = s, e, [word.text]
        else:
            buf.append(word.text)
            end = e
    if start is not None and buf:
        cues.append((" ".join(buf), start, end))
    return cues
