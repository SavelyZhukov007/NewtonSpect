from __future__ import annotations

import json

from ..schemas import TranscriptSegment, TranslationTrack
from .ollama_client import OllamaClient


class LocalTranslator:
    SUPPORTED_LANGUAGES = ("ru", "en", "es", "de", "fr")

    def __init__(self, ollama_client: OllamaClient) -> None:
        self.ollama_client = ollama_client

    def translate(
        self,
        segments: list[TranscriptSegment],
        *,
        target_language: str,
    ) -> TranslationTrack:
        lang = target_language.lower().strip()
        if lang not in self.SUPPORTED_LANGUAGES:
            return TranslationTrack(language=lang, segments=segments)
        translated = self._try_ollama_batch_translate(segments, lang)
        if translated is None:
            translated = self._fallback_translate(segments, lang)
        return TranslationTrack(language=lang, segments=translated)

    def _try_ollama_batch_translate(
        self, segments: list[TranscriptSegment], target_language: str
    ) -> list[TranscriptSegment] | None:
        if not segments:
            return []
        payload = [
            {"id": idx, "start": seg.start, "end": seg.end, "speaker_ref": seg.speaker_ref, "text": seg.text}
            for idx, seg in enumerate(segments)
        ]
        prompt = (
            "Translate subtitles to target language and preserve timing and meaning.\n"
            f"Target language: {target_language}\n"
            "Return ONLY JSON array with objects: id,text\n"
            f"Input: {json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            response = self.ollama_client.generate(prompt=prompt, timeout_seconds=180)
            parsed = json.loads(response)
            if not isinstance(parsed, list):
                return None
            by_id = {}
            for item in parsed:
                if isinstance(item, dict) and isinstance(item.get("id"), int) and isinstance(item.get("text"), str):
                    by_id[int(item["id"])] = str(item["text"])
            if not by_id:
                return None
            translated: list[TranscriptSegment] = []
            for idx, seg in enumerate(segments):
                translated.append(
                    TranscriptSegment(
                        id=seg.id,
                        start=seg.start,
                        end=seg.end,
                        text=by_id.get(idx, seg.text),
                        confidence=seg.confidence,
                        speaker_ref=seg.speaker_ref,
                    )
                )
            return translated
        except Exception:
            return None

    @staticmethod
    def _fallback_translate(segments: list[TranscriptSegment], target_language: str) -> list[TranscriptSegment]:
        # Local-only deterministic fallback when model translation is unavailable.
        translated: list[TranscriptSegment] = []
        for seg in segments:
            translated.append(
                TranscriptSegment(
                    id=seg.id,
                    start=seg.start,
                    end=seg.end,
                    text=f"[{target_language.upper()}] {seg.text}",
                    confidence=seg.confidence,
                    speaker_ref=seg.speaker_ref,
                )
            )
        return translated
