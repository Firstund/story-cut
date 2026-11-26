from story_cut.audio import parse_silencedetect, speech_ranges
from story_cut.classify import Classification
from story_cut.merge import AnalyzedShot, apply_keep, loud_threshold, merge_kept, select_within_budget
from story_cut.scenes import Shot


def _shot(i, s, e, label, conf=0.9, loud=None):
    item = AnalyzedShot(Shot(i, s, e), Classification(label, conf, label), "")
    item.loudness = loud
    return item


def test_loud_shots_are_kept_in_highlight_style():
    shots = [
        _shot(0, 0, 5, "exploration", loud=-40),
        _shot(1, 5, 10, "exploration", loud=-10),  # shouting
        _shot(2, 10, 15, "combat", loud=-30),
        _shot(3, 15, 20, "menu", loud=-5),  # loud menu is still dropped
        _shot(4, 20, 25, "exploration", loud=-42),
    ]
    th = loud_threshold(shots, 75)
    assert th is not None
    apply_keep(shots, {"combat"}, 0.0, loud_threshold=th)
    assert [s.keep for s in shots] == [False, True, True, False, False]


def test_budget_picks_best_and_restores_order():
    shots = [
        _shot(0, 0, 10, "exploration", 0.9),
        _shot(1, 20, 30, "cutscene", 0.95),
        _shot(2, 40, 50, "combat", 0.9),
        _shot(3, 60, 70, "dialogue", 0.5),
    ]
    apply_keep(shots, {"exploration", "cutscene", "combat", "dialogue"}, 0.0)
    ranges = merge_kept(shots, pad=0.0, media_duration=80, max_gap=0.0)
    weights = {"cutscene": 1.0, "combat": 0.9, "dialogue": 0.6, "exploration": 0.1}
    picked = select_within_budget(ranges, max_total=20, weights=weights)
    assert [(r.start, r.end) for r in picked] == [(20, 30), (40, 50)]
    assert sum(r.duration for r in picked) <= 20


def test_max_clip_windows_long_ranges_around_best_shot():
    shots = [_shot(0, 0, 10, "exploration", 0.3), _shot(1, 10, 20, "combat", 0.95), _shot(2, 20, 30, "exploration", 0.3)]
    apply_keep(shots, {"exploration", "combat"}, 0.0)
    ranges = merge_kept(shots, pad=0.0, media_duration=30, max_gap=1.0)
    assert len(ranges) == 1
    picked = select_within_budget(ranges, max_total=0, max_clip=8, weights={"combat": 1.0, "exploration": 0.1})
    assert len(picked) == 1
    r = picked[0]
    assert abs(r.duration - 8) < 1e-6 and r.start >= 10 and r.end <= 20


def test_budget_trims_when_only_one_long_range():
    shots = [_shot(0, 0, 100, "cutscene", 0.9)]
    apply_keep(shots, {"cutscene"}, 0.0)
    ranges = merge_kept(shots, pad=0.0, media_duration=100)
    picked = select_within_budget(ranges, max_total=58, weights={"cutscene": 1.0})
    assert len(picked) == 1 and abs(picked[0].duration - 58) < 1e-6


SILENCE_LOG = """
[silencedetect @ 0x1] silence_start: 0
[silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 2.5
[silencedetect @ 0x1] silence_start: 6.0
[silencedetect @ 0x1] silence_end: 9.0 | silence_duration: 3
[silencedetect @ 0x1] silence_start: 9.5
[silencedetect @ 0x1] silence_end: 9.55 | silence_duration: 0.05
[silencedetect @ 0x1] silence_start: 14
"""


def test_silence_parsing_and_speech_ranges():
    silences = parse_silencedetect(SILENCE_LOG, 20.0)
    assert silences == [(0.0, 2.5), (6.0, 9.0), (9.5, 9.55), (14.0, 20.0)]
    spans = speech_ranges(silences, 20.0, pad=0.2, min_keep=0.4)
    # 2.5-6.0 and 9.0-9.5 / 9.55-14 (the 0.05 s gap merges after padding)
    assert spans == [(2.3, 6.2), (8.8, 14.2)]


def test_speech_ranges_drop_tiny_blips():
    spans = speech_ranges([(0, 5), (5.05, 10)], 10, pad=0.2, min_keep=0.4)
    assert spans == []
