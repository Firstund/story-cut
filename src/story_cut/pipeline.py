from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from story_cut.cache import AnalysisCache
from story_cut.classify import PROMPT_VERSION, Classification, VisionBackend, build_backend
from story_cut.fftool import MediaInfo, probe
from story_cut.frames import extract_frames
from story_cut.merge import (
    AnalyzedShot,
    KeptRange,
    apply_keep,
    drop_short_ranges,
    loud_threshold,
    merge_kept,
    select_within_budget,
)
from story_cut.modes import ModePreset, PromptPlan, plan_prompt, resolve_preset
from story_cut.export_openreel import build_openreel_edit
from story_cut.report import write_reports
from story_cut.scenes import SCENES_VERSION, Shot, detect_shots, shots_to_json
from story_cut.transcribe import Transcript, empty_transcript, transcribe, transcript_from_json, transcript_to_json


@dataclass
class AnalyzeOptions:
    video: Path
    out: Path
    backend: str = "lmstudio"
    endpoint: str = "http://localhost:1234/v1"
    model: str = "qwen3-vl-8b"
    api_key: str | None = None
    # Edit mode (story, highlight, combat, talk, shorts) and/or a free-text request.
    mode: str | None = None
    prompt: str | None = None
    # None = use the mode preset value.
    keep: tuple[str, ...] | None = None
    pad: float | None = None
    min_shot: float | None = None
    max_segment: float | None = None
    min_confidence: float | None = None
    min_keep: float | None = None
    merge_gap: float | None = None
    max_total: float | None = None
    max_clip: float | None = None
    aspect: str | None = None
    silence_db: float = -35.0
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
    preset: ModePreset | None = None
    plan: PromptPlan | None = None
    sidecar: Path | None = None


def log(message: str) -> None:
    print(message, flush=True)


def progress(step: int, done: int | None = None, count: int | None = None) -> None:
    """Machine-readable progress for `story-cut serve` (one line, only when asked for)."""
    if os.environ.get("STORY_CUT_PROGRESS") == "1":
        print("@@progress " + json.dumps({"step": step, "steps": 6, "done": done, "count": count}), flush=True)


def resolve_options(opts: AnalyzeOptions) -> tuple[ModePreset, PromptPlan | None]:
    plan = None
    if opts.prompt and opts.prompt.strip():
        plan = plan_prompt(opts.prompt, backend=opts.backend, endpoint=opts.endpoint, model=opts.model, api_key=opts.api_key)
    overrides = {
        "keep": tuple(opts.keep) if opts.keep else None,
        "pad": opts.pad,
        "min_shot": opts.min_shot,
        "max_segment": opts.max_segment,
        "min_confidence": opts.min_confidence,
        "min_keep": opts.min_keep,
        "merge_gap": opts.merge_gap,
        "max_total": opts.max_total,
        "max_clip": opts.max_clip,
        "aspect": opts.aspect,
    }
    return resolve_preset(opts.mode, plan, overrides), plan


def analyze(opts: AnalyzeOptions) -> AnalyzeResult:
    video = opts.video.expanduser().resolve()
    out = opts.out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    cache_root = (opts.cache_dir or (out.parent / ".story-cut-cache")).expanduser().resolve()
    cache = AnalysisCache(cache_root, video)
    preset, plan = resolve_options(opts)
    if plan is not None:
        log(f"요청 해석: {plan.reason} ({'AI' if plan.source == 'llm' else '키워드'})")
    extra = f", 최대 {preset.max_total:.0f}초" if preset.max_total else ""
    log(f"편집 모드: {preset.title} ({preset.name}), 화면 {preset.aspect}{extra}")

    progress(1)
    log("[1/6] 영상 정보를 읽는 중입니다...")
    media = probe(video)
    log(f"    {media.filename}  {media.width}x{media.height}  {media.duration:.1f}초")

    progress(2)
    log("[2/6] 장면(샷)을 나누는 중입니다...")
    scenes_name = "scenes.json"
    if (preset.min_shot, preset.max_segment) != (1.5, 20.0):
        scenes_name = f"scenes-{preset.min_shot:g}-{preset.max_segment:g}.json"
    cached_shots = cache.read(scenes_name)
    scenes_ok = (
        isinstance(cached_shots, dict)
        and cached_shots.get("scenes_version") == SCENES_VERSION
        and cached_shots.get("min_shot") == preset.min_shot
        and cached_shots.get("max_segment") == preset.max_segment
        and isinstance(cached_shots.get("shots"), list)
    )
    if not preset.needs_vision:
        shots = []
        log("    이 모드는 장면 나누기가 필요 없습니다.")
    elif scenes_ok:
        shots = [Shot(**item) for item in cached_shots["shots"]]
        log(f"    캐시에서 {len(shots)}개 샷을 불러왔습니다.")
    else:
        shots = detect_shots(
            video,
            min_shot=preset.min_shot,
            max_segment=preset.max_segment,
            duration=media.duration,
        )
        cache.write(scenes_name, {
            "scenes_version": SCENES_VERSION,
            "min_shot": preset.min_shot,
            "max_segment": preset.max_segment,
            "shots": shots_to_json(shots),
        })
        log(f"    샷 {len(shots)}개를 찾았습니다.")

    progress(3)
    log("[3/6] 대사를 받아쓰는 중입니다...")
    cached_tr = cache.read("transcript.json")
    language_option = opts.language or "auto"
    tr_ok = (
        isinstance(cached_tr, dict)
        and cached_tr.get("whisper_model") == opts.whisper_model
        and cached_tr.get("language_option") == language_option
        and "text" in cached_tr
    )
    if opts.skip_transcribe:
        transcript = empty_transcript()
        log("    받아쓰기를 건너뛰었습니다.")
    elif tr_ok:
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
        cache.write("transcript.json", {
            "whisper_model": opts.whisper_model,
            "language_option": language_option,
            **transcript_to_json(transcript),
        })
        log(f"    언어: {transcript.language}, 단어 {len(transcript.words)}개")

    analyzed: list[AnalyzedShot] = []
    if preset.needs_vision:
        analyzed = _classify_shots(opts, video, cache, shots, transcript)
    else:
        log("[4/6] 이 모드는 AI 분류를 건너뜁니다.")

    progress(5)
    log(f"[5/6] {preset.title} 모드로 구간을 고르고 이어 붙입니다...")
    ranges = _select_ranges(preset, video, cache, media, analyzed, opts)
    kept_dur = sum(r.duration for r in ranges)
    log(f"    남긴 클립 {len(ranges)}개, 약 {kept_dur:.1f}초 / 원본 {media.duration:.1f}초")

    progress(6)
    log("[6/6] OpenReel 편집 초안과 보고서를 저장합니다...")
    doc = build_openreel_edit(media, ranges, transcript, aspect=preset.aspect, mode=preset.name)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    from story_cut.project import write_sidecar
    sidecar = write_sidecar(out, video=video, cache_root=cache.root, preset=preset, plan=plan, ranges=ranges, shots=analyzed, media=media)
    report_md = opts.report_md or out.with_suffix(".report.md")
    report_csv = opts.report_csv or out.with_suffix(".report.csv")
    write_reports(
        report_md,
        report_csv,
        video_name=media.filename,
        shots=analyzed,
        ranges=ranges,
        keep_labels=list(preset.keep) if preset.strategy != "speech" else ["speech"],
        mode_title=preset.title,
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
        preset=preset,
        plan=plan,
        sidecar=sidecar,
    )


def _select_ranges(
    preset: ModePreset,
    video: Path,
    cache: AnalysisCache,
    media: MediaInfo,
    analyzed: list[AnalyzedShot],
    opts: AnalyzeOptions,
) -> list[KeptRange]:
    if preset.strategy == "speech":
        from story_cut.audio import detect_silence, speech_ranges
        silences = detect_silence(video, noise_db=opts.silence_db, min_silence=preset.merge_gap, duration=media.duration)
        spans = speech_ranges(silences, media.duration, pad=preset.pad, min_keep=preset.min_keep)
        cut = sum(e - s for s, e in silences)
        log(f"    무음 {len(silences)}곳, 약 {cut:.1f}초를 찾았습니다.")
        ranges = [KeptRange(s, e, [], ["speech"], fixed_label="speech", fixed_reason="말소리 구간") for s, e in spans]
        if preset.max_total:
            ranges = _budget_by_loudness(ranges, video, cache, preset)
        return ranges

    use_loud = preset.loud_percentile > 0 or preset.strategy == "best"
    if use_loud and analyzed:
        try:
            from story_cut.audio import ensure_wav, load_wav, loudness_db
            samples = load_wav(ensure_wav(video, cache.root))
            for item in analyzed:
                item.loudness = loudness_db(samples, item.shot.start, item.shot.end)
        except Exception as exc:  # loudness is a bonus signal only
            log(f"    소리 크기 분석을 건너뜁니다: {exc}")
    threshold = loud_threshold(analyzed, preset.loud_percentile) if preset.loud_percentile > 0 else None
    apply_keep(analyzed, set(preset.keep), preset.min_confidence, loud_threshold=threshold)
    if threshold is not None:
        log(f"    소리가 큰 순간({threshold:.1f} dB 이상)도 남깁니다.")
    ranges = merge_kept(analyzed, pad=preset.pad, media_duration=media.duration, max_gap=preset.merge_gap)
    before = len(ranges)
    ranges = drop_short_ranges(ranges, preset.min_keep)
    if before and len(ranges) < before:
        log(f"    {preset.min_keep:.1f}초보다 짧은 조각 {before - len(ranges)}개를 버렸습니다.")
    if preset.max_total or preset.max_clip:
        total_before = sum(r.duration for r in ranges)
        ranges = select_within_budget(
            ranges,
            max_total=preset.max_total,
            max_clip=preset.max_clip,
            weights=preset.weights,
            min_piece=min(2.0, preset.min_keep or 2.0),
        )
        if preset.max_total and total_before > preset.max_total:
            log(f"    {preset.max_total:.0f}초 안에 들도록 가장 좋은 구간 {len(ranges)}개만 골랐습니다.")
    return ranges


def _budget_by_loudness(ranges: list[KeptRange], video: Path, cache: AnalysisCache, preset: ModePreset) -> list[KeptRange]:
    """Talk mode with a length cap: keep the liveliest (loudest) speech."""
    from story_cut.audio import ensure_wav, load_wav, loudness_db
    from story_cut.classify import Classification
    from story_cut.scenes import Shot as _Shot
    samples = load_wav(ensure_wav(video, cache.root))
    for i, item in enumerate(ranges):
        shot = AnalyzedShot(_Shot(i, item.start, item.end), Classification("speech", 1.0, "말소리 구간"), "")
        shot.loudness = loudness_db(samples, item.start, item.end)
        item.shots = [shot]
    picked = select_within_budget(ranges, max_total=preset.max_total, weights={"speech": 0.3}, min_piece=1.0)
    for item in picked:
        item.shots = []
    return picked


def _classify_shots(opts: AnalyzeOptions, video: Path, cache: AnalysisCache, shots: list[Shot], transcript: Transcript) -> list[AnalyzedShot]:
    backend = build_backend(opts.backend, endpoint=opts.endpoint, model=opts.model, api_key=opts.api_key)
    store = cache.get_classifications()
    analyzed: list[AnalyzedShot] = []
    progress(4, 0, len(shots))
    log(f"[4/6] 구간을 분류하는 중입니다... (백엔드: {backend.name}, 모델: {backend.model})")
    for shot in shots:
        key = cache.classification_key(shot.start, shot.end, backend.name, backend.model, PROMPT_VERSION)
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
        progress(4, len(analyzed) + 1, len(shots))
        analyzed.append(AnalyzedShot(
            shot=shot,
            classification=classification,
            transcript=snippet,
            ocr_text=ocr_text,
            frame_paths=[str(p) for p in frame_paths],
        ))

    return analyzed
