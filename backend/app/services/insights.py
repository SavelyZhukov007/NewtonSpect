from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from typing import Iterable

from ..schemas import (
    Chapter,
    FactCheckItem,
    KeyQuote,
    PersonProfile,
    QualityScore,
    RunComparison,
    SpeakerTimelineItem,
    TranscriptSegment,
    VideoReport,
)


PII_PATTERNS = [
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", flags=re.IGNORECASE),
    re.compile(r"\b(?:\+?\d[\d\-\s()]{8,}\d)\b"),
]


def apply_glossary_to_segments(
    segments: list[TranscriptSegment], glossary_pairs: Iterable[tuple[str, str]]
) -> list[TranscriptSegment]:
    normalized = [(src.strip(), dst.strip()) for src, dst in glossary_pairs if src.strip() and dst.strip()]
    if not normalized:
        return segments
    patched = deepcopy(segments)
    for segment in patched:
        text = segment.text
        for source, target in normalized:
            text = re.sub(rf"\b{re.escape(source)}\b", target, text, flags=re.IGNORECASE)
        segment.text = text
    return patched


def redact_segments_for_privacy(segments: list[TranscriptSegment]) -> tuple[list[TranscriptSegment], bool]:
    redacted = deepcopy(segments)
    changed = False
    for segment in redacted:
        text = segment.text
        for pattern in PII_PATTERNS:
            next_text = pattern.sub("[REDACTED]", text)
            if next_text != text:
                changed = True
            text = next_text
        segment.text = text
    return redacted, changed


def build_chapters(segments: list[TranscriptSegment], *, max_chapters: int = 12) -> list[Chapter]:
    if not segments:
        return []
    boundaries = [0]
    for index in range(1, len(segments)):
        pause = segments[index].start - segments[index - 1].end
        if pause >= 5.5 or (index - boundaries[-1]) >= 8:
            boundaries.append(index)
    boundaries.append(len(segments))
    chapters: list[Chapter] = []
    for cidx in range(len(boundaries) - 1):
        start_i = boundaries[cidx]
        end_i = boundaries[cidx + 1] - 1
        if start_i > end_i:
            continue
        chunk = segments[start_i : end_i + 1]
        text = " ".join(item.text for item in chunk).strip()
        title = _summarize_topic_title(text, fallback=f"Chapter {cidx + 1}")
        confidence = _avg([item.confidence for item in chunk])
        chapters.append(
            Chapter(
                chapter_id=f"CH{cidx + 1:03d}",
                title=title,
                start=chunk[0].start,
                end=chunk[-1].end,
                confidence=confidence,
            )
        )
        if len(chapters) >= max_chapters:
            break
    return chapters


def build_key_quotes(segments: list[TranscriptSegment], *, limit: int = 8) -> list[KeyQuote]:
    scored: list[tuple[float, int, TranscriptSegment]] = []
    for index, segment in enumerate(segments):
        words = len(segment.text.split())
        punctuation_bonus = 0.3 if any(ch in segment.text for ch in ".!?;:") else 0.0
        score = (words / 22.0) + max(segment.confidence, 0.0) + punctuation_bonus
        scored.append((score, index, segment))
    scored.sort(key=lambda item: item[0], reverse=True)
    quotes: list[KeyQuote] = []
    used_indices: set[int] = set()
    for rank, (_, index, segment) in enumerate(scored, start=1):
        if index in used_indices:
            continue
        quotes.append(
            KeyQuote(
                quote_id=f"Q{rank:03d}",
                start=segment.start,
                end=segment.end,
                text=segment.text,
                score=round(scored[rank - 1][0], 4),
                speaker_ref=segment.speaker_ref,
            )
        )
        used_indices.add(index)
        if len(quotes) >= limit:
            break
    quotes.sort(key=lambda item: item.start)
    return quotes


def build_speaker_timeline(segments: list[TranscriptSegment]) -> list[SpeakerTimelineItem]:
    timeline: list[SpeakerTimelineItem] = []
    if not segments:
        return timeline
    current_speaker = segments[0].speaker_ref or "UNKNOWN"
    start = segments[0].start
    end = segments[0].end
    for segment in segments[1:]:
        speaker = segment.speaker_ref or "UNKNOWN"
        if speaker == current_speaker and segment.start - end <= 2.0:
            end = segment.end
            continue
        timeline.append(
            SpeakerTimelineItem(
                speaker_ref=current_speaker,
                start=start,
                end=end,
                duration=max(end - start, 0.0),
            )
        )
        current_speaker = speaker
        start = segment.start
        end = segment.end
    timeline.append(
        SpeakerTimelineItem(
            speaker_ref=current_speaker,
            start=start,
            end=end,
            duration=max(end - start, 0.0),
        )
    )
    return timeline


def compute_quality_score(
    *,
    segments: list[TranscriptSegment],
    people: list[PersonProfile],
    report: VideoReport,
    transcript_duration: float,
) -> QualityScore:
    asr_confidence = _avg([max(item.confidence, 0.0) for item in segments])
    spoken_duration = sum(max(seg.end - seg.start, 0.0) for seg in segments)
    subtitle_coverage = min(spoken_duration / max(transcript_duration, 1e-6), 1.0) if transcript_duration > 0 else 0.0
    named_speakers = [seg for seg in segments if seg.speaker_ref]
    speaker_stability = len(named_speakers) / max(len(segments), 1)
    people_stability = _avg([person.track_stats.avg_confidence for person in people]) if people else 0.0
    report_completeness = 0.0
    if report.summary_md.strip():
        report_completeness += 0.45
    if report.key_topics:
        report_completeness += 0.25
    if report.people_highlights:
        report_completeness += 0.2
    if report.latex_blocks:
        report_completeness += 0.1
    report_completeness = min(report_completeness, 1.0)
    overall = (
        asr_confidence * 0.28
        + subtitle_coverage * 0.22
        + speaker_stability * 0.18
        + people_stability * 0.17
        + report_completeness * 0.15
    )
    notes: list[str] = []
    if speaker_stability < 0.35:
        notes.append("Low speaker stability")
    if subtitle_coverage < 0.5:
        notes.append("Low subtitle coverage")
    if people and people_stability < 0.4:
        notes.append("Low people detection stability")
    timeline = build_speaker_timeline(segments)
    return QualityScore(
        overall=round(overall, 4),
        asr_confidence=round(asr_confidence, 4),
        subtitle_coverage=round(subtitle_coverage, 4),
        speaker_stability=round(speaker_stability, 4),
        people_stability=round(people_stability, 4),
        report_completeness=round(report_completeness, 4),
        notes=notes,
        speaker_timeline=timeline,
    )


def compare_runs(
    *,
    current_job_id: str,
    previous_job_id: str | None,
    current_segments: list[TranscriptSegment],
    previous_segments: list[TranscriptSegment],
    current_people: list[PersonProfile],
    previous_people: list[PersonProfile],
    current_quality: QualityScore,
    previous_quality: QualityScore,
) -> RunComparison:
    curr_text = " ".join(seg.text for seg in current_segments)
    prev_text = " ".join(seg.text for seg in previous_segments)
    wer_like_delta = _text_distance_ratio(curr_text, prev_text)
    current_spoken = sum(max(seg.end - seg.start, 0.0) for seg in current_segments)
    previous_spoken = sum(max(seg.end - seg.start, 0.0) for seg in previous_segments)
    summary = (
        f"Quality {previous_quality.overall:.3f} -> {current_quality.overall:.3f}. "
        f"People {len(previous_people)} -> {len(current_people)}."
    )
    return RunComparison(
        current_job_id=current_job_id,
        previous_job_id=previous_job_id,
        wer_like_delta=round(wer_like_delta, 4),
        people_delta=len(current_people) - len(previous_people),
        subtitle_coverage_delta=round(current_quality.subtitle_coverage - previous_quality.subtitle_coverage, 4),
        speaker_stability_delta=round(current_quality.speaker_stability - previous_quality.speaker_stability, 4),
        duration_speech_delta=round(current_spoken - previous_spoken, 4),
        summary_md=summary,
    )


def extract_claim_candidates(segments: list[TranscriptSegment], *, limit: int = 25) -> list[str]:
    candidates: list[str] = []
    for segment in segments:
        text = segment.text.strip()
        if len(text.split()) < 6:
            continue
        if not any(marker in text.lower() for marker in ("это", "is", "будет", "was", "является", "должен", "must")):
            continue
        candidates.append(text)
        if len(candidates) >= limit:
            break
    return candidates


@dataclass
class KBMatch:
    path: str
    text: str
    score: float


def run_offline_fact_check(
    claims: list[str],
    kb_chunks: list[dict[str, str]],
    *,
    top_k: int = 3,
) -> list[FactCheckItem]:
    results: list[FactCheckItem] = []
    for claim in claims:
        matches = _search_chunks(claim, kb_chunks, top_k=top_k)
        if not matches:
            results.append(
                FactCheckItem(
                    claim=claim,
                    status="not_found",
                    reason="No relevant matches in offline knowledge base.",
                    evidence_refs=[],
                )
            )
            continue
        best = matches[0]
        contradiction = any(token in claim.lower() for token in ("не ", "not ", "никогда", "never"))
        status: str = "supported"
        reason = "Best lexical match in offline KB."
        if contradiction and best.score < 0.22:
            status = "contradicted"
            reason = "Claim has negation and weak lexical evidence."
        elif best.score < 0.15:
            status = "not_found"
            reason = "Weak lexical evidence in offline KB."
        results.append(
            FactCheckItem(
                claim=claim,
                status=status,  # type: ignore[arg-type]
                reason=reason,
                evidence_refs=[f"{item.path}: {item.text[:120]}" for item in matches],
            )
        )
    return results


def _search_chunks(query: str, chunks: list[dict[str, str]], *, top_k: int) -> list[KBMatch]:
    qtokens = _tokenize(query)
    if not qtokens:
        return []
    scored: list[KBMatch] = []
    for item in chunks:
        text = item.get("text", "")
        tokens = _tokenize(text)
        if not tokens:
            continue
        overlap = sum((qtokens & tokens).values())  # type: ignore[arg-type]
        score = overlap / max(sum(qtokens.values()), 1)
        if score <= 0:
            continue
        scored.append(KBMatch(path=item.get("path", ""), text=text, score=score))
    scored.sort(key=lambda row: row.score, reverse=True)
    return scored[:top_k]


def _tokenize(text: str) -> Counter[str]:
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9_]+", text.lower())
    return Counter(words)


def _text_distance_ratio(a: str, b: str) -> float:
    atokens = _tokenize(a)
    btokens = _tokenize(b)
    if not atokens and not btokens:
        return 0.0
    all_keys = set(atokens) | set(btokens)
    diff = sum(abs(atokens.get(key, 0) - btokens.get(key, 0)) for key in all_keys)
    total = sum(atokens.values()) + sum(btokens.values())
    return diff / max(total, 1)


def _summarize_topic_title(text: str, *, fallback: str) -> str:
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9_]+", text.lower())
    if not words:
        return fallback
    stop = {
        "и",
        "в",
        "на",
        "the",
        "a",
        "an",
        "to",
        "is",
        "это",
        "как",
        "что",
        "for",
        "with",
    }
    freq = Counter(word for word in words if word not in stop and len(word) > 2)
    if not freq:
        return fallback
    top = [item[0] for item in freq.most_common(3)]
    return " / ".join(word.capitalize() for word in top)


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)
