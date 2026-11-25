from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from story_cut import LABELS

SYSTEM_PROMPT = """You label a short game-play video segment for an editor.
Return ONLY a JSON object with these keys:
{"label":"cutscene"|"dialogue"|"combat"|"exploration"|"menu"|"other","confidence":0.0-1.0,"reason":"short reason in Korean or English"}

Label meanings:
- cutscene: in-engine or pre-rendered story cinematic; the player is mostly watching
- dialogue: conversation, talking, dialogue boxes, NPC speech
- combat: fighting, a boss, skills being used in battle
- exploration: walking, traveling, looting, puzzles, looking around
- menu: inventory, map, settings, title screen, loading
- other: death screen, AFK, junk, unclear
Do not add markdown. Do not invent events that the frames and transcript do not support.
"""


@dataclass
class Classification:
    label: str
    confidence: float
    reason: str
    cached: bool = False

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


class VisionBackend(Protocol):
    name: str
    model: str

    def classify(self, frames: list[Path], transcript: str, ocr_text: str, shot_range: str) -> Classification:
        ...


def parse_classification(text: str) -> Classification:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I).strip()
    match = re.search(r"\{.*\}", raw, flags=re.S)
    if not match:
        raise ValueError("모델 응답에서 JSON을 찾지 못했습니다.")
    data = json.loads(match.group(0))
    label = str(data.get("label") or "").strip().lower()
    if label not in LABELS:
        label = "other"
    try:
        conf = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    reason = str(data.get("reason") or "").strip() or "이유 없음"
    return Classification(label=label, confidence=conf, reason=reason[:240])


def _user_prompt(transcript: str, ocr_text: str, shot_range: str) -> str:
    parts = [f"Segment time range (seconds): {shot_range}"]
    parts.append("Transcript (speech in this range, may be empty):")
    parts.append(transcript.strip() or "(no speech)")
    if ocr_text.strip():
        parts.append("On-screen text (OCR, may be noisy):")
        parts.append(ocr_text.strip())
    parts.append("Look at the attached frames and return the JSON label.")
    return "\n".join(parts)


def _encode_image(path: Path) -> str:
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


class OpenAICompatibleBackend:
    """LM Studio and any OpenAI-compatible vision endpoint."""

    name = "lmstudio"

    def __init__(
        self,
        endpoint: str = "http://localhost:1234/v1",
        model: str = "qwen3-vl-8b",
        api_key: str | None = None,
        timeout: float = 180.0,
    ):
        base = endpoint.rstrip("/")
        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")]
        parsed = urlparse(base)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("엔드포인트 URL이 올바르지 않습니다.")
        self.base = base
        self.model = model
        self.api_key = api_key or os.environ.get("STORY_CUT_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self.timeout = timeout

    def classify(self, frames: list[Path], transcript: str, ocr_text: str, shot_range: str) -> Classification:
        content: list[dict[str, Any]] = [{"type": "text", "text": _user_prompt(transcript, ocr_text, shot_range)}]
        for path in frames:
            content.append({"type": "image_url", "image_url": {"url": _encode_image(path)}})
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        }
        url = f"{self.base}/chat/completions"
        last_error: Exception | None = None
        for attempt, with_format in enumerate((True, True, False)):
            body = dict(payload)
            if with_format:
                body["response_format"] = {"type": "json_object"}
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    res = client.post(url, headers=headers, json=body)
                if res.status_code >= 400 and with_format and attempt == 0:
                    # Some local servers reject response_format.
                    continue
                res.raise_for_status()
                data = res.json()
                text = data["choices"][0]["message"]["content"]
                return parse_classification(text)
            except (httpx.HTTPError, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                continue
        raise RuntimeError(_friendly_http_error(last_error, self.base))


class GeminiBackend:
    name = "gemini"

    def __init__(self, model: str = "gemini-2.0-flash", api_key: str | None = None, timeout: float = 180.0):
        self.model = model
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "Gemini를 쓰려면 GEMINI_API_KEY 환경 변수를 설정하세요. "
                "Google AI Studio에서 키를 만들 수 있습니다."
            )
        self.timeout = timeout

    def classify(self, frames: list[Path], transcript: str, ocr_text: str, shot_range: str) -> Classification:
        parts: list[dict[str, Any]] = [{"text": SYSTEM_PROMPT + "\n\n" + _user_prompt(transcript, ocr_text, shot_range)}]
        for path in frames:
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(path.read_bytes()).decode("ascii")}})
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            f"?key={self.api_key}"
        )
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(url, json=payload)
                res.raise_for_status()
                data = res.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return parse_classification(text)
        except (httpx.HTTPError, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(_friendly_http_error(exc, "https://generativelanguage.googleapis.com")) from exc


class MockBackend:
    """Deterministic labels for tests (no local model required)."""

    name = "mock"
    model = "mock"

    _cycle = ("cutscene", "dialogue", "combat", "exploration", "menu", "other")

    def __init__(self, index_start: int = 0):
        self._i = index_start

    def classify(self, frames: list[Path], transcript: str, ocr_text: str, shot_range: str) -> Classification:
        label = self._cycle[self._i % len(self._cycle)]
        self._i += 1
        reason = "mock backend (테스트용 순환 라벨)"
        if transcript.strip():
            reason += "; transcript present"
        return Classification(label=label, confidence=0.9, reason=reason)


def build_backend(name: str, *, endpoint: str, model: str, api_key: str | None) -> VisionBackend:
    key = name.lower().strip()
    if key in {"mock"}:
        return MockBackend()
    if key in {"gemini", "google"}:
        return GeminiBackend(model=model or "gemini-2.0-flash", api_key=api_key)
    if key in {"lmstudio", "openai", "openai-compatible", "openai_compatible", "local"}:
        return OpenAICompatibleBackend(
            endpoint=endpoint or "http://localhost:1234/v1",
            model=model or "qwen3-vl-8b",
            api_key=api_key,
        )
    raise ValueError(f"알 수 없는 백엔드입니다: {name} (lmstudio, gemini, mock 중 하나)")


def _friendly_http_error(exc: Exception | None, endpoint: str) -> str:
    if exc is None:
        return f"비전 모델 호출에 실패했습니다. 주소: {endpoint}"
    if isinstance(exc, httpx.ConnectError):
        return (
            f"비전 서버에 연결하지 못했습니다: {endpoint}\n"
            "LM Studio가 켜져 있고 로컬 서버(기본 포트 1234)가 시작됐는지 확인하세요. "
            "서버가 없다면 테스트용으로 --backend mock 을 쓸 수 있습니다."
        )
    if isinstance(exc, httpx.HTTPStatusError):
        body = (exc.response.text or "")[:400]
        return f"비전 서버가 오류를 반환했습니다 ({exc.response.status_code}). {body}"
    return f"비전 모델 호출에 실패했습니다: {exc}"
