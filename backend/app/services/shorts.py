from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..schemas import Chapter, KeyQuote, ShortsExport, ShortsPreset
from .media import MediaService


class ShortsGenerator:
    def __init__(self, media: MediaService) -> None:
        self.media = media

    def generate(
        self,
        *,
        job_id: str,
        input_video: Path,
        output_dir: Path,
        preset: ShortsPreset,
        quotes: list[KeyQuote],
        chapters: list[Chapter],
    ) -> list[ShortsExport]:
        output_dir.mkdir(parents=True, exist_ok=True)
        windows = self._pick_windows(preset, quotes, chapters)
        exports: list[ShortsExport] = []
        for index, (start, end, label) in enumerate(windows, start=1):
            short_id = f"short-{uuid4()}"
            path = output_dir / f"short_{index:02d}.mp4"
            self.media.run_ffmpeg(
                [
                    "-ss",
                    f"{max(start, 0.0):.3f}",
                    "-t",
                    f"{max(end - start, 1.0):.3f}",
                    "-i",
                    str(input_video),
                    "-vf",
                    "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=1080:1920",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "21",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    str(path),
                ]
            )
            exports.append(
                ShortsExport(
                    short_id=short_id,
                    job_id=job_id,
                    label=label,
                    path=str(path),
                    start=start,
                    end=end,
                    created_at=datetime.now(timezone.utc),
                )
            )
        return exports

    @staticmethod
    def _pick_windows(
        preset: ShortsPreset, quotes: list[KeyQuote], chapters: list[Chapter]
    ) -> list[tuple[float, float, str]]:
        windows: list[tuple[float, float, str]] = []
        duration = max(float(preset.clip_duration_seconds), 10.0)
        candidates: list[tuple[float, str]] = []
        for quote in quotes:
            candidates.append((quote.start, f"Quote {quote.quote_id}"))
        for chapter in chapters:
            candidates.append((chapter.start, f"Chapter {chapter.chapter_id}"))
        if not candidates:
            candidates = [(0.0, "Short Intro")]
        candidates.sort(key=lambda row: row[0])
        for start, label in candidates[: max(preset.clip_count, 1)]:
            windows.append((max(start - 2.0, 0.0), max(start - 2.0, 0.0) + duration, label))
        return windows
