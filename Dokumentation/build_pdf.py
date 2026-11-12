#!/usr/bin/env python3
"""Build the German project documentation PDF from its Markdown source.

Install the isolated documentation dependency with:
    python -m pip install -r requirements-pdf.txt
Then run:
    python build_pdf.py
"""
from __future__ import annotations

import html
import math
import os
import re
import textwrap
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    LongTable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "Projekt-Dokumentation.md"
OUTPUT = ROOT / "Projekt-Dokumentation.pdf"
# Keep the PDF's metadata aligned with the documented review date and builds reproducible.
os.environ.setdefault("SOURCE_DATE_EPOCH", "1791676800")  # 2026-10-11 00:00:00 UTC

NAVY = colors.HexColor("#14263D")
INK = colors.HexColor("#243447")
TEAL = colors.HexColor("#007F86")
CYAN = colors.HexColor("#35C2C8")
PALE = colors.HexColor("#EEF5F8")
PALE_BLUE = colors.HexColor("#F2F6FA")
GRID = colors.HexColor("#D4DEE5")
MUTED = colors.HexColor("#5B6B78")
WHITE = colors.white


def _find_font(filename: str) -> Path | None:
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu") / filename,
        Path("/usr/share/fonts/dejavu") / filename,
        Path("/Library/Fonts") / filename,
        Path("C:/Windows/Fonts") / filename,
        Path(__file__).resolve().parent / "fonts" / filename,
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def register_fonts() -> None:
    regular = _find_font("DejaVuSans.ttf")
    bold = _find_font("DejaVuSans-Bold.ttf")
    mono = _find_font("DejaVuSansMono.ttf")
    if regular and bold and mono:
        pdfmetrics.registerFont(TTFont("DocSans", str(regular)))
        pdfmetrics.registerFont(TTFont("DocSans-Bold", str(bold)))
        pdfmetrics.registerFont(TTFont("DocMono", str(mono)))
        pdfmetrics.registerFontFamily(
            "DocSans", normal="DocSans", bold="DocSans-Bold",
            italic="DocSans", boldItalic="DocSans-Bold",
        )
    else:
        # Helvetica is a portable fallback; DejaVu is preferred for full Unicode.
        pdfmetrics.registerFontFamily(
            "Helvetica", normal="Helvetica", bold="Helvetica-Bold",
            italic="Helvetica-Oblique", boldItalic="Helvetica-BoldOblique",
        )


def font_name(kind: str) -> str:
    if "DocSans" in pdfmetrics.getRegisteredFontNames():
        return {"regular": "DocSans", "bold": "DocSans-Bold", "mono": "DocMono"}[kind]
    return {"regular": "Helvetica", "bold": "Helvetica-Bold", "mono": "Courier"}[kind]


register_fonts()
FONT = font_name("regular")
FONT_BOLD = font_name("bold")
FONT_MONO = font_name("mono")

STYLES = getSampleStyleSheet()
STYLES.add(ParagraphStyle(
    name="BodyDE", parent=STYLES["BodyText"], fontName=FONT, fontSize=8.8,
    leading=13.2, textColor=INK, spaceAfter=5.5, alignment=TA_LEFT,
    splitLongWords=1, allowWidows=0, allowOrphans=0,
))
STYLES.add(ParagraphStyle(
    name="H1DE", parent=STYLES["Heading1"], fontName=FONT_BOLD,
    fontSize=17, leading=21, textColor=NAVY, spaceBefore=15, spaceAfter=7,
    keepWithNext=True, pageBreakBefore=False,
))
STYLES.add(ParagraphStyle(
    name="H2DE", parent=STYLES["Heading2"], fontName=FONT_BOLD,
    fontSize=11.5, leading=15, textColor=TEAL, spaceBefore=10, spaceAfter=4,
    keepWithNext=True,
))
STYLES.add(ParagraphStyle(
    name="H3DE", parent=STYLES["Heading3"], fontName=FONT_BOLD,
    fontSize=9.5, leading=12.5, textColor=NAVY, spaceBefore=7, spaceAfter=3,
    keepWithNext=True,
))
STYLES.add(ParagraphStyle(
    name="BulletDE", parent=STYLES["BodyDE"], leftIndent=14, firstLineIndent=-10,
    bulletIndent=0, bulletFontName=FONT, bulletFontSize=7.5, bulletColor=TEAL,
    spaceAfter=3.5,
))
STYLES.add(ParagraphStyle(
    name="TableBodyDE", parent=STYLES["BodyDE"], fontSize=7.3, leading=9.7,
    spaceAfter=0, textColor=INK,
))
STYLES.add(ParagraphStyle(
    name="TableHeadDE", parent=STYLES["TableBodyDE"], fontName=FONT_BOLD,
    textColor=WHITE,
))
STYLES.add(ParagraphStyle(
    name="CodeDE", fontName=FONT_MONO, fontSize=7.0, leading=9.2,
    textColor=INK, backColor=PALE_BLUE, borderColor=GRID, borderWidth=0.4,
    borderPadding=6, leftIndent=0, rightIndent=0, spaceBefore=2, spaceAfter=7,
    splitLongWords=1,
))
STYLES.add(ParagraphStyle(
    name="CoverTitleDE", fontName=FONT_BOLD, fontSize=27, leading=34,
    textColor=NAVY, alignment=TA_LEFT, spaceAfter=12,
))
STYLES.add(ParagraphStyle(
    name="CoverSubtitleDE", fontName=FONT, fontSize=14, leading=20,
    textColor=TEAL, alignment=TA_LEFT, spaceAfter=18,
))
STYLES.add(ParagraphStyle(
    name="CoverMetaDE", fontName=FONT, fontSize=9.5, leading=15,
    textColor=MUTED, spaceAfter=3,
))
STYLES.add(ParagraphStyle(
    name="SmallDE", fontName=FONT, fontSize=7.4, leading=10,
    textColor=MUTED, spaceAfter=2,
))
STYLES.add(ParagraphStyle(
    name="TOC1DE", fontName=FONT_BOLD, fontSize=9.2, leading=13,
    textColor=NAVY, leftIndent=0, firstLineIndent=0, spaceBefore=3,
))
STYLES.add(ParagraphStyle(
    name="TOC2DE", fontName=FONT, fontSize=8.2, leading=11,
    textColor=INK, leftIndent=14, firstLineIndent=0, spaceBefore=1,
))


def inline_markup(value: str) -> str:
    """Convert the small inline-Markdown subset used in the document to XML."""
    token_re = re.compile(
        r"\[([^\]]+)\]\(([^)]+)\)|`([^`]+)`|\*\*(.+?)\*\*|(?<!\*)\*(?!\*)(.+?)(?<!\*)\*"
    )
    out: list[str] = []
    pos = 0
    for match in token_re.finditer(value):
        out.append(escape(value[pos:match.start()]))
        label, href, code, bold, italic = match.groups()
        if label is not None:
            # Link labels may themselves contain inline code (for example
            # ``[`../LICENSE`](../LICENSE)``); preserve that markup recursively.
            safe_label = inline_markup(label)
            if re.match(r"^https?://", href, re.I):
                safe_href = html.escape(href, quote=True)
                out.append(f'<link href="{safe_href}" color="#007F86">{safe_label}</link>')
            else:
                out.append(f'<font color="#007F86">{safe_label}</font>')
        elif code is not None:
            out.append(f'<font name="{FONT_MONO}" size="7.7" color="#243447">{escape(code)}</font>')
        elif bold is not None:
            out.append(f"<b>{inline_markup(bold)}</b>")
        elif italic is not None:
            out.append(f"<i>{inline_markup(italic)}</i>")
        pos = match.end()
    out.append(escape(value[pos:]))
    return "".join(out)


def paragraph(text: str, style=STYLES["BodyDE"]) -> Paragraph:
    return Paragraph(inline_markup(text), style)


def split_table_row(line: str) -> list[str]:
    value = line.strip().strip("|")
    return [part.strip() for part in value.split("|")]


def is_table_rule(line: str) -> bool:
    cells = split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def make_table(rows: list[list[str]], available_width: float):
    if not rows:
        return Spacer(1, 1)
    cols = max(len(row) for row in rows)
    normalized = [row + [""] * (cols - len(row)) for row in rows]
    matrix = []
    for row_index, row in enumerate(normalized):
        style = STYLES["TableHeadDE"] if row_index == 0 else STYLES["TableBodyDE"]
        matrix.append([Paragraph(inline_markup(cell), style) for cell in row])
    if cols == 2:
        proportions = [0.34, 0.66]
    elif cols == 3 and rows[0][0].strip().casefold() == "endpunkt":
        # Give long API routes a wide first column so monospaced parameters
        # such as bbox=N,S,E,W and flight=<callsign> stay intact.
        proportions = [0.45, 0.20, 0.35]
    elif cols == 3:
        proportions = [0.24, 0.24, 0.52]
    else:
        proportions = [1.0 / cols] * cols
    widths = [available_width * proportion for proportion in proportions]
    table = LongTable(
        matrix, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1,
        spaceBefore=3, spaceAfter=8,
    )
    table.setStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, PALE]),
        ("GRID", (0, 0), (-1, -1), 0.35, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ])
    return table


class ArchitectureDiagram(Flowable):
    """Compact vector system diagram, drawn directly into the PDF."""

    def __init__(self):
        super().__init__()
        self.height = 150

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        return self.width, self.height

    def _box(self, c, x, y, w, h, title, subtitle, fill):
        c.setFillColor(fill)
        c.setStrokeColor(GRID)
        c.setLineWidth(0.8)
        c.roundRect(x, y, w, h, 7, stroke=1, fill=1)
        c.setFillColor(NAVY)
        c.setFont(FONT_BOLD, 9.0)
        c.drawCentredString(x + w / 2, y + h - 16, title)
        c.setFillColor(MUTED)
        c.setFont(FONT, 7.2)
        for i, line in enumerate(subtitle.split("\n")):
            c.drawCentredString(x + w / 2, y + h - 28 - i * 9, line)

    def _arrow(self, c, x1, y1, x2, y2, color=TEAL, dashed=False):
        c.saveState()
        c.setStrokeColor(color)
        c.setFillColor(color)
        c.setLineWidth(1.25)
        if dashed:
            c.setDash(3, 2)
        c.line(x1, y1, x2, y2)
        angle = math.atan2(y2 - y1, x2 - x1)
        size = 4.5
        left = (x2 - size * math.cos(angle - math.pi / 6), y2 - size * math.sin(angle - math.pi / 6))
        right = (x2 - size * math.cos(angle + math.pi / 6), y2 - size * math.sin(angle + math.pi / 6))
        p = c.beginPath()
        p.moveTo(x2, y2)
        p.lineTo(*left)
        p.lineTo(*right)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
        c.restoreState()

    def draw(self):
        c = self.canv
        c.saveState()
        scale = min(self.width / 500.0, 1.0)
        c.translate((self.width - 500 * scale) / 2, 0)
        c.scale(scale, scale)
        self._box(c, 0, 94, 130, 47, "Qt-Client", "PySide6 · Engine · Leaflet", PALE_BLUE)
        self._box(c, 185, 94, 130, 47, "PHP-Webrelay", "Traffic-API · Status · Mesh", colors.HexColor("#E8F7F4"))
        self._box(c, 370, 94, 130, 47, "C++-Collector", "Demand · Regionalabfragen", colors.HexColor("#FFF3E5"))
        self._box(c, 0, 10, 130, 47, "Microsoft Flight Simulator", "SimConnect · Spielerposition · KI", colors.HexColor("#F3EEF9"))
        self._box(c, 185, 10, 315, 47, "Aktive Upstream-Quellen", "adsb.lol  ·  adsb.fi Open Data  ·  OpenSky Network", PALE)

        self._arrow(c, 130, 126, 185, 126, TEAL)
        self._arrow(c, 185, 109, 130, 109, NAVY)
        self._arrow(c, 315, 126, 370, 126, colors.HexColor("#CC7A19"))
        self._arrow(c, 370, 109, 315, 109, colors.HexColor("#CC7A19"))
        self._arrow(c, 435, 94, 435, 59, colors.HexColor("#CC7A19"))
        self._arrow(c, 65, 94, 65, 59, NAVY)
        self._arrow(c, 125, 94, 185, 59, colors.HexColor("#7A5AA6"), dashed=True)

        c.setFillColor(MUTED)
        c.setFont(FONT, 7.0)
        c.drawCentredString(157.5, 134, "Traffic / HB")
        c.drawCentredString(157.5, 99, "JSON / Status")
        c.drawCentredString(342.5, 134, "Demand")
        c.drawCentredString(342.5, 99, "Token-Push")
        c.drawString(72, 73, "SimConnect")
        c.drawString(130, 72, "Direkt-Fallback (letzte Stufe)")
        c.drawString(442, 73, "Upstream API")
        c.restoreState()


class ProjectDocTemplate(BaseDocTemplate):
    def __init__(self, filename: str):
        page_w, page_h = A4
        left = 18 * mm
        right = 18 * mm
        top = 18 * mm
        bottom = 17 * mm
        frame = Frame(
            left, bottom, page_w - left - right, page_h - top - bottom,
            id="content", leftPadding=0, rightPadding=0,
            topPadding=0, bottomPadding=0,
        )
        super().__init__(
            filename, pagesize=A4, leftMargin=left, rightMargin=right,
            topMargin=top, bottomMargin=bottom,
            title="MSFS Live Traffic Utility – technische Projektdokumentation",
            author="PhW3130 / Live Traffic contributors",
            subject="Technische Dokumentation des Projekts MSFS Live Traffic Utility",
        )
        self.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=self._draw_page)])
        self._heading_counter = 0

    def beforeDocument(self):
        # multiBuild repeats layout passes for the live table of contents;
        # heading IDs must remain identical between passes.
        super().beforeDocument()
        self._heading_counter = 0

    def _draw_page(self, canvas, doc):
        canvas.saveState()
        page_w, page_h = A4
        if doc.page == 1:
            canvas.setFillColor(colors.HexColor("#F6FAFC"))
            canvas.rect(0, 0, page_w, page_h, stroke=0, fill=1)
            canvas.setFillColor(NAVY)
            canvas.rect(0, page_h - 13 * mm, page_w, 13 * mm, stroke=0, fill=1)
            canvas.setFillColor(CYAN)
            canvas.rect(0, page_h - 13 * mm, 7 * mm, 13 * mm, stroke=0, fill=1)
        else:
            canvas.setStrokeColor(GRID)
            canvas.setLineWidth(0.5)
            canvas.line(18 * mm, page_h - 13 * mm, page_w - 18 * mm, page_h - 13 * mm)
            canvas.setFont(FONT, 7)
            canvas.setFillColor(MUTED)
            canvas.drawString(18 * mm, page_h - 10 * mm, "MSFS Live Traffic Utility · Technische Projektdokumentation")
            canvas.line(18 * mm, 13 * mm, page_w - 18 * mm, 13 * mm)
            canvas.setFont(FONT, 7)
            canvas.drawString(18 * mm, 8 * mm, "Projektstand: 11. Oktober 2026")
            canvas.drawRightString(page_w - 18 * mm, 8 * mm, f"Seite {doc.page}")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if not isinstance(flowable, Paragraph):
            return
        style_name = flowable.style.name
        if style_name not in ("H1DE", "H2DE"):
            return
        level = 0 if style_name == "H1DE" else 1
        text = flowable.getPlainText()
        self._heading_counter += 1
        key = f"section-{self._heading_counter}"
        self.canv.bookmarkPage(key)
        try:
            self.canv.addOutlineEntry(text, key, level=level, closed=False)
        except ValueError:
            # The PDF outline has a maximum nesting depth of 0/1 here.
            pass
        self.notify("TOCEntry", (level, text, self.page, key))


def _wrap_code_line(line: str, max_width: int = 108) -> list[str]:
    if len(line) <= max_width:
        return [line]
    indent = len(line) - len(line.lstrip())
    chunks = textwrap.wrap(
        line, width=max_width, subsequent_indent=" " * min(indent + 2, 12),
        break_long_words=True, break_on_hyphens=False,
    )
    return chunks or [line]


def _make_code_block(code_lines: list[str]):
    escaped_lines = []
    for line in code_lines:
        for chunk in _wrap_code_line(line):
            escaped_lines.append(chunk)
    code = "\n".join(escaped_lines)
    return Preformatted(code, STYLES["CodeDE"], maxLineLength=112, splitChars=" \t")


def _make_list(items: list[str], ordered: bool) -> list[Paragraph]:
    """Build explicit paragraph bullets so ordered lists increment reliably."""
    flowables = []
    for number, item in enumerate(items, start=1):
        bullet = f"{number}." if ordered else "•"
        flowables.append(Paragraph(
            inline_markup(item), STYLES["BulletDE"], bulletText=bullet
        ))
    return flowables


def parse_markdown(markdown: str, available_width: float):
    if "[[TOC]]" not in markdown:
        raise ValueError("Markdown source is missing [[TOC]] marker")
    body = markdown.split("[[TOC]]", 1)[1]
    lines = body.splitlines()
    story = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped == "[[ARCHITECTURE_DIAGRAM]]":
            story.extend([ArchitectureDiagram(), Spacer(1, 7)])
            i += 1
            continue
        if stripped.startswith("```"):
            i += 1
            block = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            story.append(_make_code_block(block))
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                current = lines[i].strip()
                if not is_table_rule(current):
                    rows.append(split_table_row(current))
                i += 1
            story.extend([make_table(rows, available_width), Spacer(1, 4)])
            continue
        if stripped.startswith("#### "):
            story.append(paragraph(stripped[5:], STYLES["H3DE"]))
            i += 1
            continue
        if stripped.startswith("### "):
            story.append(paragraph(stripped[4:], STYLES["H2DE"]))
            i += 1
            continue
        if stripped.startswith("## "):
            story.append(paragraph(stripped[3:], STYLES["H1DE"]))
            i += 1
            continue
        if stripped in ("---", "***", "___"):
            story.append(Spacer(1, 4))
            i += 1
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            items = []
            while i < len(lines):
                current = lines[i].strip()
                if current.startswith("- ") or current.startswith("* "):
                    items.append(current[2:].strip())
                    i += 1
                elif current.startswith("  ") and items:
                    items[-1] += " " + current.strip()
                    i += 1
                else:
                    break
            story.extend(_make_list(items, ordered=False))
            continue
        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            story.extend(_make_list(items, ordered=True))
            continue
        if stripped.startswith("> "):
            text = stripped[2:]
            story.append(Paragraph(
                inline_markup(text),
                ParagraphStyle("QuoteDE", parent=STYLES["BodyDE"],
                               leftIndent=10, borderColor=TEAL, borderWidth=1,
                               borderPadding=5, textColor=MUTED),
            ))
            i += 1
            continue

        para_lines = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith(("## ", "### ", "#### ", "```", "|", "- ", "* ", "> ", "[[ARCHITECTURE_DIAGRAM]]"))
                    or re.match(r"^\d+\.\s+", nxt)):
                break
            para_lines.append(nxt)
            i += 1
        story.append(paragraph(" ".join(para_lines)))
    return story


def build_pdf() -> None:
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    markdown = SOURCE.read_text(encoding="utf-8")
    if FONT == "Helvetica":
        # The built-in PDF fonts do not cover the arrows used in the source.
        markdown = markdown.replace("→", "->").replace("↔", "<->")
    page_w, _ = A4
    usable_width = page_w - 36 * mm

    toc = TableOfContents()
    toc.levelStyles = [STYLES["TOC1DE"], STYLES["TOC2DE"]]
    toc.dotsMinLevel = 0

    story = [
        Spacer(1, 36 * mm),
        Paragraph("MSFS Live Traffic Utility", STYLES["CoverTitleDE"]),
        Paragraph("Technische Projektdokumentation", STYLES["CoverSubtitleDE"]),
        Spacer(1, 2 * mm),
        Paragraph("Desktopanwendung · ADS-B-Livekarte · MSFS/SimConnect · PHP-Proxy · C++-Collector", STYLES["CoverMetaDE"]),
        Spacer(1, 13 * mm),
        Paragraph("Dokumentstand: 11. Oktober 2026", STYLES["CoverMetaDE"]),
        Paragraph("Zielgruppe: Anwender, Entwickler und Proxy-Betreiber", STYLES["CoverMetaDE"]),
        Spacer(1, 18 * mm),
        Paragraph(
            "Diese Unterlage beschreibt Bedienung, Datenfluss, Modularchitektur, "
            "Netzwerk-Failover, Einstellungen, Tests, Release-Build und den "
            "vollständigen Self-Hosting-Betrieb.",
            ParagraphStyle("CoverSummaryDE", parent=STYLES["BodyDE"],
                           fontSize=10.5, leading=16, textColor=INK, rightIndent=24 * mm),
        ),
        Spacer(1, 35 * mm),
        Paragraph("Quelle: Projekt-Repository PhW3130/MSFS-Live-Traffic-Utility-PUBLIC-BETA", STYLES["SmallDE"]),
        PageBreak(),
        Paragraph("Inhaltsverzeichnis", STYLES["H1DE"]),
        toc,
        PageBreak(),
    ]
    story.extend(parse_markdown(markdown, usable_width))

    # The PDF embeds a subset of the available DejaVu fonts. Include its full
    # license notice in the PDF itself so it travels with this standalone file.
    license_path = ROOT / "DejaVu-Font-LICENSE.txt"
    license_paragraphs = re.split(r"\n\s*\n", license_path.read_text(encoding="utf-8"))
    story.extend([
        PageBreak(),
        Paragraph("Anhang A. Lizenzhinweis für eingebettete PDF-Schriften", STYLES["H1DE"]),
        paragraph(
            "Der PDF-Build bettet bei verfügbaren Systemschriften Teilmengen von DejaVu Sans "
            "und DejaVu Sans Mono ein. Der folgende vollständige Lizenzhinweis wird deshalb "
            "mit diesem Dokument weitergegeben.",
            STYLES["BodyDE"],
        ),
    ])
    for license_text in license_paragraphs[1:]:
        cleaned = " ".join(line.strip() for line in license_text.splitlines() if line.strip())
        if cleaned:
            story.append(Paragraph(escape(cleaned), STYLES["BodyDE"]))

    doc = ProjectDocTemplate(str(OUTPUT))
    doc.multiBuild(story, maxPasses=6)
    print(f"PDF written: {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    build_pdf()
