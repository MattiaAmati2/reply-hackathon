# Current Task: Step 2 — Multi-Agent Evaluation, Socratic Contraddittorio, SQLite Persistence & Pipeline Wiring

## Specification
Implement the Level 2 + Level 3A evaluation backend for **Sparring** on top of Step 1's services (`SlideService`, `VideoService`, `AudioService`, `AlignmentService`):
- **Ollama-first LLM layer (`backend/services/llm_service.py`)**: runs 100% locally on the T480 via Ollama (`OLLAMA_MODEL=qwen2.5:7b` default, switchable to `llama3.2:3b` via `.env`), with an optional Cloud API adapter when `LLM_API_KEY` is set (and automatic fallback to Ollama on error).
- **Hybrid Multi-Agent Pipeline (`ProblemIdentifier -> 3 Solvers -> Orchestrator`)**:
  - `ProblemIdentifier` runs deterministically in Python (zero LLM calls) to keep T480 CPU latency low.
  - The 3 Solvers (`SpecchioSolver`, `DeliverySolver`, `ActionableOutputSolver`) run **sequentially** on local Ollama (preventing 4-core CPU thrashing) and **concurrently** via `asyncio.gather` when a Cloud API key is configured.
  - Asymmetric modes: `professor_live` runs **3 Comprehension Lenses** (*Freshman*, *Average*, *MSc*) with zero content judgment + generates a **Post-Lecture WeBeep Digest**; `student_training` ingests manual clicks + webcam MediaPipe telemetry; both student modes support **Socratic Contraddittorio** (max 3 turns) with a strict `no_ghostwriting` boundary.
- **SQLite Persistence & Full API Wiring**: wire `POST /api/session/init`, `POST /api/session/analyze` (`multipart/form-data` accepting raw audio or pre-processed `transcript_json`), `POST /api/contraddittorio/chat`, `GET /api/session/{session_id}`, and static `/outputs` download routes.

## Steps
- [ ] **1. Extend `backend/schemas.py`**
  - Add `SessionMode = Literal["student_training", "student_live", "professor_live"]`.
  - Add models:
    - `ProblemFlag`: `page_number` (`int`, `ge=1`), `severity` (`Literal["low", "medium", "high"]`), `dimension` (`Literal["alignment", "relevance", "highlighting", "clarity", "complexity", "delivery"]`), `summary` (`str`).
    - `ComprehensionLensFeedback`: `lens` (`Literal["freshman", "average", "msc"]`), `clarity_score` (`int`, `ge=1`, `le=10`), `friction_points` (`list[str]`), `suggestions` (`list[str]`).
    - `EvaluationReport`: `session_id` (`str`), `mode` (`SessionMode`), `overall_summary` (`str`), `key_message_coverage` (`dict[str, bool]`), `problem_flags` (`list[ProblemFlag]`), `specchio_feedback` (`list[str]`), `comprehension_lenses` (`list[ComprehensionLensFeedback]`), `delivery_feedback` (`dict[str, Any]`), `slide_notes` (`list[SlideAnnotationNote]`), `annotated_pdf_url` (`Optional[str] = None`), `digest_url` (`Optional[str] = None`).
    - `SessionRecord`: `session_id` (`str`), `mode` (`SessionMode`), `filename` (`str`), `pdf_path` (`str`), `key_messages` (`list[str]`), `slides` (`list[SlideMetadata]`), `aligned_intervals` (`list[AlignedInterval] = Field(default_factory=list)`), `report` (`Optional[EvaluationReport] = None`), `annotated_pdf_path` (`Optional[str] = None`), `digest_path` (`Optional[str] = None`), `created_at` (`str`).
    - `ContraddittorioRequest`: `session_id` (`str`), `message` (`str`).
    - `ContraddittorioResponse`: `status` (`Literal["ok", "refused", "budget_exhausted", "unsupported_mode"]`), `session_id` (`str`), `turn` (`int`, `ge=0`, `le=3`), `max_turns` (`int = 3`), `boundary` (`Optional[str] = None`), `reply` (`str`).

- [ ] **2. Create `backend/services/db_service.py` (SQLite Persistence)**
  - Implement `DatabaseService(db_path: Path | str)`:
    - Initialize SQLite schema (`WAL` mode) with two tables:
      - `sessions` (`session_id PK`, `mode`, `filename`, `pdf_path`, `key_messages_json`, `slides_json`, `intervals_json`, `report_json`, `annotated_pdf_path`, `digest_path`, `created_at`).
      - `contraddittorio_turns` (`id PK AUTOINCREMENT`, `session_id`, `turn`, `role`, `content`, `refused`, `created_at`).
    - Implement `create_session(...) -> SessionRecord`, `get_session(session_id: str) -> Optional[SessionRecord]`, `save_analysis(session_id: str, intervals: list[AlignedInterval], report: EvaluationReport, annotated_pdf_path: Optional[str], digest_path: Optional[str]) -> None`, `get_chat_history(session_id: str) -> list[dict[str, Any]]`, `get_user_turn_count(session_id: str) -> int`, and `append_chat_turn(session_id: str, turn: int, role: str, content: str, refused: bool = False) -> None`.

- [ ] **3. Create `backend/services/llm_service.py` (Ollama-First + Optional Cloud Key)**
  - Load `.env` via `python-dotenv`; read `OLLAMA_BASE_URL` (default `http://localhost:11434`), `OLLAMA_MODEL` (default `qwen2.5:7b`), `LLM_API_KEY` (default `""`), `LLM_PROVIDER` (default `ollama`), `LLM_CLOUD_MODEL` (default `gemini-2.0-flash`), and `LLM_BASE_URL` (optional OpenAI-compatible URL).
  - Implement `LLMServiceError(RuntimeError)` and `LLMService`:
    - Property `is_cloud_enabled -> bool` (`bool(self.api_key.strip())`).
    - `async generate(prompt: str, system_prompt: str = "", max_tokens: int = 320, temperature: float = 0.2) -> str`:
      - If `is_cloud_enabled`, attempt cloud call via `httpx.AsyncClient` (supporting Gemini REST or OpenAI-compatible `/chat/completions`) and automatically fall back to local Ollama on request/HTTP failure.
      - Local Ollama call posts to `{ollama_base_url}/api/chat` (or `/api/generate`) with T480 CPU options (`num_thread: 4`, `num_ctx: 2048`, `num_predict: max_tokens`, `temperature`, `top_p: 0.85`, `repeat_penalty: 1.15`).
    - `async generate_json(prompt: str, system_prompt: str = "", fallback: Optional[dict[str, Any]] = None, max_tokens: int = 320) -> dict[str, Any]`:
      - Strips markdown code fences, extracts first JSON object, parses safely, and returns `fallback` (or `{}`) if LLM output is malformed or unreachable.

- [ ] **4. Create `backend/services/evaluation_service.py` (Level 2 + 3A Multi-Agent Pipeline & Contraddittorio)**
  - Implement deterministic `ProblemIdentifier`:
    - Inspects `AlignedInterval`s, `key_messages` (for student modes), and `telemetry` (`student_training` only) to emit `list[ProblemFlag]` and `key_message_coverage: dict[str, bool]`:
      - Flags `wpm < 95` or `wpm > 175`, `filler_count >= 3`, `pause_count >= 2`, `confidence_score < 0.25` (live mode), short/uncovered intervals (`duration < 5.0` or empty `transcript_slice`), and low MediaPipe telemetry (`eye_contact_ratio < 0.45` or `posture_score < 0.5`).
  - Implement 3 Solvers + `EvaluationOrchestrator`:
    - `SpecchioSolver`:
      - Student modes: concise narrative & key-message alignment feedback (`specchio_feedback`).
      - `professor_live`: 3 `ComprehensionLensFeedback` entries (`freshman`, `average`, `msc`) focusing strictly on understandability, jargon scaffolding, and pacing — **zero content judgment**.
    - `DeliverySolver`: aggregates WPM, pauses, fillers, and (in `student_training`) webcam telemetry into actionable delivery feedback (`delivery_feedback`).
    - `ActionableOutputSolver`: generates concise `list[SlideAnnotationNote]` (`<= 180` chars each, valid `page_number` & `dimension`) for PDF annotation.
    - Execution policy: run the 3 solvers sequentially when `not llm.is_cloud_enabled` (Ollama CPU) and concurrently via `asyncio.gather` when `llm.is_cloud_enabled`, with clean deterministic fallbacks if Ollama is unavailable.
    - Artifact generation:
      - Call `SlideService(slides_out_dir).annotate_pdf(Path(session.pdf_path), slide_notes, annotated_pdf_path)` into `OUTPUTS_DIR / "annotated"`.
      - For `professor_live`, render a structured **Post-Lecture WeBeep Digest** Markdown file into `OUTPUTS_DIR / "digests"` (slide timeline, key concepts discussed, comprehension-lens takeaways).
  - Implement `ContraddittorioAgent`:
    - Reject `professor_live` with `status="unsupported_mode"`.
    - Enforce `no_ghostwriting` boundary (case-insensitive EN/IT regex for requests to write/rewrite thesis/slides/text + explicit system prompt guardrail); refused turns return `status="refused"`, `boundary="no_ghostwriting"`, without advancing the 3-turn Socratic budget.
    - Enforce max 3 valid user turns per session (`get_user_turn_count >= 3` -> `status="budget_exhausted"`).
    - Generate 1 focused Socratic follow-up question anchored to the session's slides, transcript slices, and missing key messages, persisting both turns in `DatabaseService`.
  - Export new services in `backend/services/__init__.py`.

- [ ] **5. Wire Full Session, Analysis & Contraddittorio Endpoints in `backend/main.py`**
  - Create output subdirectories in `lifespan`: `ANNOTATED_DIR = OUTPUTS_DIR / "annotated"` and `DIGESTS_DIR = OUTPUTS_DIR / "digests"`, and initialize `DatabaseService(DATA_DIR / "sparring.db")`.
  - Mount `OUTPUTS_DIR` at `/outputs` (`StaticFiles(directory=str(OUTPUTS_DIR))`) **before** the `/` frontend mount.
  - Replace stubs in `backend/main.py`:
    - `POST /api/session/init`: save uploaded PDF to `UPLOADS_DIR`, extract slides + thumbnails via `SlideService(SLIDES_OUT_DIR).process_pdf`, store session in `DatabaseService`, return `session_id`, `mode`, `filename`, `key_messages`, `slide_count`, and `slides`.
    - `POST /api/session/analyze`: accept `multipart/form-data` (`session_id: str = Form(...)`, `audio: Optional[UploadFile] = File(default=None)`, `transcript_json: Optional[str] = Form(default=None)`, `telemetry_json: str = Form(default="{}")`, `slide_clicks_json: str = Form(default="[]")`):
      - Load session (404 if missing).
      - Produce `list[TranscriptSegment]` from `transcript_json` if provided, otherwise save `audio`, convert non-`.wav` via `VideoService.extract_audio`, and transcribe via `AudioService.transcribe(..., initial_prompt=AudioService.build_initial_prompt(session.slides))`.
      - Align via `AlignmentService.align_from_manual_timestamps` (`student_training` when `slide_clicks` is non-empty) or `AlignmentService.align_live_audio`.
      - Run `EvaluationOrchestrator.evaluate(...)`, save results in SQLite, and return `session_id`, `mode`, `aligned_intervals`, and `report` (with `/outputs/...` URLs).
    - `POST /api/contraddittorio/chat`: invoke `ContraddittorioAgent.reply(...)` and return `ContraddittorioResponse` (404 if session missing).
    - `GET /api/session/{session_id}`: return persisted `SessionRecord` (404 if missing).

- [ ] **6. Add Verification Tests (`tests/test_step2_pipeline.py`) & Run `pytest`**
  - Add unit + FastAPI `TestClient` integration tests using mocked `LLMService` (plus real JSON fallback tests), synthetic PyMuPDF PDFs, and `transcript_json`:
    - Verify `POST /api/session/init` -> `POST /api/session/analyze` for `student_training` (manual clicks + telemetry + annotated PDF written and accessible via `/outputs/...`).
    - Verify `professor_live` ignores `key_messages`, produces 3 `ComprehensionLensFeedback` entries, writes both Annotated PDF and WeBeep Digest `.md`, and returns `unsupported_mode` on `/api/contraddittorio/chat`.
    - Verify `/api/contraddittorio/chat` enforces `no_ghostwriting` refusal (EN & IT) without consuming a turn and enforces the 3-turn budget (`budget_exhausted` after 3 valid turns).
    - Verify `EvaluationOrchestrator` calls solvers sequentially when `llm.is_cloud_enabled` is `False` and concurrently when `True`.
  - Run `pytest -q` (via `silent-test-runner` or quiet CLI) and verify Step 1 + Step 2 tests all pass.

- [ ] **7. Update Docs & Archive Plan**
  - Update `ARCHITECTURE.md` (directory layout, SQLite store, services, endpoints) and `STATE.md` (Step 2 completed, modules list, Next Up: Step 3 — Frontend Live/Training UI, MediaPipe Telemetry & Audio Capture).
  - Archive `.agents/plans/CURRENT_TASK.md` to `past-tasks/002_CURRENT_TASK.md` per `task-executor`.
