from app.schemas import TranscriptSegment
from app.services.subtitles import to_ass, to_srt, to_vtt


def test_subtitle_formats() -> None:
    segments = [
        TranscriptSegment(start=0.0, end=1.4, text="Привет мир", confidence=0.9),
        TranscriptSegment(
            start=2.0,
            end=5.0,
            text="Это длинная строка субтитров для проверки умного переноса текста",
            confidence=0.8,
        ),
    ]

    srt = to_srt(segments)
    assert "00:00:00,000 --> 00:00:01,400" in srt
    assert "Привет мир" in srt

    vtt = to_vtt(segments)
    assert vtt.startswith("WEBVTT")
    assert "00:00:02.000 --> 00:00:05.000" in vtt

    ass = to_ass(segments)
    assert "[V4+ Styles]" in ass
    assert r"\fad(120,120)" in ass

