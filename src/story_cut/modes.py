"""Edit modes (presets) and free-text prompt -> mode planning.

A mode decides *which* parts of the video to keep. Shot labels from the vision
model are the same for every mode, so the classification cache is shared: you
can analyze a video once in story mode and re-run it as shorts or highlight
without asking the model again.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from story_cut import LABELS


@dataclass(frozen=True)
class ModePreset:
    name: str
    title: str  # Korean title shown in the UI
    description: str  # Korean one-liner
    strategy: str  # "labels" | "speech" | "best"
    keep: tuple[str, ...] = ()
    pad: float = 0.25
    # Shot splitting is the same for every vision mode on purpose: shot times are
    # part of the classification cache key, so equal splits let modes share labels.
    min_shot: float = 1.5
    max_segment: float = 20.0
    min_confidence: float = 0.0
    min_keep: float = 2.0
    merge_gap: float = 1.0
    needs_vision: bool = True
    # 0 means "no cap". Otherwise the best ranges are chosen until this many seconds.
    max_total: float = 0.0
    # Longest single clip when the mode picks highlights (best strategy / budget).
    max_clip: float = 0.0
    aspect: str = "source"  # "source" | "9:16"
    # Keep shots whose loudness is at or above this percentile (0 = off).
    loud_percentile: float = 0.0
    # Label weights for the "best" strategy and for budget scoring.
    weights: dict[str, float] = field(default_factory=dict)


MODES: dict[str, ModePreset] = {
    "story": ModePreset(
        name="story",
        title="스토리",
        description="컷신과 게임 속 대사만 남깁니다. (기본값)",
        strategy="labels",
        keep=("cutscene", "dialogue"),
        weights={"cutscene": 1.0, "dialogue": 0.9},
    ),
    "highlight": ModePreset(
        name="highlight",
        title="하이라이트",
        description="전투와 반응이 큰(소리가 큰) 순간을 남깁니다.",
        strategy="labels",
        keep=("combat",),
        pad=0.5,
        min_confidence=0.5,
        min_keep=3.0,
        merge_gap=2.0,
        loud_percentile=85.0,
        weights={"combat": 1.0, "cutscene": 0.8, "dialogue": 0.5, "exploration": 0.3},
    ),
    "combat": ModePreset(
        name="combat",
        title="전투만",
        description="전투 장면을 모두 남깁니다.",
        strategy="labels",
        keep=("combat",),
        pad=0.4,
        min_keep=2.0,
        merge_gap=2.0,
        weights={"combat": 1.0},
    ),
    "talk": ModePreset(
        name="talk",
        title="무음 컷 (토크 압축)",
        description="말이 없는 구간을 잘라 냅니다. AI 모델 없이 빠르게 돕니다.",
        strategy="speech",
        pad=0.2,
        min_shot=1.5,
        min_keep=0.4,
        merge_gap=0.6,
        needs_vision=False,
    ),
    "shorts": ModePreset(
        name="shorts",
        title="쇼츠 (세로 60초)",
        description="가장 볼만한 순간만 골라 9:16 세로, 1분 안으로 만듭니다.",
        strategy="best",
        keep=("cutscene", "combat", "dialogue"),
        pad=0.3,
        min_confidence=0.4,
        min_keep=2.0,
        merge_gap=0.5,
        max_total=58.0,
        max_clip=12.0,
        aspect="9:16",
        loud_percentile=80.0,
        weights={"cutscene": 1.0, "combat": 0.9, "dialogue": 0.6, "exploration": 0.15},
    ),
}

ALIASES = {
    "silence": "talk",
    "silence-cut": "talk",
    "tighten": "talk",
    "short": "shorts",
    "highlights": "highlight",
    "fight": "combat",
    "battle": "combat",
}

DEFAULT_MODE = "story"


def get_mode(name: str | None) -> ModePreset:
    key = (name or DEFAULT_MODE).strip().lower()
    key = ALIASES.get(key, key)
    if key not in MODES:
        raise ValueError(f"알 수 없는 모드입니다: {name} (가능: {', '.join(MODES)})")
    return MODES[key]


@dataclass
class PromptPlan:
    mode: str
    keep: tuple[str, ...] | None = None
    max_total: float | None = None
    vertical: bool | None = None
    reason: str = ""
    source: str = "keywords"  # "keywords" | "llm"


_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("shorts", ("쇼츠", "숏츠", "숏폼", "shorts", "short", "릴스", "reels", "틱톡", "tiktok", "세로", "9:16")),
    ("talk", ("무음", "침묵", "조용한", "공백", "말 없는", "말없는", "잡담", "토크", "silence", "silent", "talk", "podcast", "팟캐스트", "말하는 부분", "말한 부분")),
    ("highlight", ("하이라이트", "highlight", "명장면", "재밌는", "재미있는", "웃긴", "반응", "레전드", "킬", "클립")),
    ("combat", ("전투", "보스", "싸움", "배틀", "fight", "combat", "boss", "battle")),
    ("story", ("스토리", "컷신", "대사", "줄거리", "story", "cutscene", "dialogue", "plot")),
]

_LABEL_WORDS = {
    "combat": ("전투", "보스", "싸움", "배틀", "combat", "boss", "fight"),
    "exploration": ("탐험", "탐색", "이동", "필드", "exploration", "explore"),
    "cutscene": ("컷신", "cutscene", "시네마틱"),
    "dialogue": ("대사", "대화", "dialogue"),
    "menu": ("메뉴", "menu", "인벤토리"),
}


_DUR_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(시간|분|초|hours?|hrs?|minutes?|mins?|seconds?|secs?|h|m|s)(?![a-z])"
)
_TARGET_HINTS = ("으로", "로", "이내", "안", "미만", "이하", "정도", "짜리로", "under", "within", "to", "max", "below")


def _duration_from_text(text: str) -> float | None:
    """Find the requested output length. "4시간 방송을 10분으로" -> 600, "1분 30초" -> 90."""
    t = text.lower()
    groups: list[list[float | int]] = []  # [seconds, start, end]
    for match in _DUR_RE.finditer(t):
        n = float(match.group(1))
        unit = match.group(2)
        if unit == "시간" or unit.startswith("h"):
            sec = n * 3600
        elif unit == "분" or unit.startswith("m"):
            sec = n * 60
        else:
            sec = n
        if groups and t[groups[-1][2]:match.start()].strip() == "":
            groups[-1][0] += sec
            groups[-1][2] = match.end()
        else:
            groups.append([sec, match.start(), match.end()])
    if not groups:
        return None
    for sec, _, end in groups:
        tail = t[int(end):int(end) + 6].lstrip()
        if any(tail.startswith(h) for h in _TARGET_HINTS):
            return float(sec)
    return float(min(g[0] for g in groups))


def plan_from_keywords(prompt: str) -> PromptPlan:
    text = (prompt or "").strip().lower()
    mode = DEFAULT_MODE
    hits: dict[str, int] = {}
    for name, words in _KEYWORDS:
        for word in words:
            if word in text:
                hits[name] = hits.get(name, 0) + 1
    # Priority: format words (shorts) > silence cut > highlight > combat > story.
    for name, _ in _KEYWORDS:
        if hits.get(name):
            mode = name
            break
    # "스토리랑 전투" = a story edit that also keeps fights.
    if mode == "combat" and hits.get("story"):
        mode = "story"
    preset = MODES[mode]
    keep: tuple[str, ...] | None = None
    if preset.strategy != "speech":
        extra = [label for label, words in _LABEL_WORDS.items() if any(w in text for w in words)]
        if extra and mode in {"story", "combat", "highlight", "shorts"}:
            merged = list(preset.keep)
            for label in extra:
                if label not in merged:
                    merged.append(label)
            if tuple(merged) != preset.keep:
                keep = tuple(merged)
    max_total = _duration_from_text(text)
    vertical = True if any(w in text for w in ("세로", "9:16", "vertical", "쇼츠", "shorts", "릴스", "틱톡")) else None
    reason = f"키워드로 '{MODES[mode].title}' 모드를 골랐습니다." if hits else "알아본 키워드가 없어 기본 스토리 모드를 씁니다."
    return PromptPlan(mode=mode, keep=keep, max_total=max_total, vertical=vertical, reason=reason, source="keywords")


PLANNER_PROMPT = """You turn a video editor's request into an edit plan for a game/stream video tool.
Return ONLY JSON: {"mode": one of [MODES], "keep": [labels] or null, "max_seconds": number or null, "vertical": true|false|null, "reason": "short Korean reason"}
Modes:
- story: keep cutscenes and in-game dialogue (story-focused edit)
- highlight: keep combat and loud/exciting reaction moments
- combat: keep all combat
- talk: cut silence, keep everything where someone speaks (podcast, chatting, tutorials)
- shorts: best moments only, vertical 9:16, under 60 seconds
Labels for keep: cutscene, dialogue, combat, exploration, menu, other. Use null to keep the mode default.
max_seconds: total length the user asked for (e.g. "10분으로" -> 600), else null.
"""


def plan_from_llm(prompt: str, *, endpoint: str, model: str, api_key: str | None = None, timeout: float = 60.0) -> PromptPlan:
    base = endpoint.rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": PLANNER_PROMPT.replace("[MODES]", ", ".join(MODES))},
            {"role": "user", "content": prompt.strip()},
        ],
    }
    with httpx.Client(timeout=timeout) as client:
        res = client.post(f"{base}/chat/completions", headers=headers, json=body)
        res.raise_for_status()
        text = res.json()["choices"][0]["message"]["content"]
    return parse_plan(text)


def parse_plan(text: str) -> PromptPlan:
    raw = text.strip()
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    match = re.search(r"\{.*\}", raw, flags=re.S)
    if not match:
        raise ValueError("계획 JSON을 찾지 못했습니다.")
    data: dict[str, Any] = json.loads(match.group(0))
    mode = ALIASES.get(str(data.get("mode") or "").strip().lower(), str(data.get("mode") or "").strip().lower())
    if mode not in MODES:
        raise ValueError(f"모르는 모드: {mode}")
    keep_raw = data.get("keep")
    keep: tuple[str, ...] | None = None
    if isinstance(keep_raw, list):
        keep = tuple(str(k).strip().lower() for k in keep_raw if str(k).strip().lower() in LABELS) or None
    max_total = None
    try:
        if data.get("max_seconds") is not None:
            max_total = float(data["max_seconds"]) or None
    except (TypeError, ValueError):
        max_total = None
    vertical = data.get("vertical") if isinstance(data.get("vertical"), bool) else None
    reason = str(data.get("reason") or "").strip()[:200] or "AI가 요청을 해석했습니다."
    return PromptPlan(mode=mode, keep=keep, max_total=max_total, vertical=vertical, reason=reason, source="llm")


def plan_prompt(prompt: str, *, backend: str, endpoint: str, model: str, api_key: str | None = None) -> PromptPlan:
    """Ask the local LLM to read the prompt; fall back to keyword rules."""
    keyword_plan = plan_from_keywords(prompt)
    if backend.lower() in {"lmstudio", "openai", "openai-compatible", "openai_compatible", "local"}:
        try:
            plan = plan_from_llm(prompt, endpoint=endpoint, model=model, api_key=api_key)
            # Numbers in the prompt are more reliable from the regex than from the model.
            if keyword_plan.max_total and not plan.max_total:
                plan.max_total = keyword_plan.max_total
            return plan
        except Exception as exc:  # LLM is optional for planning
            keyword_plan.reason += f" (AI 해석 실패: {str(exc)[:80]})"
    return keyword_plan


def resolve_preset(mode: str | None, plan: PromptPlan | None, overrides: dict[str, Any]) -> ModePreset:
    """Pick the preset (explicit mode wins over the prompt), then apply the prompt and CLI overrides."""
    preset = get_mode(mode or (plan.mode if plan else None))
    changes: dict[str, Any] = {}
    if plan is not None:
        if plan.keep and preset.strategy != "speech":
            changes["keep"] = plan.keep
        if plan.max_total:
            changes["max_total"] = float(plan.max_total)
        if plan.vertical is True:
            changes["aspect"] = "9:16"
    for key, value in overrides.items():
        if value is not None:
            changes[key] = value
    if preset.strategy == "best" and changes.get("max_total", preset.max_total) <= 0:
        changes["max_total"] = 58.0
    return replace(preset, **changes) if changes else preset


def modes_for_ui() -> list[dict[str, Any]]:
    return [
        {
            "name": m.name,
            "title": m.title,
            "description": m.description,
            "needs_vision": m.needs_vision,
            "aspect": m.aspect,
            "max_total": m.max_total,
        }
        for m in MODES.values()
    ]
