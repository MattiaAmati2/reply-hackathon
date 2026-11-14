import re
from pathlib import Path
from typing import Any, Optional

from backend.schemas import SlideMetadata, TranscriptSegment


class AudioProcessingError(RuntimeError):
    pass


_STOPWORDS = frozenset(
    {
        # English
        "that", "this", "with", "from", "your", "have", "will", "what", "when",
        "where", "which", "there", "their", "about", "they", "been", "were",
        "into", "more", "some", "than", "then", "them", "these", "those",
        "also", "only", "very", "such", "each", "other", "over", "most",
        # Italian
        "della", "dello", "degli", "delle", "nella", "nello", "negli", "nelle",
        "questo", "questa", "questi", "queste", "quello", "quella", "quelli",
        "quelle", "sono", "siamo", "siete", "come", "dove", "quando", "perché",
        "perche", "anche", "molto", "tutto", "tutti", "tutte", "essere", "avere",
        "fare", "stato", "stata", "stati", "state", "alla", "allo", "agli",
        "alle", "dalla", "dallo", "dagli", "dalle", "sulla", "sullo", "sugli",
        "sulle", "con", "per", "tra", "fra", "che", "non", "una", "uno",
    }
)

_FILLER_PATTERN = re.compile(
    r"\b(?:ehm|uhm|cioè|tipo|allora|um|uh|like|basically)\b",
    re.IGNORECASE,
)


class AudioService:
    """Transcribes audio with faster-whisper and computes speech delivery metrics."""

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        cpu_threads: int = 4,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self._model: Any = None

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise AudioProcessingError(
                    "faster-whisper is not installed"
                ) from exc
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=self.cpu_threads,
            )
        return self._model

    @staticmethod
    def build_initial_prompt(slides: list[SlideMetadata]) -> Optional[str]:
        keywords: list[str] = []
        seen: set[str] = set()
        for slide in slides:
            tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9_-]{4,}", slide.clean_text)
            for token in tokens:
                lower = token.lower()
                if lower in _STOPWORDS or lower in seen:
                    continue
                if token[0].isupper() or any(ch.isdigit() or ch in "_-" for ch in token):
                    seen.add(lower)
                    keywords.append(token)
                    if len(keywords) >= 100:
                        return ", ".join(keywords)
        return ", ".join(keywords) if keywords else None

    def transcribe(
        self,
        audio_path: Path | str,
        initial_prompt: Optional[str] = None,
        language: Optional[str] = None,
    ) -> list[TranscriptSegment]:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise AudioProcessingError(f"Audio file not found: {audio_path}")

        model = self._get_model()
        try:
            segments_iter, _info = model.transcribe(
                str(audio_path),
                language=language,
                initial_prompt=initial_prompt,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False,
            )
            return [
                TranscriptSegment(
                    start=float(seg.start),
                    end=float(seg.end),
                    text=str(seg.text).strip(),
                )
                for seg in segments_iter
                if str(seg.text).strip()
            ]
        except Exception as exc:
            raise AudioProcessingError(
                f"Transcription failed for {audio_path}: {exc}"
            ) from exc

    @staticmethod
    def compute_speech_metrics(
        segments: list[TranscriptSegment],
        start_time: float,
        end_time: float,
    ) -> dict[str, float | int]:
        overlapping = [
            seg
            for seg in segments
            if (start_time <= seg.start < end_time)
            or (start_time <= (seg.start + seg.end) / 2.0 < end_time)
        ]
        if not overlapping:
            return {"wpm": 0.0, "pause_count": 0, "filler_count": 0}

        full_text = " ".join(seg.text for seg in overlapping if seg.text)
        word_count = len(full_text.split())
        duration_minutes = max((end_time - start_time) / 60.0, 1e-6)
        wpm = round(word_count / duration_minutes, 2)

        pause_count = 0
        ordered = sorted(overlapping, key=lambda s: s.start)
        for idx in range(len(ordered) - 1):
            if ordered[idx + 1].start - ordered[idx].end > 1.5:
                pause_count += 1

        filler_count = len(_FILLER_PATTERN.findall(full_text))
        return {
            "wpm": wpm,
            "pause_count": pause_count,
            "filler_count": filler_count,
        }
