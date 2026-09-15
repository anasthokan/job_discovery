"""Convert PDF files to DOCX and DOCX files to PDF."""

from __future__ import annotations

import io
import re
from pathlib import Path

from jobscraper.resume_parser import MAX_UPLOAD_BYTES

PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_WINDOWS_FONTS = (
    Path(r"C:\Windows\Fonts\arial.ttf"),
    Path(r"C:\Windows\Fonts\calibri.ttf"),
    Path(r"C:\Windows\Fonts\segoeui.ttf"),
)
_LINUX_FONTS = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
)


class ConvertError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def pdf_to_docx(data: bytes, filename: str = "") -> bytes:
    _validate_upload(data, filename, expected="pdf")
    from pypdf import PdfReader
    from docx import Document
    from docx.shared import Pt

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ConvertError("Could not read PDF. The file may be corrupt.", 422) from exc

    if not reader.pages:
        raise ConvertError("PDF has no pages.", 422)

    document = Document()
    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    extracted = False
    for index, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if index:
            document.add_page_break()
        paragraphs = _split_paragraphs(text)
        if paragraphs:
            extracted = True
            for paragraph in paragraphs:
                document.add_paragraph(paragraph)
        else:
            document.add_paragraph("")

    if not extracted:
        raise ConvertError(
            "No extractable text in PDF. Image-only / scanned PDFs are not supported (no OCR).",
            422,
        )
    return _save_docx(document)


def docx_to_pdf(data: bytes, filename: str = "") -> bytes:
    _validate_upload(data, filename, expected="docx")
    from docx import Document

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise ConvertError("Could not read DOCX. The file may be corrupt.", 422) from exc

    blocks: list[tuple[str, str]] = []
    for paragraph in document.paragraphs:
        text = (paragraph.text or "").strip()
        style_name = (paragraph.style.name or "").lower() if paragraph.style else ""
        if not text:
            if blocks and blocks[-1][1] != "blank":
                blocks.append(("", "blank"))
            continue
        if "heading 1" in style_name or style_name == "title":
            kind = "h1"
        elif "heading 2" in style_name:
            kind = "h2"
        elif "heading" in style_name:
            kind = "h3"
        else:
            kind = "p"
        blocks.append((text, kind))

    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                blocks.append((" | ".join(cells), "p"))

    if not any(text for text, kind in blocks if kind != "blank"):
        raise ConvertError("DOCX has no extractable text.", 422)

    return _render_pdf(blocks)


def _validate_upload(data: bytes, filename: str, expected: str) -> None:
    if not data:
        raise ConvertError("Empty file.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ConvertError("File is larger than 8 MB.", 413)

    ext = _extension(filename)
    if expected == "pdf":
        if ext == ".doc":
            raise ConvertError("Old .doc is not supported. Save as PDF or DOCX first.", 415)
        if ext and ext != ".pdf":
            raise ConvertError("Upload a PDF file (.pdf).", 415)
        if not ext and not data.startswith(b"%PDF"):
            raise ConvertError("Upload a PDF file (.pdf).", 415)
    elif expected == "docx":
        if ext == ".doc":
            raise ConvertError("Old .doc is not supported. Save as DOCX or convert to PDF first.", 415)
        if ext and ext != ".docx":
            raise ConvertError("Upload a Word file (.docx).", 415)
        if not ext and not _looks_like_docx(data):
            raise ConvertError("Upload a Word file (.docx).", 415)


def _looks_like_docx(data: bytes) -> bool:
    return data[:2] == b"PK"


def _extension(filename: str) -> str:
    name = (filename or "").strip().lower()
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1]


def _split_paragraphs(text: str) -> list[str]:
    cleaned = (text or "").replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace("\u00a0", " ")
    chunks = re.split(r"\n\s*\n", cleaned)
    paragraphs = []
    for chunk in chunks:
        line = re.sub(r"[ \t]+", " ", chunk)
        line = re.sub(r"\n+", "\n", line).strip()
        if line:
            paragraphs.append(line)
    if paragraphs:
        return paragraphs
    fallback = cleaned.strip()
    return [fallback] if fallback else []


def _save_docx(document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _font_file() -> Path | None:
    for path in (*_WINDOWS_FONTS, *_LINUX_FONTS):
        if path.exists():
            return path
    return None


def _render_pdf(blocks: list[tuple[str, str]]) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="Letter", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_margins(18, 18, 18)

    font_path = _font_file()
    family = "Helvetica"
    if font_path is not None:
        family = "Body"
        pdf.add_font("Body", "", str(font_path))
        bold_path = _bold_companion(font_path)
        if bold_path is not None:
            pdf.add_font("Body", "B", str(bold_path))
        else:
            pdf.add_font("Body", "B", str(font_path))

    def write(text: str, size: int, style: str = "", space_after: float = 3) -> None:
        payload = text if font_path is not None else _latin1(text)
        pdf.set_font(family, style=style, size=size)
        pdf.multi_cell(0, size * 0.45 + 2, payload)
        pdf.ln(space_after)

    pdf.set_text_color(15, 23, 42)
    for text, kind in blocks:
        if kind == "blank":
            pdf.ln(3)
            continue
        if kind == "h1":
            write(text, 16, "B", 4)
        elif kind == "h2":
            write(text, 13, "B", 3)
        elif kind == "h3":
            write(text, 12, "B", 2)
        else:
            write(text, 11, "", 2)

    output = pdf.output()
    return bytes(output)


def _bold_companion(regular: Path) -> Path | None:
    name = regular.name.lower()
    mapping = {
        "arial.ttf": "arialbd.ttf",
        "calibri.ttf": "calibrib.ttf",
        "segoeui.ttf": "segoeuib.ttf",
        "dejavusans.ttf": "DejaVuSans-Bold.ttf",
        "liberationsans-regular.ttf": "LiberationSans-Bold.ttf",
    }
    bold_name = mapping.get(name)
    if not bold_name:
        return None
    candidate = regular.with_name(bold_name)
    return candidate if candidate.exists() else None


def _latin1(text: str) -> str:
    return text.encode("latin-1", "replace").decode("latin-1")
