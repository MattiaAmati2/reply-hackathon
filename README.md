# Kairos

**Asymmetric Multimodal Presentation & Lecture Coaching Pipeline**

Kairos pairs a zero-build vanilla HTML/JS + browser MediaPipe frontend with a CPU-friendly FastAPI backend designed to run on a headless ThinkPad T480 (`i5-8350U`, 15 GB RAM, no discrete GPU) over Tailscale. It supports both **student rehearsal/defense coaching** and **zero-prep professor lecture analysis** on one unified pipeline.

---

## Features & Asymmetric Modes

Kairos adapts its input capture, slide alignment strategy, evaluation solvers, and post-session outputs based on the selected `mode`:

| Mode (`SessionMode`) | Target User | Inputs | Alignment | Outputs & Post-Session Loop |
| :--- | :--- | :--- | :--- | :--- |
| **`student_training`** | Students rehearsing a thesis or project pitch | PDF deck + up to 3 key messages + manual slide clicks + mic/media upload + optional browser webcam MediaPipe telemetry | Manual slide click intervals (`align_from_manual_timestamps`), falling back to live alignment if no clicks are recorded | Specchio feedback, key-message coverage, problem flags, delivery & telemetry feedback, annotated PDF, **Socratic Contraddittorio** (max 3 turns, `no_ghostwriting` boundary) |
| **`student_live`** | Students in a live thesis defense | PDF deck + optional key messages + audio/media capture (no webcam overlay) | Automatic monotonic HMM + Viterbi semantic alignment (`align_live_audio`) | Specchio feedback, key-message coverage, problem flags, delivery feedback, annotated PDF, **Socratic Contraddittorio** (max 3 turns, `no_ghostwriting` boundary) |
| **`professor_live`** | Professors in a live lecture (**Zero Prep**) | PDF deck + audio/media capture (key messages cleared/hidden, no webcam telemetry) | Automatic monotonic HMM + Viterbi semantic alignment (`align_live_audio`) | **3 Comprehension Lenses** (`freshman`, `average`, `msc` clarity scores, friction points, tips — zero content judgment), problem flags, annotated PDF, **Post-Lecture WeBeep Digest** (`.md`). Contraddittorio disabled (`unsupported_mode`) |

---

## Architecture & Pipeline

### Hardware Split

- **Client Browser (`frontend/`)**:
  - Single-page UI built with Tailwind CSS CDN and vanilla JavaScript (no Node/bundler build step).
  - Live microphone recording via `MediaRecorder` (`audio/webm;codecs=opus` / `audio/webm` / `audio/mp4`) with elapsed timer, **plus** an audio/video file upload fallback (`accept="audio/*,video/*"`).
  - Lazy-loaded `@mediapipe/tasks-vision@0.10.14` (`FaceLandmarker`, `HandLandmarker`, `PoseLandmarker`) in `student_training` mode, extracting `eye_contact_ratio`, `posture_score`, `gesture_activity_ratio`, `stage_coverage`, `head_stability`, and `sample_count`. Gracefully falls back to `{}` when camera permissions are unavailable or blocked on non-HTTPS LAN origins.
- **T480 Backend (`backend/`)**:
  - FastAPI (`Kairos API`, port `8101`) serving API routes, generated artifacts under `/outputs`, and the static frontend under `/`.

### Backend Processing Stages

1. **PDF Ingestion & Annotation ([`SlideService`](backend/services/slide_service.py))**
   - Uses PyMuPDF (`fitz`) to extract text blocks, filter repeating headers/footers appearing on `> 5` pages, strip page numbers/dates, flag low-text slides (`word_count < 10`), and render `150 DPI` PNG thumbnails.
   - Annotates slides post-evaluation with sticky `Note` annotations and top-right callout boxes across dimensions (`alignment`, `relevance`, `highlighting`, `clarity`, `complexity`).
2. **Audio Normalization & Transcription ([`VideoService`](backend/services/video_service.py), [`AudioService`](backend/services/audio_service.py))**
   - Converts non-WAV audio/video uploads to `16 kHz` mono `pcm_s16le` WAV via `ffmpeg`.
   - Transcribes with `faster-whisper` (`base` model, CPU `int8`, `4` threads, `vad_filter=True`, automatic language detection `language=None`), seeded with domain terms extracted from slide text (`build_initial_prompt`).
   - Computes per-interval speech metrics: `wpm`, `pause_count` (inter-segment gaps `> 1.5s`), and `filler_count` (EN/IT fillers such as `ehm`, `uhm`, `cioè`, `tipo`, `allora`, `um`, `uh`, `like`, `basically`).
3. **Slide Alignment ([`AlignmentService`](backend/services/alignment_service.py))**
   - `student_training` (with clicks): builds contiguous intervals from recorded `{slide_page, start_time, end_time}` transitions.
   - Live / fallback alignment (`align_live_audio`): decodes monotonic slide sequences via HMM + Viterbi (`HMM_P_STAY=0.90`, `HMM_P_ADVANCE_1=0.085`, `HMM_P_ADVANCE_MULTI=0.01`, `HMM_P_BACKWARD=0.005`) over 3-segment midpoint windows using TF-IDF cosine similarity (and optional `SentenceTransformer("all-MiniLM-L6-v2")` dense blending when installed), preserving low-text slides with an emission floor.
4. **Multi-Agent Evaluation ([`EvaluationOrchestrator`](backend/services/evaluation_service.py))**
   - **Level 2 (`ProblemIdentifier`)**: deterministic checks for uncovered/short slides (`< 5.0s`), pace (`< 95` or `> 175` WPM), fillers (`>= 3`), pauses (`>= 2`), low semantic alignment (`< 0.25` in live modes), low eye contact (`< 0.45`), low posture (`< 0.50`), and key-message token coverage.
   - **Level 3A (`SpecchioSolver`, `DeliverySolver`, `ActionableOutputSolver`)**: runs **sequentially** against local Ollama to avoid CPU contention, or **concurrently** (`asyncio.gather`) when a cloud API key is configured, with automatic JSON fallback structures if an LLM call fails.
   - Generates `EvaluationReport`, annotated PDFs (`data/outputs/annotated/`), and Markdown digests (`data/outputs/digests/` in `professor_live`).
5. **Persistence & Socratic Contraddittorio ([`DatabaseService`](backend/services/db_service.py), [`ContraddittorioAgent`](backend/services/evaluation_service.py))**
   - SQLite WAL storage at `data/kairos.db` (`sessions` and `contraddittorio_turns` tables).
   - `ContraddittorioAgent` conducts up to `3` Socratic follow-up turns in student modes and enforces a strict `no_ghostwriting` boundary (refusing requests in English or Italian to write, rewrite, draft, or generate slides/thesis text).

---

## Repository Layout

```text
Reply-Hackathon/
├── backend/
│   ├── __init__.py
│   ├── main.py                      # FastAPI app ("Kairos API"), endpoints, static mounts
│   ├── schemas.py                   # Pydantic v2 models for sessions, alignment, reports & chat
│   └── services/
│       ├── __init__.py
│       ├── alignment_service.py     # Manual click & HMM+Viterbi live slide alignment
│       ├── audio_service.py         # faster-whisper transcription & speech delivery metrics
│       ├── db_service.py            # SQLite WAL persistence (sessions & contraddittorio_turns)
│       ├── evaluation_service.py    # ProblemIdentifier, 3 Solvers, Orchestrator, ContraddittorioAgent
│       ├── llm_service.py           # Ollama-first async LLM client + cloud adapter & JSON fallback
│       ├── slide_service.py         # PyMuPDF text cleaning, 150 DPI thumbnails & PDF annotation
│       └── video_service.py         # ffmpeg 16 kHz mono WAV audio extraction
├── frontend/
│   ├── index.html                   # Single-page Tailwind CDN UI (Setup, Stage, Report, Chat)
│   ├── app.js                       # Session lifecycle, MediaRecorder, slide clicks, report rendering
│   └── mediapipe_telemetry.js       # Lazy CDN MediaPipe Face/Hand/Pose landmarking & metrics
├── tests/
│   ├── test_step1_services.py       # Unit tests: SlideService, AudioService, AlignmentService
│   ├── test_step2_pipeline.py       # End-to-end pipeline, Contraddittorio & API integration tests
│   └── test_step3_frontend.py       # Static asset serving & student_training telemetry/click tests
├── data/                            # Gitignored runtime storage (auto-created on startup)
│   ├── kairos.db                    # SQLite WAL database
│   ├── uploads/                     # Uploaded PDFs, raw audio/video & extracted WAVs
│   └── outputs/
│       ├── slides/                  # 150 DPI slide thumbnails
│       ├── annotated/               # Annotated PDF outputs
│       └── digests/                 # Post-lecture WeBeep Markdown digests
├── .agents/plans/CURRENT_TASK.md    # Latest task plan
├── past-tasks/                      # Completed step plans (001–003)
├── ARCHITECTURE.md                  # System architecture blueprint
├── STATE.md                         # Step-by-step project progress tracker
├── requirements.txt                 # Python dependencies
├── t480_ops.sh                      # Remote git-based deployment & tailnet lifecycle script
└── .gitignore
```

---

## Local Setup & Running

### Prerequisites

- **Python** `3.10+`
- **`ffmpeg`** available in `PATH` (required when uploading `.webm`, `.mp3`, `.mp4`, or other non-WAV media)
- **LLM backend**:
  - Local **Ollama** running at `http://localhost:11434` with `qwen2.5:7b` (default), **or**
  - A cloud LLM API key (`LLM_API_KEY`) for Gemini or an OpenAI-compatible endpoint

### Installation & Launch

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

uvicorn backend.main:app --host 127.0.0.1 --port 8101 --reload
```

Open `http://127.0.0.1:8101` in your browser.

> **Note on Camera & Microphone Access**: Browsers restrict `getUserMedia` / `MediaRecorder` to secure contexts (`https://` or `http://localhost` / `http://127.0.0.1`). When accessing Kairos over plain `http://` on a LAN or Tailnet IP, you can still use the built-in **audio/video file upload fallback**; MediaPipe webcam telemetry safely degrades to `{}` without blocking analysis.

---

## Configuration

Kairos loads environment variables automatically via `python-dotenv` (place a `.env` file in the project root if needed; `.env` is gitignored).

### LLM Configuration (`backend/services/llm_service.py`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama server base URL |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Local Ollama model name |
| `LLM_API_KEY` | `""` | Optional cloud API key. When set, cloud calls run concurrently and automatically fall back to Ollama on error |
| `LLM_PROVIDER` | `ollama` | Set to `openai` or `openai_compatible` for `/chat/completions`; otherwise uses the Gemini REST API when `LLM_API_KEY` is set |
| `LLM_CLOUD_MODEL` | `gemini-2.0-flash` | Cloud model identifier |
| `LLM_BASE_URL` | `""` | Optional base URL for OpenAI-compatible APIs (defaults to `https://api.openai.com/v1` when `LLM_PROVIDER` is `openai`) |

---

## API Reference

| Method & Path | Description |
| :--- | :--- |
| `GET /api/health` | Health check returning `{"status": "ok", "host": "t480", "port": 8101, "modes": [...]}` |
| `POST /api/session/init` | Multipart form (`mode`, `key_messages`, `pdf`) — extracts slide metadata/thumbnails and creates a session |
| `POST /api/session/analyze` | Multipart form (`session_id`, `audio` or `transcript_json`, `telemetry_json`, `slide_clicks_json`) — transcribes, aligns, evaluates, annotates PDF, and returns `aligned_intervals` + `report` |
| `POST /api/contraddittorio/chat` | JSON (`{"session_id": "...", "message": "..."}`) — returns next Socratic follow-up (max 3 turns; rejects ghostwriting or `professor_live`) |
| `GET /api/session/{session_id}` | Retrieves stored `SessionRecord` from SQLite |
| `POST /api/slides/upload` | Multipart form (`pdf`) — standalone slide extraction & thumbnail preview |
| `GET /outputs/...` | Static mount serving slide PNGs, annotated PDFs, and WeBeep digests |
| `GET /` | Static mount serving `frontend/index.html` and JS assets |

---

## Deployment on T480 (Tailnet)

[`t480_ops.sh`](t480_ops.sh) deploys Kairos directly from the `main` branch on GitHub onto the headless T480 server and brings it online on Tailscale (`0.0.0.0:8101`).

### Commands

```bash
./t480_ops.sh pull     # Clone (first run) or git pull --ff-only origin main on t480
./t480_ops.sh sync     # Alias for pull
./t480_ops.sh setup    # Pull repo, create/update .venv, and install requirements.txt
./t480_ops.sh start    # Pull repo, ensure .venv, source .env, restart uvicorn on :8101, verify /api/health & print Tailnet URL
./t480_ops.sh stop     # Stop server on port 8101 and remove server.pid
./t480_ops.sh logs     # Tail remote server.log
./t480_ops.sh status   # Inspect listening socket, Tailnet URL, and /api/health
```

### Script Environment Overrides

| Variable | Default |
| :--- | :--- |
| `T480_HOST` | `t480` (pass e.g. `T480_HOST=selfhost@100.107.184.14` if `t480` is not in `~/.ssh/config`) |
| `T480_REMOTE_DIR` | `/home/selfhost/Reply-Hackathon` |
| `REPO_URL` | `https://github.com/MattiaAmati2/reply-hackathon.git` |
| `BRANCH` | `main` |
| `HOST` | `0.0.0.0` |
| `PORT` | `8101` |

Example deployment when addressing the server by its Tailscale IP:

```bash
T480_HOST=selfhost@100.107.184.14 ./t480_ops.sh start
```

---

## Testing

Run the offline test suite using `python -m pytest` (ensuring the repository root is on `sys.path` for `backend` imports):

```bash
python -m pytest -v
```

Test coverage across the three steps (`11 passed` verified offline with mocked transcription and deterministic LLM fallbacks):
- [`tests/test_step1_services.py`](tests/test_step1_services.py) (5 tests): `SlideService` text cleaning/thumbnails/PDF annotation, `AudioService` prompt & delivery metrics, `AlignmentService` manual and live HMM+Viterbi alignment.
- [`tests/test_step2_pipeline.py`](tests/test_step2_pipeline.py) (4 tests): `student_training` & `professor_live` end-to-end pipelines, WeBeep digest output, Socratic Contraddittorio 3-turn budget & `no_ghostwriting` refusal, sequential vs. concurrent solver execution, and JSON fallback handling.
- [`tests/test_step3_frontend.py`](tests/test_step3_frontend.py) (2 tests): static frontend asset serving (`/`, `/app.js`, `/mediapipe_telemetry.js`) and `student_training` flow with MediaPipe telemetry & manual slide clicks.

---

## Related Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — Hardware split & system design blueprint
- [STATE.md](STATE.md) — Module completion tracker
- [.agents/plans/CURRENT_TASK.md](.agents/plans/CURRENT_TASK.md) & [past-tasks/](past-tasks/) — Step-by-step implementation plans
