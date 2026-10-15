import pymupdf

from app.jobs.queue import PermanentJobError


def extract_text(pdf: bytes) -> str:
    try:
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:
            text = "\n".join(page.get_text() for page in doc)
    except (pymupdf.FileDataError, RuntimeError) as error:
        raise PermanentJobError(f"Could not read the PDF: {error}")
    if not text.strip():
        raise PermanentJobError(
            "The PDF has no text. It is probably scanned images; upload a PDF with selectable text."
        )
    return text
