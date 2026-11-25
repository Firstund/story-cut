from story_cut.scenes import _cap_length, _merge_short


def test_cap_length_absorbs_short_remainder():
    # 20s cap of a 20.37s span must not leave a 0.37s leftover shot.
    capped = _cap_length([(0.0, 20.37)], max_segment=20.0, min_shot=1.5)
    assert capped == [(0.0, 20.37)]


def test_cap_length_splits_when_remainder_meets_min_shot():
    capped = _cap_length([(0.0, 22.0)], max_segment=20.0, min_shot=1.5)
    assert capped == [(0.0, 20.0), (20.0, 22.0)]


def test_cap_length_absorbs_remainder_after_full_chunks():
    # 40.37s -> 20 + 20.37, not 20 + 20 + 0.37
    capped = _cap_length([(0.0, 40.37)], max_segment=20.0, min_shot=1.5)
    assert capped == [(0.0, 20.0), (20.0, 40.37)]


def test_cap_length_without_min_shot_keeps_old_split():
    capped = _cap_length([(0.0, 20.37)], max_segment=20.0)
    assert capped == [(0.0, 20.0), (20.0, 20.37)]


def test_merge_then_cap_does_not_recreate_short_tail():
    # The bug from test20: merge_short folds a 0.37s tail into a 20s shot,
    # then cap must not split that 0.37s back out.
    merged = _merge_short([(0.0, 20.0), (20.0, 20.37)], min_shot=1.5)
    assert merged == [(0.0, 20.37)]
    capped = _cap_length(merged, max_segment=20.0, min_shot=1.5)
    assert capped == [(0.0, 20.37)]
    assert all(end - start >= 1.5 for start, end in capped)
