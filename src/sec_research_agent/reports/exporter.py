"""Export a Markdown report to ``.md``, ``.docx`` and ``.pdf``.

Only the Markdown subset the report prompts produce is supported: headings, bold/italic,
bullet and numbered lists, horizontal rules, fenced code and pipe tables.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from docx import Document as DocxDocument
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.text.paragraph import Paragraph as DocxParagraph
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
)

ACCENT_HEX = "#1F3A5F"
RULE_HEX = "#B0B0B0"

_HR = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_ORDERED = re.compile(r"^\d+\.\s+(.*)$")
# Inline emphasis. Underscore variants require non-word neighbours so file names such as
# NVDA_FY_2025_10-K.pdf are left alone.
_BOLD = re.compile(r"\*\*(.+?)\*\*|(?<!\w)__(.+?)__(?!\w)")
_ITALIC = re.compile(
    r"(?<![*\w])\*(?![*\s])(.+?)(?<![*\s])\*(?![*\w])|(?<!\w)_(?![_\s])(.+?)(?<![_\s])_(?!\w)"
)
_CODE = re.compile(r"`([^`]+)`")
_INLINE_TOKEN = re.compile(
    r"(\*\*.+?\*\*|(?<!\w)__.+?__(?!\w)|(?<![*\w])\*(?![*\s]).+?(?<![*\s])\*(?![*\w])"
    r"|(?<!\w)_(?![_\s]).+?(?<![_\s])_(?!\w))"
)


@dataclass(frozen=True)
class ExportedReport:
    """Paths of the exported files."""

    markdown: Path
    docx: Path
    pdf: Path


def export_report(
    markdown_text: str,
    output_dir: Path,
    base_filename: str,
    *,
    brand_name: str = "",
    letterhead_image: Path | None = None,
) -> ExportedReport:
    """Write ``<base_filename>.md/.docx/.pdf`` into ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"{base_filename}.md"
    docx_path = output_dir / f"{base_filename}.docx"
    pdf_path = output_dir / f"{base_filename}.pdf"

    md_path.write_text(markdown_text, encoding="utf-8")
    markdown_to_docx(markdown_text, docx_path, brand_name=brand_name)
    markdown_to_pdf(markdown_text, pdf_path, brand_name=brand_name, letterhead_image=letterhead_image)
    return ExportedReport(md_path, docx_path, pdf_path)


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def _docx_rule(paragraph: DocxParagraph) -> None:
    """Render a paragraph as a thin horizontal rule (bottom border)."""
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, value in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", RULE_HEX[1:])):
        bottom.set(qn(key), value)
    borders.append(bottom)
    p_pr.append(borders)


def _docx_runs(paragraph: DocxParagraph, text: str, color: RGBColor | None = None) -> None:
    """Add ``text`` to ``paragraph`` honouring **bold** and *italic* markers."""
    for part in _INLINE_TOKEN.split(text):
        if not part:
            continue
        bold = italic = False
        if (part.startswith("**") and part.endswith("**")) or (part.startswith("__") and part.endswith("__")):
            bold, part = True, part[2:-2]
        elif len(part) > 2 and part[0] == part[-1] and part[0] in "*_":
            italic, part = True, part[1:-1]
        run = paragraph.add_run(part)
        run.bold, run.italic = bold, italic
        if color is not None:
            run.font.color.rgb = color


def markdown_to_docx(markdown_text: str, output_path: Path, *, brand_name: str = "") -> Path:
    """Convert Markdown to a Word document."""
    doc = DocxDocument()
    accent = RGBColor.from_string(ACCENT_HEX[1:])

    if brand_name:
        header = doc.sections[0].header.paragraphs[0]
        run = header.add_run(brand_name)
        run.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = accent

    for line in markdown_text.splitlines():
        s = line.strip()
        if not s:
            continue
        if _HR.match(s):
            _docx_rule(doc.add_paragraph())
            continue
        heading = _HEADING.match(s)
        if heading:
            level = len(heading.group(1))
            para = doc.add_paragraph(style=f"Heading {level}")
            _docx_runs(para, heading.group(2), accent if level == 1 else None)
        elif s.startswith(("- ", "* ")):
            _docx_runs(doc.add_paragraph(style="List Bullet"), s[2:])
        elif ordered := _ORDERED.match(s):
            _docx_runs(doc.add_paragraph(style="List Number"), ordered.group(1))
        else:
            _docx_runs(doc.add_paragraph(), s)

    doc.save(str(output_path))
    return output_path


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _inline_html(md: str) -> str:
    """Markdown inline formatting -> the HTML subset ReportLab paragraphs understand."""
    text = escape(md)
    text = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
    return _CODE.sub(r"<font face='Courier'>\1</font>", text)


def _pdf_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle("Body", parent=base["BodyText"], fontName="Helvetica", fontSize=10, leading=13)
    styles = {
        "body": body,
        "code": ParagraphStyle(
            "CodeBlock",
            parent=base["Code"],
            fontName="Courier",
            fontSize=9,
            leading=11,
            backColor=colors.whitesmoke,
            leftIndent=6,
            rightIndent=6,
            spaceBefore=4,
            spaceAfter=4,
        ),
        "table": ParagraphStyle("TableText", parent=body, fontName="Courier", fontSize=8.5, leading=10.5),
    }
    sizes = {1: 20, 2: 16, 3: 13, 4: 12, 5: 11, 6: 10}
    for level, size in sizes.items():
        styles[f"h{level}"] = ParagraphStyle(
            f"H{level}",
            parent=base["Heading1"] if level == 1 else base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=size,
            leading=size + 4,
            spaceBefore=12 if level <= 2 else 8,
            spaceAfter=8 if level <= 2 else 4,
            textColor=colors.HexColor(ACCENT_HEX) if level == 1 else colors.black,
        )
    return styles


def _page_decorator(brand_name: str, letterhead_image: Path | None) -> Callable[[Canvas, Any], None]:
    """Return an ``onPage`` callback drawing the header (brand or image) and page footer."""
    image = ImageReader(str(letterhead_image)) if letterhead_image and letterhead_image.exists() else None
    generated = datetime.now().strftime("%B %d, %Y")

    def draw(canvas: Canvas, doc: Any) -> None:
        width, height = LETTER
        canvas.saveState()
        if image is not None:
            img_w, img_h = image.getSize()
            draw_h = img_h * width / float(img_w)
            canvas.drawImage(image, 0, height - draw_h, width=width, height=draw_h, mask="auto")
        elif brand_name:
            canvas.setFillColor(colors.HexColor(ACCENT_HEX))
            canvas.setFont("Helvetica-Bold", 9)
            canvas.drawString(inch, height - 0.6 * inch, brand_name)
            canvas.setStrokeColor(colors.HexColor(ACCENT_HEX))
            canvas.setLineWidth(0.8)
            canvas.line(inch, height - 0.68 * inch, width - inch, height - 0.68 * inch)
        canvas.setFillColor(colors.grey)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(inch, 0.55 * inch, generated)
        canvas.drawRightString(width - inch, 0.55 * inch, f"Page {doc.page}")
        canvas.restoreState()

    return draw


def markdown_to_pdf(
    markdown_text: str, output_path: Path, *, brand_name: str = "", letterhead_image: Path | None = None
) -> Path:
    """Convert Markdown to PDF with ReportLab (no Word installation required)."""
    styles = _pdf_styles()
    story: list[Any] = []
    bullets: list[ListItem] = []
    code: list[str] | None = None

    def flush_bullets() -> None:
        if bullets:
            story.append(ListFlowable(list(bullets), bulletType="bullet", leftIndent=12, bulletFontSize=8))
            story.append(Spacer(1, 4))
            bullets.clear()

    lines = markdown_text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        i += 1

        if s.startswith("```"):
            if code is None:
                flush_bullets()
                code = []
            else:
                story.append(Preformatted("\n".join(code), styles["code"]))
                code = None
            continue
        if code is not None:
            code.append(raw)
            continue
        if not s:
            flush_bullets()
            story.append(Spacer(1, 6))
            continue
        if _HR.match(s):
            flush_bullets()
            story.append(
                HRFlowable(
                    width="100%", thickness=0.7, color=colors.HexColor(RULE_HEX), spaceBefore=6, spaceAfter=6
                )
            )
            continue
        if heading := _HEADING.match(s):
            flush_bullets()
            story.append(Paragraph(_inline_html(heading.group(2)), styles[f"h{len(heading.group(1))}"]))
            continue
        if s.count("|") >= 2:
            flush_bullets()
            table = [s]
            while i < len(lines) and lines[i].strip().count("|") >= 2:
                table.append(lines[i].strip())
                i += 1
            story.append(Preformatted("\n".join(table), styles["table"]))
            story.append(Spacer(1, 6))
            continue
        item = s[2:] if s.startswith(("- ", "* ")) else None
        if item is None and (ordered := _ORDERED.match(s)):
            item = ordered.group(1)
        if item is not None:
            bullets.append(ListItem(Paragraph(_inline_html(item), styles["body"])))
            continue
        flush_bullets()
        story.append(Paragraph(_inline_html(s), styles["body"]))

    if code:
        story.append(Preformatted("\n".join(code), styles["code"]))
    flush_bullets()

    top_margin = 1.4 * inch if letterhead_image else inch
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=LETTER,
        leftMargin=inch,
        rightMargin=inch,
        topMargin=top_margin,
        bottomMargin=inch,
        title=output_path.stem,
        author=brand_name or None,
    )
    on_page = _page_decorator(brand_name, letterhead_image)
    doc.build(story or [Spacer(1, 1)], onFirstPage=on_page, onLaterPages=on_page)
    return output_path
