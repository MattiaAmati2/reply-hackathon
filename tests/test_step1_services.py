from pathlib import Path

import fitz

from backend.schemas import SlideAnnotationNote, SlideMetadata, TranscriptSegment
from backend.services.alignment_service import AlignmentService
from backend.services.audio_service import AudioService
from backend.services.slide_service import SlideService


def _create_synthetic_pdf(pdf_path: Path) -> Path:
    doc = fitz.open()

    # Page 1: rich text slide
    page1 = doc.new_page()
    page1.insert_text(
        (72, 72),
        "Introduction to Sparring Architecture\n"
        "We present a multimodal thesis rehearsal system combining speech analysis, "
        "slide alignment, and Socratic questioning for academic presentations.",
    )

    # Page 2: near-blank / low-text diagram slide
    page2 = doc.new_page()
    page2.draw_rect(fitz.Rect(100, 120, 400, 280), color=(0, 0, 1), width=2)
    page2.insert_text((180, 200), "System Diagram")

    # Page 3: second rich text slide
    page3 = doc.new_page()
    page3.insert_text(
        (72, 72),
        "Empirical Evaluation and Results\n"
        "Our experiments demonstrate a 24 percent improvement in pacing consistency "
        "and stronger alignment between spoken claims and visual evidence.",
    )

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


def test_slide_service_process_pdf(tmp_path: Path) -> None:
    pdf_path = _create_synthetic_pdf(tmp_path / "deck.pdf")
    out_dir = tmp_path / "slides_out"
    service = SlideService(out_dir)

    slides = service.process_pdf(pdf_path, prefix="test")

    assert len(slides) == 3
    assert slides[0].page_number == 1
    assert not slides[0].is_low_text
    assert slides[0].word_count >= 10

    assert slides[1].page_number == 2
    assert slides[1].is_low_text
    assert slides[1].word_count < 10

    assert slides[2].page_number == 3
    assert not slides[2].is_low_text

    for slide in slides:
        assert slide.thumbnail_path is not None
        thumb = Path(slide.thumbnail_path)
        assert thumb.exists()
        assert thumb.stat().st_size > 0


def test_slide_service_annotate_pdf(tmp_path: Path) -> None:
    pdf_path = _create_synthetic_pdf(tmp_path / "deck.pdf")
    out_pdf = tmp_path / "annotated.pdf"
    service = SlideService(tmp_path / "slides_out")

    notes = [
        SlideAnnotationNote(
            page_number=1,
            dimension="alignment",
            comment="Strong opening alignment with spoken thesis.",
        ),
        SlideAnnotationNote(
            page_number=2,
            dimension="clarity",
            comment="Add axis labels to the system diagram.",
        ),
    ]
    result_path = service.annotate_pdf(pdf_path, notes, out_pdf)

    assert result_path.exists()
    doc = fitz.open(result_path)
    try:
        assert len(list(doc[0].annots())) >= 1
        assert len(list(doc[1].annots())) >= 1
    finally:
        doc.close()


def test_audio_service_prompt_and_metrics() -> None:
    slides = [
        SlideMetadata(
            page_number=1,
            raw_text="Sparring Architecture WhisperX PyMuPDF",
            clean_text="Sparring Architecture WhisperX PyMuPDF",
            word_count=4,
            is_low_text=True,
        )
    ]
    prompt = AudioService.build_initial_prompt(slides)
    assert prompt is not None
    assert "Sparring" in prompt
    assert "PyMuPDF" in prompt

    segments = [
        TranscriptSegment(start=0.0, end=2.0, text="Ehm welcome to Sparring"),
        TranscriptSegment(start=4.0, end=6.0, text="like basically it works"),
        TranscriptSegment(start=6.5, end=8.0, text="allora let us continue"),
    ]
    metrics = AudioService.compute_speech_metrics(segments, 0.0, 10.0)
    assert metrics["pause_count"] == 1  # 4.0 - 2.0 = 2.0 > 1.5
    assert metrics["filler_count"] == 4  # Ehm, like, basically, allora
    assert metrics["wpm"] > 0.0


def test_alignment_service_manual_timestamps() -> None:
    slides = [
        SlideMetadata(
            page_number=1,
            raw_text="Intro",
            clean_text="Intro",
            word_count=1,
            is_low_text=True,
        ),
        SlideMetadata(
            page_number=2,
            raw_text="Results and evaluation metrics",
            clean_text="Results and evaluation metrics",
            word_count=4,
            is_low_text=True,
        ),
    ]
    segments = [
        TranscriptSegment(start=0.0, end=4.0, text="Welcome to the intro"),
        TranscriptSegment(start=5.0, end=9.0, text="Here are the results"),
    ]
    clicks = [
        {"slide_page": 1, "start_time": 0.0, "end_time": 4.5},
        {"slide_page": 2, "start_time": 4.5, "end_time": 10.0},
    ]

    service = AlignmentService()
    intervals = service.align_from_manual_timestamps(segments, slides, clicks)

    assert len(intervals) == 2
    assert intervals[0].slide_page == 1
    assert intervals[0].confidence_score == 1.0
    assert "intro" in intervals[0].transcript_slice.lower()
    assert intervals[1].slide_page == 2
    assert "results" in intervals[1].transcript_slice.lower()


def test_alignment_service_live_audio_contiguous_and_keeps_low_text() -> None:
    slides = [
        SlideMetadata(
            page_number=1,
            raw_text="Introduction to multimodal speech alignment",
            clean_text="Introduction to multimodal speech alignment",
            word_count=5,
            is_low_text=False,
        ),
        SlideMetadata(
            page_number=2,
            raw_text="Diagram",
            clean_text="Diagram",
            word_count=1,
            is_low_text=True,
        ),
        SlideMetadata(
            page_number=3,
            raw_text="Empirical evaluation results and pacing metrics",
            clean_text="Empirical evaluation results and pacing metrics",
            word_count=6,
            is_low_text=False,
        ),
    ]
    segments = [
        TranscriptSegment(start=0.0, end=3.0, text="Introduction to multimodal speech"),
        TranscriptSegment(start=3.0, end=6.0, text="speech alignment for presentations"),
        TranscriptSegment(start=6.0, end=9.0, text="Looking at this diagram here"),
        TranscriptSegment(start=9.0, end=12.0, text="Empirical evaluation results"),
        TranscriptSegment(start=12.0, end=15.0, text="and pacing metrics improvement"),
    ]

    service = AlignmentService()
    intervals = service.align_live_audio(segments, slides)

    assert len(intervals) >= 1
    assert intervals[0].start_time == 0.0
    assert intervals[-1].end_time == 15.0
    for i, interval in enumerate(intervals):
        assert interval.end_time > interval.start_time
        if i > 0:
            assert interval.start_time == intervals[i - 1].end_time
