from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas import PersonProfile


@dataclass
class FaceTrackSample:
    person_id: str
    time_sec: float
    confidence: float
    mouth_activity: float


@dataclass
class VisionAnalysisResult:
    people: list[PersonProfile] = field(default_factory=list)
    samples: list[FaceTrackSample] = field(default_factory=list)
    device_used: str = "CPU"

