from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from ..schemas import TranscriptSegment
from .types import FaceTrackSample


@dataclass
class SpeakerAttributionResult:
    segments: list[TranscriptSegment]
    method: str


class ActiveSpeakerAttributor:
    def __init__(self, models_dir: Path) -> None:
        self.models_dir = models_dir
        self.asd_model_path = self._find_asd_model()

    def _find_asd_model(self) -> Path | None:
        matches = list(self.models_dir.rglob("active-speaker*.onnx"))
        return matches[0] if matches else None

    def assign_speakers(
        self,
        segments: list[TranscriptSegment],
        samples: list[FaceTrackSample],
        *,
        use_asd_model: bool,
    ) -> SpeakerAttributionResult:
        if use_asd_model and self.asd_model_path is not None:
            # Placeholder for a specialized ASD model.
            # In this V1 we still keep a deterministic fallback path for robustness.
            fallback = self._assign_with_activity(segments, samples)
            return SpeakerAttributionResult(segments=fallback, method="asd_model+fallback")

        fallback = self._assign_with_activity(segments, samples)
        return SpeakerAttributionResult(segments=fallback, method="activity_fallback")

    def _assign_with_activity(
        self, segments: list[TranscriptSegment], samples: list[FaceTrackSample]
    ) -> list[TranscriptSegment]:
        by_person: dict[str, list[FaceTrackSample]] = {}
        for sample in samples:
            by_person.setdefault(sample.person_id, []).append(sample)

        updated: list[TranscriptSegment] = []
        for segment in segments:
            start, end = segment.start, segment.end
            best_person: str | None = None
            best_score = -1.0
            for person_id, person_samples in by_person.items():
                score = self._score_segment_activity(person_samples, start, end)
                if score > best_score:
                    best_score = score
                    best_person = person_id
            updated.append(
                TranscriptSegment(
                    start=segment.start,
                    end=segment.end,
                    text=segment.text,
                    confidence=segment.confidence,
                    speaker_ref=best_person,
                )
            )
        return updated

    @staticmethod
    def _score_segment_activity(
        samples: Iterable[FaceTrackSample], start: float, end: float
    ) -> float:
        window = [s for s in samples if (s.time_sec >= start - 0.3 and s.time_sec <= end + 0.3)]
        if not window:
            return -1.0
        mouth = np.mean([s.mouth_activity for s in window])
        conf = np.mean([s.confidence for s in window])
        return float(0.7 * mouth + 0.3 * conf)

