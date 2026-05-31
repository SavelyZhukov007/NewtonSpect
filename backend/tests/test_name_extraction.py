from app.schemas import TranscriptSegment
from app.services.pipeline import PipelineRunner


class _DummyPipeline:
    _normalize_name_token = staticmethod(PipelineRunner._normalize_name_token)


def test_extract_names_by_person_ru_en_patterns() -> None:
    dummy = _DummyPipeline()
    segments = [
        TranscriptSegment(
            start=0.0,
            end=2.5,
            text="Меня зовут Анна, и сегодня расскажу о проекте.",
            confidence=0.91,
            speaker_ref="P001",
        ),
        TranscriptSegment(
            start=2.6,
            end=4.0,
            text="Call me Mark for this demo.",
            confidence=0.88,
            speaker_ref="P002",
        ),
    ]

    extracted = PipelineRunner._extract_names_by_person(dummy, segments)  # type: ignore[arg-type]
    assert extracted["P001"][0] == "Анна"
    assert extracted["P002"][0] == "Mark"
    assert 0.0 <= extracted["P001"][1] <= 1.0
    assert 0.0 <= extracted["P002"][1] <= 1.0
