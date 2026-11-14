import json
from pathlib import Path

import fitz
from fastapi.testclient import TestClient
import pytest

import backend.main as main_mod
from backend.schemas import TranscriptSegment
from backend.services.db_service import DatabaseService
from backend.services.llm_service import LLMService


class _DummyLLM(LLMService):
    async def generate_json(self, prompt, system_prompt="", fallback=None, max_tokens=512):
        return fallback or {}

    async def generate(self, prompt, system_prompt="", max_tokens=300, temperature=0.2):
        return "Why did you choose that specific metric over baseline F1?"


def _create_pdf(path: Path) -> bytes:
    doc = fitz.open()
    for text in (
        "Kairos Overview\nMultimodal rehearsal coach",
        "Methodology\nAudio and MediaPipe telemetry",
    ):
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return path.read_bytes()


@pytest.fixture
def frontend_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    data_dir = tmp_path / "data"
    uploads = data_dir / "uploads"
    outputs = data_dir / "outputs"
    slides = outputs / "slides"
    annotated = outputs / "annotated"
    digests = outputs / "digests"
    for d in (uploads, outputs, slides, annotated, digests):
        d.mkdir(parents=True, exist_ok=True)

    db = DatabaseService(data_dir / "kairos_test.db")
    monkeypatch.setattr(main_mod, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(main_mod, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(main_mod, "SLIDES_OUT_DIR", slides)
    monkeypatch.setattr(main_mod, "ANNOTATED_DIR", annotated)
    monkeypatch.setattr(main_mod, "DIGESTS_DIR", digests)
    monkeypatch.setattr(main_mod, "get_db", lambda: db)
    monkeypatch.setattr(main_mod, "get_llm", lambda: _DummyLLM())

    monkeypatch.setattr(
        main_mod.AudioService,
        "transcribe",
        lambda self, wav_path, initial_prompt="": [
            TranscriptSegment(start=0.0, end=4.0, text="Welcome to Kairos overview."),
            TranscriptSegment(start=4.0, end=8.0, text="We combine audio and MediaPipe telemetry."),
        ],
    )
    return TestClient(main_mod.app)


def test_static_frontend_assets_served(frontend_client: TestClient) -> None:
    index_resp = frontend_client.get("/")
    assert index_resp.status_code == 200
    html = index_resp.text
    assert "Kairos" in html
    for elem_id in (
        'id="session-ready"',
        'id="stage-section"',
        'id="slide-viewer-img"',
        'id="record-btn"',
        'id="audio-file-input"',
        'id="webcam-overlay-section"',
        'id="analyze-btn"',
        'id="report-section"',
        'id="contraddittorio-section"',
    ):
        assert elem_id in html

    assert frontend_client.get("/app.js").status_code == 200
    assert frontend_client.get("/mediapipe_telemetry.js").status_code == 200


def test_student_training_flow_with_telemetry_and_slide_clicks(
    frontend_client: TestClient, tmp_path: Path
) -> None:
    pdf_bytes = _create_pdf(tmp_path / "deck.pdf")
    init_resp = frontend_client.post(
        "/api/session/init",
        data={"mode": "student_training", "key_messages": "Multimodal coaching\nMediaPipe"},
        files={"pdf": ("deck.pdf", pdf_bytes, "application/pdf")},
    )
    assert init_resp.status_code == 200
    init_data = init_resp.json()
    session_id = init_data["session_id"]
    assert init_data["slide_count"] == 2

    telemetry = {
        "eye_contact_ratio": 0.82,
        "posture_score": 0.91,
        "gesture_activity_ratio": 0.65,
        "stage_coverage": 0.4,
        "head_stability": 0.88,
        "sample_count": 24,
    }
    slide_clicks = [
        {"slide_page": 1, "start_time": 0.0, "end_time": 4.0},
        {"slide_page": 2, "start_time": 4.0, "end_time": 8.0},
    ]

    analyze_resp = frontend_client.post(
        "/api/session/analyze",
        data={
            "session_id": session_id,
            "telemetry_json": json.dumps(telemetry),
            "slide_clicks_json": json.dumps(slide_clicks),
        },
        files={"audio": ("recording.wav", b"RIFF0000WAVEfmt ", "audio/wav")},
    )
    assert analyze_resp.status_code == 200
    body = analyze_resp.json()
    assert body["report"]["delivery_feedback"]["telemetry"]["eye_contact_ratio"] == 0.82
    assert len(body["aligned_intervals"]) == 2

    chat_resp = frontend_client.post(
        "/api/contraddittorio/chat",
        json={"session_id": session_id, "message": "Because accuracy alone hides class imbalance."},
    )
    assert chat_resp.status_code == 200
    assert chat_resp.json()["turn"] == 1
    assert chat_resp.json()["status"] == "ok"
    assert "baseline F1" in chat_resp.json()["reply"]

    refuse_resp = frontend_client.post(
        "/api/contraddittorio/chat",
        json={"session_id": session_id, "message": "Scrivimi il discorso per la slide 1"},
    )
    assert refuse_resp.status_code == 200
    assert refuse_resp.json()["status"] == "refused"
    assert "Kairos Boundary" in refuse_resp.json()["reply"]
