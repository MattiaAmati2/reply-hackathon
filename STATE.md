# Sparring — Project State

## Current Phase
- **Step 2 (Multi-Agent Evaluation, Socratic Contraddittorio, SQLite Persistence & Pipeline Wiring)** -> Completed.

## Completed Modules
- `ARCHITECTURE.md` — System blueprint, hardware split, asymmetric modes, and layout.
- `STATE.md` — Project status tracker.
- `.agents/plans/CURRENT_TASK.md` — Step 0 execution plan.
- `t480_ops.sh` — Executable rsync, venv setup, start, logs, and status script for T480.
- `requirements.txt` — CPU-friendly Python dependencies.
- `backend/__init__.py` & `backend/main.py` — FastAPI skeleton with CORS, health check, session/contraddittorio stubs, and static frontend mount.
- `frontend/index.html` & `frontend/app.js` — Responsive Tailwind CDN UI with Mode Selector and T480 health badge.
- `backend/schemas.py` — Pydantic v2 models (`SlideMetadata`, `TranscriptSegment`, `AlignedInterval`, `SlideAnnotationNote`).
- `backend/services/slide_service.py` — PyMuPDF text extraction, 150 DPI PNG thumbnail rendering, and PDF diagnostic annotation.
- `backend/services/video_service.py` — `ffmpeg` 16 kHz mono WAV audio extraction.
- `backend/services/audio_service.py` — `faster-whisper` transcription (`language=None` default), `build_initial_prompt`, and `compute_speech_metrics`.
- `backend/services/alignment_service.py` — Manual timestamp alignment and fixed HMM+Viterbi live alignment (keeps low-text slides, midpoint chunk bounds, TF-IDF + optional dense blending).
- `backend/main.py` — Wired `POST /api/slides/upload`.
- `tests/test_step1_services.py` — Unit tests for SlideService, AudioService, and AlignmentService (5/5 passing).
- `backend/schemas.py` (extended) — `SessionMode`, `ProblemFlag`, `ComprehensionLensFeedback`, `EvaluationReport`, `SessionRecord`, `ContraddittorioRequest`, `ContraddittorioResponse`.
- `backend/services/db_service.py` — SQLite WAL persistence for `sessions` and `contraddittorio_turns`.
- `backend/services/llm_service.py` — Ollama-first async LLM client (`qwen2.5:7b` default) with optional cloud API adapter and safe JSON fallback parsing.
- `backend/services/evaluation_service.py` — Deterministic `ProblemIdentifier`, 3 Solvers (`SpecchioSolver`, `DeliverySolver`, `ActionableOutputSolver`), `EvaluationOrchestrator` (sequential on Ollama, concurrent on Cloud), WeBeep Digest generator, and `ContraddittorioAgent` (3-turn budget + `no_ghostwriting` boundary).
- `backend/main.py` (wired) — `POST /api/session/init`, `POST /api/session/analyze`, `POST /api/contraddittorio/chat`, `GET /api/session/{session_id}`, and `/outputs` static mount.
- `tests/test_step2_pipeline.py` — End-to-end FastAPI `TestClient` & orchestrator tests (4/4 passing).

## Next Up
- **Step 3**: Frontend Live/Training UI, MediaPipe Telemetry & Audio Capture.

## T480 Deployment Info
- **Target**: `/home/selfhost/Reply-Hackathon`
- **Port**: `8101`
- **Deployment Method**: `rsync` via `./t480_ops.sh` (no git)
