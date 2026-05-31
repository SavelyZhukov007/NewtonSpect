from __future__ import annotations

import json
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

from .media import MediaService


MODEL_NAMES = [
    "age-gender-recognition-retail-0013",
    "emotions-recognition-retail-0003",
    "face-detection-retail-0004",
    "face-reidentification-retail-0095",
    "facial-landmarks-35-adas-0002",
    "facial-landmarks-98-detection-0001",
    "human-pose-estimation-0001",
    "person-detection-retail-0013",
    "person-reidentification-retail-0277",
]


@dataclass
class _ModelInfo:
    name: str
    compiled: Any
    input_name: str
    output_names: list[str]
    input_w: int
    input_h: int


@dataclass
class MaskOverlayResult:
    output_video_path: Path
    metadata_path: Path
    models_loaded: list[str]
    models_missing: list[str]


class OpenVINOMaskOverlayRenderer:
    def __init__(self, models_dir: Path, preferred_devices: tuple[str, ...]) -> None:
        self.models_dir = models_dir
        self.core = Core()
        self.device = self._resolve_device(preferred_devices)
        self.media = MediaService()
        self.models: dict[str, _ModelInfo] = {}
        for name in MODEL_NAMES:
            loaded = self._load_model(name)
            if loaded is not None:
                self.models[name] = loaded
        self.haar_detector = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )

    def render(
        self,
        input_video_path: Path,
        output_dir: Path,
        *,
        progress_callback: Callable[[int, int, float], None] | None = None,
    ) -> MaskOverlayResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        tmp_video_path = output_dir / "masked_silent.mp4"
        final_video_path = output_dir / "masked_output.mp4"
        metadata_path = output_dir / "masked_metadata.json"

        cap = cv2.VideoCapture(str(input_video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video for mask render: {input_video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS)
        fps = fps if fps and fps > 0 else 25.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if width <= 0 or height <= 0:
            cap.release()
            raise RuntimeError("Video dimensions are invalid for mask render")

        writer = cv2.VideoWriter(
            str(tmp_video_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps,
            (width, height),
        )
        started = time.perf_counter()
        frame_idx = 0
        frame_samples: list[dict[str, Any]] = []

        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                annotated, sample = self._annotate_frame(frame, frame_idx, fps)
                writer.write(annotated)

                if frame_idx % max(int(fps), 1) == 0:
                    frame_samples.append(sample)

                if progress_callback is not None:
                    elapsed = max(time.perf_counter() - started, 1e-6)
                    progress_callback(frame_idx + 1, total_frames, elapsed)
                frame_idx += 1
        finally:
            cap.release()
            writer.release()

        self.media.mux_audio_from_source(tmp_video_path, input_video_path, final_video_path)

        models_loaded = sorted(self.models.keys())
        models_missing = [name for name in MODEL_NAMES if name not in self.models]
        metadata = {
            "device": self.device,
            "models_loaded": models_loaded,
            "models_missing": models_missing,
            "frames_processed": frame_idx,
            "frame_samples": frame_samples,
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return MaskOverlayResult(
            output_video_path=final_video_path,
            metadata_path=metadata_path,
            models_loaded=models_loaded,
            models_missing=models_missing,
        )

    def _resolve_device(self, preferred_devices: tuple[str, ...]) -> str:
        available = set(self.core.available_devices)
        for preferred in preferred_devices:
            if preferred in available:
                return preferred
        return "CPU"

    def _find_model_xml(self, model_name: str) -> Path | None:
        matches = list(self.models_dir.rglob(f"{model_name}.xml"))
        return matches[0] if matches else None

    def _load_model(self, model_name: str) -> _ModelInfo | None:
        xml_path = self._find_model_xml(model_name)
        if not xml_path:
            return None
        model = self.core.read_model(model=str(xml_path))
        compiled = self.core.compile_model(model=model, device_name=self.device)
        input_port = compiled.input(0)
        input_shape = list(input_port.shape)
        output_names = [port.get_any_name() for port in compiled.outputs]
        return _ModelInfo(
            name=model_name,
            compiled=compiled,
            input_name=input_port.get_any_name(),
            output_names=output_names,
            input_w=int(input_shape[3]),
            input_h=int(input_shape[2]),
        )

    @staticmethod
    def _preprocess(image: np.ndarray, width: int, height: int) -> np.ndarray:
        blob = cv2.resize(image, (width, height))
        blob = np.transpose(blob, (2, 0, 1))[None, ...].astype(np.float32)
        return blob

    def _infer_outputs(self, model: _ModelInfo, image: np.ndarray) -> dict[str, np.ndarray]:
        blob = self._preprocess(image, model.input_w, model.input_h)
        ov_out = model.compiled({model.input_name: blob})
        outputs: dict[str, np.ndarray] = {}
        for output_port in model.compiled.outputs:
            outputs[output_port.get_any_name()] = np.asarray(ov_out[output_port])
        return outputs

    def _detect_faces(self, frame: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        model = self.models.get("face-detection-retail-0004")
        if model is None:
            # fallback to 0005 if present from previous pipeline setup
            model = self.models.get("face-detection-retail-0005")
        if model is not None:
            outputs = self._infer_outputs(model, frame)
            first = next(iter(outputs.values()))
            h, w = frame.shape[:2]
            boxes: list[tuple[int, int, int, int, float]] = []
            for det in np.reshape(first, (-1, 7)):
                conf = float(det[2])
                if conf < 0.45:
                    continue
                x1 = max(int(det[3] * w), 0)
                y1 = max(int(det[4] * h), 0)
                x2 = min(int(det[5] * w), w - 1)
                y2 = min(int(det[6] * h), h - 1)
                if x2 <= x1 or y2 <= y1:
                    continue
                boxes.append((x1, y1, x2, y2, conf))
            return boxes

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        detected = self.haar_detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        return [(int(x), int(y), int(x + w), int(y + h), 0.4) for (x, y, w, h) in detected]

    def _detect_persons(self, frame: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        model = self.models.get("person-detection-retail-0013")
        if model is None:
            return []
        outputs = self._infer_outputs(model, frame)
        first = next(iter(outputs.values()))
        h, w = frame.shape[:2]
        boxes: list[tuple[int, int, int, int, float]] = []
        for det in np.reshape(first, (-1, 7)):
            conf = float(det[2])
            if conf < 0.40:
                continue
            x1 = max(int(det[3] * w), 0)
            y1 = max(int(det[4] * h), 0)
            x2 = min(int(det[5] * w), w - 1)
            y2 = min(int(det[6] * h), h - 1)
            if x2 <= x1 or y2 <= y1:
                continue
            boxes.append((x1, y1, x2, y2, conf))
        return boxes

    def _age_gender(self, face_crop: np.ndarray) -> tuple[int | None, str | None]:
        model = self.models.get("age-gender-recognition-retail-0013")
        if model is None:
            return None, None
        outputs = self._infer_outputs(model, face_crop)
        age = None
        gender = None
        for name, arr in outputs.items():
            flat = arr.reshape(-1)
            if flat.size == 1:
                age = int(max(min(float(flat[0]) * 100.0, 99.0), 0.0))
            elif flat.size == 2:
                gender = "male" if float(flat[1]) >= float(flat[0]) else "female"
        return age, gender

    def _emotion(self, face_crop: np.ndarray) -> str | None:
        model = self.models.get("emotions-recognition-retail-0003")
        if model is None:
            return None
        outputs = self._infer_outputs(model, face_crop)
        arr = next(iter(outputs.values())).reshape(-1)
        if arr.size < 5:
            return None
        labels = ["neutral", "happy", "sad", "surprise", "anger"]
        idx = int(np.argmax(arr))
        return labels[idx] if 0 <= idx < len(labels) else None

    def _landmarks(self, face_crop: np.ndarray) -> list[tuple[float, float]]:
        model = self.models.get("facial-landmarks-98-detection-0001")
        if model is None:
            model = self.models.get("facial-landmarks-35-adas-0002")
        if model is None:
            return []
        outputs = self._infer_outputs(model, face_crop)
        values = next(iter(outputs.values())).reshape(-1)
        if values.size < 4:
            return []
        pairs = []
        for idx in range(0, values.size - 1, 2):
            x = float(values[idx])
            y = float(values[idx + 1])
            if math.isnan(x) or math.isnan(y):
                continue
            pairs.append((x, y))
        return pairs

    def _annotate_frame(
        self, frame: np.ndarray, frame_idx: int, fps: float
    ) -> tuple[np.ndarray, dict[str, Any]]:
        out = frame.copy()
        h, w = out.shape[:2]
        persons = self._detect_persons(out)
        faces = self._detect_faces(out)

        for x1, y1, x2, y2, conf in persons:
            cv2.rectangle(out, (x1, y1), (x2, y2), (29, 203, 101), 2)
            cv2.putText(
                out,
                f"person {conf:.2f}",
                (x1, max(y1 - 6, 14)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (29, 203, 101),
                1,
                cv2.LINE_AA,
            )

        face_infos: list[dict[str, Any]] = []
        for x1, y1, x2, y2, conf in faces:
            face_crop = out[y1:y2, x1:x2]
            if face_crop.size == 0:
                continue
            age, gender = self._age_gender(face_crop)
            emotion = self._emotion(face_crop)
            landmarks = self._landmarks(face_crop)
            cv2.rectangle(out, (x1, y1), (x2, y2), (33, 133, 242), 2)
            label_bits = [f"face {conf:.2f}"]
            if age is not None:
                label_bits.append(f"age:{age}")
            if gender:
                label_bits.append(gender)
            if emotion:
                label_bits.append(emotion)
            cv2.putText(
                out,
                " | ".join(label_bits),
                (x1, min(y2 + 16, h - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (33, 133, 242),
                1,
                cv2.LINE_AA,
            )
            for lx, ly in landmarks:
                px = int(x1 + lx * max((x2 - x1), 1))
                py = int(y1 + ly * max((y2 - y1), 1))
                if 0 <= px < w and 0 <= py < h:
                    cv2.circle(out, (px, py), 1, (249, 219, 20), -1)
            face_infos.append(
                {
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "confidence": round(conf, 4),
                    "age": age,
                    "gender": gender,
                    "emotion": emotion,
                    "landmarks_points": len(landmarks),
                }
            )

        model_line = f"models:{len(self.models)}/{len(MODEL_NAMES)} dev:{self.device}"
        cv2.putText(
            out,
            model_line,
            (10, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            out,
            model_line,
            (10, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (20, 20, 20),
            1,
            cv2.LINE_AA,
        )

        sample = {
            "time_sec": round(frame_idx / max(fps, 1.0), 3),
            "persons": len(persons),
            "faces": face_infos,
        }
        return out, sample

