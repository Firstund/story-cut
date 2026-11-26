import pytest

from story_cut.modes import (
    MODES,
    _duration_from_text,
    get_mode,
    parse_plan,
    plan_from_keywords,
    plan_prompt,
    resolve_preset,
)


def test_story_preset_keeps_previous_defaults():
    story = get_mode("story")
    assert story.keep == ("cutscene", "dialogue")
    assert (story.pad, story.min_shot, story.max_segment) == (0.25, 1.5, 20.0)
    assert (story.min_confidence, story.min_keep, story.merge_gap) == (0.0, 2.0, 1.0)
    assert story.max_total == 0 and story.aspect == "source"
    assert get_mode(None).name == "story"


def test_vision_modes_share_shot_split_for_cache_reuse():
    splits = {(m.min_shot, m.max_segment) for m in MODES.values() if m.needs_vision}
    assert splits == {(1.5, 20.0)}


def test_aliases_and_unknown():
    assert get_mode("silence").name == "talk"
    assert get_mode("Shorts").name == "shorts"
    with pytest.raises(ValueError):
        get_mode("nope")


@pytest.mark.parametrize("prompt,mode", [
    ("이 게임 영상을 스토리 위주로 편집해줘", "story"),
    ("보스전 위주로 하이라이트 만들어줘", "highlight"),
    ("전투 장면만 남겨줘", "combat"),
    ("말 없는 부분 잘라줘", "talk"),
    ("유튜브 쇼츠로 만들어줘", "shorts"),
    ("세로 영상 30초", "shorts"),
    ("아무거나", "story"),
])
def test_keyword_modes(prompt, mode):
    assert plan_from_keywords(prompt).mode == mode


def test_keyword_extra_labels_and_length():
    plan = plan_from_keywords("스토리랑 전투 같이 10분으로")
    assert plan.mode == "story"
    assert plan.keep and "combat" in plan.keep and "cutscene" in plan.keep
    assert plan.max_total == 600


@pytest.mark.parametrize("text,seconds", [
    ("4시간 방송을 10분으로 줄여줘", 600),
    ("1분 30초 쇼츠", 90),
    ("30초", 30),
    ("3 minutes highlight", 180),
    ("길이 상관없음", None),
])
def test_duration_parsing(text, seconds):
    assert _duration_from_text(text) == seconds


def test_parse_plan_from_llm_text():
    plan = parse_plan('<think>hmm</think>```json\n{"mode":"highlights","keep":["combat","bogus"],"max_seconds":180,"vertical":false,"reason":"전투 위주"}\n```')
    assert plan.mode == "highlight"
    assert plan.keep == ("combat",)
    assert plan.max_total == 180
    assert plan.vertical is False
    with pytest.raises(ValueError):
        parse_plan('{"mode":"weird"}')


def test_plan_prompt_without_llm_uses_keywords():
    plan = plan_prompt("쇼츠", backend="mock", endpoint="http://x", model="m")
    assert plan.mode == "shorts" and plan.source == "keywords"


def test_plan_prompt_falls_back_when_llm_unreachable():
    plan = plan_prompt("하이라이트 2분", backend="lmstudio", endpoint="http://127.0.0.1:9/v1", model="m")
    assert plan.mode == "highlight" and plan.source == "keywords"
    assert plan.max_total == 120


def test_resolve_preset_priority():
    plan = plan_from_keywords("쇼츠 30초")
    p = resolve_preset(None, plan, {})
    assert p.name == "shorts" and p.max_total == 30 and p.aspect == "9:16"
    # explicit mode wins over the prompt, CLI overrides win over both
    p = resolve_preset("story", plan, {"pad": 1.0, "keep": None})
    assert p.name == "story" and p.pad == 1.0 and p.max_total == 30
    # talk mode ignores label lists
    p = resolve_preset("talk", plan_from_keywords("전투 무음 컷"), {})
    assert p.name == "talk" and p.keep == ()
