from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from story_cut.fftool import run


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Transcript:
    language: str
    text: str
    words: list[Word] = field(default_factory=list)

    def snippet(self, start: float, end: float) -> str:
        bits = [w.text for w in self.words if w.end > start and w.start < end]
        if bits:
            return " ".join(bits).strip()
        return ""


def _extract_mono16k(video_path: Path, wav_path: Path) -> Path:
    """Decode with system ffmpeg so we are not tied to PyAV's API."""
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(wav_path),
    ], capture=True)
    return wav_path


def _load_wav_f32(wav_path: Path) -> np.ndarray:
    import wave
    with wave.open(str(wav_path), "rb") as fh:
        if fh.getnchannels() != 1 or fh.getsampwidth() != 2 or fh.getframerate() != 16000:
            raise RuntimeError("받아쓰기용 오디오 형식이 예상과 다릅니다 (16 kHz mono s16).")
        data = np.frombuffer(fh.readframes(fh.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0


def transcribe(
    video_path: Path,
    *,
    model_size: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str | None = None,
    work_dir: Path | None = None,
) -> Transcript:
    from faster_whisper import WhisperModel

    cleanup = False
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="story-cut-whisper-"))
        cleanup = True
    wav_path = Path(work_dir) / f"{video_path.stem}.16k.wav"
    try:
        _extract_mono16k(video_path, wav_path)
        audio = _load_wav_f32(wav_path)
        try:
            model = WhisperModel(model_size, device=device, compute_type=compute_type)
        except Exception:
            if compute_type != "float32":
                model = WhisperModel(model_size, device=device, compute_type="float32")
            else:
                raise
        segments, info = model.transcribe(
            audio,
            language=language or None,
            word_timestamps=True,
            vad_filter=True,
            beam_size=1,
        )
        words: list[Word] = []
        texts: list[str] = []
        for segment in segments:
            texts.append(segment.text.strip())
            for word in segment.words or []:
                token = (word.word or "").strip()
                if not token:
                    continue
                words.append(Word(text=token, start=float(word.start), end=float(word.end)))
        lang = getattr(info, "language", None) or language or "unknown"
        return Transcript(language=str(lang), text=" ".join(t for t in texts if t).strip(), words=words)
    finally:
        if cleanup:
            try:
                wav_path.unlink(missing_ok=True)
                work_dir.rmdir()
            except OSError:
                pass


def empty_transcript() -> Transcript:
    return Transcript(language="unknown", text="", words=[])


def transcript_to_json(tr: Transcript) -> dict:
    return {
        "language": tr.language,
        "text": tr.text,
        "words": [asdict(w) for w in tr.words],
    }


def transcript_from_json(data: dict) -> Transcript:
    words = [Word(**w) for w in data.get("words") or []]
    return Transcript(language=data.get("language") or "unknown", text=data.get("text") or "", words=words)
