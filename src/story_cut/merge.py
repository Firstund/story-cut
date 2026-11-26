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
    # 90th-percentile loudness in dB, filled only by modes that use audio.
    loudness: float | None = None

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
    # Set for ranges that do not come from labelled shots (talk mode: "speech").
    fixed_label: str | None = None
    fixed_reason: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def primary_label(self) -> str:
        if self.fixed_label:
            return self.fixed_label
        # Prefer the first kept label; if mixed, join unique in order.
        seen: list[str] = []
        for label in self.labels:
            if label not in seen:
                seen.append(label)
        return "+".join(seen) if seen else "other"

    @property
    def reason(self) -> str:
        if self.fixed_reason and not self.shots:
            return self.fixed_reason
        reasons = [s.classification.reason for s in self.shots if s.classification.reason]
        return " | ".join(reasons[:4])

    @property
    def confidence(self) -> float:
        if not self.shots:
            return 1.0 if self.fixed_label else 0.0
        return sum(s.classification.confidence for s in self.shots) / len(self.shots)


def apply_keep(
    shots: list[AnalyzedShot],
    keep_labels: set[str],
    min_confidence: float,
    *,
    loud_threshold: float | None = None,
    loud_labels: set[str] | None = None,
) -> list[AnalyzedShot]:
    """Mark shots to keep by label. Optionally also keep loud shots (reactions, big moments)."""
    allowed_loud = loud_labels if loud_labels is not None else {"cutscene", "dialogue", "combat", "exploration"}
    for item in shots:
        ok = item.classification.label in keep_labels
        if item.classification.confidence < min_confidence:
            ok = False
        if (
            not ok
            and loud_threshold is not None
            and item.loudness is not None
            and item.loudness >= loud_threshold
            and item.classification.label in allowed_loud
        ):
            ok = True
        item._keep = ok
    return shots


def loud_threshold(shots: list[AnalyzedShot], percentile: float) -> float | None:
    values = [s.loudness for s in shots if s.loudness is not None and s.loudness > -89]
    if percentile <= 0 or len(values) < 4:
        return None
    values.sort()
    k = min(len(values) - 1, max(0, int(round(percentile / 100.0 * (len(values) - 1)))))
    return values[k]


def shot_score(item: AnalyzedShot, weights: dict[str, float], loud_lo: float, loud_hi: float) -> float:
    base = weights.get(item.classification.label, 0.0) * max(0.05, item.classification.confidence)
    if item.loudness is None or loud_hi - loud_lo < 1e-6:
        return base
    loud = (item.loudness - loud_lo) / (loud_hi - loud_lo)
    return base + 0.35 * max(0.0, min(1.0, loud))


def range_score(item: KeptRange, weights: dict[str, float], loud_lo: float, loud_hi: float) -> float:
    if not item.shots:
        return 0.5
    scores = [shot_score(s, weights, loud_lo, loud_hi) for s in item.shots]
    # Mean rewards consistently good ranges, max rewards one great moment.
    return 0.5 * (sum(scores) / len(scores)) + 0.5 * max(scores)


def _window(item: KeptRange, max_clip: float, weights: dict[str, float], lo: float, hi: float) -> KeptRange:
    if max_clip <= 0 or item.duration <= max_clip or not item.shots:
        if max_clip > 0 and item.duration > max_clip:
            return KeptRange(item.start, item.start + max_clip, [], list(item.labels), item.fixed_label, item.fixed_reason)
        return item
    best = max(item.shots, key=lambda s: shot_score(s, weights, lo, hi))
    center = (max(item.start, best.shot.start) + min(item.end, best.shot.end)) / 2
    start = max(item.start, center - max_clip / 2)
    end = min(item.end, start + max_clip)
    start = max(item.start, end - max_clip)
    shots = [s for s in item.shots if s.shot.end > start and s.shot.start < end]
    return KeptRange(start, end, shots, [s.classification.label for s in shots] or list(item.labels), item.fixed_label, item.fixed_reason)


def select_within_budget(
    ranges: list[KeptRange],
    *,
    max_total: float,
    max_clip: float = 0.0,
    weights: dict[str, float] | None = None,
    min_piece: float = 2.0,
) -> list[KeptRange]:
    """Keep the best-scoring ranges until max_total seconds, then restore source order."""
    weights = weights or {}
    loud = [s.loudness for r in ranges for s in r.shots if s.loudness is not None and s.loudness > -89]
    lo, hi = (min(loud), max(loud)) if loud else (0.0, 0.0)
    pieces = [_window(r, max_clip, weights, lo, hi) for r in ranges]
    if max_total <= 0 or sum(p.duration for p in pieces) <= max_total + 1e-6:
        return pieces
    ranked = sorted(pieces, key=lambda r: range_score(r, weights, lo, hi), reverse=True)
    chosen: list[KeptRange] = []
    total = 0.0
    for item in ranked:
        room = max_total - total
        if room < min_piece:
            break
        if item.duration <= room + 1e-6:
            chosen.append(item)
            total += item.duration
        elif room >= min_piece and not chosen:
            trimmed = _window(item, room, weights, lo, hi)
            chosen.append(trimmed)
            total += trimmed.duration
    return sorted(chosen, key=lambda r: r.start)


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


def drop_short_ranges(ranges: list[KeptRange], min_keep: float) -> list[KeptRange]:
    """Drop isolated kept ranges shorter than min_keep seconds (skill flashes, etc.)."""
    if min_keep <= 0:
        return ranges
    return [item for item in ranges if item.duration + 1e-9 >= min_keep]


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
