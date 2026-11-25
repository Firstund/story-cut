from __future__ import annotations

import argparse
import sys
from pathlib import Path

from story_cut import DEFAULT_KEEP, LABELS, __version__


def _configure_stdio() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="story-cut",
        description="게임 플레이 영상에서 스토리 구간을 골라 OpenReel 편집 초안(JSON)을 만듭니다.",
    )
    parser.add_argument("--version", action="version", version=f"story-cut {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="영상을 분석하고 편집 초안을 저장합니다.")
    analyze.add_argument("input", type=Path, help="분석할 영상 파일 (mp4 등)")
    analyze.add_argument("--out", type=Path, required=True, help="OpenReel openreel-edit v3 JSON 저장 경로")
    analyze.add_argument(
        "--backend",
        default="lmstudio",
        choices=["lmstudio", "openai-compatible", "gemini", "mock"],
        help="구간 분류 백엔드. 기본값은 LM Studio (로컬). 테스트는 mock.",
    )
    analyze.add_argument(
        "--endpoint",
        default="http://localhost:1234/v1",
        help="OpenAI 호환 서버 주소. 기본값: http://localhost:1234/v1",
    )
    analyze.add_argument(
        "--model",
        default="qwen3-vl-8b",
        help="모델 이름. LM Studio에 보이는 이름을 그대로 넣으세요. 예: qwen/qwen3-vl-8b",
    )
    analyze.add_argument("--api-key", default=None, help="API 키 (로컬 LM Studio는 보통 필요 없음)")
    analyze.add_argument(
        "--keep",
        default=",".join(DEFAULT_KEEP),
        help=f"남길 라벨. 쉼표로 구분. 가능한 값: {', '.join(LABELS)}. 기본값: cutscene,dialogue",
    )
    analyze.add_argument("--pad", type=float, default=0.25, help="남긴 구간 앞뒤에 붙일 여유(초). 기본 0.25")
    analyze.add_argument("--min-shot", type=float, default=1.5, help="이보다 짧은 샷은 이웃과 합칩니다. 기본 1.5")
    analyze.add_argument("--max-segment", type=float, default=20.0, help="한 구간의 최대 길이(초). 기본 20")
    analyze.add_argument("--min-confidence", type=float, default=0.0, help="이 신뢰도 미만은 버립니다. 기본 0")
    analyze.add_argument("--min-keep", type=float, default=2.0, help="이보다 짧은 남긴 구간(초)은 버립니다. 기본 2")
    analyze.add_argument("--merge-gap", type=float, default=1.0, help="이 간격(초) 안의 남긴 구간은 하나로 합칩니다. 기본 1")
    analyze.add_argument("--frames", type=int, default=3, choices=(2, 3, 4), help="구간마다 보낼 프레임 수. 기본 3")
    analyze.add_argument("--ocr", action="store_true", help="대사창 OCR을 켭니다. EasyOCR 별도 설치 필요.")
    analyze.add_argument("--cache-dir", type=Path, default=None, help="이어서 분석할 캐시 폴더")
    analyze.add_argument("--whisper-model", default="small", help="faster-whisper 모델. 기본 small. 빠르게는 tiny/base")
    analyze.add_argument("--device", default="cpu", help="받아쓰기 장치. AMD 그래픽카드는 cpu를 쓰세요. 기본 cpu")
    analyze.add_argument("--compute-type", default="int8", help="받아쓰기 연산 형식. 기본 int8 (CPU)")
    analyze.add_argument(
        "--language",
        default="auto",
        help="받아쓰기 언어. auto면 자동. 한국어 방송은 ko를 권장합니다.",
    )
    analyze.add_argument("--skip-transcribe", action="store_true", help="받아쓰기를 건너뜁니다.")
    analyze.add_argument("--report-md", type=Path, default=None, help="마크다운 보고서 경로")
    analyze.add_argument("--report-csv", type=Path, default=None, help="CSV 보고서 경로")

    sample = sub.add_parser("sample", help="테스트용 짧은 색 전환 영상을 만듭니다.")
    sample.add_argument("--out", type=Path, required=True, help="저장할 mp4 경로")
    sample.add_argument("--scenes", type=int, default=4, help="장면 수. 기본 4")
    sample.add_argument("--scene-seconds", type=float, default=3.0, help="장면당 초. 기본 3")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "sample":
            from story_cut.fftool import make_sample_video
            dest = make_sample_video(args.out.expanduser(), scene_seconds=args.scene_seconds, scenes=args.scenes)
            print(f"테스트 영상을 만들었습니다: {dest}")
            return 0

        keep = tuple(part.strip() for part in str(args.keep).split(",") if part.strip())
        unknown = [k for k in keep if k not in LABELS]
        if unknown:
            parser.error(f"알 수 없는 라벨입니다: {', '.join(unknown)}")
        language = None if str(args.language).lower() in {"", "auto"} else str(args.language)
        model = args.model
        if args.backend == "gemini" and model == "qwen3-vl-8b":
            model = "gemini-2.0-flash"

        from story_cut.pipeline import AnalyzeOptions, analyze
        analyze(AnalyzeOptions(
            video=args.input,
            out=args.out,
            backend=args.backend,
            endpoint=args.endpoint,
            model=model,
            api_key=args.api_key,
            keep=keep or DEFAULT_KEEP,
            pad=args.pad,
            min_shot=args.min_shot,
            max_segment=args.max_segment,
            min_confidence=args.min_confidence,
            min_keep=args.min_keep,
            merge_gap=args.merge_gap,
            frame_count=args.frames,
            ocr=args.ocr,
            cache_dir=args.cache_dir,
            whisper_model=args.whisper_model,
            whisper_device=args.device,
            whisper_compute=args.compute_type,
            language=language,
            skip_transcribe=args.skip_transcribe,
            report_md=args.report_md,
            report_csv=args.report_csv,
        ))
        return 0
    except KeyboardInterrupt:
        print("중단했습니다.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
