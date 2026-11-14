from typing import Any, Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from backend.schemas import AlignedInterval, SlideMetadata, TranscriptSegment
from backend.services.audio_service import AudioService


class AlignmentError(RuntimeError):
    pass


class AlignmentService:
    """Aligns transcript segments to slides via manual clicks or HMM+Viterbi decoding."""

    HMM_P_STAY: float = 0.90
    HMM_P_ADVANCE_1: float = 0.085
    HMM_P_ADVANCE_MULTI: float = 0.01
    HMM_P_BACKWARD: float = 0.005
    SOFTMAX_TEMPERATURE: float = 0.25
    _CHUNK_WINDOW: int = 3

    def __init__(self) -> None:
        self._dense_model: Optional[Any] = None
        self._dense_unavailable: bool = False

    def _get_dense_model(self) -> Optional[Any]:
        if self._dense_unavailable:
            return None
        if self._dense_model is None:
            try:
                from sentence_transformers import SentenceTransformer

                self._dense_model = SentenceTransformer("all-MiniLM-L6-v2")
            except ImportError:
                self._dense_unavailable = True
                return None
        return self._dense_model

    @staticmethod
    def _build_interval(
        start_time: float,
        end_time: float,
        slide: SlideMetadata,
        confidence_score: float,
        segments: list[TranscriptSegment],
    ) -> AlignedInterval:
        overlapping = [
            seg
            for seg in segments
            if (start_time <= seg.start < end_time)
            or (start_time <= (seg.start + seg.end) / 2.0 < end_time)
        ]
        transcript_slice = " ".join(seg.text for seg in overlapping if seg.text).strip()
        metrics = AudioService.compute_speech_metrics(segments, start_time, end_time)
        return AlignedInterval(
            start_time=start_time,
            end_time=end_time,
            slide_page=slide.page_number,
            matched_slide=slide,
            confidence_score=confidence_score,
            transcript_slice=transcript_slice,
            wpm=float(metrics["wpm"]),
            filler_count=int(metrics["filler_count"]),
            pause_count=int(metrics["pause_count"]),
        )

    def align_from_manual_timestamps(
        self,
        segments: list[TranscriptSegment],
        slides: list[SlideMetadata],
        slide_click_timestamps: list[dict[str, Any]],
    ) -> list[AlignedInterval]:
        if not slides:
            raise AlignmentError("Slides list cannot be empty")
        if not slide_click_timestamps:
            return []

        slides_by_page = {s.page_number: s for s in slides}
        intervals: list[AlignedInterval] = []

        for click in slide_click_timestamps:
            start_time = float(click["start_time"])
            end_time = float(click["end_time"])
            if "slide_page" in click:
                slide_page = int(click["slide_page"])
                slide = slides_by_page.get(slide_page)
            elif "slide_index" in click:
                idx = int(click["slide_index"])
                slide = slides[idx] if 0 <= idx < len(slides) else None
            else:
                slide = None

            if slide is None:
                raise AlignmentError(f"Invalid slide reference in click record: {click}")

            intervals.append(
                self._build_interval(
                    start_time=start_time,
                    end_time=end_time,
                    slide=slide,
                    confidence_score=1.0,
                    segments=segments,
                )
            )

        return intervals

    @staticmethod
    def _build_contextual_slide_texts(
        slides: list[SlideMetadata],
    ) -> tuple[list[str], list[bool]]:
        texts: list[str] = []
        low_text_mask: list[bool] = []
        n = len(slides)
        for i, slide in enumerate(slides):
            is_low = slide.is_low_text or slide.word_count < 10
            low_text_mask.append(is_low)
            if is_low:
                parts: list[str] = []
                if i > 0 and slides[i - 1].clean_text:
                    parts.append(slides[i - 1].clean_text[:200])
                if slide.clean_text:
                    parts.append(slide.clean_text)
                if i + 1 < n and slides[i + 1].clean_text:
                    parts.append(slides[i + 1].clean_text[:200])
                texts.append(" ".join(parts).strip())
            else:
                texts.append(slide.clean_text)
        return texts, low_text_mask

    def _build_chunks(
        self, segments: list[TranscriptSegment]
    ) -> list[tuple[float, float, str]]:
        window = min(self._CHUNK_WINDOW, len(segments))
        raw_chunks: list[tuple[float, str]] = []
        for i in range(0, len(segments) - window + 1):
            group = segments[i : i + window]
            center = (group[0].start + group[-1].end) / 2.0
            text = " ".join(s.text for s in group if s.text).strip()
            raw_chunks.append((center, text))

        if not raw_chunks:
            return []

        m = len(raw_chunks)
        bounds: list[tuple[float, float, str]] = []
        for t in range(m):
            eff_start = (
                segments[0].start
                if t == 0
                else (raw_chunks[t - 1][0] + raw_chunks[t][0]) / 2.0
            )
            eff_end = (
                segments[-1].end
                if t == m - 1
                else (raw_chunks[t][0] + raw_chunks[t + 1][0]) / 2.0
            )
            bounds.append((eff_start, eff_end, raw_chunks[t][1]))
        return bounds

    def _compute_similarity_matrix(
        self,
        chunk_texts: list[str],
        slide_texts: list[str],
        low_text_mask: list[bool],
    ) -> np.ndarray:
        m = len(chunk_texts)
        n = len(slide_texts)

        try:
            vectorizer = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)
            corpus = slide_texts + chunk_texts
            tfidf_matrix = vectorizer.fit_transform(corpus)
            slide_vecs = tfidf_matrix[:n]
            chunk_vecs = tfidf_matrix[n:]
            tfidf_sims = cosine_similarity(chunk_vecs, slide_vecs)
        except ValueError:
            tfidf_sims = np.zeros((m, n), dtype=np.float64)

        dense_model = self._get_dense_model()
        if dense_model is not None:
            slide_emb = dense_model.encode(slide_texts, normalize_embeddings=True)
            chunk_emb = dense_model.encode(chunk_texts, normalize_embeddings=True)
            dense_sims = np.clip(chunk_emb @ slide_emb.T, 0.0, 1.0)
            sims = 0.5 * tfidf_sims + 0.5 * dense_sims
        else:
            sims = tfidf_sims

        low_indices = [idx for idx, is_low in enumerate(low_text_mask) if is_low]
        if low_indices:
            for row in range(m):
                row_median = float(np.median(sims[row]))
                for col in low_indices:
                    sims[row, col] = row_median

        return sims

    def _build_transition_matrix(self, n: int) -> np.ndarray:
        if n == 1:
            return np.zeros((1, 1), dtype=np.float64)

        trans = np.zeros((n, n), dtype=np.float64)
        for i in range(n):
            trans[i, i] = self.HMM_P_STAY
            if i + 1 < n:
                trans[i, i + 1] = self.HMM_P_ADVANCE_1

            if i + 2 < n:
                forward_indices = list(range(i + 2, n))
                weights = np.array([0.5**k for k in range(len(forward_indices))])
                weights /= weights.sum()
                for idx, w in zip(forward_indices, weights):
                    trans[i, idx] = self.HMM_P_ADVANCE_MULTI * float(w)

            if i > 0:
                back_indices = list(range(i - 1, -1, -1))
                weights = np.array([0.5**k for k in range(len(back_indices))])
                weights /= weights.sum()
                for idx, w in zip(back_indices, weights):
                    trans[i, idx] = self.HMM_P_BACKWARD * float(w)

            row_sum = trans[i].sum()
            if row_sum > 0:
                trans[i] /= row_sum

        return np.log(np.clip(trans, 1e-12, 1.0))

    @staticmethod
    def _build_initial_log_probs(n: int) -> np.ndarray:
        pi = np.full(n, 1e-6, dtype=np.float64)
        if n >= 1:
            pi[0] = 0.95
        if n >= 2:
            pi[1] = 0.04
        if n >= 3:
            pi[2] = 0.01
        pi /= pi.sum()
        return np.log(np.clip(pi, 1e-12, 1.0))

    @staticmethod
    def _viterbi_decode(
        log_A: np.ndarray, log_B: np.ndarray, log_pi: np.ndarray
    ) -> list[int]:
        m, n = log_B.shape
        dp = np.zeros((m, n), dtype=np.float64)
        backptr = np.zeros((m, n), dtype=np.int64)

        dp[0] = log_pi + log_B[0]
        for t in range(1, m):
            scores = dp[t - 1][:, None] + log_A
            best_prev = np.argmax(scores, axis=0)
            dp[t] = scores[best_prev, np.arange(n)] + log_B[t]
            backptr[t] = best_prev

        path = [0] * m
        path[-1] = int(np.argmax(dp[-1]))
        for t in range(m - 2, -1, -1):
            path[t] = int(backptr[t + 1, path[t + 1]])
        return path

    def align_live_audio(
        self,
        segments: list[TranscriptSegment],
        slides: list[SlideMetadata],
    ) -> list[AlignedInterval]:
        if not slides:
            raise AlignmentError("Slides list cannot be empty")
        if not segments:
            return []

        n = len(slides)
        slide_texts, low_text_mask = self._build_contextual_slide_texts(slides)
        chunks = self._build_chunks(segments)
        chunk_texts = [c[2] for c in chunks]

        sims = self._compute_similarity_matrix(chunk_texts, slide_texts, low_text_mask)
        scaled = sims / self.SOFTMAX_TEMPERATURE
        scaled -= np.max(scaled, axis=1, keepdims=True)
        exp_scaled = np.exp(scaled)
        probs = exp_scaled / np.sum(exp_scaled, axis=1, keepdims=True)
        log_B = np.log(np.clip(probs, 1e-12, 1.0))

        log_A = self._build_transition_matrix(n)
        log_pi = self._build_initial_log_probs(n)
        state_path = self._viterbi_decode(log_A, log_B, log_pi)

        intervals: list[AlignedInterval] = []
        run_start = chunks[0][0]
        run_end = chunks[0][1]
        run_state = state_path[0]
        run_confs = [float(probs[0, run_state])]

        for t in range(1, len(chunks)):
            state = state_path[t]
            conf = float(probs[t, state])
            if state == run_state:
                run_end = chunks[t][1]
                run_confs.append(conf)
            else:
                intervals.append(
                    self._build_interval(
                        start_time=run_start,
                        end_time=run_end,
                        slide=slides[run_state],
                        confidence_score=round(sum(run_confs) / len(run_confs), 4),
                        segments=segments,
                    )
                )
                run_start = chunks[t][0]
                run_end = chunks[t][1]
                run_state = state
                run_confs = [conf]

        intervals.append(
            self._build_interval(
                start_time=run_start,
                end_time=run_end,
                slide=slides[run_state],
                confidence_score=round(sum(run_confs) / len(run_confs), 4),
                segments=segments,
            )
        )
        return intervals
