from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Must match OpenReel packages/core/src/editing/portable-project.ts metadataSchema.
PORTABLE_MEDIA_METADATA_KEYS = (
    "duration",
    "width",
    "height",
    "frameRate",
    "codec",
    "sampleRate",
    "channels",
    "fileSize",
    "audioTrackCount",
    "audioStreamChannels",
    "hasVideo",
    "hasAudio",
)
REQUIRED_MEDIA_METADATA = (
    "duration",
    "width",
    "height",
    "frameRate",
    "codec",
    "sampleRate",
    "channels",
    "fileSize",
)


def sanitize_portable_edit(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a copy whose media metadata only uses portable-schema keys.

    OpenReel's parsePortableEdit / validatePortableEdit reject unknown
    metadata fields such as sourceColor with "unsupported property".
    """
    out = json.loads(json.dumps(doc))
    if out.get("format") != "openreel-edit":
        raise ValueError("format이 openreel-edit가 아닙니다.")
    version = out.get("version")
    if version not in (3, 4):
        raise ValueError(f"지원하지 않는 openreel-edit 버전입니다: {version}")
    project = out.get("project")
    if not isinstance(project, dict):
        raise ValueError("project 객체가 없습니다.")
    media = project.get("media")
    if not isinstance(media, list) or not media:
        raise ValueError("project.media가 비어 있습니다.")
    for index, item in enumerate(media):
        if not isinstance(item, dict):
            raise ValueError(f"project.media[{index}]가 객체가 아닙니다.")
        meta = item.get("metadata")
        if not isinstance(meta, dict):
            raise ValueError(f"project.media[{index}].metadata가 없습니다.")
        cleaned = {key: meta[key] for key in PORTABLE_MEDIA_METADATA_KEYS if key in meta}
        missing = [key for key in REQUIRED_MEDIA_METADATA if key not in cleaned]
        if missing:
            raise ValueError(
                f"project.media[{index}].metadata에 필수 키가 없습니다: {', '.join(missing)}"
            )
        item["metadata"] = cleaned
    return out


def fix_openreel_edit_file(src: Path, dest: Path) -> Path:
    src = src.expanduser()
    dest = dest.expanduser()
    raw = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("JSON 루트가 객체가 아닙니다.")
    fixed = sanitize_portable_edit(raw)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(fixed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest.resolve()
