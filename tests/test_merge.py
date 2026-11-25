from story_cut.classify import Classification
from story_cut.merge import AnalyzedShot, apply_keep, cue_groups, merge_kept, remap_time, words_in_ranges
from story_cut.scenes import Shot
from story_cut.transcribe import Transcript, Word


def _shot(index: int, start: float, end: float, label: str, conf: float = 0.9) -> AnalyzedShot:
    return AnalyzedShot(
        shot=Shot(index=index, start=start, end=end),
        classification=Classification(label=label, confidence=conf, reason="test"),
        transcript="",
    )


def test_merge_adjacent_kept_with_padding():
    shots = [
        _shot(0, 0, 3, "cutscene"),
        _shot(1, 3, 6, "dialogue"),
        _shot(2, 6, 9, "combat"),
        _shot(3, 9, 12, "exploration"),
    ]
    apply_keep(shots, {"cutscene", "dialogue"}, 0.0)
    ranges = merge_kept(shots, pad=0.25, media_duration=12.0)
    assert len(ranges) == 1
    assert ranges[0].start == 0.0
    assert ranges[0].end == 6.25
    assert ranges[0].primary_label == "cutscene+dialogue"


def test_non_adjacent_kept_stay_separate():
    shots = [
        _shot(0, 0, 3, "cutscene"),
        _shot(1, 3, 6, "combat"),
        _shot(2, 6, 9, "dialogue"),
    ]
    apply_keep(shots, {"cutscene", "dialogue"}, 0.0)
    ranges = merge_kept(shots, pad=0.25, media_duration=9.0)
    assert len(ranges) == 2
    assert ranges[0].end == 3.25
    assert ranges[1].start == 5.75
    assert remap_time(6.0, ranges) == 3.25 + 0.25  # 0.25s into second clip? first dur 3.25, source 6 is 0.25 into second (5.75-9.25)
    assert abs(remap_time(6.0, ranges) - (3.25 + 0.25)) < 1e-6


def test_low_confidence_is_dropped():
    shots = [_shot(0, 0, 3, "cutscene", conf=0.2), _shot(1, 3, 6, "dialogue", conf=0.9)]
    apply_keep(shots, {"cutscene", "dialogue"}, 0.5)
    assert shots[0].keep is False
    assert shots[1].keep is True


def test_subtitle_remap():
    shots = [_shot(0, 0, 3, "cutscene"), _shot(1, 3, 6, "combat"), _shot(2, 6, 9, "dialogue")]
    apply_keep(shots, {"cutscene", "dialogue"}, 0.0)
    ranges = merge_kept(shots, pad=0.0, media_duration=9.0)
    tr = Transcript(language="ko", text="안녕 싸움 이야기", words=[
        Word("안녕", 0.5, 1.5),
        Word("싸움", 3.5, 4.5),
        Word("이야기", 6.5, 7.2),
    ])
    mapped = words_in_ranges(tr, ranges)
    texts = [w.text for w, _, _ in mapped]
    assert texts == ["안녕", "이야기"]
    # "이야기" at 6.5 in source -> second range starts 6, output starts at 3.0 + 0.5
    assert abs(mapped[1][1] - 3.5) < 1e-6
    cues = cue_groups(mapped, max_chars=80, max_duration=10)
    assert len(cues) >= 1
