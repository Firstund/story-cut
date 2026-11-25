from __future__ import annotations

from pathlib import Path


def read_dialogue_boxes(frame_paths: list[Path], languages: list[str] | None = None) -> str:
    """Optional EasyOCR pass over sampled frames. Import is delayed so the extra is optional."""
    try:
        import easyocr  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "OCR를 쓰려면 EasyOCR이 필요합니다. 다음을 실행하세요:\n"
            "  python -m pip install -r requirements-ocr.txt"
        ) from exc

    langs = languages or ["ko", "en", "ja"]
    reader = easyocr.Reader(langs, gpu=False)
    lines: list[str] = []
    seen: set[str] = set()
    for path in frame_paths:
        results = reader.readtext(str(path), detail=1, paragraph=True)
        for item in results:
            text = item[1] if len(item) > 1 else str(item)
            text = " ".join(str(text).split())
            if text and text not in seen:
                seen.add(text)
                lines.append(text)
    return "\n".join(lines)
