from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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
    name: str
    compiled: Any
    input_name: str
    output_name: str
    input_h: int
    input_w: int


@dataclass
class _PersonAccumulator:
    person_id: str
    person_embedding_centroid: np.ndarray
    person_embedding_count: int = 1
    face_embedding_centroid: np.ndarray | None = None
    face_embedding_count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    confidence_sum: float = 0.0
    detections: int = 0
    speaking_seconds: float = 0.0
    best_portrait: np.ndarray | None = None
    best_area: int = 0
    previous_mouth_ratio: float = 0.0
    face_hits: int = 0


class OpenVINOPeopleAnalyzer:
    def __init__(
        self,
        models_dir: Path,
        preferred_devices: tuple[str, ...],
        model_search_paths: tuple[Path, ...] | None = None,
    ) -> None:
        self.models_dir = models_dir
        self.model_search_paths = tuple(
            path.resolve() for path in (model_search_paths or (models_dir,))
        )
        self.preferred_devices = preferred_devices
        self.core = Core()
        self.device = self._resolve_device()

        self.person_detector = self._load_model("person-detection-retail-0013")
        self.person_reid = self._load_model("person-reidentification-retail-0277")
        self.face_detector = self._load_model("face-detection-retail-0004") or self._load_model(
            "face-detection-retail-0005"
        )
        self.face_reid = self._load_model("face-recognition-resnet100-arcface-onnx") or self._load_model(
            "face-reidentification-retail-0095"
        )
        self.landmarks = (
            self._load_model("facial-landmarks-98-detection-0001")
            or self._load_model("facial-landmarks-35-adas-0002")
            or self._load_model("landmarks-regression-retail-0009")
        )
        self.haar_detector = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        self._next_person_index = 1

    def analyze(
        self,
        video_path: Path,
        people_output_dir: Path,
        progress_callback: Callable[[int, int, float], None] | None = None,
    ) -> VisionAnalysisResult:
        people_output_dir.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS)
        fps = fps if fps and fps > 0 else 25.0
        sample_every = max(int(fps // 3), 1)  # ~3 FPS for better tracking quality
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        started = time.perf_counter()

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
            person_boxes = self._detect_persons(frame)
            for x1, y1, x2, y2, conf in person_boxes:
                crop = frame[y1:y2, x1:x2]
                if crop.size == 0:
                    continue

                person_embedding = self._compute_person_embedding(crop)
                face_crop, face_conf, mouth_activity = self._extract_face_features(crop)
                face_embedding = (
                    self._compute_face_embedding(face_crop) if face_crop is not None else None
                )

                person_id = self._match_or_create_person(
                    accumulators,
                    person_embedding=person_embedding,
                    face_embedding=face_embedding,
                    time_sec=time_sec,
                )
                acc = accumulators[person_id]
                acc.last_seen = time_sec
                combined_conf = 0.7 * conf + 0.3 * face_conf
                acc.confidence_sum += combined_conf
                acc.detections += 1
                if face_crop is not None:
                    acc.face_hits += 1

                area = max((x2 - x1) * (y2 - y1), 0)
                portrait_candidate = face_crop if face_crop is not None else crop
                if area > acc.best_area and portrait_candidate is not None and portrait_candidate.size > 0:
                    acc.best_area = area
                    acc.best_portrait = portrait_candidate.copy()

                if mouth_activity > 0.015:
                    acc.speaking_seconds += 1.0 / max(fps / sample_every, 1.0)

                samples.append(
                    FaceTrackSample(
                        person_id=person_id,
                        time_sec=time_sec,
                        confidence=combined_conf,
                        mouth_activity=mouth_activity,
                    )
                )

            if progress_callback is not None:
                elapsed = max(time.perf_counter() - started, 1e-6)
                progress_callback(frame_idx + 1, total_frames, elapsed)
            frame_idx += 1

        cap.release()

        people: list[PersonProfile] = []
        for acc in accumulators.values():
            avg_conf = acc.confidence_sum / acc.detections if acc.detections else 0.0
            screen_time = max(acc.last_seen - acc.first_seen, 0.0)
            # Anti-false-positive gating:
            # keep only stable tracks or tracks backed by visible face evidence.
            if acc.detections < 2 and acc.face_hits == 0:
                continue
            if screen_time < 0.35 and acc.face_hits == 0:
                continue
            if avg_conf < 0.42:
                continue

            portrait_path: str | None = None
            if acc.best_portrait is not None:
                out_path = people_output_dir / f"{acc.person_id}.jpg"
                cv2.imwrite(str(out_path), acc.best_portrait)
                portrait_path = str(out_path)

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
        for root in self.model_search_paths:
            matches = list(root.rglob(f"{model_name}.xml"))
            if matches:
                return matches[0]
        return None

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
            name=model_name,
            compiled=compiled,
            input_name=input_port.get_any_name(),
            output_name=output_port.get_any_name(),
            input_h=int(shape[2]),
            input_w=int(shape[3]),
        )

    def _detect_persons(self, frame: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        if self.person_detector is None:
            # Fallback: use full frame as one region if no detector available.
            h, w = frame.shape[:2]
            return [(0, 0, w - 1, h - 1, 0.2)]

        blob = cv2.resize(frame, (self.person_detector.input_w, self.person_detector.input_h))
        blob = np.transpose(blob, (2, 0, 1))[None, ...].astype(np.float32)
        results = self.person_detector.compiled({self.person_detector.input_name: blob})[
            self.person_detector.output_name
        ]

        h, w = frame.shape[:2]
        boxes: list[tuple[int, int, int, int, float]] = []
        for det in np.reshape(results, (-1, 7)):
            conf = float(det[2])
            if conf < 0.45:
                continue
            x1 = max(int(det[3] * w), 0)
            y1 = max(int(det[4] * h), 0)
            x2 = min(int(det[5] * w), w - 1)
            y2 = min(int(det[6] * h), h - 1)
            if x2 <= x1 or y2 <= y1:
                continue
            area_ratio = ((x2 - x1) * (y2 - y1)) / max(float(w * h), 1.0)
            if area_ratio < 0.01 or area_ratio > 0.95:
                continue
            aspect = (x2 - x1) / max(float(y2 - y1), 1.0)
            if aspect < 0.15 or aspect > 1.25:
                continue
            boxes.append((x1, y1, x2, y2, conf))
        return boxes

    def _extract_face_features(
        self, person_crop: np.ndarray
    ) -> tuple[np.ndarray | None, float, float]:
        faces = self._detect_faces(person_crop)
        if not faces:
            return None, 0.0, 0.0
        x1, y1, x2, y2, conf = max(
            faces,
            key=lambda item: (item[2] - item[0]) * (item[3] - item[1]),
        )
        face_crop = person_crop[y1:y2, x1:x2]
        if face_crop.size == 0:
            return None, 0.0, 0.0
        mouth_ratio = self._estimate_mouth_ratio(face_crop)
        return face_crop, conf, mouth_ratio

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
        return [(int(x), int(y), int(x + w), int(y + h), 0.5) for (x, y, w, h) in detected]

    def _compute_person_embedding(self, crop: np.ndarray) -> np.ndarray:
        if self.person_reid is not None:
            blob = cv2.resize(crop, (self.person_reid.input_w, self.person_reid.input_h))
            blob = np.transpose(blob, (2, 0, 1))[None, ...].astype(np.float32)
            vec = self.person_reid.compiled({self.person_reid.input_name: blob})[
                self.person_reid.output_name
            ]
            emb = np.asarray(vec, dtype=np.float32).reshape(-1)
            norm = np.linalg.norm(emb) + 1e-8
            return emb / norm
        return self._hist_embedding(crop)

    def _compute_face_embedding(self, crop: np.ndarray) -> np.ndarray:
        if self.face_reid is not None:
            blob = cv2.resize(crop, (self.face_reid.input_w, self.face_reid.input_h))
            blob = np.transpose(blob, (2, 0, 1))[None, ...].astype(np.float32)
            vec = self.face_reid.compiled({self.face_reid.input_name: blob})[
                self.face_reid.output_name
            ]
            emb = np.asarray(vec, dtype=np.float32).reshape(-1)
            norm = np.linalg.norm(emb) + 1e-8
            return emb / norm
        return self._hist_embedding(crop)

    @staticmethod
    def _hist_embedding(crop: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
        hist = cv2.normalize(hist, hist).flatten().astype(np.float32)
        return hist / (np.linalg.norm(hist) + 1e-8)

    def _match_or_create_person(
        self,
        accumulators: dict[str, _PersonAccumulator],
        *,
        person_embedding: np.ndarray,
        face_embedding: np.ndarray | None,
        time_sec: float,
    ) -> str:
        best_person: str | None = None
        best_score = -1.0
        for person_id, acc in accumulators.items():
            person_sim = float(np.dot(acc.person_embedding_centroid, person_embedding))
            if face_embedding is not None and acc.face_embedding_centroid is not None:
                face_sim = float(np.dot(acc.face_embedding_centroid, face_embedding))
                score = 0.65 * person_sim + 0.35 * face_sim
            else:
                score = person_sim
            if score > best_score:
                best_score = score
                best_person = person_id

        threshold = 0.58 if self.person_reid is not None else 0.82
        if best_person is None or best_score < threshold:
            person_id = f"P{self._next_person_index:03d}"
            self._next_person_index += 1
            accumulators[person_id] = _PersonAccumulator(
                person_id=person_id,
                person_embedding_centroid=person_embedding.copy(),
                first_seen=time_sec,
                last_seen=time_sec,
                face_embedding_centroid=face_embedding.copy() if face_embedding is not None else None,
                face_embedding_count=1 if face_embedding is not None else 0,
            )
            return person_id

        acc = accumulators[best_person]
        acc.person_embedding_count += 1
        alpha = 1.0 / acc.person_embedding_count
        acc.person_embedding_centroid = (
            (1.0 - alpha) * acc.person_embedding_centroid + alpha * person_embedding
        )
        acc.person_embedding_centroid = acc.person_embedding_centroid / (
            np.linalg.norm(acc.person_embedding_centroid) + 1e-8
        )
        if face_embedding is not None:
            if acc.face_embedding_centroid is None:
                acc.face_embedding_centroid = face_embedding.copy()
                acc.face_embedding_count = 1
            else:
                acc.face_embedding_count += 1
                beta = 1.0 / acc.face_embedding_count
                acc.face_embedding_centroid = (
                    (1.0 - beta) * acc.face_embedding_centroid + beta * face_embedding
                )
                acc.face_embedding_centroid = acc.face_embedding_centroid / (
                    np.linalg.norm(acc.face_embedding_centroid) + 1e-8
                )
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
        gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
        h = gray.shape[0]
        lower = gray[int(h * 0.55) :, :]
        lap = cv2.Laplacian(lower, cv2.CV_32F)
        energy = float(np.mean(np.abs(lap)))
        return math.tanh(energy / 80.0)
