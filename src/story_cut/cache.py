from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def fingerprint(path: Path) -> str:
    st = path.stat()
    raw = f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class AnalysisCache:
    def __init__(self, root: Path, video: Path):
        self.root = root / fingerprint(video)
        self.root.mkdir(parents=True, exist_ok=True)
        self.frames_dir = self.root / "frames"
        self.frames_dir.mkdir(exist_ok=True)

    def read(self, name: str) -> Any | None:
        path = self.root / name
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, name: str, data: Any) -> None:
        path = self.root / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def classification_key(self, start: float, end: float, backend: str, model: str) -> str:
        raw = f"{start:.3f}:{end:.3f}:{backend}:{model}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def get_classifications(self) -> dict[str, Any]:
        data = self.read("classifications.json")
        return data if isinstance(data, dict) else {}

    def put_classification(self, key: str, value: dict[str, Any]) -> None:
        store = self.get_classifications()
        store[key] = value
        self.write("classifications.json", store)
