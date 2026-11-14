from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Any, Optional
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.schemas import (
    ContraddittorioRequest,
    ContraddittorioResponse,
    SessionMode,
    SessionRecord,
    SlideMetadata,
    TranscriptSegment,
)
from backend.services.alignment_service import AlignmentService
from backend.services.audio_service import AudioService
from backend.services.db_service import DatabaseService
from backend.services.evaluation_service import (
    ContraddittorioAgent,
    EvaluationOrchestrator,
)
from backend.services.llm_service import LLMService
from backend.services.slide_service import SlideService
from backend.services.video_service import VideoService

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
OUTPUTS_DIR = DATA_DIR / "outputs"
SLIDES_OUT_DIR = OUTPUTS_DIR / "slides"
ANNOTATED_DIR = OUTPUTS_DIR / "annotated"
DIGESTS_DIR = OUTPUTS_DIR / "digests"
DB_PATH = DATA_DIR / "kairos.db"
FRONTEND_DIR = BASE_DIR / "frontend"

VALID_MODES = ["student_training", "student_live", "professor_live"]

# Ensure output directories exist before mounting StaticFiles.
for _dir in (UPLOADS_DIR, OUTPUTS_DIR, SLIDES_OUT_DIR, ANNOTATED_DIR, DIGESTS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

_db_service: Optional[DatabaseService] = None


def get_db() -> DatabaseService:
    global _db_service
    if _db_service is None:
        _db_service = DatabaseService(DB_PATH)
    return _db_service


def get_llm() -> LLMService:
    return LLMService()


@asynccontextmanager
async def lifespan(_: FastAPI):
    for d in (UPLOADS_DIR, OUTPUTS_DIR, SLIDES_OUT_DIR, ANNOTATED_DIR, DIGESTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    get_db()
    yield


app = FastAPI(title="Kairos API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health_check() -> dict[str, Any]:
    return {
        "status": "ok",
        "host": "t480",
        "port": 8101,
        "modes": VALID_MODES,
    }


@app.post("/api/session/init")
async def init_session(
    mode: SessionMode = Form(...),
    key_messages: str = Form(default=""),
    pdf: UploadFile = File(...),
) -> dict[str, Any]:
    messages = [m.strip() for m in key_messages.splitlines() if m.strip()]
    if mode == "professor_live":
        messages = []

    session_id = uuid.uuid4().hex[:12]
    safe_name = Path(pdf.filename or "slides.pdf").name
    saved_path = UPLOADS_DIR / f"{session_id}_{safe_name}"
    saved_path.write_bytes(await pdf.read())

    slides: list[SlideMetadata] = SlideService(SLIDES_OUT_DIR).process_pdf(
        saved_path, prefix=session_id
    )
    record = get_db().create_session(
        session_id=session_id,
        mode=mode,
        filename=safe_name,
        pdf_path=str(saved_path),
        key_messages=messages,
        slides=slides,
    )
    return {
        "session_id": record.session_id,
        "mode": record.mode,
        "filename": record.filename,
        "key_messages": record.key_messages,
        "slide_count": len(record.slides),
        "slides": record.slides,
    }


@app.post("/api/session/analyze")
async def analyze_session(
    session_id: str = Form(...),
    audio: Optional[UploadFile] = File(default=None),
    transcript_json: Optional[str] = Form(default=None),
    telemetry_json: str = Form(default="{}"),
    slide_clicks_json: str = Form(default="[]"),
) -> dict[str, Any]:
    db = get_db()
    session = db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    try:
        telemetry = json.loads(telemetry_json) if telemetry_json.strip() else {}
        slide_clicks = json.loads(slide_clicks_json) if slide_clicks_json.strip() else []
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc

    segments: list[TranscriptSegment]
    if transcript_json is not None and transcript_json.strip():
        try:
            raw_segments = json.loads(transcript_json)
            segments = [TranscriptSegment(**seg) for seg in raw_segments]
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid transcript_json: {exc}"
            ) from exc
    elif audio is not None:
        audio_ext = Path(audio.filename or "recording.wav").suffix or ".wav"
        raw_audio_path = UPLOADS_DIR / f"{session_id}_audio{audio_ext}"
        raw_audio_path.write_bytes(await audio.read())

        wav_path = (
            raw_audio_path
            if raw_audio_path.suffix.lower() == ".wav"
            else VideoService(UPLOADS_DIR).extract_audio(raw_audio_path)
        )
        audio_svc = AudioService()
        initial_prompt = audio_svc.build_initial_prompt(session.slides)
        segments = audio_svc.transcribe(wav_path, initial_prompt=initial_prompt)
    else:
        raise HTTPException(
            status_code=400,
            detail="Either 'audio' upload or 'transcript_json' must be provided",
        )

    alignment_svc = AlignmentService()
    if session.mode == "student_training" and slide_clicks:
        intervals = alignment_svc.align_from_manual_timestamps(
            segments=segments,
            slides=session.slides,
            slide_click_timestamps=slide_clicks,
        )
    else:
        intervals = alignment_svc.align_live_audio(
            segments=segments,
            slides=session.slides,
        )

    orchestrator = EvaluationOrchestrator(
        llm=get_llm(),
        slides_out_dir=SLIDES_OUT_DIR,
        annotated_dir=ANNOTATED_DIR,
        digests_dir=DIGESTS_DIR,
    )
    report, annotated_path, digest_path = await orchestrator.evaluate(
        session=session,
        intervals=intervals,
        telemetry=telemetry,
    )

    db.save_analysis(
        session_id=session_id,
        intervals=intervals,
        report=report,
        annotated_pdf_path=str(annotated_path) if annotated_path else None,
        digest_path=str(digest_path) if digest_path else None,
    )

    return {
        "session_id": session.session_id,
        "mode": session.mode,
        "aligned_intervals": intervals,
        "report": report,
    }


@app.post("/api/contraddittorio/chat", response_model=ContraddittorioResponse)
async def contraddittorio_chat(
    payload: ContraddittorioRequest,
) -> ContraddittorioResponse:
    db = get_db()
    session = db.get_session(payload.session_id)
    if session is None:
        raise HTTPException(
            status_code=404, detail=f"Session '{payload.session_id}' not found"
        )

    agent = ContraddittorioAgent(llm=get_llm(), db=db)
    return await agent.reply(session=session, user_message=payload.message)


@app.get("/api/session/{session_id}", response_model=SessionRecord)
async def get_session_endpoint(session_id: str) -> SessionRecord:
    session = get_db().get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return session


@app.post("/api/slides/upload")
async def upload_slides(pdf: UploadFile = File(...)) -> dict[str, Any]:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    SLIDES_OUT_DIR.mkdir(parents=True, exist_ok=True)

    safe_name = Path(pdf.filename or "slides.pdf").name
    upload_prefix = uuid.uuid4().hex[:8]
    saved_path = UPLOADS_DIR / f"{upload_prefix}_{safe_name}"
    saved_path.write_bytes(await pdf.read())

    slides: list[SlideMetadata] = SlideService(SLIDES_OUT_DIR).process_pdf(
        saved_path, prefix=upload_prefix
    )
    return {
        "filename": safe_name,
        "slide_count": len(slides),
        "slides": slides,
    }


app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
