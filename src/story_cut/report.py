from __future__ import annotations

import csv
from pathlib import Path

from story_cut.merge import AnalyzedShot, KeptRange


def write_reports(
    md_path: Path,
    csv_path: Path,
    *,
    video_name: str,
    shots: list[AnalyzedShot],
    ranges: list[KeptRange],
    keep_labels: list[str],
    mode_title: str = "스토리",
) -> None:
    md_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    kept_dur = sum(r.duration for r in ranges)
    total = shots[-1].shot.end if shots else (ranges[-1].end if ranges else 0.0)
    lines = [
        f"# {video_name} {mode_title} 구간 보고서",
        "",
        f"- 원본 길이: **{total:.2f}초**",
        f"- 남긴 구간: **{kept_dur:.2f}초** ({len(ranges)}개 클립)",
        f"- 남긴 라벨: {', '.join(keep_labels)}",
        "",
    ]
    if shots:
        lines += [
            "## 샷 목록",
            "",
            "| # | 시작 | 끝 | 길이 | 라벨 | 신뢰도 | 남김 | 대사 일부 | 이유 |",
            "|---|------|----|------|------|--------|------|-----------|------|",
        ]
    for item in shots:
        snip = (item.transcript or "").replace("|", " ").replace("\n", " ")
        if len(snip) > 40:
            snip = snip[:37] + "..."
        reason = item.classification.reason.replace("|", " ").replace("\n", " ")
        keep = "예" if item.keep else "아니오"
        lines.append(
            f"| {item.shot.index} | {item.shot.start:.2f} | {item.shot.end:.2f} | "
            f"{item.shot.duration:.2f} | {item.classification.label} | "
            f"{item.classification.confidence:.2f} | {keep} | {snip} | {reason} |"
        )
    lines += ["", "## 합쳐진 출력 클립", ""]
    if not ranges:
        lines.append("남긴 구간이 없습니다. 라벨이나 신뢰도 기준을 확인해 주세요.")
    else:
        lines += [
            "| 출력 시작 | 원본 시작 | 원본 끝 | 길이 | 라벨 | 신뢰도 |",
            "|-----------|-----------|----------|------|------|--------|",
        ]
        cursor = 0.0
        for item in ranges:
            lines.append(
                f"| {cursor:.2f} | {item.start:.2f} | {item.end:.2f} | "
                f"{item.duration:.2f} | {item.primary_label} | {item.confidence:.2f} |"
            )
            cursor += item.duration
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "index", "start", "end", "duration", "label", "confidence",
            "keep", "transcript", "ocr", "reason",
        ])
        for item in shots:
            writer.writerow([
                item.shot.index,
                f"{item.shot.start:.3f}",
                f"{item.shot.end:.3f}",
                f"{item.shot.duration:.3f}",
                item.classification.label,
                f"{item.classification.confidence:.3f}",
                "yes" if item.keep else "no",
                item.transcript,
                item.ocr_text,
                item.classification.reason,
            ])
