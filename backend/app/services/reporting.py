from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..schemas import PersonProfile, TranscriptSegment, VideoReport
from .ollama_client import OllamaClient


def _extract_json_object(text: str) -> dict | None:
    text = text.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
    return None


def _extract_latex_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for expr in re.findall(r"\$\$(.+?)\$\$", text, flags=re.DOTALL):
        blocks.append(expr.strip())
    return blocks


def _transcript_to_prompt_lines(segments: list[TranscriptSegment], limit: int = 200) -> str:
    rows: list[str] = []
    for idx, segment in enumerate(segments[:limit], start=1):
        speaker = segment.speaker_ref or "UNKNOWN"
        rows.append(
            f"{idx}. [{segment.start:.2f}-{segment.end:.2f}] ({speaker}) {segment.text.strip()}"
        )
    return "\n".join(rows)


def _people_to_prompt_lines(people: list[PersonProfile]) -> str:
    rows: list[str] = []
    for person in people:
        rows.append(
            (
                f"{person.person_id}: screen_time={person.track_stats.screen_time_seconds:.2f}s, "
                f"speaking={person.track_stats.speaking_seconds:.2f}s, "
                f"avg_conf={person.track_stats.avg_confidence:.3f}"
            )
        )
    return "\n".join(rows) if rows else "No detected people."


@dataclass
class ReportGenerator:
    ollama_client: OllamaClient

    def generate(self, segments: list[TranscriptSegment], people: list[PersonProfile]) -> VideoReport:
        prompt = self._build_prompt(segments, people)
        try:
            raw = self.ollama_client.generate(prompt=prompt)
        except Exception as exc:  # noqa: BLE001
            fallback = self._fallback_report(segments, people)
            fallback.raw_markdown = f"Ollama unavailable: {exc}"
            return fallback

        parsed = _extract_json_object(raw)
        if not parsed:
            fallback = self._fallback_report(segments, people)
            fallback.raw_markdown = raw
            return fallback

        summary_md = str(parsed.get("summary_md", "")).strip()
        key_topics = parsed.get("key_topics", [])
        latex_blocks = parsed.get("latex_blocks", [])
        people_highlights = parsed.get("people_highlights", {})
        if not isinstance(key_topics, list):
            key_topics = []
        if not isinstance(latex_blocks, list):
            latex_blocks = []
        if not isinstance(people_highlights, dict):
            people_highlights = {}

        report = VideoReport(
            summary_md=summary_md,
            key_topics=[str(item) for item in key_topics],
            latex_blocks=[str(item) for item in latex_blocks],
            people_highlights={
                str(key): [str(item) for item in value] if isinstance(value, list) else []
                for key, value in people_highlights.items()
            },
            raw_markdown=raw,
        )
        if not report.latex_blocks:
            report.latex_blocks = _extract_latex_blocks(report.summary_md)
        return report

    def _build_prompt(self, segments: list[TranscriptSegment], people: list[PersonProfile]) -> str:
        transcript_block = _transcript_to_prompt_lines(segments)
        people_block = _people_to_prompt_lines(people)
        return f"""
You are a video-content analyst. Input contains transcript segments and detected people.
Produce a concise, useful summary in Russian.

Return ONLY valid JSON with no extra text:
{{
  "summary_md": "markdown summary with optional latex formulas where relevant",
  "key_topics": ["topic1", "topic2"],
  "latex_blocks": ["E=mc^2"],
  "people_highlights": {{
    "P001": ["key comment 1", "key comment 2"]
  }}
}}

Requirements:
- Write in Russian.
- Briefly describe the video (2-4 short paragraphs).
- Add key-topic notes as bullet points.
- Add LaTeX formulas only when mathematically relevant.
- For each person id in people_highlights, provide the most important comments tied to transcript content.
- Do not invent facts not present in the transcript.

People:
{people_block}

Transcript:
{transcript_block}
""".strip()

    def _fallback_report(
        self, segments: list[TranscriptSegment], people: list[PersonProfile]
    ) -> VideoReport:
        preview = " ".join([segment.text for segment in segments[:8]])
        topics = []
        for text in preview.split("."):
            token = text.strip()
            if len(token) > 20:
                topics.append(token[:100])
            if len(topics) >= 5:
                break
        people_highlights = {person.person_id: [] for person in people}
        summary_lines = [
            "## \u041a\u0440\u0430\u0442\u043a\u043e\u0435 \u043e\u043f\u0438\u0441\u0430\u043d\u0438\u0435",
            preview[:1200]
            if preview
            else "\u041d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e \u0434\u0430\u043d\u043d\u044b\u0445 \u0434\u043b\u044f \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u043e\u0433\u043e \u043e\u043f\u0438\u0441\u0430\u043d\u0438\u044f.",
            "",
            "## \u041a\u043e\u043d\u0441\u043f\u0435\u043a\u0442",
        ]
        if topics:
            summary_lines.extend([f"- {topic}" for topic in topics])
        else:
            summary_lines.append(
                "- \u041d\u0435\u0442 \u0432\u044b\u0434\u0435\u043b\u0435\u043d\u043d\u044b\u0445 \u0442\u0435\u0437\u0438\u0441\u043e\u0432."
            )
        summary = "\n".join(summary_lines)
        return VideoReport(
            summary_md=summary,
            key_topics=topics,
            latex_blocks=[],
            people_highlights=people_highlights,
            raw_markdown="",
        )
