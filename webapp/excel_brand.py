"""تنسيق موحّد لملفات Excel — شعار وترويسة شركة ركاز."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

BRAND_DIR = Path(__file__).resolve().parent / "static" / "brand"
LOGO_PATH = BRAND_DIR / "rekaz.png"

COMPANY_NAME = "شركة ركاز الإنجاز للمقاولات"
OFFICE_NAME = "مكتب خدمات خريص"

# ألوان متوافقة مع هوية الواجهة
ACCENT = "8A7349"
INK = "1A1814"
MUTED = "6B655C"
HEADER_BG = "8A7349"
HEADER_FG = "FFFFFF"
ALT_ROW = "F7F4EF"
TIP_BG = "FBFAF7"

_THIN = Border(
    left=Side(style="thin", color="DDD2C0"),
    right=Side(style="thin", color="DDD2C0"),
    top=Side(style="thin", color="DDD2C0"),
    bottom=Side(style="thin", color="DDD2C0"),
)


def logo_file() -> Path | None:
    return LOGO_PATH if LOGO_PATH.is_file() else None


def _add_logo(ws: Worksheet, anchor: str = "A1", max_height: int = 56) -> None:
    path = logo_file()
    if not path:
        return
    img = XLImage(str(path))
    # حافظ على النسبة مع ارتفاع مناسب للترويسة
    if img.height and img.height > max_height:
        ratio = max_height / float(img.height)
        img.height = max_height
        img.width = int(img.width * ratio)
    ws.add_image(img, anchor)


def apply_brand_header(
    ws: Worksheet,
    *,
    title: str,
    ncol: int,
    subtitle: str | None = None,
    as_of: datetime | None = None,
) -> int:
    """
    يكتب ترويسة احترافية (شعار + عنوان + شركة/تاريخ).
    يعيد رقم صف رؤوس الأعمدة (يبدأ من 1).
    """
    as_of = as_of or datetime.now()
    last_col = get_column_letter(max(ncol, 3))
    sub = subtitle or f"{COMPANY_NAME} — {OFFICE_NAME}"
    date_line = f"التاريخ: {as_of.strftime('%Y-%m-%d %H:%M')}"

    ws.sheet_view.rightToLeft = True
    ws.row_dimensions[1].height = 48
    ws.row_dimensions[2].height = 22
    ws.row_dimensions[3].height = 8

    _add_logo(ws, "A1", max_height=52)

    # العنوان بجانب الشعار
    ws.merge_cells(f"B1:{last_col}1")
    title_cell = ws["B1"]
    title_cell.value = title
    title_cell.font = Font(name="Arial", size=16, bold=True, color=INK)
    title_cell.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)

    ws.merge_cells(f"B2:{last_col}2")
    sub_cell = ws["B2"]
    sub_cell.value = f"{sub}  |  {date_line}"
    sub_cell.font = Font(name="Arial", size=10, color=MUTED)
    sub_cell.alignment = Alignment(horizontal="right", vertical="center")

    # صف فاصل بصري خفيف
    for col in range(1, ncol + 1):
        cell = ws.cell(row=3, column=col)
        cell.fill = PatternFill("solid", fgColor="E8DFD0")

    return 4  # صف رؤوس الأعمدة


def write_header_row(
    ws: Worksheet,
    headers: list[str],
    row: int,
    *,
    widths: dict[str, float] | None = None,
) -> None:
    fill = PatternFill("solid", fgColor=HEADER_BG)
    font = Font(name="Arial", size=11, bold=True, color=HEADER_FG)
    align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 28

    for idx, label in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=idx, value=label)
        cell.fill = fill
        cell.font = font
        cell.alignment = align
        cell.border = _THIN
        letter = get_column_letter(idx)
        if widths and label in widths:
            ws.column_dimensions[letter].width = widths[label]
        else:
            # عرض تقريبي حسب طول العنوان العربي
            ws.column_dimensions[letter].width = max(12, min(28, len(label) * 1.6 + 4))

    ws.freeze_panes = f"A{row + 1}"
    ws.auto_filter.ref = f"A{row}:{get_column_letter(len(headers))}{row}"


def style_data_rows(
    ws: Worksheet,
    *,
    start_row: int,
    end_row: int,
    ncol: int,
) -> None:
    if end_row < start_row:
        return
    alt = PatternFill("solid", fgColor=ALT_ROW)
    align = Alignment(horizontal="right", vertical="center")
    font = Font(name="Arial", size=10, color=INK)
    for r in range(start_row, end_row + 1):
        for c in range(1, ncol + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = font
            cell.alignment = align
            cell.border = _THIN
            if (r - start_row) % 2 == 1:
                cell.fill = alt


def write_instructions_sheet(wb, lines: list[str], title: str = "تعليمات") -> None:
    tip = wb.create_sheet(title)
    tip.sheet_view.rightToLeft = True
    tip.column_dimensions["A"].width = 72
    tip["A1"] = f"{COMPANY_NAME} — {OFFICE_NAME}"
    tip["A1"].font = Font(name="Arial", size=12, bold=True, color=INK)
    tip["A2"] = "إرشادات تعبئة القالب"
    tip["A2"].font = Font(name="Arial", size=11, bold=True, color=ACCENT)
    tip.row_dimensions[1].height = 22
    tip.row_dimensions[2].height = 20
    for i, line in enumerate(lines, start=4):
        cell = tip.cell(row=i, column=1, value=f"• {line}")
        cell.font = Font(name="Arial", size=10, color=INK)
        cell.alignment = Alignment(horizontal="right", wrap_text=True)
        tip.row_dimensions[i].height = 18


def save_workbook_bytes(wb) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()
