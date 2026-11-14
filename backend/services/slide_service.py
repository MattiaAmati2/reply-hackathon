import re
from collections import Counter, defaultdict
from pathlib import Path

import fitz

from backend.schemas import SlideAnnotationNote, SlideMetadata


class SlideProcessingError(RuntimeError):
    pass


_PAGE_NUM_PATTERN = re.compile(r"\b(?:page|slide|pp?\.?)\s*\d+\b", re.IGNORECASE)
_DATE_PATTERN = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})\b"
)
_ISOLATED_NUM_PATTERN = re.compile(r"(?m)^\s*\d{1,3}\s*$")
_WHITESPACE_PATTERN = re.compile(r"\s+")


class SlideService:
    """Extracts cleaned slide text, renders 150 DPI PNG thumbnails, and annotates PDFs."""

    def __init__(self, output_dir: Path | str) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _normalize_block_for_freq(text: str) -> str:
        stripped = _PAGE_NUM_PATTERN.sub("", text).strip()
        return _WHITESPACE_PATTERN.sub(" ", stripped)

    @staticmethod
    def _clean_text(text: str) -> str:
        cleaned = _PAGE_NUM_PATTERN.sub(" ", text)
        cleaned = _DATE_PATTERN.sub(" ", cleaned)
        cleaned = _ISOLATED_NUM_PATTERN.sub(" ", cleaned)
        cleaned = _WHITESPACE_PATTERN.sub(" ", cleaned).strip()
        return cleaned

    def process_pdf(self, pdf_path: Path | str, prefix: str = "") -> list[SlideMetadata]:
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise SlideProcessingError(f"PDF file not found: {pdf_path}")

        try:
            doc = fitz.open(pdf_path)
        except Exception as exc:
            raise SlideProcessingError(f"Failed to open PDF {pdf_path}: {exc}") from exc

        try:
            block_frequencies: Counter[str] = Counter()
            page_blocks: list[list[str]] = []

            for page in doc:
                raw_blocks = page.get_text("blocks")
                texts: list[str] = []
                seen_on_page: set[str] = set()
                for block in raw_blocks:
                    if len(block) < 5:
                        continue
                    block_text = str(block[4]).strip()
                    if not block_text:
                        continue
                    texts.append(block_text)
                    norm = self._normalize_block_for_freq(block_text)
                    if norm and norm not in seen_on_page:
                        block_frequencies[norm] += 1
                        seen_on_page.add(norm)
                page_blocks.append(texts)

            slides: list[SlideMetadata] = []
            dpi_matrix = fitz.Matrix(150 / 72, 150 / 72)

            for page_index, page in enumerate(doc):
                page_number = page_index + 1
                raw_text = "\n".join(page_blocks[page_index])
                filtered_blocks = [
                    blk
                    for blk in page_blocks[page_index]
                    if block_frequencies[self._normalize_block_for_freq(blk)] <= 5
                ]
                joined_valid = "\n".join(filtered_blocks)
                clean_text = self._clean_text(joined_valid)
                word_count = len(clean_text.split()) if clean_text else 0
                is_low_text = word_count < 10

                filename = (
                    f"{prefix}-slide-{page_number:04d}.png"
                    if prefix
                    else f"slide-{page_number:04d}.png"
                )
                preview_path = self.output_dir / filename
                pix = page.get_pixmap(matrix=dpi_matrix, alpha=False)
                pix.save(str(preview_path))

                slides.append(
                    SlideMetadata(
                        page_number=page_number,
                        raw_text=raw_text,
                        clean_text=clean_text,
                        word_count=word_count,
                        is_low_text=is_low_text,
                        thumbnail_path=str(preview_path),
                    )
                )

            return slides
        except SlideProcessingError:
            raise
        except Exception as exc:
            raise SlideProcessingError(f"Error processing PDF {pdf_path}: {exc}") from exc
        finally:
            doc.close()

    def annotate_pdf(
        self,
        pdf_path: Path | str,
        notes: list[SlideAnnotationNote],
        output_path: Path | str,
    ) -> Path:
        pdf_path = Path(pdf_path)
        output_path = Path(output_path)
        if not pdf_path.exists():
            raise SlideProcessingError(f"PDF file not found: {pdf_path}")

        notes_by_page: dict[int, list[SlideAnnotationNote]] = defaultdict(list)
        for note in notes:
            notes_by_page[note.page_number].append(note)

        try:
            doc = fitz.open(pdf_path)
        except Exception as exc:
            raise SlideProcessingError(f"Failed to open PDF {pdf_path}: {exc}") from exc

        try:
            for page_number, page_notes in notes_by_page.items():
                if page_number < 1 or page_number > len(doc):
                    continue
                page = doc[page_number - 1]
                summary_lines = [f"[{n.dimension}] {n.comment}" for n in page_notes]
                summary_text = "\n".join(summary_lines)

                rect = page.rect
                annot_point = fitz.Point(max(rect.width - 36, 10), 20)
                page.add_text_annot(annot_point, summary_text, icon="Note")

                box_width = min(260.0, rect.width * 0.45)
                box_height = min(140.0, max(40.0, 18.0 * len(summary_lines) + 16.0))
                x1 = max(rect.width - 12.0, box_width + 12.0)
                x0 = max(12.0, x1 - box_width)
                y0 = 12.0
                y1 = min(rect.height - 12.0, y0 + box_height)
                callout_rect = fitz.Rect(x0, y0, x1, y1)

                page.draw_rect(
                    callout_rect,
                    color=(0.2, 0.35, 0.65),
                    fill=(0.95, 0.97, 1.0),
                    fill_opacity=0.82,
                    width=0.8,
                )
                page.insert_textbox(
                    fitz.Rect(x0 + 6, y0 + 6, x1 - 6, y1 - 6),
                    summary_text,
                    fontsize=8.5,
                    color=(0.1, 0.15, 0.25),
                )

            output_path.parent.mkdir(parents=True, exist_ok=True)
            doc.save(str(output_path))
            return output_path
        except Exception as exc:
            raise SlideProcessingError(f"Failed to annotate PDF {pdf_path}: {exc}") from exc
        finally:
            doc.close()
