from __future__ import annotations

import io
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from webapp import db

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except Exception:  # pragma: no cover - optional deployment fallback
    arabic_reshaper = None
    get_display = None


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def money(value) -> str:
    return f"{_num(value):,.2f} ر.س"


def pct(value) -> str:
    return f"{_num(value):.1f}%"


def _date_where(column: str, date_from: str = "", date_to: str = "") -> tuple[str, list]:
    where = ["1=1"]
    params: list = []
    if date_from:
        where.append(f"date({column}) >= date(?)")
        params.append(date_from)
    if date_to:
        where.append(f"date({column}) <= date(?)")
        params.append(date_to)
    return " AND ".join(where), params


def _sum_table(conn, table: str, field: str, date_col: str | None = None, date_from: str = "", date_to: str = "") -> float:
    if date_col:
        where, params = _date_where(date_col, date_from, date_to)
    else:
        where, params = "1=1", []
    try:
        row = conn.execute(f"SELECT COALESCE(SUM(COALESCE({field},0)),0) FROM {table} WHERE {where}", params).fetchone()
        return _num(row[0] if row else 0)
    except Exception:
        return 0.0


def _count_table(conn, table: str, date_col: str | None = None, date_from: str = "", date_to: str = "") -> int:
    if date_col:
        where, params = _date_where(date_col, date_from, date_to)
    else:
        where, params = "1=1", []
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()
        return int(row[0] if row else 0)
    except Exception:
        return 0


def _ticket_rows(conn, date_from: str = "", date_to: str = "") -> list[dict]:
    where, params = _date_where("receive_date", date_from, date_to)
    return db.rows_to_dicts(conn.execute(f"SELECT * FROM tickets WHERE {where}", params).fetchall())


def _tickets_value(conn, tickets: list[dict], settings: dict) -> float:
    if not tickets:
        return 0.0
    default_ratio = _num(settings.get("emergency_ratio"))
    ids = [r.get("id") for r in tickets if r.get("id") is not None]
    ratio_map = db.map_ticket_emergency_ratios(ids, default_ratio, conn=conn)
    total = 0.0
    for row in tickets:
        base = row.get("items_value")
        if base in (None, ""):
            continue
        ratio = ratio_map.get(row.get("id"), default_ratio)
        total += _num(base) * (1 + _num(ratio))
    return round(total, 2)


def _purchase_total(conn, date_from: str = "", date_to: str = "") -> float:
    where, params = _date_where("p.purchase_date", date_from, date_to)
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(COALESCE(l.line_total, COALESCE(l.qty,0) * COALESCE(l.unit_price,0))),0)
        FROM external_purchase_lines l
        JOIN external_purchases p ON p.id = l.purchase_id
        WHERE {where}
        """,
        params,
    ).fetchone()
    return _num(row[0] if row else 0)


def build_general_report(date_from: str = "", date_to: str = "") -> dict:
    conn = db.connect()
    try:
        db.ensure_schema(conn)
        settings = db.get_settings(conn)
        tickets = _ticket_rows(conn, date_from, date_to)
        ticket_value = _tickets_value(conn, tickets, settings)
        construction_value = _sum_table(conn, "construction_works", "value", "work_date", date_from, date_to)
        project_value = _sum_table(conn, "projects", "value", "start_date", date_from, date_to)
        contractor_value = _sum_table(conn, "contractor_works", "value", "work_date", date_from, date_to)
        invoice_value = _sum_table(conn, "invoices", "value", "invoice_date", date_from, date_to)
        collected = _sum_table(conn, "invoices", "collected", "invoice_date", date_from, date_to)
        purchase_value = _purchase_total(conn, date_from, date_to)
        warehouse = db.warehouse_movements_totals(conn=conn)

        total_work_value = ticket_value + construction_value + project_value + contractor_value
        rekaz_value = max(total_work_value - contractor_value, 0.0)
        contractor_pct = (contractor_value / total_work_value * 100) if total_work_value else 0.0
        rekaz_pct = (rekaz_value / total_work_value * 100) if total_work_value else 0.0

        by_status: dict[str, int] = {}
        for row in tickets:
            st = db.normalize_ticket_status(row.get("status")) or "غير محدد"
            by_status[st] = by_status.get(st, 0) + 1

        return {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "date_from": date_from,
            "date_to": date_to,
            "settings": settings,
            "cards": {
                "tickets": len(tickets),
                "done_tickets": sum(1 for t in tickets if db.normalize_ticket_status(t.get("status")) in ("منفذ", "مغلق")),
                "construction_count": _count_table(conn, "construction_works", "work_date", date_from, date_to),
                "project_count": _count_table(conn, "projects", "start_date", date_from, date_to),
                "contractor_count": _count_table(conn, "contractor_works", "work_date", date_from, date_to),
                "warehouse_count": warehouse.get("tx_count") or 0,
            },
            "values": {
                "tickets": ticket_value,
                "construction": construction_value,
                "projects": project_value,
                "contractor": contractor_value,
                "total_work": total_work_value,
                "rekaz": rekaz_value,
                "contractor_pct": contractor_pct,
                "rekaz_pct": rekaz_pct,
                "invoices": invoice_value,
                "collected": collected,
                "remaining": invoice_value - collected,
                "purchases": purchase_value,
                "warehouse_inbound": warehouse.get("inbound") or 0,
                "warehouse_outbound": warehouse.get("outbound") or 0,
                "warehouse_balance": warehouse.get("balance") or 0,
            },
            "ticket_status": sorted(by_status.items(), key=lambda item: item[0]),
        }
    finally:
        conn.close()


def whatsapp_url(report: dict, page_url: str, pdf_url: str, phone: str = "") -> str:
    values = report["values"]
    msg = "\n".join(
        [
            "تقرير ركاز العام",
            f"إجمالي الأعمال: {money(values['total_work'])}",
            f"نسبة ركاز: {pct(values['rekaz_pct'])}",
            f"نسبة المقاول الرئيسي: {pct(values['contractor_pct'])}",
            f"رابط التقرير: {page_url}",
            f"PDF: {pdf_url}",
        ]
    )
    clean_phone = re.sub(r"\D+", "", phone or "")
    if clean_phone:
        return f"https://wa.me/{clean_phone}?text={quote(msg)}"
    return f"https://wa.me/?text={quote(msg)}"


def _font_path() -> str | None:
    candidates = [
        os.environ.get("RAKAZ_REPORT_FONT", ""),
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\ARIALUNI.TTF",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _rtl(text) -> str:
    s = str(text if text is not None else "")
    if arabic_reshaper and get_display:
        return get_display(arabic_reshaper.reshape(s))
    return s


def _p(text, style):
    return Paragraph(_rtl(text), style)


def build_general_report_pdf(report: dict) -> bytes:
    font_name = "Helvetica"
    font_file = _font_path()
    if font_file:
        font_name = "RakazArabic"
        if font_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font_name, font_file))

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "RakazTitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=18,
        leading=24,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#214E34"),
    )
    h2 = ParagraphStyle(
        "RakazH2",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=12,
        leading=16,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#8A7349"),
    )
    body = ParagraphStyle(
        "RakazBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=9,
        leading=13,
        alignment=TA_RIGHT,
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )
    values = report["values"]
    cards = report["cards"]
    period = "كل الفترات"
    if report.get("date_from") or report.get("date_to"):
        period = f"من {report.get('date_from') or 'البداية'} إلى {report.get('date_to') or 'اليوم'}"

    story = [
        _p("التقرير العام لأعمال ركاز", title),
        _p(f"{report['settings'].get('company_name') or 'شركة ركاز الإنجاز للمقاولات'} - {period} - {report['generated_at']}", body),
        Spacer(1, 8),
    ]

    summary_data = [
        [_p("المؤشر", h2), _p("القيمة", h2), _p("المؤشر", h2), _p("القيمة", h2)],
        [_p("إجمالي الأعمال", body), _p(money(values["total_work"]), body), _p("عدد الأعطال", body), _p(cards["tickets"], body)],
        [_p("نسبة ركاز", body), _p(pct(values["rekaz_pct"]), body), _p("نسبة المقاول الرئيسي", body), _p(pct(values["contractor_pct"]), body)],
        [_p("قيمة ركاز", body), _p(money(values["rekaz"]), body), _p("قيمة المقاول الرئيسي", body), _p(money(values["contractor"]), body)],
        [_p("المستخلصات", body), _p(money(values["invoices"]), body), _p("المحصل", body), _p(money(values["collected"]), body)],
        [_p("الوارد مستودع", body), _p(f"{values['warehouse_inbound']:.2f}", body), _p("المنصرف مستودع", body), _p(f"{values['warehouse_outbound']:.2f}", body)],
    ]
    table = Table(summary_data, colWidths=[58 * mm, 42 * mm, 58 * mm, 42 * mm], hAlign="CENTER")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF3EE")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D0D5DD")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAF7")]),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 10))

    sections = [
        ("الأعمال حسب القسم", [
            ("العمليات والصيانة", money(values["tickets"])),
            ("الإنشاءات", money(values["construction"])),
            ("المشاريع", money(values["projects"])),
            ("المقاول الرئيسي", money(values["contractor"])),
            ("المشتريات الخارجية", money(values["purchases"])),
        ]),
        ("حالات الأعطال", [(k, v) for k, v in report["ticket_status"]] or [("لا توجد بيانات", 0)]),
    ]
    for heading, rows in sections:
        story.append(_p(heading, h2))
        data = [[_p("البند", body), _p("القيمة", body)]]
        data.extend([[_p(a, body), _p(b, body)] for a, b in rows])
        t = Table(data, colWidths=[90 * mm, 50 * mm], hAlign="RIGHT")
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2EEE6")),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D0D5DD")),
                    ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        story.append(t)
        story.append(Spacer(1, 8))

    doc.build(story)
    return buffer.getvalue()
