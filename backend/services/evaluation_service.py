import asyncio
import json
from pathlib import Path
import re
from typing import Any, Optional

from backend.schemas import (
    AlignedInterval,
    ComprehensionLensFeedback,
    ContraddittorioResponse,
    EvaluationReport,
    ProblemFlag,
    SessionMode,
    SessionRecord,
    SlideAnnotationNote,
)
from backend.services.db_service import DatabaseService
from backend.services.llm_service import LLMService
from backend.services.slide_service import SlideService

_GHOSTWRITING_PATTERN = re.compile(
    r"\b(?:"
    r"write\s+(?:my|the|this)|"
    r"rewrite\s+(?:my|the|this)|"
    r"generate\s+(?:a\s+)?(?:slide|slides|thesis|abstract|script|text)|"
    r"draft\s+(?:my|the|a)|"
    r"do\s+it\s+for\s+me|"
    r"scrivi(?:mi)?\s+(?:la|il|le|i|una|un)|"
    r"riscrivi(?:mi)?|"
    r"genera(?:mi)?\s+(?:la|le|una)\s+slide|"
    r"fallo\s+(?:tu\s+)?per\s+me|"
    r"prepara(?:mi)?\s+il\s+testo"
    r")\b",
    re.IGNORECASE,
)

_VALID_DIMENSIONS = frozenset(
    {"alignment", "relevance", "highlighting", "clarity", "complexity"}
)


class ProblemIdentifier:
    """Deterministic Level-2 analyzer that emits ProblemFlags and key-message coverage."""

    @staticmethod
    def _check_key_messages(
        key_messages: list[str], full_transcript: str
    ) -> dict[str, bool]:
        lower_transcript = full_transcript.lower()
        coverage: dict[str, bool] = {}
        for msg in key_messages:
            tokens = [
                tok
                for tok in re.findall(r"[a-zA-ZÀ-ÿ0-9]{4,}", msg.lower())
            ]
            if not tokens:
                coverage[msg] = msg.lower() in lower_transcript
                continue
            hits = sum(1 for tok in tokens if tok in lower_transcript)
            coverage[msg] = hits >= max(1, (len(tokens) + 1) // 2)
        return coverage

    def analyze(
        self,
        mode: SessionMode,
        intervals: list[AlignedInterval],
        key_messages: list[str],
        telemetry: Optional[dict[str, Any]] = None,
    ) -> tuple[list[ProblemFlag], dict[str, bool]]:
        flags: list[ProblemFlag] = []
        telemetry = telemetry or {}

        for iv in intervals:
            duration = max(0.0, iv.end_time - iv.start_time)
            if duration < 5.0 or not iv.transcript_slice.strip():
                flags.append(
                    ProblemFlag(
                        page_number=iv.slide_page,
                        severity="medium",
                        dimension="alignment",
                        summary=(
                            f"Slide {iv.slide_page} has minimal or uncovered speech "
                            f"({duration:.1f}s)."
                        ),
                    )
                )
            if iv.wpm > 0 and iv.wpm < 95:
                flags.append(
                    ProblemFlag(
                        page_number=iv.slide_page,
                        severity="low",
                        dimension="delivery",
                        summary=f"Slow speaking pace on slide {iv.slide_page} ({iv.wpm:.0f} WPM).",
                    )
                )
            elif iv.wpm > 175:
                flags.append(
                    ProblemFlag(
                        page_number=iv.slide_page,
                        severity="medium",
                        dimension="delivery",
                        summary=f"Rushed speaking pace on slide {iv.slide_page} ({iv.wpm:.0f} WPM).",
                    )
                )

            if iv.filler_count >= 3:
                flags.append(
                    ProblemFlag(
                        page_number=iv.slide_page,
                        severity="low",
                        dimension="delivery",
                        summary=(
                            f"Elevated filler count ({iv.filler_count}) on slide {iv.slide_page}."
                        ),
                    )
                )

            if iv.pause_count >= 2:
                flags.append(
                    ProblemFlag(
                        page_number=iv.slide_page,
                        severity="low",
                        dimension="delivery",
                        summary=(
                            f"Frequent long pauses ({iv.pause_count}) on slide {iv.slide_page}."
                        ),
                    )
                )

            if mode in ("student_live", "professor_live") and iv.confidence_score < 0.25:
                flags.append(
                    ProblemFlag(
                        page_number=iv.slide_page,
                        severity="medium",
                        dimension="alignment",
                        summary=(
                            f"Low spoken-to-slide semantic alignment ({iv.confidence_score:.2f}) "
                            f"on slide {iv.slide_page}."
                        ),
                    )
                )

        if mode == "student_training" and telemetry:
            first_page = intervals[0].slide_page if intervals else 1
            eye_contact = telemetry.get("eye_contact_ratio")
            if isinstance(eye_contact, (int, float)) and eye_contact < 0.45:
                flags.append(
                    ProblemFlag(
                        page_number=first_page,
                        severity="medium",
                        dimension="delivery",
                        summary=(
                            f"Low eye contact ratio ({eye_contact:.2f}); look toward the camera more consistently."
                        ),
                    )
                )
            posture = telemetry.get("posture_score")
            if isinstance(posture, (int, float)) and posture < 0.5:
                flags.append(
                    ProblemFlag(
                        page_number=first_page,
                        severity="low",
                        dimension="delivery",
                        summary=(
                            f"Posture stability score is low ({posture:.2f}); keep shoulders centered."
                        ),
                    )
                )

        if mode == "professor_live":
            coverage: dict[str, bool] = {}
        else:
            full_transcript = " ".join(
                iv.transcript_slice for iv in intervals if iv.transcript_slice
            )
            coverage = self._check_key_messages(key_messages, full_transcript)

        return flags, coverage


class SpecchioSolver:
    """Produces Specchio narrative feedback (student) or 3 Comprehension Lenses (professor)."""

    def __init__(self, llm: LLMService) -> None:
        self.llm = llm

    async def solve(
        self,
        session: SessionRecord,
        intervals: list[AlignedInterval],
        flags: list[ProblemFlag],
        coverage: dict[str, bool],
    ) -> tuple[list[str], list[ComprehensionLensFeedback], str]:
        timeline_snippet = "\n".join(
            f"- Slide {iv.slide_page} ({iv.start_time:.1f}-{iv.end_time:.1f}s): "
            f"{iv.transcript_slice[:180]}"
            for iv in intervals[:8]
        )

        if session.mode == "professor_live":
            fallback = {
                "overall_summary": (
                    "Lecture analyzed across Freshman, Average, and MSc comprehension lenses "
                    "without content judgment."
                ),
                "lenses": [
                    {
                        "lens": "freshman",
                        "clarity_score": 6,
                        "friction_points": [
                            "Unfamiliar terminology may need brief intuitive framing."
                        ],
                        "suggestions": [
                            "Introduce a 1-sentence concrete analogy before formal definitions."
                        ],
                    },
                    {
                        "lens": "average",
                        "clarity_score": 7,
                        "friction_points": [
                            "Transitions between slides could signpost the main takeaway."
                        ],
                        "suggestions": [
                            "Recap the core link before advancing to the next slide."
                        ],
                    },
                    {
                        "lens": "msc",
                        "clarity_score": 8,
                        "friction_points": [
                            "Assumptions and edge cases are implicit in parts of the explanation."
                        ],
                        "suggestions": [
                            "Explicitly state boundary conditions or methodological trade-offs."
                        ],
                    },
                ],
            }
            prompt = (
                "Analyze this university lecture transcript strictly for student comprehension "
                "(Freshman, Average, MSc). Do NOT judge scientific correctness or content quality; "
                "focus only on understandability, jargon scaffolding, and pacing.\n"
                f"Timeline:\n{timeline_snippet}\n"
                f"Flags: {[f.summary for f in flags[:6]]}\n"
                'Return JSON: {"overall_summary": str, "lenses": [{"lens": "freshman|average|msc", '
                '"clarity_score": 1-10, "friction_points": [str], "suggestions": [str]}]}.'
            )
            data = await self.llm.generate_json(
                prompt=prompt,
                system_prompt="You are a pedagogical comprehension mirror. Output valid JSON only.",
                fallback=fallback,
                max_tokens=360,
            )
            raw_lenses = data.get("lenses", fallback["lenses"])
            lenses: list[ComprehensionLensFeedback] = []
            seen_lenses: set[str] = set()
            for item in raw_lenses:
                lens_name = str(item.get("lens", "")).lower()
                if lens_name not in ("freshman", "average", "msc") or lens_name in seen_lenses:
                    continue
                seen_lenses.add(lens_name)
                score = max(1, min(10, int(item.get("clarity_score", 7))))
                lenses.append(
                    ComprehensionLensFeedback(
                        lens=lens_name,  # type: ignore[arg-type]
                        clarity_score=score,
                        friction_points=[str(x) for x in item.get("friction_points", [])[:3]],
                        suggestions=[str(x) for x in item.get("suggestions", [])[:3]],
                    )
                )
            for fb_item in fallback["lenses"]:  # type: ignore[index]
                if fb_item["lens"] not in seen_lenses:
                    lenses.append(ComprehensionLensFeedback(**fb_item))
            summary = str(data.get("overall_summary") or fallback["overall_summary"])
            return [], lenses, summary

        # Student modes
        missing_msgs = [k for k, covered in coverage.items() if not covered]
        fallback_feedback = [
            f"Covered {sum(1 for v in coverage.values() if v)}/{len(coverage)} key messages."
            if coverage
            else "No key messages specified; evaluated narrative flow across slides.",
            (
                f"Missing key messages to reinforce: {', '.join(missing_msgs)}."
                if missing_msgs
                else "All specified key messages were addressed in your spoken delivery."
            ),
        ]
        fallback_student = {
            "overall_summary": "Specchio narrative and key-message alignment analysis complete.",
            "specchio_feedback": fallback_feedback,
        }
        prompt = (
            "Evaluate this student presentation for narrative flow and key-message coverage.\n"
            f"Key message coverage: {json.dumps(coverage)}\n"
            f"Timeline:\n{timeline_snippet}\n"
            f"Flags: {[f.summary for f in flags[:6]]}\n"
            'Return JSON: {"overall_summary": str, "specchio_feedback": [str, ...]}.'
        )
        data = await self.llm.generate_json(
            prompt=prompt,
            system_prompt="You are Specchio, a concise presentation coach. Output valid JSON only.",
            fallback=fallback_student,
            max_tokens=320,
        )
        feedback = [str(x) for x in data.get("specchio_feedback", fallback_feedback)][:5]
        summary = str(data.get("overall_summary") or fallback_student["overall_summary"])
        return feedback, [], summary


class DeliverySolver:
    """Aggregates speaking pace, pauses, fillers, and optional MediaPipe telemetry."""

    def __init__(self, llm: LLMService) -> None:
        self.llm = llm

    async def solve(
        self,
        session: SessionRecord,
        intervals: list[AlignedInterval],
        telemetry: dict[str, Any],
    ) -> dict[str, Any]:
        wpms = [iv.wpm for iv in intervals if iv.wpm > 0]
        avg_wpm = round(sum(wpms) / len(wpms), 1) if wpms else 0.0
        total_fillers = sum(iv.filler_count for iv in intervals)
        total_pauses = sum(iv.pause_count for iv in intervals)

        tips: list[str] = []
        if avg_wpm > 175:
            tips.append("Slow down slightly between key transitions to improve clarity.")
        elif 0 < avg_wpm < 95:
            tips.append("Increase vocal energy and pace to maintain audience engagement.")
        else:
            tips.append("Speaking pace is within a comfortable academic range.")

        if total_fillers >= 3:
            tips.append("Replace filler words with brief deliberate silences.")
        if session.mode == "student_training" and telemetry:
            eye = telemetry.get("eye_contact_ratio")
            if isinstance(eye, (int, float)) and eye < 0.45:
                tips.append("Maintain steadier eye contact with the camera lens.")

        fallback = {"tips": tips}
        prompt = (
            f"Summarize vocal delivery metrics into 2 concise coaching tips.\n"
            f"avg_wpm={avg_wpm}, total_fillers={total_fillers}, total_pauses={total_pauses}, "
            f"telemetry={json.dumps(telemetry) if session.mode == 'student_training' else '{}'}.\n"
            'Return JSON: {"tips": [str, str]}.'
        )
        data = await self.llm.generate_json(
            prompt=prompt,
            system_prompt="You are a concise speech delivery coach. Output valid JSON only.",
            fallback=fallback,
            max_tokens=200,
        )
        return {
            "avg_wpm": avg_wpm,
            "total_fillers": total_fillers,
            "total_pauses": total_pauses,
            "telemetry": telemetry if session.mode == "student_training" else {},
            "tips": [str(t) for t in data.get("tips", tips)][:4],
        }


class ActionableOutputSolver:
    """Produces concise SlideAnnotationNotes (<= 180 chars) for PDF annotation."""

    def __init__(self, llm: LLMService) -> None:
        self.llm = llm

    async def solve(
        self,
        session: SessionRecord,
        flags: list[ProblemFlag],
    ) -> list[SlideAnnotationNote]:
        max_page = max((s.page_number for s in session.slides), default=1)
        fallback_notes: list[SlideAnnotationNote] = []
        for flag in flags[:6]:
            dim = flag.dimension if flag.dimension in _VALID_DIMENSIONS else "clarity"
            fallback_notes.append(
                SlideAnnotationNote(
                    page_number=max(1, min(max_page, flag.page_number)),
                    dimension=dim,  # type: ignore[arg-type]
                    comment=flag.summary[:180],
                )
            )
        if not fallback_notes and session.slides:
            fallback_notes.append(
                SlideAnnotationNote(
                    page_number=1,
                    dimension="alignment",
                    comment="Opening slide aligns cleanly with spoken introduction.",
                )
            )

        fallback_payload = {
            "notes": [n.model_dump() for n in fallback_notes]
        }
        prompt = (
            f"Generate up to 5 concise slide annotation notes (<= 180 chars each) "
            f"for a {max_page}-page deck based on these flags:\n"
            f"{[f.model_dump() for f in flags[:6]]}\n"
            'Return JSON: {"notes": [{"page_number": int, '
            '"dimension": "alignment|relevance|highlighting|clarity|complexity", '
            '"comment": str}]}.'
        )
        data = await self.llm.generate_json(
            prompt=prompt,
            system_prompt="You generate concise PDF slide annotations. Output valid JSON only.",
            fallback=fallback_payload,
            max_tokens=280,
        )
        notes: list[SlideAnnotationNote] = []
        for item in data.get("notes", fallback_payload["notes"]):
            try:
                page = max(1, min(max_page, int(item.get("page_number", 1))))
                dim = str(item.get("dimension", "clarity")).lower()
                if dim not in _VALID_DIMENSIONS:
                    dim = "clarity"
                comment = str(item.get("comment", "")).strip()[:180]
                if comment:
                    notes.append(
                        SlideAnnotationNote(
                            page_number=page,
                            dimension=dim,  # type: ignore[arg-type]
                            comment=comment,
                        )
                    )
            except (TypeError, ValueError):
                continue
        return notes or fallback_notes


class EvaluationOrchestrator:
    """Runs ProblemIdentifier -> 3 Solvers -> PDF annotation & WeBeep digest."""

    def __init__(
        self,
        llm: LLMService,
        slides_out_dir: Path,
        annotated_dir: Path,
        digests_dir: Path,
    ) -> None:
        self.llm = llm
        self.slides_out_dir = Path(slides_out_dir)
        self.annotated_dir = Path(annotated_dir)
        self.digests_dir = Path(digests_dir)
        self.problem_identifier = ProblemIdentifier()
        self.specchio_solver = SpecchioSolver(llm)
        self.delivery_solver = DeliverySolver(llm)
        self.actionable_solver = ActionableOutputSolver(llm)

    @staticmethod
    def _render_webeep_digest(
        session: SessionRecord,
        intervals: list[AlignedInterval],
        lenses: list[ComprehensionLensFeedback],
        overall_summary: str,
    ) -> str:
        lines = [
            f"# Post-Lecture WeBeep Digest — {session.filename}",
            "",
            f"**Session ID:** `{session.session_id}`",
            "",
            "## Summary",
            overall_summary,
            "",
            "## Slide Timeline & Key Concepts Discussed",
        ]
        for iv in intervals:
            snippet = iv.transcript_slice.strip() or "_No speech captured._"
            lines.append(
                f"- **Slide {iv.slide_page}** ({iv.start_time:.1f}s – {iv.end_time:.1f}s): {snippet}"
            )
        lines.extend(["", "## Comprehension-Lens Takeaways"])
        for lens in lenses:
            lines.append(f"### {lens.lens.capitalize()} Lens (Clarity: {lens.clarity_score}/10)")
            if lens.friction_points:
                lines.append("- **Friction points:** " + "; ".join(lens.friction_points))
            if lens.suggestions:
                lines.append("- **Suggestions:** " + "; ".join(lens.suggestions))
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    async def evaluate(
        self,
        session: SessionRecord,
        intervals: list[AlignedInterval],
        telemetry: Optional[dict[str, Any]] = None,
    ) -> tuple[EvaluationReport, Optional[Path], Optional[Path]]:
        telemetry = telemetry or {}
        flags, coverage = self.problem_identifier.analyze(
            mode=session.mode,
            intervals=intervals,
            key_messages=session.key_messages,
            telemetry=telemetry,
        )

        if self.llm.is_cloud_enabled:
            (
                (specchio_feedback, lenses, overall_summary),
                delivery_feedback,
                slide_notes,
            ) = await asyncio.gather(
                self.specchio_solver.solve(session, intervals, flags, coverage),
                self.delivery_solver.solve(session, intervals, telemetry),
                self.actionable_solver.solve(session, flags),
            )
        else:
            specchio_feedback, lenses, overall_summary = await self.specchio_solver.solve(
                session, intervals, flags, coverage
            )
            delivery_feedback = await self.delivery_solver.solve(
                session, intervals, telemetry
            )
            slide_notes = await self.actionable_solver.solve(session, flags)

        self.annotated_dir.mkdir(parents=True, exist_ok=True)
        annotated_path = self.annotated_dir / f"{session.session_id}_annotated.pdf"
        SlideService(self.slides_out_dir).annotate_pdf(
            Path(session.pdf_path), slide_notes, annotated_path
        )
        annotated_url = f"/outputs/annotated/{annotated_path.name}"

        digest_path: Optional[Path] = None
        digest_url: Optional[str] = None
        if session.mode == "professor_live":
            self.digests_dir.mkdir(parents=True, exist_ok=True)
            digest_path = self.digests_dir / f"{session.session_id}_digest.md"
            digest_md = self._render_webeep_digest(
                session, intervals, lenses, overall_summary
            )
            digest_path.write_text(digest_md, encoding="utf-8")
            digest_url = f"/outputs/digests/{digest_path.name}"

        report = EvaluationReport(
            session_id=session.session_id,
            mode=session.mode,
            overall_summary=overall_summary,
            key_message_coverage=coverage,
            problem_flags=flags,
            specchio_feedback=specchio_feedback,
            comprehension_lenses=lenses,
            delivery_feedback=delivery_feedback,
            slide_notes=slide_notes,
            annotated_pdf_url=annotated_url,
            digest_url=digest_url,
        )
        return report, annotated_path, digest_path


class ContraddittorioAgent:
    """Socratic follow-up agent (max 3 turns, strict no_ghostwriting boundary)."""

    MAX_TURNS = 3

    def __init__(self, llm: LLMService, db: DatabaseService) -> None:
        self.llm = llm
        self.db = db

    async def reply(
        self, session: SessionRecord, user_message: str
    ) -> ContraddittorioResponse:
        if session.mode == "professor_live":
            return ContraddittorioResponse(
                status="unsupported_mode",
                session_id=session.session_id,
                turn=0,
                max_turns=self.MAX_TURNS,
                reply="Socratic Contraddittorio is only available in student modes.",
            )

        current_turns = self.db.get_user_turn_count(session.session_id)

        if _GHOSTWRITING_PATTERN.search(user_message):
            refusal_msg = (
                "Kairos Boundary: I cannot write, rewrite, or generate slides or thesis text for you. "
                "Explain the argument in your own words and I will challenge it."
            )
            self.db.append_chat_turn(
                session.session_id, current_turns, "user", user_message, refused=True
            )
            self.db.append_chat_turn(
                session.session_id, current_turns, "assistant", refusal_msg, refused=True
            )
            return ContraddittorioResponse(
                status="refused",
                session_id=session.session_id,
                turn=current_turns,
                max_turns=self.MAX_TURNS,
                boundary="no_ghostwriting",
                reply=refusal_msg,
            )

        if current_turns >= self.MAX_TURNS:
            return ContraddittorioResponse(
                status="budget_exhausted",
                session_id=session.session_id,
                turn=self.MAX_TURNS,
                max_turns=self.MAX_TURNS,
                reply=(
                    "You have completed all 3 Socratic Contraddittorio turns for this session. "
                    "Review your annotated PDF and refine your defense."
                ),
            )

        next_turn = current_turns + 1
        missing_messages: list[str] = []
        if session.report and session.report.key_message_coverage:
            missing_messages = [
                k for k, ok in session.report.key_message_coverage.items() if not ok
            ]

        slide_context = "; ".join(
            f"Slide {s.page_number}: {s.clean_text[:100]}" for s in session.slides[:4]
        )
        transcript_context = "; ".join(
            f"Slide {iv.slide_page}: {iv.transcript_slice[:120]}"
            for iv in session.aligned_intervals[:4]
        )
        history = self.db.get_chat_history(session.session_id)
        history_text = "\n".join(
            f"{h['role']}: {h['content']}" for h in history[-4:] if not h["refused"]
        )

        fallback_question = (
            f"On your slide claim ({missing_messages[0]}), what concrete evidence or counter-example "
            "would you present if a committee member challenged that assumption?"
            if missing_messages
            else "What is the strongest empirical assumption behind your main claim, and how would you defend it under scrutiny?"
        )

        prompt = (
            f"Slides: {slide_context}\n"
            f"Spoken transcript: {transcript_context}\n"
            f"Missing key messages: {missing_messages}\n"
            f"Recent dialogue:\n{history_text}\n"
            f"Student message (turn {next_turn}/{self.MAX_TURNS}): {user_message}\n"
            "Ask exactly ONE focused Socratic follow-up question. Never write or rewrite slides/text."
        )
        system_prompt = (
            "You are Kairos's Socratic examiner. Ask ONE probing question grounded in the student's "
            "slides and speech. Strictly refuse any ghostwriting or text generation."
        )
        try:
            generated = await self.llm.generate(
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=180,
                temperature=0.3,
            )
            question = generated.strip() or fallback_question
        except Exception:
            question = fallback_question

        self.db.append_chat_turn(
            session.session_id, next_turn, "user", user_message, refused=False
        )
        self.db.append_chat_turn(
            session.session_id, next_turn, "assistant", question, refused=False
        )

        return ContraddittorioResponse(
            status="ok",
            session_id=session.session_id,
            turn=next_turn,
            max_turns=self.MAX_TURNS,
            reply=question,
        )
