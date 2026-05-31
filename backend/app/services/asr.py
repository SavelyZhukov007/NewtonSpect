from __future__ import annotations

from pathlib import Path
from typing import Any

from ..schemas import TranscriptSegment


class ASRService:
    def __init__(self, model_name: str = "large-v3") -> None:
        self.model_name = model_name
        self._model = None

    def _load_model(self, quality_preset: str) -> Any:
        if self._model is not None:
            return self._model

        from faster_whisper import WhisperModel  # Lazy import for faster startup

        compute_type = {
            "max_quality": "float32",
            "balanced": "int8_float32",
            "max_speed": "int8",
        }.get(quality_preset, "float32")

        self._model = WhisperModel(self.model_name, device="cpu", compute_type=compute_type)
        return self._model

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None,
        auto_detect_language: bool,
        quality_preset: str,
    ) -> list[TranscriptSegment]:
        model = self._load_model(quality_preset=quality_preset)
        task_kwargs: dict[str, Any] = {
            "beam_size": 5,
            "word_timestamps": False,
            "vad_filter": True,
        }
        if not auto_detect_language and language:
            task_kwargs["language"] = language

        segments_iter, _info = model.transcribe(str(audio_path), **task_kwargs)
        parsed: list[TranscriptSegment] = []
        for seg in segments_iter:
            text = (seg.text or "").strip()
            if not text:
                continue
            parsed.append(
                TranscriptSegment(
                    start=float(seg.start),
                    end=float(seg.end),
                    text=text,
                    confidence=float(getattr(seg, "avg_logprob", 0.0)),
                )
            )
        if not parsed:
            parsed.append(
                TranscriptSegment(
                    start=0.0,
                    end=1.5,
                    text="[Тишина или не удалось распознать речь]",
                    confidence=0.0,
                )
            )
        return parsed

