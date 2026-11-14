import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from fastapi.testclient import TestClient
import fitz
import pytest

import backend.main as main_mod
from backend.schemas import AlignedInterval, SlideMetadata
from backend.services.db_service import DatabaseService
from backend.services.evaluation_service import EvaluationOrchestrator
from backend.services.llm_service import LLMService


class MockLLMService(LLMService):
    """Deterministic mock LLMService for fast offline tests."""

    def __init__(self, cloud_enabled: bool = False) -> None:
        super().__init__(api_key="test-key" if cloud_enabled else "")

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int = 320,
        temperature: float = 0.2,
    ) -> str:
        return "What empirical evidence supports your main assumption on slide 1?"

    async def generate_json(
        self,
        prompt: str,
        system_prompt: str = "",
        fallback: Optional[dict[str, Any]] = None,
        max_tokens: int = 320,
    ) -> dict[str, Any]:
        return dict(fallback) if fallback is not None else {}


def _create_test_pdf(path: Path) -> bytes:
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text(
        (72, 72),
        "Sparring Multimodal Rehearsal\n"
        "We introduce an automated thesis defense coach combining speech pacing and slide alignment.",
    )
    p2 = doc.new_page()
    p2.insert_text(
        (72, 72),
        "Empirical Results and Evaluation\n"
        "Experiments show 24 percent improvement in alignment consistency across student presentations.",
    )
    data = doc.tobytes()
    doc.close()
    path.write_bytes(data)
    return data


@pytest.fixture()
def isolated_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    uploads = tmp_path / "uploads"
    outputs = tmp_path / "outputs"
    slides_out = outputs / "slides"
    annotated = outputs / "annotated"
    digests = outputs / "digests"
    for d in (uploads, outputs, slides_out, annotated, digests):
        d.mkdir(parents=True, exist_ok=True)

    db = DatabaseService(tmp_path / "test_sparring.db")
    monkeypatch.setattr(main_mod, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(main_mod, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(main_mod, "SLIDES_OUT_DIR", slides_out)
    monkeypatch.setattr(main_mod, "ANNOTATED_DIR", annotated)
    monkeypatch.setattr(main_mod, "DIGESTS_DIR", digests)
    monkeypatch.setattr(main_mod, "_db_service", db)
    monkeypatch.setattr(main_mod, "get_llm", lambda: MockLLMService(cloud_enabled=False))

    # Rebind /outputs static route to temp outputs directory for this test client
    for route in list(main_mod.app.routes):
        if getattr(route, "name", None) == "outputs":
            route.app.directory = str(outputs)  # type: ignore[attr-defined]
            route.app.all_directories = [str(outputs)]  # type: ignore[attr-defined]

    with TestClient(main_mod.app) as client:
        yield client


def test_student_training_pipeline_and_outputs(
    isolated_app: TestClient, tmp_path: Path
) -> None:
    pdf_bytes = _create_test_pdf(tmp_path / "deck.pdf")

    init_resp = isolated_app.post(
        "/api/session/init",
        data={
            "mode": "student_training",
            "key_messages": "automated thesis defense coach\nalignment consistency improvement",
        },
        files={"pdf": ("deck.pdf", pdf_bytes, "application/pdf")},
    )
    assert init_resp.status_code == 200
    init_data = init_resp.json()
    session_id = init_data["session_id"]
    assert init_data["slide_count"] == 2
    assert len(init_data["key_messages"]) == 2

    transcript = [
        {"start": 0.0, "end": 6.0, "text": "Our automated thesis defense coach helps students."},
        {"start": 6.0, "end": 12.0, "text": "We achieved alignment consistency improvement in tests."},
    ]
    clicks = [
        {"slide_page": 1, "start_time": 0.0, "end_time": 6.0},
        {"slide_page": 2, "start_time": 6.0, "end_time": 12.0},
    ]
    telemetry = {"eye_contact_ratio": 0.35, "posture_score": 0.8}

    analyze_resp = isolated_app.post(
        "/api/session/analyze",
        data={
            "session_id": session_id,
            "transcript_json": json.dumps(transcript),
            "slide_clicks_json": json.dumps(clicks),
            "telemetry_json": json.dumps(telemetry),
        },
    )
    assert analyze_resp.status_code == 200
    analyze_data = analyze_resp.json()
    report = analyze_data["report"]
    assert report["mode"] == "student_training"
    assert report["annotated_pdf_url"].startswith("/outputs/annotated/")
    assert report["digest_url"] is None
    assert any("eye contact" in f["summary"].lower() for f in report["problem_flags"])

    # Verify annotated PDF is downloadable via /outputs
    pdf_download = isolated_app.get(report["annotated_pdf_url"])
    assert pdf_download.status_code == 200
    assert pdf_download.content.startswith(b"%PDF")

    # Verify GET /api/session/{session_id} returns persisted record
    get_resp = isolated_app.get(f"/api/session/{session_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["report"]["session_id"] == session_id


def test_professor_live_lenses_digest_and_contraddittorio_rejection(
    isolated_app: TestClient, tmp_path: Path
) -> None:
    pdf_bytes = _create_test_pdf(tmp_path / "lecture.pdf")

    init_resp = isolated_app.post(
        "/api/session/init",
        data={
            "mode": "professor_live",
            "key_messages": "should be ignored in professor_live",
        },
        files={"pdf": ("lecture.pdf", pdf_bytes, "application/pdf")},
    )
    assert init_resp.status_code == 200
    init_data = init_resp.json()
    assert init_data["key_messages"] == []
    session_id = init_data["session_id"]

    transcript = [
        {"start": 0.0, "end": 5.0, "text": "Today we introduce Sparring multimodal rehearsal."},
        {"start": 5.0, "end": 10.0, "text": "Here are the empirical results and evaluation."},
        {"start": 10.0, "end": 15.0, "text": "Alignment consistency improves across decks."},
    ]
    analyze_resp = isolated_app.post(
        "/api/session/analyze",
        data={
            "session_id": session_id,
            "transcript_json": json.dumps(transcript),
        },
    )
    assert analyze_resp.status_code == 200
    report = analyze_resp.json()["report"]
    lenses = [item["lens"] for item in report["comprehension_lenses"]]
    assert sorted(lenses) == ["average", "freshman", "msc"]
    assert report["digest_url"].startswith("/outputs/digests/")

    digest_resp = isolated_app.get(report["digest_url"])
    assert digest_resp.status_code == 200
    assert "Post-Lecture WeBeep Digest" in digest_resp.text

    chat_resp = isolated_app.post(
        "/api/contraddittorio/chat",
        json={"session_id": session_id, "message": "Challenge my lecture"},
    )
    assert chat_resp.status_code == 200
    assert chat_resp.json()["status"] == "unsupported_mode"


def test_contraddittorio_no_ghostwriting_and_3_turn_budget(
    isolated_app: TestClient, tmp_path: Path
) -> None:
    pdf_bytes = _create_test_pdf(tmp_path / "thesis.pdf")
    init_resp = isolated_app.post(
        "/api/session/init",
        data={"mode": "student_live", "key_messages": "core thesis claim"},
        files={"pdf": ("thesis.pdf", pdf_bytes, "application/pdf")},
    )
    session_id = init_resp.json()["session_id"]

    # Refused EN & IT requests must NOT consume a turn
    refuse_en = isolated_app.post(
        "/api/contraddittorio/chat",
        json={"session_id": session_id, "message": "Please write my thesis introduction"},
    ).json()
    assert refuse_en["status"] == "refused"
    assert refuse_en["boundary"] == "no_ghostwriting"
    assert refuse_en["turn"] == 0

    refuse_it = isolated_app.post(
        "/api/contraddittorio/chat",
        json={"session_id": session_id, "message": "Scrivimi la slide sui risultati"},
    ).json()
    assert refuse_it["status"] == "refused"
    assert refuse_it["boundary"] == "no_ghostwriting"
    assert refuse_it["turn"] == 0

    # 3 valid turns
    for expected_turn in (1, 2, 3):
        ok_resp = isolated_app.post(
            "/api/contraddittorio/chat",
            json={"session_id": session_id, "message": f"My defense argument {expected_turn}"},
        ).json()
        assert ok_resp["status"] == "ok"
        assert ok_resp["turn"] == expected_turn

    # 4th valid turn hits budget_exhausted
    exhausted = isolated_app.post(
        "/api/contraddittorio/chat",
        json={"session_id": session_id, "message": "One more defense point"},
    ).json()
    assert exhausted["status"] == "budget_exhausted"
    assert exhausted["turn"] == 3


def test_orchestrator_sequential_vs_concurrent_and_json_fallback(tmp_path: Path) -> None:
    pdf_path = tmp_path / "deck.pdf"
    _create_test_pdf(pdf_path)

    async def _run_mode(cloud_enabled: bool) -> list[str]:
        llm = MockLLMService(cloud_enabled=cloud_enabled)
        orchestrator = EvaluationOrchestrator(
            llm=llm,
            slides_out_dir=tmp_path / "slides",
            annotated_dir=tmp_path / "annotated",
            digests_dir=tmp_path / "digests",
        )
        events: list[str] = []

        async def _fake_specchio(*args: Any, **kwargs: Any):
            events.append("specchio_start")
            await asyncio.sleep(0.02)
            events.append("specchio_end")
            return ["ok"], [], "summary"

        async def _fake_delivery(*args: Any, **kwargs: Any):
            events.append("delivery_start")
            await asyncio.sleep(0.02)
            events.append("delivery_end")
            return {"avg_wpm": 120.0}

        async def _fake_actionable(*args: Any, **kwargs: Any):
            events.append("actionable_start")
            await asyncio.sleep(0.02)
            events.append("actionable_end")
            return []

        orchestrator.specchio_solver.solve = _fake_specchio  # type: ignore[method-assign]
        orchestrator.delivery_solver.solve = _fake_delivery  # type: ignore[method-assign]
        orchestrator.actionable_solver.solve = _fake_actionable  # type: ignore[method-assign]

        from backend.schemas import SessionRecord

        session = SessionRecord(
            session_id="s1" if not cloud_enabled else "s2",
            mode="student_live",
            filename="deck.pdf",
            pdf_path=str(pdf_path),
            key_messages=[],
            slides=[
                SlideMetadata(
                    page_number=1,
                    raw_text="Intro",
                    clean_text="Intro",
                    word_count=1,
                    is_low_text=True,
                )
            ],
            created_at="2026-11-14T00:00:00Z",
        )
        intervals = [
            AlignedInterval(
                start_time=0.0,
                end_time=6.0,
                slide_page=1,
                confidence_score=0.9,
                transcript_slice="Intro speech",
            )
        ]
        await orchestrator.evaluate(session, intervals)
        return events

    seq_events = asyncio.run(_run_mode(cloud_enabled=False))
    assert seq_events == [
        "specchio_start",
        "specchio_end",
        "delivery_start",
        "delivery_end",
        "actionable_start",
        "actionable_end",
    ]

    conc_events = asyncio.run(_run_mode(cloud_enabled=True))
    assert conc_events[:3] == ["specchio_start", "delivery_start", "actionable_start"]

    # Verify LLMService.generate_json safely returns fallback on malformed output
    bad_llm = LLMService(ollama_base_url="http://127.0.0.1:9")
    fallback_res = asyncio.run(
        bad_llm.generate_json("hello", fallback={"safe": True})
    )
    assert fallback_res == {"safe": True}
