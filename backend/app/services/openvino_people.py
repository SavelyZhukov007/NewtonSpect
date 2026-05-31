from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
try:
    from openvino import Core
except ImportError:  # pragma: no cover
    from openvino.runtime import Core

from ..schemas import PersonProfile, PersonTrackStats
from .types import FaceTrackSample, VisionAnalysisResult


@dataclass
class _CompiledModelInfo:
    compiled: Any
    input_name: str
    output_name: str
    input_h: int
    input_w: int


@dataclass
class _PersonAccumulator:
    person_id: str
    embedding_centroid: np.ndarray
    embedding_count: int = 1
    first_seen: float = 0.0
    last_seen: float = 0.0
    confidence_sum: float = 0.0
    detections: int = 0
    speaking_seconds: float = 0.0
    best_portrait: np.ndarray | None = None
    best_area: int = 0
    previous_mouth_ratio: float = 0.0


class OpenVINOPeopleAnalyzer:
    def __init__(self, models_dir: Path, preferred_devices: tuple[str, ...]) -> None:
        self.models_dir = models_dir
        self.preferred_devices = preferred_devices
        self.core = Core()
        self.device = self._resolve_device()
        self.face_detector = self._load_model("face-detection-retail-0005")
        self.face_reid = self._load_model("face-reidentification-retail-0095")
        self.landmarks = self._load_model("landmarks-regression-retail-0009")
        self.haar_detector = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        self._next_person_index = 1

    def analyze(self, video_path: Path, people_output_dir: Path) -> VisionAnalysisResult:
        people_output_dir.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS)
        fps = fps if fps and fps > 0 else 25.0
        sample_every = max(int(fps // 2), 1)  # ~2 FPS for cost control

        accumulators: dict[str, _PersonAccumulator] = {}
        samples: list[FaceTrackSample] = []

        frame_idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_idx % sample_every != 0:
                frame_idx += 1
                continue
            time_sec = frame_idx / fps

            faces = self._detect_faces(frame)
            for x1, y1, x2, y2, conf in faces:
                face_crop = frame[y1:y2, x1:x2]
                if face_crop.size == 0:
                    continue
                emb = self._compute_embedding(face_crop)
                person_id = self._match_or_create_person(accumulators, emb, time_sec)
                acc = accumulators[person_id]
                acc.last_seen = time_sec
                acc.confidence_sum += conf
                acc.detections += 1
                area = max((x2 - x1) * (y2 - y1), 0)
                if area > acc.best_area:
                    acc.best_area = area
                    acc.best_portrait = face_crop.copy()

                mouth_ratio = self._estimate_mouth_ratio(face_crop)
                mouth_activity = abs(mouth_ratio - acc.previous_mouth_ratio)
                acc.previous_mouth_ratio = mouth_ratio
                if mouth_activity > 0.015:
                    acc.speaking_seconds += 1.0 / max(fps / sample_every, 1.0)

                samples.append(
                    FaceTrackSample(
                        person_id=person_id,
                        time_sec=time_sec,
                        confidence=conf,
                        mouth_activity=mouth_activity,
                    )
                )
            frame_idx += 1
        cap.release()

        people: list[PersonProfile] = []
        for acc in accumulators.values():
            portrait_path: str | None = None
            if acc.best_portrait is not None:
                out_path = people_output_dir / f"{acc.person_id}.jpg"
                cv2.imwrite(str(out_path), acc.best_portrait)
                portrait_path = str(out_path)

            screen_time = max(acc.last_seen - acc.first_seen, 0.0)
            avg_conf = acc.confidence_sum / acc.detections if acc.detections else 0.0
            people.append(
                PersonProfile(
                    person_id=acc.person_id,
                    portrait_path=portrait_path,
                    track_stats=PersonTrackStats(
                        screen_time_seconds=screen_time,
                        first_seen=acc.first_seen,
                        last_seen=acc.last_seen,
                        avg_confidence=avg_conf,
                        speaking_seconds=acc.speaking_seconds,
                    ),
                    key_comments=[],
                )
            )
        people.sort(key=lambda p: p.track_stats.screen_time_seconds, reverse=True)
        return VisionAnalysisResult(people=people, samples=samples, device_used=self.device)

    def _resolve_device(self) -> str:
        available = set(self.core.available_devices)
        for preferred in self.preferred_devices:
            if preferred in available:
                return preferred
        return "CPU"

    def _find_model_xml(self, model_name: str) -> Path | None:
        matches = list(self.models_dir.rglob(f"{model_name}.xml"))
        return matches[0] if matches else None

    def _load_model(self, model_name: str) -> _CompiledModelInfo | None:
        xml_path = self._find_model_xml(model_name)
        if not xml_path:
            return None
        model = self.core.read_model(model=str(xml_path))
        compiled = self.core.compile_model(model=model, device_name=self.device)
        input_port = compiled.input(0)
        output_port = compiled.output(0)
        shape = list(input_port.shape)
        return _CompiledModelInfo(
            compiled=compiled,
            input_name=input_port.get_any_name(),
            output_name=output_port.get_any_name(),
            input_h=int(shape[2]),
            input_w=int(shape[3]),
        )

    def _detect_faces(self, frame: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        if self.face_detector is not None:
            return self._detect_faces_openvino(frame)
        return self._detect_faces_haar(frame)

    def _detect_faces_openvino(self, frame: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        assert self.face_detector is not None
        blob = cv2.resize(frame, (self.face_detector.input_w, self.face_detector.input_h))
        blob = np.transpose(blob, (2, 0, 1))[None, ...].astype(np.float32)
        results = self.face_detector.compiled({self.face_detector.input_name: blob})[
            self.face_detector.output_name
        ]
        h, w = frame.shape[:2]
        boxes: list[tuple[int, int, int, int, float]] = []
        for det in np.reshape(results, (-1, 7)):
            conf = float(det[2])
            if conf < 0.5:
                continue
            x1 = max(int(det[3] * w), 0)
            y1 = max(int(det[4] * h), 0)
            x2 = min(int(det[5] * w), w - 1)
            y2 = min(int(det[6] * h), h - 1)
            if x2 <= x1 or y2 <= y1:
                continue
            boxes.append((x1, y1, x2, y2, conf))
        return boxes

    def _detect_faces_haar(self, frame: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        detected = self.haar_detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        boxes: list[tuple[int, int, int, int, float]] = []
        for (x, y, w, h) in detected:
            boxes.append((int(x), int(y), int(x + w), int(y + h), 0.5))
        return boxes

    def _compute_embedding(self, face_crop: np.ndarray) -> np.ndarray:
        if self.face_reid is not None:
            blob = cv2.resize(face_crop, (self.face_reid.input_w, self.face_reid.input_h))
            blob = np.transpose(blob, (2, 0, 1))[None, ...].astype(np.float32)
            vec = self.face_reid.compiled({self.face_reid.input_name: blob})[
                self.face_reid.output_name
            ]
            emb = np.asarray(vec, dtype=np.float32).reshape(-1)
            norm = np.linalg.norm(emb) + 1e-8
            return emb / norm

        # Fallback embedding: HSV histogram descriptor.
        hsv = cv2.cvtColor(face_crop, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
        hist = cv2.normalize(hist, hist).flatten().astype(np.float32)
        norm = np.linalg.norm(hist) + 1e-8
        return hist / norm

    def _match_or_create_person(
        self, accumulators: dict[str, _PersonAccumulator], embedding: np.ndarray, time_sec: float
    ) -> str:
        best_person: str | None = None
        best_sim = -1.0
        for person_id, acc in accumulators.items():
            sim = float(np.dot(acc.embedding_centroid, embedding))
            if sim > best_sim:
                best_sim = sim
                best_person = person_id

        threshold = 0.62 if self.face_reid is not None else 0.86
        if best_person is None or best_sim < threshold:
            person_id = f"P{self._next_person_index:03d}"
            self._next_person_index += 1
            accumulators[person_id] = _PersonAccumulator(
                person_id=person_id,
                embedding_centroid=embedding.copy(),
                first_seen=time_sec,
                last_seen=time_sec,
            )
            return person_id

        acc = accumulators[best_person]
        acc.embedding_count += 1
        alpha = 1.0 / acc.embedding_count
        acc.embedding_centroid = (1.0 - alpha) * acc.embedding_centroid + alpha * embedding
        norm = np.linalg.norm(acc.embedding_centroid) + 1e-8
        acc.embedding_centroid = acc.embedding_centroid / norm
        return best_person

    def _estimate_mouth_ratio(self, face_crop: np.ndarray) -> float:
        if self.landmarks is not None:
            blob = cv2.resize(face_crop, (self.landmarks.input_w, self.landmarks.input_h))
            blob = np.transpose(blob, (2, 0, 1))[None, ...].astype(np.float32)
            pts = self.landmarks.compiled({self.landmarks.input_name: blob})[
                self.landmarks.output_name
            ]
            pts = np.asarray(pts).reshape(-1)
            if pts.size >= 10:
                mouth_left = np.array([pts[6], pts[7]], dtype=np.float32)
                mouth_right = np.array([pts[8], pts[9]], dtype=np.float32)
                dist = float(np.linalg.norm(mouth_right - mouth_left))
                return max(dist, 0.0)
        # Fallback: use local motion proxy based on lower-face texture energy.
        gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
        h = gray.shape[0]
        lower = gray[int(h * 0.55) :, :]
        lap = cv2.Laplacian(lower, cv2.CV_32F)
        energy = float(np.mean(np.abs(lap)))
        return math.tanh(energy / 80.0)
