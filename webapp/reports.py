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


def _setting_ratio(settings: dict, key: str) -> float:
    return max(0.0, min(100.0, _num((settings or {}).get(key))))


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
        rekaz_pct = _setting_ratio(settings, "rekaz_ratio")
        contractor_pct = _setting_ratio(settings, "main_contractor_ratio")
        rekaz_value = round(total_work_value * rekaz_pct / 100, 2)
        contractor_ratio_value = round(total_work_value * contractor_pct / 100, 2)

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
            "metrics": {
                "tickets": ticket_value,
                "construction": construction_value,
                "projects": project_value,
                "contractor": contractor_value,
                "total_work": total_work_value,
                "rekaz": rekaz_value,
                "contractor_ratio": contractor_ratio_value,
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
    values = report["metrics"]
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


INK = colors.HexColor("#1A1814")
GOLD = colors.HexColor("#8A7349")
GOLD_DARK = colors.HexColor("#6E5A38")
CREAM = colors.HexColor("#F7F4EF")
IVORY = colors.HexColor("#FBFAF7")
LINE = colors.HexColor("#DDD2C0")
WHITE = colors.white
COMPANY_FALLBACK = "شركة ركاز الإنجاز للمقاولات"
OFFICE_FALLBACK = "مكتب خدمات خريص"


def _font_path() -> str | None:
    candidates = [
        os.environ.get("RAKAZ_REPORT_FONT", ""),
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\ARIALUNI.TTF",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _font_name() -> str:
    font_file = _font_path()
    if not font_file:
        return "Helvetica"
    name = "RakazArabic"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, font_file))
    return name


def _rtl(text) -> str:
    s = str(text if text is not None else "")
    if arabic_reshaper and get_display:
        return get_display(arabic_reshaper.reshape(s))
    return s


def _p(text, style):
    return Paragraph(_rtl(text), style)


def _styles(font_name: str) -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "RakazLuxTitle",
            parent=base["Title"],
            fontName=font_name,
            fontSize=18,
            leading=24,
            alignment=TA_CENTER,
            textColor=INK,
            spaceAfter=2,
        ),
        "kicker": ParagraphStyle(
            "RakazLuxKicker",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=8.5,
            leading=12,
            alignment=TA_CENTER,
            textColor=GOLD_DARK,
        ),
        "meta": ParagraphStyle(
            "RakazLuxMeta",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=8,
            leading=11,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#6B655C"),
        ),
        "h2": ParagraphStyle(
            "RakazLuxH2",
            parent=base["Heading2"],
            fontName=font_name,
            fontSize=11,
            leading=15,
            alignment=TA_RIGHT,
            textColor=GOLD_DARK,
            spaceBefore=4,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "RakazLuxBody",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=8.5,
            leading=12,
            alignment=TA_RIGHT,
            textColor=INK,
        ),
        "head": ParagraphStyle(
            "RakazLuxHead",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=8,
            leading=11,
            alignment=TA_CENTER,
            textColor=WHITE,
        ),
        "cell": ParagraphStyle(
            "RakazLuxCell",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=7.4,
            leading=10,
            alignment=TA_RIGHT,
            textColor=INK,
        ),
    }


def _luxury_table_style(*, header=True) -> TableStyle:
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.35, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    if header:
        cmds.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), GOLD),
                ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, CREAM]),
            ]
        )
    else:
        cmds.append(("ROWBACKGROUNDS", (0, 0), (-1, -1), [WHITE, CREAM]))
    return TableStyle(cmds)


def _draw_page(canvas, doc, *, subtitle: str = ""):
    font_name = _font_name()
    page_w, page_h = canvas._pagesize
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.rect(0, page_h - 16 * mm, page_w, 16 * mm, fill=1, stroke=0)
    canvas.setFillColor(GOLD)
    canvas.rect(0, page_h - 18.2 * mm, page_w, 2.2 * mm, fill=1, stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont(font_name, 9)
    canvas.drawRightString(page_w - 12 * mm, page_h - 8 * mm, _rtl(COMPANY_FALLBACK))
    canvas.setFont(font_name, 8)
    canvas.drawString(12 * mm, page_h - 8 * mm, _rtl(subtitle or "نظام ركاز — تصدير رسمي"))

    canvas.setFillColor(GOLD)
    canvas.rect(0, 0, page_w, 11 * mm, fill=1, stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont(font_name, 8)
    canvas.drawCentredString(page_w / 2, 4.4 * mm, _rtl(f"{OFFICE_FALLBACK}  ·  صفحة {doc.page}"))
    canvas.restoreState()


def _build_pdf(story, *, pagesize, subtitle: str = "") -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=24 * mm,
        bottomMargin=16 * mm,
    )
    doc.build(
        story,
        onFirstPage=lambda c, d: _draw_page(c, d, subtitle=subtitle),
        onLaterPages=lambda c, d: _draw_page(c, d, subtitle=subtitle),
    )
    return buffer.getvalue()


def build_table_pdf(
    *,
    title_text: str,
    headers: list[str],
    rows: list[dict],
    field_keys: list[str],
    filters: list[str] | None = None,
    generated_at: str | None = None,
) -> bytes:
    font_name = _font_name()
    styles = _styles(font_name)

    def _val(row: dict, key: str):
        value = row.get(key) if isinstance(row, dict) else ""
        if value is None or value == "":
            return "—"
        return str(value)

    safe_headers = headers or field_keys or ["البيان"]
    safe_keys = field_keys or headers or ["value"]
    width = 273 * mm
    col_count = max(len(safe_headers), 1)
    col_widths = [width / col_count for _ in range(col_count)]
    table_data = [[_p(h, styles["head"]) for h in safe_headers]]
    for row in rows or []:
        table_data.append([_p(_val(row, key), styles["cell"]) for key in safe_keys])
    if not rows:
        table_data.append([_p("لا توجد بيانات حسب الفلترة الحالية", styles["cell"])] + [_p("", styles["cell"]) for _ in safe_keys[1:]])

    stamp = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    story = [
        _p("تصدير رسمي", styles["kicker"]),
        _p(title_text, styles["title"]),
        _p(f"{COMPANY_FALLBACK} — {OFFICE_FALLBACK}", styles["meta"]),
        _p(f"عدد السجلات: {len(rows or [])}  ·  تاريخ التصدير: {stamp}", styles["meta"]),
    ]
    filter_text = " | ".join([f for f in (filters or []) if f])
    if filter_text:
        story.append(_p(f"الفلاتر: {filter_text}", styles["meta"]))
    story.append(Spacer(1, 8))
    table = Table(table_data, colWidths=col_widths, repeatRows=1, hAlign="CENTER")
    table.setStyle(_luxury_table_style())
    story.append(table)
    return _build_pdf(story, pagesize=landscape(A4), subtitle=title_text)


def build_general_report_pdf(report: dict) -> bytes:
    font_name = _font_name()
    styles = _styles(font_name)
    metrics = report["metrics"]
    cards = report["cards"]
    company = report["settings"].get("company_name") or COMPANY_FALLBACK
    period = "كل الفترات"
    if report.get("date_from") or report.get("date_to"):
        period = f"من {report.get('date_from') or 'البداية'} إلى {report.get('date_to') or 'اليوم'}"

    story = [
        _p("تقرير تنفيذي", styles["kicker"]),
        _p("التقرير العام لأعمال ركاز", styles["title"]),
        _p(f"{company} — {period} — {report['generated_at']}", styles["meta"]),
        Spacer(1, 10),
    ]

    summary_data = [
        [_p("المؤشر", styles["head"]), _p("القيمة", styles["head"]), _p("المؤشر", styles["head"]), _p("القيمة", styles["head"])],
        [_p("إجمالي الأعمال", styles["body"]), _p(money(metrics["total_work"]), styles["body"]), _p("عدد الأعطال", styles["body"]), _p(cards["tickets"], styles["body"])],
        [_p("نسبة ركاز", styles["body"]), _p(pct(metrics["rekaz_pct"]), styles["body"]), _p("نسبة المقاول الرئيسي", styles["body"]), _p(pct(metrics["contractor_pct"]), styles["body"])],
        [_p("قيمة ركاز", styles["body"]), _p(money(metrics["rekaz"]), styles["body"]), _p("قيمة المقاول الرئيسي", styles["body"]), _p(money(metrics["contractor_ratio"]), styles["body"])],
        [_p("المستخلصات", styles["body"]), _p(money(metrics["invoices"]), styles["body"]), _p("المحصل", styles["body"]), _p(money(metrics["collected"]), styles["body"])],
        [_p("الوارد مستودع", styles["body"]), _p(f"{metrics['warehouse_inbound']:.2f}", styles["body"]), _p("المنصرف مستودع", styles["body"]), _p(f"{metrics['warehouse_outbound']:.2f}", styles["body"])],
    ]
    table = Table(summary_data, colWidths=[58 * mm, 42 * mm, 58 * mm, 42 * mm], hAlign="CENTER")
    table.setStyle(_luxury_table_style())
    story.append(table)
    story.append(Spacer(1, 12))

    sections = [
        ("الأعمال حسب القسم", [
            ("العمليات والصيانة", money(metrics["tickets"])),
            ("الإنشاءات", money(metrics["construction"])),
            ("المشاريع", money(metrics["projects"])),
            ("المقاول الرئيسي", money(metrics["contractor"])),
            ("المشتريات الخارجية", money(metrics["purchases"])),
        ]),
        ("حالات الأعطال", [(k, v) for k, v in report["ticket_status"]] or [("لا توجد بيانات", 0)]),
    ]
    for heading, rows in sections:
        story.append(_p(heading, styles["h2"]))
        data = [[_p("البند", styles["head"]), _p("القيمة", styles["head"])]]
        data.extend([[_p(a, styles["body"]), _p(b, styles["body"])] for a, b in rows])
        t = Table(data, colWidths=[110 * mm, 50 * mm], hAlign="CENTER")
        t.setStyle(_luxury_table_style())
        story.append(t)
        story.append(Spacer(1, 8))

    return _build_pdf(story, pagesize=landscape(A4), subtitle="التقرير العام")
