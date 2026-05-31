from __future__ import annotations

from pathlib import Path

from ..schemas import TranscriptSegment


def _format_timestamp_srt(seconds: float) -> str:
    ms_total = int(max(seconds, 0) * 1000)
    hours = ms_total // 3_600_000
    minutes = (ms_total % 3_600_000) // 60_000
    secs = (ms_total % 60_000) // 1000
    ms = ms_total % 1000
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def _format_timestamp_vtt(seconds: float) -> str:
    ms_total = int(max(seconds, 0) * 1000)
    hours = ms_total // 3_600_000
    minutes = (ms_total % 3_600_000) // 60_000
    secs = (ms_total % 60_000) // 1000
    ms = ms_total % 1000
    return f"{hours:02}:{minutes:02}:{secs:02}.{ms:03}"


def _format_timestamp_ass(seconds: float) -> str:
    cs_total = int(max(seconds, 0) * 100)  # centiseconds
    hours = cs_total // 360_000
    minutes = (cs_total % 360_000) // 6000
    secs = (cs_total % 6000) // 100
    cs = cs_total % 100
    return f"{hours}:{minutes:02}:{secs:02}.{cs:02}"


def _smart_break_text(text: str, max_line_chars: int = 42) -> str:
    words = text.strip().split()
    if not words:
        return ""
    lines: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        projected = current_len + (1 if current else 0) + len(word)
        if projected > max_line_chars and current:
            lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len = projected
    if current:
        lines.append(" ".join(current))
    if len(lines) <= 2:
        return "\n".join(lines)
    midpoint = (len(lines) + 1) // 2
    first = " ".join(lines[:midpoint])
    second = " ".join(lines[midpoint:])
    return f"{first}\n{second}"


def to_srt(segments: list[TranscriptSegment]) -> str:
    rows: list[str] = []
    for idx, segment in enumerate(segments, start=1):
        rows.append(str(idx))
        rows.append(
            f"{_format_timestamp_srt(segment.start)} --> {_format_timestamp_srt(segment.end)}"
        )
        rows.append(_smart_break_text(segment.text))
        rows.append("")
    return "\n".join(rows).strip() + "\n"


def to_vtt(segments: list[TranscriptSegment]) -> str:
    rows = ["WEBVTT", ""]
    for segment in segments:
        rows.append(
            f"{_format_timestamp_vtt(segment.start)} --> {_format_timestamp_vtt(segment.end)}"
        )
        rows.append(_smart_break_text(segment.text))
        rows.append("")
    return "\n".join(rows).strip() + "\n"


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def to_ass(segments: list[TranscriptSegment]) -> str:
    header = """[Script Info]
Title: NewtonSpect Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Inter,46,&H00FFFFFF,&H0000FFFF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,60,60,48,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    for segment in segments:
        start = _format_timestamp_ass(segment.start)
        end = _format_timestamp_ass(segment.end)
        text = _escape_ass_text(_smart_break_text(segment.text))
        animated_text = rf"{{\fad(120,120)}}{text}"
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{animated_text}")
    return header + "\n".join(events) + "\n"


def write_subtitle_files(
    segments: list[TranscriptSegment], srt_path: Path, vtt_path: Path, ass_path: Path
) -> None:
    srt_path.write_text(to_srt(segments), encoding="utf-8")
    vtt_path.write_text(to_vtt(segments), encoding="utf-8")
    ass_path.write_text(to_ass(segments), encoding="utf-8")

