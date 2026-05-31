from __future__ import annotations

import subprocess
from pathlib import Path


class MediaService:
    def run_ffmpeg(self, args: list[str], cwd: Path | None = None) -> None:
        cmd = ["ffmpeg", "-y", *args]
        completed = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(
                "ffmpeg failed: "
                f"{' '.join(cmd)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )

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

