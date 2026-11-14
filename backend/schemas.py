from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SessionMode = Literal["student_training", "student_live", "professor_live"]


class SlideMetadata(BaseModel):
    page_number: int = Field(..., ge=1)
    raw_text: str
    clean_text: str
    word_count: int = Field(..., ge=0)
    is_low_text: bool = False
    thumbnail_path: Optional[str] = None


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class AlignedInterval(BaseModel):
    start_time: float
    end_time: float
    slide_page: int = Field(..., ge=1)
    matched_slide: Optional[SlideMetadata] = None
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    transcript_slice: str = ""
    wpm: float = 0.0
    filler_count: int = 0
    pause_count: int = 0


class SlideAnnotationNote(BaseModel):
    page_number: int = Field(..., ge=1)
    dimension: Literal["alignment", "relevance", "highlighting", "clarity", "complexity"]
    comment: str


class ProblemFlag(BaseModel):
    page_number: int = Field(..., ge=1)
    severity: Literal["low", "medium", "high"]
    dimension: Literal[
        "alignment", "relevance", "highlighting", "clarity", "complexity", "delivery"
    ]
    summary: str


class ComprehensionLensFeedback(BaseModel):
    lens: Literal["freshman", "average", "msc"]
    clarity_score: int = Field(..., ge=1, le=10)
    friction_points: list[str]
    suggestions: list[str]


class EvaluationReport(BaseModel):
    session_id: str
    mode: SessionMode
    overall_summary: str
    key_message_coverage: dict[str, bool]
    problem_flags: list[ProblemFlag]
    specchio_feedback: list[str]
    comprehension_lenses: list[ComprehensionLensFeedback]
    delivery_feedback: dict[str, Any]
    slide_notes: list[SlideAnnotationNote]
    annotated_pdf_url: Optional[str] = None
    digest_url: Optional[str] = None


class SessionRecord(BaseModel):
    session_id: str
    mode: SessionMode
    filename: str
    pdf_path: str
    key_messages: list[str]
    slides: list[SlideMetadata]
    aligned_intervals: list[AlignedInterval] = Field(default_factory=list)
    report: Optional[EvaluationReport] = None
    annotated_pdf_path: Optional[str] = None
    digest_path: Optional[str] = None
    created_at: str


class ContraddittorioRequest(BaseModel):
    session_id: str
    message: str


class ContraddittorioResponse(BaseModel):
    status: Literal["ok", "refused", "budget_exhausted", "unsupported_mode"]
    session_id: str
    turn: int = Field(..., ge=0, le=3)
    max_turns: int = 3
    boundary: Optional[str] = None
    reply: str
