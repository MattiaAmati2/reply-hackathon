# Current Task: Step 1 — Core Extraction, Audio Transcription, PDF Annotation & Fixed HMM+Viterbi Alignment

## Specification
Port and upgrade proven `RecNotes` services into `backend/` for **Sparring**, keeping `SlideService` streamlined (text extraction + 150 DPI PNG thumbnails for every page + PDF diagnostic annotation, without heavy multi-pass diagram/vector classification) and fixing the three mathematical/structural bugs in `AlignmentService` and `AudioService`:
1. Keep all slides `0..K-1` (including low-text/diagram slides) in the HMM state space and assign chunk boundaries using midpoints between adjacent chunk centers so contiguous intervals never produce negative/zero gaps.
2. Fix HMM transition priors (`P_STAY=0.90`, `P_ADVANCE_1=0.085`, `P_ADVANCE_MULTI=0.01`, `P_BACKWARD=0.005`, `SOFTMAX_TEMPERATURE=0.25`) and blend TF-IDF (`ngram_range=(1, 2)`, `sublinear_tf=True`) with optional `SentenceTransformer("all-MiniLM-L6-v2")` (graceful TF-IDF fallback if not installed).
3. Support configurable/auto-detected language (`language=None` default) in `AudioService.transcribe` for mixed Italian/English presentations, plus `compute_speech_metrics` (`wpm`, `pause_count`, `filler_count`).

Reference patterns: `/home/elitebook-ai/Projects/RecNotes/app/services/{slide_service,audio_service,video_service,alignment_service}.py` and `app/orchestrator.py` (`_build_initial_prompt`).

## Steps
- [ ] **1. Create `backend/schemas.py`**
  - Define Pydantic v2 models:
    - `SlideMetadata`: `page_number` (`int`, `ge=1`), `raw_text` (`str`), `clean_text` (`str`), `word_count` (`int`, `ge=0`), `is_low_text` (`bool`, true when `word_count < 10`), `thumbnail_path` (`Optional[str] = None`).
    - `TranscriptSegment`: `start` (`float`), `end` (`float`), `text` (`str`).
    - `AlignedInterval`: `start_time` (`float`), `end_time` (`float`), `slide_page` (`int`, `ge=1`), `matched_slide` (`Optional[SlideMetadata] = None`), `confidence_score` (`float`, `ge=0.0`, `le=1.0`), `transcript_slice` (`str = ""`), `wpm` (`float = 0.0`), `filler_count` (`int = 0`), `pause_count` (`int = 0`).
    - `SlideAnnotationNote`: `page_number` (`int`, `ge=1`), `dimension` (`Literal["alignment", "relevance", "highlighting", "clarity", "complexity"]`), `comment` (`str`).

- [ ] **2. Create `backend/services/__init__.py` and `backend/services/slide_service.py`**
  - Implement `SlideProcessingError(RuntimeError)` and `SlideService(output_dir: Path | str)`:
    - `process_pdf(pdf_path: Path, prefix: str = "") -> list[SlideMetadata]`:
      - Open with `fitz.open(pdf_path)` (PyMuPDF).
      - First pass: count `text_block_frequencies` across pages (after stripping `page/slide/pp. N` patterns) to filter recurring header/footer blocks (`freq <= 5`).
      - Second pass per page (`page_number = page_index + 1`):
        - Join valid blocks, run `_clean_text(...)` to remove dates, page numbers, isolated artifacts, and collapse whitespace.
        - Compute `word_count = len(clean_text.split())` and `is_low_text = word_count < 10`.
        - Render a 150 DPI PNG preview for **every** page directly via `page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), alpha=False).save(str(preview_path))` (no `Pillow`/`ImageHash` dependency required). File name: `{prefix}-slide-{page_number:04d}.png` (or `slide-{page_number:04d}.png` if `prefix` is empty).
    - `annotate_pdf(pdf_path: Path, notes: list[SlideAnnotationNote], output_path: Path) -> Path`:
      - Group `notes` by 1-indexed `page_number`.
      - For each annotated page, format diagnostic comments (`[dimension] comment`) without altering original slide content:
        - Add a sticky-note annotation via `page.add_text_annot(fitz.Point(page.rect.width - 36, 20), summary_text, icon="Note")`.
        - Draw a subtle semi-transparent top-right diagnostic callout box (`draw_rect` + `insert_textbox`) summarizing the notes cleanly within page bounds.
      - Save to `output_path` and return `Path(output_path)`.

- [ ] **3. Create `backend/services/video_service.py` and `backend/services/audio_service.py`**
  - In `backend/services/video_service.py`:
    - Implement `VideoProcessingError(RuntimeError)` and `VideoService(working_dir: Path | str)`:
      - `extract_audio(input_path: Path, output_wav: Optional[Path] = None) -> Path`: verify `ffmpeg` via `shutil.which("ffmpeg")` and run `ffmpeg -y -i <input_path> -vn -acodec pcm_s16le -ar 16000 -ac 1 <output_wav>`.
  - In `backend/services/audio_service.py`:
    - Implement `AudioProcessingError(RuntimeError)` and `AudioService(model_size: str = "base", device: str = "cpu", compute_type: str = "int8", cpu_threads: int = 4)`:
      - `_get_model()`: lazily initialize `faster_whisper.WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type, cpu_threads=self.cpu_threads)`.
      - `build_initial_prompt(slides: list[SlideMetadata]) -> Optional[str]`: extract up to 100 unique capitalized/technical words (`len >= 4`, excluding common EN/IT stopwords) across `slides` to prime Whisper.
      - `transcribe(audio_path: Path, initial_prompt: Optional[str] = None, language: Optional[str] = None) -> list[TranscriptSegment]`: run `model.transcribe(..., language=language, vad_filter=True, vad_parameters={"min_silence_duration_ms": 500}, condition_on_previous_text=False)` and return `list[TranscriptSegment]`.
      - `@staticmethod compute_speech_metrics(segments: list[TranscriptSegment], start_time: float, end_time: float) -> dict[str, float | int]`:
        - Filter segments overlapping `[start_time, end_time)` (by midpoint or `start_time <= seg.start < end_time`), compute `wpm` (`word_count / max((end_time - start_time) / 60.0, 1e-6)`), `pause_count` (count gaps `next_seg.start - prev_seg.end > 1.5`), and `filler_count` (case-insensitive word-boundary regex for `"ehm"`, `"uhm"`, `"cioè"`, `"tipo"`, `"allora"`, `"um"`, `"uh"`, `"like"`, `"basically"`). Return `{"wpm": ..., "pause_count": ..., "filler_count": ...}`.

- [ ] **4. Create `backend/services/alignment_service.py`**
  - Implement `AlignmentError(RuntimeError)` and `AlignmentService`:
    - Constants: `HMM_P_STAY = 0.90`, `HMM_P_ADVANCE_1 = 0.085`, `HMM_P_ADVANCE_MULTI = 0.01`, `HMM_P_BACKWARD = 0.005`, `SOFTMAX_TEMPERATURE = 0.25`, `_CHUNK_WINDOW = 3`.
    - `align_from_manual_timestamps(segments: list[TranscriptSegment], slides: list[SlideMetadata], slide_click_timestamps: list[dict[str, Any]]) -> list[AlignedInterval]`:
      - For **Training** mode: map each click record (`slide_page` or 1-based/0-based slide reference + `start_time` / `end_time`) to the corresponding `SlideMetadata`, gather segments in `[start_time, end_time)`, set `confidence_score=1.0`, and populate `wpm`, `filler_count`, `pause_count` via `AudioService.compute_speech_metrics`.
    - `align_live_audio(segments: list[TranscriptSegment], slides: list[SlideMetadata]) -> list[AlignedInterval]`:
      - Validate non-empty `slides`; return `[]` if `segments` is empty.
      - Keep **all** `N = len(slides)` in the HMM state space `0..N-1`.
      - Build contextual slide texts: for low-text slides (`slide.is_low_text` or `slide.word_count < 10`), enrich with neighboring slide text snippets (`slides[i-1]` / `slides[i+1]`) so context is preserved; track `is_low_text` mask.
      - Build rolling transcript micro-chunks (`window=3`, `stride=1`) and assign contiguous **non-overlapping** `[eff_start, eff_end]` bounds by splitting at midpoints between adjacent chunk centers `(c_t + c_{t+1}) / 2` (with first chunk starting at `segments[0].start` and last ending at `segments[-1].end`).
      - Compute `(M, N)` similarity matrix:
        - Fit `TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)` over slide texts + chunk texts and compute cosine similarity (fallback to zero matrix if vocabulary is empty).
        - Optionally compute `SentenceTransformer("all-MiniLM-L6-v2")` cosine similarity inside `try/except ImportError` (lazy-loaded). Blend `0.5 * tfidf + 0.5 * dense` when dense is available, else `1.0 * tfidf`.
        - For low-text slides (`word_count < 10`), replace their column scores in each row with the row median similarity so Viterbi transitions smoothly through diagram/title-only slides.
      - Compute `log_B` via temperature-scaled log-softmax (`sims / SOFTMAX_TEMPERATURE`), `log_A` (`N x N`) with `HMM_P_STAY`, `HMM_P_ADVANCE_1`, decaying `HMM_P_ADVANCE_MULTI`, and decaying `HMM_P_BACKWARD`, and `log_pi` (`[0.95, 0.04, 0.01, ...]`).
      - Run `_viterbi_decode(log_A, log_B, log_pi)`, consolidate adjacent chunks with the same slide into contiguous `AlignedInterval`s, attach `transcript_slice`, and populate `wpm`, `filler_count`, `pause_count` via `AudioService.compute_speech_metrics`.

- [ ] **5. Wire `POST /api/slides/upload` into `backend/main.py`**
  - Import `SlideService` and `SlideMetadata`.
  - Initialize `SLIDES_OUT_DIR = OUTPUTS_DIR / "slides"` in `lifespan` (and ensure it exists before processing).
  - Add `POST /api/slides/upload` accepting `pdf: UploadFile = File(...)`:
    - Persist uploaded PDF safely under `UPLOADS_DIR` (using a sanitized filename or UUID prefix).
    - Run `SlideService(SLIDES_OUT_DIR).process_pdf(saved_path, prefix=...)`.
    - Return JSON containing `filename`, `slide_count`, and `slides` (`list[SlideMetadata]`).
  - Ensure `StaticFiles` mount for `/` remains last so API routes are never shadowed.

- [ ] **6. Add verification tests (`tests/test_step1_services.py`) & run `pytest`**
  - Create synthetic PDFs with `fitz` in `tmp_path` (including a text slide, a near-blank/low-text diagram slide, and a second text slide) and test:
    - `SlideService.process_pdf` extracts cleaned text, flags `is_low_text`, and writes 150 DPI PNG thumbnails for every page.
    - `SlideService.annotate_pdf` writes a valid annotated PDF with text annotations.
    - `AudioService.build_initial_prompt` & `AudioService.compute_speech_metrics` compute expected WPM, pauses, and IT/EN fillers.
    - `AlignmentService.align_from_manual_timestamps` maps manual clicks accurately.
    - `AlignmentService.align_live_audio` keeps all slides (including low-text diagram slides) without negative/zero gaps and produces contiguous non-overlapping intervals.
  - Run `pytest -q` (via `silent-test-runner` or quiet CLI) and verify all tests pass.

- [ ] **7. Update `STATE.md` and report verification**
  - Update `STATE.md` to mark Step 1 completed, list created/updated modules, and point Next Up to Step 2.
  - Archive this completed plan per the `task-executor` archive instruction and provide a concise summary plus a 1-line `curl` command to test `POST /api/slides/upload`.
