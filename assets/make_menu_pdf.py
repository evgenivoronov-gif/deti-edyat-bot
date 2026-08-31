"""Красивое PDF-меню на один день (понедельник) — из xlsx-меню сада/школы."""

import re
from pathlib import Path

import openpyxl
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

HERE = Path(__file__).parent
FILES_DIR = HERE.parent / "files"

GREEN = colors.HexColor("#8FC98A")
GREEN_DARK = colors.HexColor("#5B9A56")
CORAL = colors.HexColor("#E9949E")
CREAM = colors.HexColor("#FFFBF4")
TEXT_DARK = colors.HexColor("#3A3A3A")
GRAY = colors.HexColor("#84807A")
LINE = colors.HexColor("#EDE7D8")

pdfmetrics.registerFont(TTFont("PTSans", str(HERE / "PTSans-Regular.ttf")))
pdfmetrics.registerFont(TTFont("PTSans-Bold", str(HERE / "PTSans-Bold.ttf")))
pdfmetrics.registerFont(TTFont("PTSans-Italic", str(HERE / "PTSans-Italic.ttf")))

MAJOR_LABELS = {"ЗАВТРАК", "ВТОРОЙ ЗАВТРАК", "ОБЕД", "ПОЛДНИК", "УЖИН"}
COMPANY_PHONE = "+7 (911) 920-12-94"
LOGO_PATH = HERE / "logo_transparent.png"
LOGO_IMAGE = ImageReader(str(LOGO_PATH))


def load_monday_groups(xlsx_path: Path, sheet_name: str = "неделя 1"):
    """Возвращает список блоков-приёмов пищи (ЗАВТРАК/ОБЕД/...), каждый со
    своими подразделами (СУП/ГАРНИР/... или None) и блюдами внутри."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb[sheet_name]

    groups = []
    current_group = None
    current_child = None
    for row in ws.iter_rows(min_row=3, values_only=True):
        label = row[1] if len(row) > 1 else None
        dish = row[2] if len(row) > 2 else None
        portion = row[3] if len(row) > 3 else None

        if label:
            label = str(label).strip()
            if label in MAJOR_LABELS:
                current_group = {"label": label, "children": []}
                groups.append(current_group)
                current_child = {"label": None, "entries": []}
                current_group["children"].append(current_child)
            elif current_group is not None:
                current_child = {"label": label, "entries": []}
                current_group["children"].append(current_child)
            if dish and current_child is not None:
                current_child["entries"].append((str(dish).strip(), portion))
        elif dish and current_child is not None:
            current_child["entries"].append((str(dish).strip(), portion))

    for group in groups:
        group["children"] = [c for c in group["children"] if c["entries"]]
    return [g for g in groups if g["children"]]


def split_dish(dish_text: str):
    parts = dish_text.split("\n", 1)
    name = parts[0].strip()
    extra = parts[1].strip().replace("\n", ", ") if len(parts) > 1 else ""
    extra = re.sub(r",\s*,", ",", extra)
    return name, extra


def format_portion(portion) -> str:
    if portion is None:
        return ""
    text = str(portion).strip()
    if not text:
        return ""
    if "шт" in text or "г" in text:
        return text
    try:
        value = float(text)
    except ValueError:
        return text + " г"
    return text + (" шт" if value <= 5 else " г")


def header_footer(canvas, doc, institution_label: str):
    canvas.saveState()
    page_w, page_h = A4

    banner_h = 22 * mm

    logo_size = 17 * mm
    logo_y = page_h - banner_h + (banner_h - logo_size) / 2
    canvas.drawImage(
        LOGO_IMAGE,
        16 * mm,
        logo_y,
        width=logo_size,
        height=logo_size,
        mask="auto",
        preserveAspectRatio=True,
    )

    canvas.setFillColor(TEXT_DARK)
    canvas.setFont("PTSans", 8.5)
    canvas.drawString(16 * mm + logo_size + 4 * mm, page_h - 12 * mm, "ДЕТИ ЕДЯТ")
    canvas.drawString(16 * mm + logo_size + 4 * mm, page_h - 16.5 * mm, "Доставка питания в сады и школы")

    canvas.setFont("PTSans-Bold", 11)
    canvas.drawRightString(page_w - 16 * mm, page_h - 10 * mm, "Меню — пример одного дня")
    canvas.setFont("PTSans", 8.5)
    canvas.drawRightString(page_w - 16 * mm, page_h - 16 * mm, institution_label)

    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.6)
    canvas.line(16 * mm, page_h - banner_h, page_w - 16 * mm, page_h - banner_h)

    canvas.setFillColor(GRAY)
    canvas.setFont("PTSans-Italic", 7.5)
    footer_text = (
        f"Показан пример одного дня меню. Полное меню на неделю — по запросу: {COMPANY_PHONE}"
    )
    canvas.drawCentredString(page_w / 2, 9 * mm, footer_text)
    canvas.setFont("PTSans", 7.5)
    canvas.drawCentredString(page_w / 2, 5.5 * mm, "deti-edyat.ru")

    canvas.restoreState()


def build_story(groups):
    major_style = ParagraphStyle(
        "major",
        fontName="PTSans-Bold",
        fontSize=11.5,
        textColor=colors.white,
        leading=14,
    )
    minor_style = ParagraphStyle(
        "minor",
        fontName="PTSans-Bold",
        fontSize=8.7,
        textColor=CORAL,
        leading=10.5,
    )
    dish_style = ParagraphStyle(
        "dish",
        fontName="PTSans-Bold",
        fontSize=8.6,
        textColor=TEXT_DARK,
        leading=10.5,
    )
    extra_style = ParagraphStyle(
        "extra",
        fontName="PTSans-Italic",
        fontSize=6.8,
        textColor=GRAY,
        leading=8.4,
    )
    portion_style = ParagraphStyle(
        "portion",
        fontName="PTSans-Bold",
        fontSize=8.2,
        textColor=GREEN_DARK,
        alignment=TA_RIGHT,
        leading=10.5,
    )

    col_w = [144 * mm, 30 * mm]
    card_w = col_w[0] + col_w[1]

    story = []
    for group in groups:
        rows = [[Paragraph(group["label"], major_style), ""]]
        row_kinds = ["header"]

        for child in group["children"]:
            if child["label"]:
                rows.append([Paragraph(child["label"], minor_style), ""])
                row_kinds.append("sub")
            for dish_text, portion in child["entries"]:
                name, extra = split_dish(dish_text)
                cell = [Paragraph(name, dish_style)]
                if extra:
                    cell.append(Paragraph(extra, extra_style))
                rows.append([cell, Paragraph(format_portion(portion), portion_style)])
                row_kinds.append("dish")

        table = Table(rows, colWidths=col_w)
        style = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0), GREEN),
            ("SPAN", (0, 0), (-1, 0)),
            ("BOX", (0, 0), (-1, -1), 0.8, GREEN),
            ("ROUNDEDCORNERS", [5, 5, 5, 5]),
            ("BACKGROUND", (0, 1), (-1, -1), CREAM),
            ("TOPPADDING", (0, 0), (-1, 0), 2.6),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 2.6),
            ("LEFTPADDING", (0, 0), (-1, 0), 7),
            ("LEFTPADDING", (0, 1), (0, -1), 7),
            ("RIGHTPADDING", (-1, 0), (-1, -1), 7),
        ]
        for i, kind in enumerate(row_kinds):
            if kind == "sub":
                style.append(("SPAN", (0, i), (-1, i)))
                style.append(("TOPPADDING", (0, i), (-1, i), 1.6))
                style.append(("BOTTOMPADDING", (0, i), (-1, i), 0.6))
            elif kind == "dish":
                style.append(("TOPPADDING", (0, i), (-1, i), 1.1))
                style.append(("BOTTOMPADDING", (0, i), (-1, i), 1.1))
                if i < len(row_kinds) - 1 and row_kinds[i + 1] == "dish":
                    style.append(("LINEBELOW", (0, i), (-1, i), 0.3, LINE))
        table.setStyle(TableStyle(style))

        story.append(KeepTogether(table) if len(rows) <= 3 else table)
        story.append(Spacer(1, 3 * mm))

    return story


def make_pdf(xlsx_name: str, institution_label: str, out_name: str):
    groups = load_monday_groups(HERE / xlsx_name)
    story = build_story(groups)

    out_path = FILES_DIR / out_name
    doc = BaseDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=27 * mm,
        bottomMargin=13 * mm,
        title=f"Меню — пример одного дня — {institution_label}",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    template = PageTemplate(
        id="menu",
        frames=[frame],
        onPage=lambda c, d: header_footer(c, d, institution_label),
    )
    doc.addPageTemplates([template])
    doc.build(story)
    print("saved", out_path)


if __name__ == "__main__":
    make_pdf("menu_kindergarten.xlsx", "Детский сад", "Меню_детский_сад.pdf")
    make_pdf("menu_school.xlsx", "Школа", "Меню_школа.pdf")
