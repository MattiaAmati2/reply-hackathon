# Sparring — System Architecture Blueprint

## Hardware Split
- **Client Browser (Laptop / Mobile)**:
  - Handles webcam/microphone capture and real-time MediaPipe inference (Hands, Pose, Face Mesh/Gaze) in JavaScript.
  - Sends lightweight JSON telemetry and audio blobs to the T480 backend (`:8101`).
- **T480 Server (`Ubuntu 26.04`, i5-8350U CPU, 15GB RAM, no dGPU)**:
  - Runs **FastAPI** on port `8101`, serving both API routes and the static vanilla HTML/JS frontend from `/`.
  - **PyMuPDF**: PDF slide text/structure extraction and post-session PDF annotation.
  - **faster-whisper**: CPU `int8` transcription (`tiny` / `base` model) with word/segment timestamps.
  - **librosa**: Vocal pitch, energy, and speaking pace analysis.
  - **SQLite**: Local session memory and state persistence.
  - **LLM Orchestration**: Level 2 + Level 3A Multi-Agent pipeline (`Problem Identifier` -> `3 Solvers` -> `Orchestrator`).

## Two Asymmetric Modes on One Pipeline
| Mode | Target User | Inputs | Pipeline & Outputs |
| :--- | :--- | :--- | :--- |
| **Training** | Students ONLY (Thesis / Project) | PDF upload + 3 key messages + manual "Next Slide" clicks + Audio & Webcam MediaPipe telemetry | Voice/Gesture feedback + Specchio + Socratic Contraddittorio (max 3 follow-ups, strict boundary refusing ghostwriting) + Annotated PDF |
| **Live** | Students in real defense + Professors in real lecture | PDF upload + Audio-only recording (zero webcam/key-message prep) | Automatic Monotonic Semantic Slide Alignment (`allinea_slide_live`) + Specchio (Comprehension lenses for Professor: Freshman / Avg / MSc, zero content judgment) + Annotated PDF + Post-Lecture WeBeep Digest |

## Directory Layout
```text
Reply-Hackathon/
├── backend/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app, session/analysis/contraddittorio API, /outputs & / mounts
│   ├── schemas.py               # Pydantic v2 models (slides, intervals, evaluation, contraddittorio)
│   └── services/
│       ├── __init__.py
│       ├── slide_service.py     # PyMuPDF text extraction, 150 DPI PNG thumbnails, PDF annotation
│       ├── video_service.py     # ffmpeg 16 kHz mono WAV extraction
│       ├── audio_service.py     # faster-whisper transcription & speech metrics
│       ├── alignment_service.py # Manual click & HMM+Viterbi live slide alignment
│       ├── db_service.py        # SQLite WAL persistence (sessions & contraddittorio_turns)
│       ├── llm_service.py       # Ollama-first async client + optional cloud API fallback
│       └── evaluation_service.py# ProblemIdentifier, 3 Solvers, Orchestrator, ContraddittorioAgent
├── frontend/
│   ├── index.html               # Single-page UI (Tailwind CDN)
│   └── app.js                   # Mode selector, conditional UI, health polling
├── data/
│   ├── sparring.db              # SQLite database (auto-created)
│   ├── uploads/                 # Incoming PDFs & audio blobs (auto-created)
│   └── outputs/
│       ├── slides/              # 150 DPI slide thumbnails
│       ├── annotated/           # Annotated PDFs
│       └── digests/             # Post-Lecture WeBeep Digest Markdown files
├── tests/
│   ├── test_step1_services.py   # Step 1 service unit tests
│   └── test_step2_pipeline.py   # Step 2 pipeline, Contraddittorio & API integration tests
├── .agents/
│   └── plans/
│       └── CURRENT_TASK.md      # Active step plan & progress
├── ARCHITECTURE.md
├── STATE.md
├── requirements.txt
└── t480_ops.sh                  # Rsync & remote lifecycle script for T480
```
