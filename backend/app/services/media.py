from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable


class MediaService:
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

    def burn_ass_subtitles(self, video_path: Path, ass_path: Path, output_video: Path) -> Path:
        output_video.parent.mkdir(parents=True, exist_ok=True)
        self.run_ffmpeg(
            [
                "-i",
                str(video_path),
                "-vf",
                f"ass={ass_path.name}",
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
                str(output_video),
            ],
            cwd=ass_path.parent,
        )
        return output_video

    def burn_ass_subtitles_with_progress(
        self,
        video_path: Path,
        ass_path: Path,
        output_video: Path,
        *,
        duration_seconds: float,
        progress_callback: Callable[[float, float, float], None] | None = None,
    ) -> Path:
        output_video.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-i",
            str(video_path),
            "-vf",
            f"ass={ass_path.name}",
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
