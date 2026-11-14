# Current Task: Step 3 — Frontend Live/Training UI, MediaPipe Telemetry & Audio Capture

## Specification
Build the complete client-side presentation & lecture flow in the existing single-page vanilla HTML/JS + Tailwind CDN frontend (`frontend/index.html` and `frontend/app.js`) with zero build tools, wired to the Step 2 FastAPI endpoints:
- **Session Initialization (`POST /api/session/init`)**: Upload PDF, select mode (`student_training`, `student_live`, `professor_live`), enter optional 3 key messages (hidden in `professor_live`), and display slide deck thumbnails (`/outputs/slides/...`).
- **Live / Training Recording & Slide Navigation**:
  - **Audio Capture**: Live microphone recording via `MediaRecorder` (with elapsed timer and stop/reset) **plus** an optional audio/video file upload fallback (`.wav`, `.mp3`, `.webm`, `.mp4`) for demos or environments without a mic.
  - **Manual Slide Clicks (`student_training`)**: Prev/Next slide controls + keyboard arrow support recording `{slide_page, start_time, end_time}` intervals in `slide_clicks_json`.
  - **Rich MediaPipe Telemetry (`student_training`)**: Load `@mediapipe/tasks-vision` via CDN (`FaceLandmarker`, `HandLandmarker`, `PoseLandmarker`) on a live `<video>` + `<canvas>` overlay to compute and aggregate:
    - `eye_contact_ratio` (0–1, gaze/head-forward proxy from face landmarks)
    - `posture_score` (0–1, shoulder symmetry & uprightness from pose landmarks)
    - `gesture_activity_ratio` (0–1, hand movement/visibility across frames from hand landmarks)
    - `stage_coverage` (0–1, horizontal/spatial movement & space occupation from pose torso center variance)
    - `head_stability` (0–1) and `sample_count`
    - Graceful fallback to `{}` (with clear UI status badge) if camera permission is denied, unavailable, or blocked on non-HTTPS LAN origins.
- **Analysis & Report Rendering (`POST /api/session/analyze`)**:
  - Submit `session_id`, `audio` blob/file, `telemetry_json`, and `slide_clicks_json` with loading state.
  - Render `EvaluationReport`:
    - `specchio_summary` & `key_message_coverage`
    - `problem_flags` (severity badge, slide page, dimension, summary)
    - `comprehension_lenses` (Freshman / Avg / MSc cards in `professor_live`)
    - `delivery_metrics` & MediaPipe telemetry summary
    - `slide_notes` + clickable download/view links for `annotated_pdf_url` (`/outputs/annotated/...`) and `digest_url` (`/outputs/digests/...`).
- **Socratic Contraddittorio Chat (`POST /api/contraddittorio/chat`)**:
  - Shown only for `student_training` and `student_live` after analysis completes.
  - Displays turn counter (`turn_index` / 3 max) and disables input when `budget_exhausted` is true.
- **Branding Update (`Sparring` -> `Kairos`)**: Rename user-facing demo branding from `Sparring` to `Kairos` (title/header in `frontend/index.html`, FastAPI `title="Kairos API"` and `DB_PATH = DATA_DIR / "kairos.db"` in `backend/main.py`, and `STATE.md` / `ARCHITECTURE.md` headings & DB path).
- **State & Docs Sync**: Update `STATE.md` to mark Step 3 completed.

---

## Steps

### 1. Expand `frontend/index.html` Layout & Sections
- [ ] Keep Tailwind CDN and existing header/health badge + mode selector (`student_training`, `student_live`, `professor_live`).
- [ ] Update `#session-setup` card to show a clean **Session Ready** state after `/api/session/init` (session ID, slide count, slide thumbnail strip).
- [ ] Add `#stage-section` (shown after init):
  - Slide viewer (`<img>` bound to `/outputs/slides/...`) + Prev/Next Slide buttons + current slide indicator + recording timer (manual slide click tracking enabled in `student_training`, auto-alignment notice in `student_live` / `professor_live`).
  - Audio controls: **Start / Stop Mic Recording** (`MediaRecorder`), live status indicator, **or** `<input id="audio-file-input" type="file" accept="audio/*,video/*">` fallback.
  - `#webcam-overlay-section` (visible only in `student_training`, hidden in `student_live` and `professor_live`): `<video>` + `<canvas>` overlay for MediaPipe landmarks + live telemetry pills (Eye Contact, Posture, Gestures, Stage Space).
  - **Analyze Session** button (`#analyze-btn`) + clean progress bar & status label (disables double-submit during CPU transcription/LLM analysis).
- [ ] Add `#report-section` (hidden until `/api/session/analyze` returns):
  - Specchio summary, Key Message Coverage, Problem Flags list, Comprehension Lenses grid (for `professor_live`), Delivery & Telemetry metrics, Slide Notes, and download links (`annotated_pdf_url`, `digest_url`).
- [ ] Add `#contraddittorio-section` (hidden in `professor_live` and until report is ready):
  - Chat history container, turn budget badge (`0 / 3`), message input + Send button.

### 2. Implement Client-Side MediaPipe Telemetry (`frontend/mediapipe_telemetry.js` or inline module in `frontend/app.js`)
- [ ] Load `@mediapipe/tasks-vision` from CDN lazily only when `student_training` camera is started (avoid blocking `professor_live` / `student_live` or offline tests).
- [ ] Initialize `FaceLandmarker`, `HandLandmarker`, and `PoseLandmarker` (CPU/GPU auto delegate) and run `requestAnimationFrame` / throttled (~5–10 FPS) inference while recording is active.
- [ ] Compute running aggregates:
  - `eye_contact_ratio`: nose/iris/head-yaw centering ratio within forward threshold.
  - `posture_score`: shoulder levelness & torso uprightness from `PoseLandmarker`.
  - `gesture_activity_ratio`: fraction of sampled frames with active/moving `HandLandmarker` keypoints.
  - `stage_coverage`: normalized horizontal spread (std/range of hip/shoulder midpoint X) capturing walking & space occupation.
  - `head_stability` and `sample_count`.
- [ ] Draw lightweight landmark overlay on `<canvas>` and update live telemetry pills; handle `NotAllowedError` / `NotFoundError` / insecure-origin errors gracefully without blocking audio recording.

### 3. Wire Recording, Slide Timing, Analysis & Contraddittorio in `frontend/app.js`
- [ ] Refactor `frontend/app.js` cleanly around session state (`sessionId`, `mode`, `slides`, `currentSlideIdx`, `slideClicks`, `isRecording`, `audioBlob`, `mediaRecorder`).
- [ ] Implement `MediaRecorder` lifecycle (`audio/webm` or browser-supported MIME), elapsed timer, and mutual fallback with `#audio-file-input`.
- [ ] Track manual slide transitions in `student_training` as `{slide_page, start_time, end_time}` (closing the active slide interval when recording stops).
- [ ] Implement `POST /api/session/analyze` `FormData` submission (`session_id`, `audio`, `telemetry_json`, `slide_clicks_json`) and render `EvaluationReport` fields + `/outputs/...` links.
- [ ] Implement `POST /api/contraddittorio/chat` submission, append user/assistant turns, and lock input on `budget_exhausted`.

### 4. Verification & Documentation
- [ ] Add a lightweight frontend/static-serving smoke test in `tests/test_step3_frontend.py` (verifying `GET /` serves `index.html` with required element IDs and `GET /app.js` returns 200, plus a full mocked `init` -> `analyze` with `telemetry_json` & `slide_clicks_json` -> `contraddittorio` check).
- [ ] Run `python -m pytest -v` (target: `silent-test-runner` or local pytest) and verify all tests pass.
- [ ] Optionally run `uvicorn backend.main:app --host 127.0.0.1 --port 8101` for manual browser smoke test.
- [ ] Rebrand user-facing demo strings from `Sparring` to `Kairos` (`frontend/index.html`, `backend/main.py` `title` & `DB_PATH = DATA_DIR / "kairos.db"`, `STATE.md`, `ARCHITECTURE.md`).
- [ ] Update `STATE.md` to record Step 3 completion.
