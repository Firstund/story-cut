from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from story_cut.cache import AnalysisCache
from story_cut.classify import Classification, VisionBackend, build_backend
from story_cut.fftool import MediaInfo, probe
from story_cut.frames import extract_frames
from story_cut.merge import AnalyzedShot, KeptRange, apply_keep, merge_kept
from story_cut.export_openreel import build_openreel_edit
from story_cut.report import write_reports
from story_cut.scenes import Shot, detect_shots, shots_to_json
from story_cut.transcribe import Transcript, empty_transcript, transcribe, transcript_from_json, transcript_to_json


@dataclass
class AnalyzeOptions:
    video: Path
    out: Path
    backend: str = "lmstudio"
    endpoint: str = "http://localhost:1234/v1"
    model: str = "qwen3-vl-8b"
    api_key: str | None = None
    keep: tuple[str, ...] = ("cutscene", "dialogue")
    pad: float = 0.25
    min_shot: float = 0.5
    max_segment: float = 20.0
    min_confidence: float = 0.0
    frame_count: int = 3
    ocr: bool = False
    cache_dir: Path | None = None
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute: str = "int8"
    language: str | None = None
    skip_transcribe: bool = False
    report_md: Path | None = None
    report_csv: Path | None = None


@dataclass
class AnalyzeResult:
    media: MediaInfo
    shots: list[AnalyzedShot]
    ranges: list[KeptRange]
    transcript: Transcript
    edit_path: Path
    report_md: Path
    report_csv: Path


def log(message: str) -> None:
    print(message, flush=True)


def analyze(opts: AnalyzeOptions) -> AnalyzeResult:
    video = opts.video.expanduser().resolve()
    out = opts.out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    cache_root = (opts.cache_dir or (out.parent / ".story-cut-cache")).expanduser().resolve()
    cache = AnalysisCache(cache_root, video)

    log("[1/6] 영상 정보를 읽는 중입니다...")
    media = probe(video)
    log(f"    {media.filename}  {media.width}x{media.height}  {media.duration:.1f}초")

    log("[2/6] 장면(샷)을 나누는 중입니다...")
    cached_shots = cache.read("scenes.json")
    if cached_shots:
        shots = [Shot(**item) for item in cached_shots]
        log(f"    캐시에서 {len(shots)}개 샷을 불러왔습니다.")
    else:
        shots = detect_shots(
            video,
            min_shot=opts.min_shot,
            max_segment=opts.max_segment,
            duration=media.duration,
        )
        cache.write("scenes.json", shots_to_json(shots))
        log(f"    샷 {len(shots)}개를 찾았습니다.")

    log("[3/6] 대사를 받아쓰는 중입니다...")
    cached_tr = cache.read("transcript.json")
    if opts.skip_transcribe:
        transcript = empty_transcript()
        log("    받아쓰기를 건너뛰었습니다.")
    elif cached_tr:
        transcript = transcript_from_json(cached_tr)
        log(f"    캐시된 받아쓰기 (언어: {transcript.language}, 단어 {len(transcript.words)}개)")
    else:
        transcript = transcribe(
            video,
            model_size=opts.whisper_model,
            device=opts.whisper_device,
            compute_type=opts.whisper_compute,
            language=opts.language,
            work_dir=cache.root,
        )
        cache.write("transcript.json", transcript_to_json(transcript))
        log(f"    언어: {transcript.language}, 단어 {len(transcript.words)}개")

    backend = build_backend(opts.backend, endpoint=opts.endpoint, model=opts.model, api_key=opts.api_key)
    store = cache.get_classifications()
    analyzed: list[AnalyzedShot] = []
    log(f"[4/6] 구간을 분류하는 중입니다... (백엔드: {backend.name}, 모델: {backend.model})")
    for shot in shots:
        key = cache.classification_key(shot.start, shot.end, backend.name, backend.model)
        snippet = transcript.snippet(shot.start, shot.end)
        ocr_text = ""
        frame_paths: list[Path] = []
        cached = store.get(key)
        if cached and cached.get("label"):
            classification = Classification(
                label=cached["label"],
                confidence=float(cached.get("confidence") or 0),
                reason=str(cached.get("reason") or ""),
                cached=True,
            )
            ocr_text = str(cached.get("ocr_text") or "")
            log(f"    샷 {shot.index}: {classification.label} (캐시, {shot.start:.1f}-{shot.end:.1f}s)")
        else:
            nframes = opts.frame_count if shot.duration >= 2 else 2
            frame_paths = extract_frames(video, shot, cache.frames_dir, count=nframes)
            if opts.ocr:
                from story_cut.ocr import read_dialogue_boxes
                try:
                    ocr_text = read_dialogue_boxes(frame_paths)
                except RuntimeError as exc:
                    raise
                except Exception as exc:  # OCR should not abort the whole job
                    log(f"    샷 {shot.index}: OCR 실패 ({exc})")
                    ocr_text = ""
            try:
                classification = backend.classify(
                    frame_paths,
                    snippet,
                    ocr_text,
                    f"{shot.start:.2f}-{shot.end:.2f}",
                )
            except Exception as exc:
                log(f"    샷 {shot.index}: 분류 실패, other로 표시합니다. ({exc})")
                classification = Classification(label="other", confidence=0.0, reason=f"분류 실패: {exc}")
            else:
                cache.put_classification(key, {
                    "label": classification.label,
                    "confidence": classification.confidence,
                    "reason": classification.reason,
                    "ocr_text": ocr_text,
                    "start": shot.start,
                    "end": shot.end,
                })
                store = cache.get_classifications()
            log(f"    샷 {shot.index}: {classification.label} ({classification.confidence:.2f}, {shot.start:.1f}-{shot.end:.1f}s)")
        analyzed.append(AnalyzedShot(
            shot=shot,
            classification=classification,
            transcript=snippet,
            ocr_text=ocr_text,
            frame_paths=[str(p) for p in frame_paths],
        ))

    log("[5/6] 스토리 구간을 남기고 이어 붙입니다...")
    apply_keep(analyzed, set(opts.keep), opts.min_confidence)
    ranges = merge_kept(analyzed, pad=opts.pad, media_duration=media.duration)
    kept_dur = sum(r.duration for r in ranges)
    log(f"    남긴 클립 {len(ranges)}개, 약 {kept_dur:.1f}초 / 원본 {media.duration:.1f}초")

    log("[6/6] OpenReel 편집 초안과 보고서를 저장합니다...")
    doc = build_openreel_edit(media, ranges, transcript)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    report_md = opts.report_md or out.with_suffix(".report.md")
    report_csv = opts.report_csv or out.with_suffix(".report.csv")
    write_reports(
        report_md,
        report_csv,
        video_name=media.filename,
        shots=analyzed,
        ranges=ranges,
        keep_labels=list(opts.keep),
    )
    log(f"    편집 초안: {out}")
    log(f"    보고서: {report_md}")
    log(f"    표: {report_csv}")
    if not ranges:
        log("    주의: 남긴 구간이 없습니다. 보고서에서 라벨을 확인하세요.")
    return AnalyzeResult(
        media=media,
        shots=analyzed,
        ranges=ranges,
        transcript=transcript,
        edit_path=out,
        report_md=report_md,
        report_csv=report_csv,
    )
