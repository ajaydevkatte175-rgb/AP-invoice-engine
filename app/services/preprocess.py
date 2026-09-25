import hashlib
import io
from pathlib import Path

import pypdf
import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

# Non-Negotiable Rule 11: Page Processing Limit (Budget Guard)
MAX_PAGE_THRESHOLD = 5
HEAD_PAGES = 3
TAIL_PAGES = 2


class PreprocessedDocument(BaseModel):
    """Result of preprocessing an uploaded document."""

    sha256_hash: str
    page_count: int
    pages_processed: list[int] = Field(default_factory=list)
    truncated: bool = False
    text_content: str = ""
    file_path: str = ""
    mime_type: str = ""


def compute_sha256(content: bytes) -> str:
    """Compute the SHA-256 hexadecimal digest of raw content bytes."""
    return hashlib.sha256(content).hexdigest()


def preprocess_document(
    file_bytes: bytes,
    file_path: str | Path,
    mime_type: str = "application/pdf",
) -> PreprocessedDocument:
    """Preprocess document file, enforcing SHA256 calculation and the 5-page budget limit.

    NON-NEGOTIABLE RULE 11:
    If a PDF exceeds 5 pages, preprocess and extract ONLY the first 3 pages
    and the last 2 pages (where totals and line items reside) to prevent token
    budget blowouts on long T&C attachments.
    """
    file_str = str(file_path)
    sha256 = compute_sha256(file_bytes)

    if mime_type == "application/pdf" or file_str.lower().endswith(".pdf"):
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        total_pages = len(reader.pages)

        if total_pages <= MAX_PAGE_THRESHOLD:
            pages_to_process = list(range(total_pages))
            truncated = False
        else:
            # First 3 pages (0, 1, 2) and last 2 pages (total - 2, total - 1)
            first_pages = list(range(min(HEAD_PAGES, total_pages)))
            last_pages = list(range(max(0, total_pages - TAIL_PAGES), total_pages))
            pages_to_process = sorted(set(first_pages + last_pages))
            truncated = True
            logger.info(
                "Enforcing 5-page processing limit budget guard",
                total_pages=total_pages,
                pages_selected=pages_to_process,
            )

        extracted_text_blocks = []
        for idx in pages_to_process:
            page = reader.pages[idx]
            page_text = page.extract_text() or ""
            extracted_text_blocks.append(f"--- PAGE {idx + 1} ---\n{page_text}")

        full_text = "\n\n".join(extracted_text_blocks)

        return PreprocessedDocument(
            sha256_hash=sha256,
            page_count=total_pages,
            pages_processed=[p + 1 for p in pages_to_process],  # 1-indexed for reporting
            truncated=truncated,
            text_content=full_text,
            file_path=file_str,
            mime_type="application/pdf",
        )

    # For images or plain text formats
    return PreprocessedDocument(
        sha256_hash=sha256,
        page_count=1,
        pages_processed=[1],
        truncated=False,
        text_content=file_bytes.decode("utf-8", errors="ignore"),
        file_path=file_str,
        mime_type=mime_type,
    )
