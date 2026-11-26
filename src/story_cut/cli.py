from __future__ import annotations

import argparse
import sys
from pathlib import Path

from story_cut import LABELS, __version__
from story_cut.modes import ALIASES, MODES


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
        description="게임/방송 영상을 분석해 OpenReel 편집 초안(JSON)을 만듭니다. 스토리, 하이라이트, 전투, 무음 컷, 쇼츠 모드를 지원합니다.",
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
        "--mode",
        default=None,
        choices=sorted(MODES) + sorted(ALIASES),
        help="편집 모드: story(기본), highlight, combat, talk(무음 컷), shorts(세로 60초). `story-cut modes`로 설명 보기",
    )
    analyze.add_argument(
        "--prompt",
        default=None,
        help='원하는 편집을 말로 적기. 예: "보스전 위주로 3분 하이라이트", "쇼츠로 만들어줘". --mode를 같이 주면 --mode가 우선',
    )
    analyze.add_argument(
        "--keep",
        default=None,
        help=f"남길 라벨. 쉼표로 구분. 가능한 값: {', '.join(LABELS)}. 기본값은 모드마다 다름 (스토리: cutscene,dialogue)",
    )
    analyze.add_argument("--max-total", type=float, default=None, help="결과 영상 최대 길이(초). 넘으면 좋은 구간만 고릅니다. 쇼츠 기본 58")
    analyze.add_argument("--max-clip", type=float, default=None, help="클립 하나의 최대 길이(초). 쇼츠 기본 12")
    analyze.add_argument("--aspect", default=None, choices=["source", "9:16", "1:1", "16:9"], help="화면 비율. 쇼츠는 9:16")
    analyze.add_argument("--silence-db", type=float, default=-35.0, help="무음 컷: 이 소리 크기(dB)보다 작으면 무음. 기본 -35")
    analyze.add_argument("--pad", type=float, default=None, help="남긴 구간 앞뒤에 붙일 여유(초). 스토리 기본 0.25")
    analyze.add_argument("--min-shot", type=float, default=None, help="이보다 짧은 샷은 이웃과 합칩니다. 기본 1.5")
    analyze.add_argument("--max-segment", type=float, default=None, help="한 구간의 최대 길이(초). 기본 20")
    analyze.add_argument("--min-confidence", type=float, default=None, help="이 신뢰도 미만은 버립니다. 스토리 기본 0")
    analyze.add_argument("--min-keep", type=float, default=None, help="이보다 짧은 남긴 구간(초)은 버립니다. 스토리 기본 2")
    analyze.add_argument("--merge-gap", type=float, default=None, help="이 간격(초) 안의 남긴 구간은 하나로 합칩니다 (무음 컷에서는 이보다 긴 무음만 자름). 스토리 기본 1")
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

    fix = sub.add_parser(
        "fix-json",
        help="이미 만든 openreel-edit JSON에서 OpenReel이 거부하는 메타데이터 키를 제거합니다.",
    )
    fix.add_argument("input", type=Path, help="고칠 edit.json")
    fix.add_argument("output", type=Path, help="저장할 JSON 경로")

    sub.add_parser("modes", help="편집 모드 목록을 보여 줍니다.")

    render = sub.add_parser("render", help="edit.json을 PC에서 바로 MP4로 만듭니다 (컷, 자막, 세로 자르기).")
    render.add_argument("edit", type=Path, help="openreel-edit JSON")
    render.add_argument("video", type=Path, help="원본 영상")
    render.add_argument("--out", type=Path, required=True, help="저장할 mp4")
    render.add_argument("--burn-subs", action="store_true", help="자막을 영상에 새겨 넣습니다")
    render.add_argument("--encoder", default="libx264", help="영상 인코더. 기본 libx264. AMD 그래픽카드 가속: h264_amf")

    serve = sub.add_parser("serve", help="휴대폰/브라우저에서 쓰는 웹 화면을 켭니다.")
    serve.add_argument("--videos", type=Path, default=Path("."), help="영상이 있는 폴더 (하위 폴더 포함). 기본: 현재 폴더")
    serve.add_argument("--host", default="127.0.0.1", help="0.0.0.0이면 같은 와이파이의 휴대폰에서도 접속됩니다. 기본 127.0.0.1 (이 PC만)")
    serve.add_argument("--port", type=int, default=8765, help="포트. 기본 8765")
    serve.add_argument("--token", default=None, help="접속 비밀번호. 안 주면 자동으로 만들어 저장합니다")
    serve.add_argument("--no-token", action="store_true", help="비밀번호 없이 엽니다 (이 PC에서만 쓸 때)")
    serve.add_argument("--work-dir", type=Path, default=None, help="결과 저장 폴더. 기본: <영상 폴더>\\story-cut-results")
    serve.add_argument("--openreel-url", default=None, help="OpenReel 주소. 기본: 같은 주소의 5173 포트")
    serve.add_argument("--backend", default="lmstudio", choices=["lmstudio", "openai-compatible", "gemini", "mock"])
    serve.add_argument("--endpoint", default="http://localhost:1234/v1")
    serve.add_argument("--model", default="qwen/qwen3-vl-8b")
    serve.add_argument("--language", default="ko")
    serve.add_argument("--whisper-model", default="small")
    serve.add_argument("--cache-dir", type=Path, default=None, help="분석 캐시 폴더. 기본: 현재 폴더에 .story-cut-cache가 있으면 그것, 없으면 결과 폴더 안")
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

        if args.command == "fix-json":
            from story_cut.portable import fix_openreel_edit_file
            dest = fix_openreel_edit_file(args.input, args.output)
            print(f"정리한 초안을 저장했습니다: {dest}")
            return 0

        if args.command == "modes":
            for m in MODES.values():
                extra = []
                if not m.needs_vision:
                    extra.append("AI 모델 불필요")
                if m.aspect != "source":
                    extra.append(m.aspect)
                if m.max_total:
                    extra.append(f"최대 {m.max_total:.0f}초")
                tail = f" ({', '.join(extra)})" if extra else ""
                print(f"{m.name:10s} {m.title}: {m.description}{tail}")
            return 0

        if args.command == "render":
            from story_cut.render import render_edit
            last = [-1]
            def show(p: float) -> None:
                pct = int(p * 100)
                if pct // 5 != last[0] // 5:
                    last[0] = pct
                    print(f"    {pct}%", flush=True)
            dest = render_edit(args.edit, args.video, args.out, burn_subtitles=args.burn_subs, encoder=args.encoder, on_progress=show)
            print(f"영상을 만들었습니다: {dest}")
            return 0

        if args.command == "serve":
            from story_cut.server import ServeOptions, run_server
            run_server(ServeOptions(
                videos=args.videos,
                host=args.host,
                port=args.port,
                token=None if args.no_token else args.token,
                no_token=args.no_token,
                work_dir=args.work_dir,
                openreel_url=args.openreel_url,
                backend=args.backend,
                endpoint=args.endpoint,
                model=args.model,
                language=args.language,
                whisper_model=args.whisper_model,
                cache_dir=args.cache_dir,
            ))
            return 0

        keep = None
        if args.keep:
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
            mode=args.mode,
            prompt=args.prompt,
            keep=keep,
            max_total=args.max_total,
            max_clip=args.max_clip,
            aspect=args.aspect,
            silence_db=args.silence_db,
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
