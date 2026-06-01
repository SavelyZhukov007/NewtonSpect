from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable


class MediaService:
    CURATED_FORMATS: tuple[tuple[str, str, bool, str | None, str | None], ...] = (
        ("mp4", "mp4", True, "mov_text", "Best compatibility on iPhone/Android"),
        ("mkv", "matroska", True, "srt", "Best subtitle flexibility"),
        ("mov", "mov", True, "mov_text", "Apple ecosystem compatibility"),
        ("webm", "webm", False, None, "Use sidecar subtitles"),
        ("avi", "avi", False, None, "Use sidecar subtitles"),
        ("mpegts", "mpegts", False, None, "Use sidecar subtitles"),
    )
    FORMAT_EXTENSION_ALIASES: dict[str, str] = {
        "mpegts": "ts",
    }

    def run_ffmpeg(self, args: list[str], cwd: Path | None = None) -> None:
        cmd = ["ffmpeg", "-y", *args]
        completed = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(
                "ffmpeg failed: "
                f"{' '.join(cmd)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )

    def probe_duration_seconds(self, media_path: Path) -> float:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True)
        if completed.returncode != 0:
            return 0.0
        try:
            return max(float(completed.stdout.strip()), 0.0)
        except ValueError:
            return 0.0

    def extract_audio_wav(self, video_path: Path, output_wav: Path) -> Path:
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        self.run_ffmpeg(
            [
                "-i",
                str(video_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-acodec",
                "pcm_s16le",
                str(output_wav),
            ]
        )
        return output_wav

    def list_ffmpeg_muxers(self) -> list[str]:
        cmd = ["ffmpeg", "-hide_banner", "-muxers"]
        completed = subprocess.run(cmd, capture_output=True, text=True)
        if completed.returncode != 0:
            return []
        muxers: list[str] = []
        for line in completed.stdout.splitlines():
            line = line.rstrip()
            if not line or line.startswith(" " * 2) is False and line.startswith("Formats:"):
                continue
            if " E " not in line and not line.startswith(" E"):
                continue
            # Example: "  E  mp4             MP4 (MPEG-4 Part 14)"
            tokens = line.split()
            if len(tokens) >= 2:
                muxers.append(tokens[1].strip().lower())
        return sorted(set(muxers))

    def curated_video_formats(self) -> list[dict[str, str | bool | None]]:
        return [
            {
                "format": item[0],
                "ffmpeg_muxer": item[1],
                "can_embed_subtitles": item[2],
                "preferred_subtitle_codec": item[3],
                "notes": item[4],
            }
            for item in self.CURATED_FORMATS
        ]

    @classmethod
    def output_extension(cls, container_format: str | None) -> str:
        normalized = (container_format or "").lower().strip().lstrip(".")
        if not normalized:
            return "mp4"
        return cls.FORMAT_EXTENSION_ALIASES.get(normalized, normalized)

    @staticmethod
    def _burn_encode_args(container_format: str) -> list[str]:
        if container_format == "webm":
            return [
                "-c:v",
                "libvpx-vp9",
                "-crf",
                "32",
                "-b:v",
                "0",
                "-c:a",
                "libopus",
                "-b:a",
                "128k",
            ]
        return [
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "medium",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]

    def burn_ass_subtitles(
        self,
        video_path: Path,
        ass_path: Path,
        output_video: Path,
        *,
        container_format: str | None = None,
    ) -> Path:
        output_video.parent.mkdir(parents=True, exist_ok=True)
        normalized_format = (container_format or output_video.suffix.lstrip(".") or "mp4").lower()
        self.run_ffmpeg(
            [
                "-i",
                str(video_path),
                "-vf",
                f"ass={ass_path.name}",
                *self._burn_encode_args(normalized_format),
                str(output_video),
            ],
            cwd=ass_path.parent,
        )
        return output_video

    def embed_soft_subtitles(
        self,
        input_video: Path,
        subtitle_path: Path,
        output_video: Path,
        *,
        container_format: str,
        subtitle_codec: str,
    ) -> Path:
        output_video.parent.mkdir(parents=True, exist_ok=True)
        ext = output_video.suffix.lower()
        if ext == ".mp4" or container_format in {"mp4", "mov"}:
            subtitle_codec = "mov_text"
        elif ext == ".mkv" and subtitle_codec not in {"srt", "ass", "ssa", "webvtt"}:
            subtitle_codec = "srt"

        self.run_ffmpeg(
            [
                "-i",
                str(input_video),
                "-i",
                str(subtitle_path),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-map",
                "1:0",
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                "-c:s",
                subtitle_codec,
                "-metadata:s:s:0",
                "language=rus",
                str(output_video),
            ]
        )
        return output_video

    def burn_ass_subtitles_with_progress(
        self,
        video_path: Path,
        ass_path: Path,
        output_video: Path,
        *,
        duration_seconds: float,
        container_format: str | None = None,
        progress_callback: Callable[[float, float, float], None] | None = None,
    ) -> Path:
        output_video.parent.mkdir(parents=True, exist_ok=True)
        normalized_format = (container_format or output_video.suffix.lstrip(".") or "mp4").lower()
        cmd = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-i",
            str(video_path),
            "-vf",
            f"ass={ass_path.name}",
            *self._burn_encode_args(normalized_format),
            str(output_video),
        ]
        process = subprocess.Popen(
            cmd,
            cwd=ass_path.parent,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            universal_newlines=True,
        )
        time_pattern = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
        try:
            while True:
                line = process.stderr.readline() if process.stderr else ""
                if not line and process.poll() is not None:
                    break
                match = time_pattern.search(line)
                if match and progress_callback is not None and duration_seconds > 0:
                    hh, mm, ss = match.groups()
                    processed = int(hh) * 3600 + int(mm) * 60 + float(ss)
                    eta = max(duration_seconds - processed, 0.0)
                    progress_callback(processed, duration_seconds, eta)
            return_code = process.wait()
            if return_code != 0:
                raise RuntimeError(f"ffmpeg burn failed with code {return_code}")
        finally:
            if process.stderr:
                process.stderr.close()
        return output_video

    def mux_audio_from_source(
        self,
        source_video: Path,
        source_audio_video: Path,
        output_path: Path,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.run_ffmpeg(
            [
                "-i",
                str(source_video),
                "-i",
                str(source_audio_video),
                "-c:v",
                "copy",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0?",
                "-shortest",
                str(output_path),
            ]
        )
        return output_path
