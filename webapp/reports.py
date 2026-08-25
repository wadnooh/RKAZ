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


def _sum_reinforcement_department(conn, pattern: str, date_from: str = "", date_to: str = "") -> float:
    where, params = _date_where("work_date", date_from, date_to)
    try:
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM(COALESCE(value,0)),0)
            FROM reinforcement_works
            WHERE {where} AND department LIKE ?
            """,
            [*params, f"%{pattern}%"],
        ).fetchone()
        return _num(row[0] if row else 0)
    except Exception:
        return 0.0


def _count_reinforcement_department(conn, pattern: str, date_from: str = "", date_to: str = "") -> int:
    where, params = _date_where("work_date", date_from, date_to)
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM reinforcement_works WHERE {where} AND department LIKE ?",
            [*params, f"%{pattern}%"],
        ).fetchone()
        return int(row[0] if row else 0)
    except Exception:
        return 0


def _sum_reinforcement_other(conn, date_from: str = "", date_to: str = "") -> float:
    where, params = _date_where("work_date", date_from, date_to)
    try:
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM(COALESCE(value,0)),0)
            FROM reinforcement_works
            WHERE {where}
              AND COALESCE(department,'') NOT LIKE ?
              AND COALESCE(department,'') NOT LIKE ?
            """,
            [*params, "%عدادات%", "%محطات%"],
        ).fetchone()
        return _num(row[0] if row else 0)
    except Exception:
        return 0.0


def _count_reinforcement_other(conn, date_from: str = "", date_to: str = "") -> int:
    where, params = _date_where("work_date", date_from, date_to)
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM reinforcement_works
            WHERE {where}
              AND COALESCE(department,'') NOT LIKE ?
              AND COALESCE(department,'') NOT LIKE ?
            """,
            [*params, "%عدادات%", "%محطات%"],
        ).fetchone()
        return int(row[0] if row else 0)
    except Exception:
        return 0


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


def _group_count(conn, table: str, field: str, date_col: str | None = None, date_from: str = "", date_to: str = "", *, limit: int = 8) -> list[tuple[str, int]]:
    if date_col:
        where, params = _date_where(date_col, date_from, date_to)
    else:
        where, params = "1=1", []
    try:
        rows = conn.execute(
            f"""
            SELECT COALESCE(NULLIF(TRIM({field}), ''), 'غير محدد') AS label, COUNT(*) AS n
            FROM {table}
            WHERE {where}
            GROUP BY label
            ORDER BY n DESC, label
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        return [(r["label"], int(r["n"] or 0)) for r in rows]
    except Exception:
        return []


def _latest_ref(conn, table: str, date_col: str, ref_col: str) -> str:
    try:
        row = conn.execute(
            f"""
            SELECT {ref_col}, {date_col}
            FROM {table}
            ORDER BY COALESCE({date_col}, '') DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return "—"
        ref = row[ref_col] or "—"
        date = row[date_col] or "—"
        return f"{ref} — {date}"
    except Exception:
        return "—"


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
        primary_team_value = _sum_table(conn, "primary_team_orders", "amount", "order_date", date_from, date_to)
        construction_value = _sum_table(conn, "construction_works", "value", "work_date", date_from, date_to)
        reinforcement_value = _sum_reinforcement_other(conn, date_from, date_to)
        reinforcement_count = _count_reinforcement_other(conn, date_from, date_to)
        metering_count = _count_reinforcement_department(conn, "عدادات", date_from, date_to)
        metering_value = _sum_reinforcement_department(conn, "عدادات", date_from, date_to)
        station_count = _count_reinforcement_department(conn, "محطات", date_from, date_to)
        station_value = _sum_reinforcement_department(conn, "محطات", date_from, date_to)
        project_value = _sum_table(conn, "projects", "value", "start_date", date_from, date_to)
        contractor_value = _sum_table(conn, "contractor_works", "value", "work_date", date_from, date_to)
        invoice_value = _sum_table(conn, "invoices", "value", "invoice_date", date_from, date_to)
        collected = _sum_table(conn, "invoices", "collected", "invoice_date", date_from, date_to)
        purchase_value = _purchase_total(conn, date_from, date_to)
        warehouse = db.warehouse_movements_totals(conn=conn)

        operations_value = ticket_value + metering_value + reinforcement_value + station_value + primary_team_value
        total_work_value = operations_value + construction_value + project_value + contractor_value
        rekaz_pct = _setting_ratio(settings, "rekaz_ratio")
        contractor_pct = _setting_ratio(settings, "main_contractor_ratio")
        rekaz_value = round(total_work_value * rekaz_pct / 100, 2)
        contractor_ratio_value = round(total_work_value * contractor_pct / 100, 2)

        by_status: dict[str, int] = {}
        for row in tickets:
            st = db.normalize_ticket_status(row.get("status")) or "غير محدد"
            by_status[st] = by_status.get(st, 0) + 1
        warehouse_sources = db.warehouse_movements_totals_by_source(conn=conn)
        quality_total = (
            _count_table(conn, "new_coordinations", "request_date", date_from, date_to)
            + _count_table(conn, "quality_clearances", "request_date", date_from, date_to)
            + _count_table(conn, "quality_inspections", "inspect_date", date_from, date_to)
            + _count_table(conn, "issued_licenses", "issue_date", date_from, date_to)
        )
        safety_total = (
            _count_table(conn, "safety_permits", "permit_date", date_from, date_to)
            + _count_table(conn, "safety_incidents", "incident_date", date_from, date_to)
        )

        sections = [
            {
                "title": "العمليات والصيانة",
                "rows": [
                    ("إجمالي العمليات والصيانة", money(operations_value)),
                    ("الأعطال", len(tickets)),
                    ("الأعطال المنفذة/المغلقة", sum(1 for t in tickets if db.normalize_ticket_status(t.get("status")) in ("منفذ", "مغلق"))),
                    ("قيمة الأعطال", money(ticket_value)),
                    ("صيانة العدادات", metering_count),
                    ("قيمة صيانة العدادات", money(metering_value)),
                    ("التعزيز - اسكيمات", reinforcement_count),
                    ("قيمة التعزيز - اسكيمات", money(reinforcement_value)),
                    ("صيانة المحطات", station_count),
                    ("قيمة صيانة المحطات", money(station_value)),
                    ("الفرق الأولية", _count_table(conn, "primary_team_orders", "order_date", date_from, date_to)),
                    ("قيمة الفرق الأولية", money(primary_team_value)),
                    ("آخر عطل", _latest_ref(conn, "tickets", "receive_date", "ticket_no")),
                ],
            },
            {
                "title": "الإنشاءات",
                "rows": [
                    ("عدد المعاملات", _count_table(conn, "construction_works", "work_date", date_from, date_to)),
                    ("إجمالي القيمة", money(construction_value)),
                    ("الحالات", "، ".join(f"{k}: {v}" for k, v in _group_count(conn, "construction_works", "status", "work_date", date_from, date_to, limit=5)) or "—"),
                    ("آخر معاملة", _latest_ref(conn, "construction_works", "work_date", "work_no")),
                ],
            },
            {
                "title": "المشاريع",
                "rows": [
                    ("عدد المشاريع", _count_table(conn, "projects", "start_date", date_from, date_to)),
                    ("القيمة المسجلة", money(project_value)),
                    ("الحالات", "، ".join(f"{k}: {v}" for k, v in _group_count(conn, "projects", "status", "start_date", date_from, date_to, limit=5)) or "—"),
                    ("آخر مشروع", _latest_ref(conn, "projects", "start_date", "project_code")),
                ],
            },
            {
                "title": "المقاولين",
                "rows": [
                    ("أعمال المقاولين", _count_table(conn, "contractor_works", "work_date", date_from, date_to)),
                    ("قيمة أعمال المقاولين", money(contractor_value)),
                    ("مواد موردة من مقاول", _count_table(conn, "contractor_supplies", "supply_date", date_from, date_to)),
                    ("آخر أمر مقاول", _latest_ref(conn, "contractor_works", "work_date", "work_no")),
                ],
            },
            {
                "title": "التنسيقات والجودة",
                "rows": [
                    ("إجمالي سجلات الجودة", quality_total),
                    ("التنسيقات الجديدة", _count_table(conn, "new_coordinations", "request_date", date_from, date_to)),
                    ("الإخلاءات", _count_table(conn, "quality_clearances", "request_date", date_from, date_to)),
                    ("الفحوصات", _count_table(conn, "quality_inspections", "inspect_date", date_from, date_to)),
                    ("الرخص المصدرة", _count_table(conn, "issued_licenses", "issue_date", date_from, date_to)),
                ],
            },
            {
                "title": "السلامة",
                "rows": [
                    ("إجمالي سجلات السلامة", safety_total),
                    ("تصاريح العمل", _count_table(conn, "safety_permits", "permit_date", date_from, date_to)),
                    ("بلاغات السلامة", _count_table(conn, "safety_incidents", "incident_date", date_from, date_to)),
                ],
            },
            {
                "title": "المشتريات الخارجية والعهد",
                "rows": [
                    ("طلبات الشراء", _count_table(conn, "external_purchases", "purchase_date", date_from, date_to)),
                    ("قيمة المشتريات", money(purchase_value)),
                    ("العهد", _count_table(conn, "custody", "custody_date", date_from, date_to)),
                    ("العهد حسب الحالة", "، ".join(f"{k}: {v}" for k, v in _group_count(conn, "custody", "status", "custody_date", date_from, date_to, limit=5)) or "—"),
                ],
            },
            {
                "title": "المستودعات",
                "rows": [
                    ("عدد الحركات", warehouse.get("tx_count") or 0),
                    ("الوارد", f"{warehouse.get('inbound') or 0:.2f}"),
                    ("المنصرف", f"{warehouse.get('outbound') or 0:.2f}"),
                    ("الرصيد", f"{warehouse.get('balance') or 0:.2f}"),
                    ("حسب التخصص", "، ".join(f"{r.get('label')}: {r.get('tx_count')}" for r in warehouse_sources[:6]) or "—"),
                ],
            },
            {
                "title": "المتابعات المالية",
                "rows": [
                    ("المستخلصات", _count_table(conn, "invoices", "invoice_date", date_from, date_to)),
                    ("قيمة المستخلصات", money(invoice_value)),
                    ("المحصل", money(collected)),
                    ("المتبقي", money(invoice_value - collected)),
                ],
            },
        ]

        return {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "date_from": date_from,
            "date_to": date_to,
            "settings": settings,
            "cards": {
                "tickets": len(tickets),
                "done_tickets": sum(1 for t in tickets if db.normalize_ticket_status(t.get("status")) in ("منفذ", "مغلق")),
                "primary_team_count": _count_table(conn, "primary_team_orders", "order_date", date_from, date_to),
                "metering_count": metering_count,
                "construction_count": _count_table(conn, "construction_works", "work_date", date_from, date_to),
                "reinforcement_count": reinforcement_count,
                "station_count": station_count,
                "project_count": _count_table(conn, "projects", "start_date", date_from, date_to),
                "contractor_count": _count_table(conn, "contractor_works", "work_date", date_from, date_to),
                "warehouse_count": warehouse.get("tx_count") or 0,
            },
            "metrics": {
                "operations": operations_value,
                "tickets": ticket_value,
                "metering": metering_value,
                "primary_teams": primary_team_value,
                "construction": construction_value,
                "reinforcement": reinforcement_value,
                "stations": station_value,
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
            "sections": sections,
        }
    finally:
        conn.close()


def whatsapp_url(report: dict, page_url: str, pdf_url: str, phone: str = "") -> str:
    values = report["metrics"]
    msg = "\n".join(
        [
            "تقرير ركاز العام",
            f"إجمالي الأعمال: {money(values['total_work'])}",
            f"نسبة ركاز: {pct(values['rekaz_pct'])} — {money(values['rekaz'])}",
            f"نسبة المقاول الرئيسي: {pct(values['contractor_pct'])} — {money(values['contractor_ratio'])}",
            f"رابط التقرير: {page_url}",
            f"PDF: {pdf_url}",
        ]
    )
    clean_phone = re.sub(r"\D+", "", phone or "")
    if clean_phone:
        return f"https://wa.me/{clean_phone}?text={quote(msg)}"
    return f"https://wa.me/?text={quote(msg)}"


INK = colors.HexColor("#2C302A")
GOLD = colors.HexColor("#B79C62")
GOLD_DARK = colors.HexColor("#6F6448")
CREAM = colors.HexColor("#FCFAF6")
IVORY = colors.HexColor("#FFFFFF")
LINE = colors.HexColor("#E6DED0")
PALE_GOLD = colors.HexColor("#F3EADB")
SOFT_PAPER = colors.HexColor("#FBFAF7")
MUTED_INK = colors.HexColor("#6D6A62")
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
            fontSize=17,
            leading=23,
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
            textColor=MUTED_INK,
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
            textColor=INK,
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
        "sign": ParagraphStyle(
            "RakazLuxSign",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=8,
            leading=13,
            alignment=TA_RIGHT,
            textColor=INK,
        ),
        "signNote": ParagraphStyle(
            "RakazLuxSignNote",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=7.5,
            leading=10,
            alignment=TA_CENTER,
            textColor=MUTED_INK,
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
                ("BACKGROUND", (0, 0), (-1, 0), PALE_GOLD),
                ("TEXTCOLOR", (0, 0), (-1, 0), INK),
                ("LINEBELOW", (0, 0), (-1, 0), 0.7, GOLD),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, CREAM]),
            ]
        )
    else:
        cmds.append(("ROWBACKGROUNDS", (0, 0), (-1, -1), [WHITE, CREAM]))
    return TableStyle(cmds)


def _ratio_with_money(percent_value, amount) -> str:
    return f"{pct(percent_value)}  —  {money(amount)}"


def _archive_block(styles, *, width_mm: float):
    col = width_mm / 3.0 * mm
    lines = [
        "الاسم: ................................",
        "الصفة: ................................",
        "التوقيع: ..............................",
        "التاريخ: .... / .... / ........",
    ]
    def _sign_cell():
        return Paragraph("<br/>".join(_rtl(line) for line in lines), styles["sign"])

    data = [
        [_p("الإعداد", styles["head"]), _p("المراجعة", styles["head"]), _p("الاعتماد", styles["head"])],
        [_sign_cell(), _sign_cell(), _sign_cell()],
    ]
    table = Table(data, colWidths=[col, col, col], rowHeights=[10 * mm, 32 * mm], hAlign="CENTER")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), PALE_GOLD),
                ("TEXTCOLOR", (0, 0), (-1, 0), INK),
                ("LINEBELOW", (0, 0), (-1, 0), 0.7, GOLD),
                ("BACKGROUND", (0, 1), (-1, 1), IVORY),
                ("GRID", (0, 0), (-1, -1), 0.35, LINE),
                ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
                ("VALIGN", (0, 1), (-1, 1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, 0), 7),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
                ("TOPPADDING", (0, 1), (-1, 1), 10),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 12),
            ]
        )
    )
    return [
        Spacer(1, 14),
        _p("اعتماد ومراجعة للأرشفة الرسمية", styles["h2"]),
        _p("تعبأ خانات الإعداد والمراجعة والاعتماد يدوياً بعد الطباعة، ثم تُحفظ النسخة في الأرشيف.", styles["signNote"]),
        Spacer(1, 4),
        table,
    ]


def _cards_block(styles, cards: list[dict] | None, *, width_mm: float):
    cards = [c for c in (cards or []) if c]
    if not cards:
        return []
    card_w = width_mm / 3.0 * mm
    cells = []
    for c in cards:
        title = c.get("title") or "—"
        value = c.get("value")
        if c.get("money"):
            value = money(value)
        elif value is None or value == "":
            value = "—"
        subtitle = c.get("subtitle") or ""
        cell = [
            _p(title, styles["signNote"]),
            _p(value, styles["h2"]),
        ]
        if subtitle:
            cell.append(_p(subtitle, styles["signNote"]))
        cells.append(cell)
    while len(cells) % 3:
        cells.append([_p("", styles["signNote"]), _p("", styles["h2"])])
    rows = [cells[i : i + 3] for i in range(0, len(cells), 3)]
    table = Table(rows, colWidths=[card_w, card_w, card_w], hAlign="CENTER")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT_PAPER),
                ("BOX", (0, 0), (-1, -1), 0.45, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return [table, Spacer(1, 10)]


def _pdf_card_cell(styles, title: str, value, subtitle: str = "", *, money_value: bool = False):
    if not title and (value is None or value == "") and not subtitle:
        return [_p("", styles["signNote"]), _p("", styles["h2"])]
    if money_value:
        value = money(value)
    elif value is None or value == "":
        value = "—"
    cell = [_p(title, styles["signNote"]), _p(value, styles["h2"])]
    if subtitle:
        cell.append(_p(subtitle, styles["signNote"]))
    return cell


def _wide_card_block(styles, title: str, value, subtitle: str, *, width_mm: float, money_value: bool = True):
    data = [[_pdf_card_cell(styles, title, value, subtitle, money_value=money_value)]]
    table = Table(data, colWidths=[width_mm * mm], hAlign="CENTER")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT_PAPER),
                ("BOX", (0, 0), (-1, -1), 0.55, GOLD),
                ("LINEABOVE", (0, 0), (-1, 0), 1.0, GOLD),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    return [table, Spacer(1, 7)]


def _child_cards_block(styles, cards: list[dict], *, width_mm: float):
    cards = [c for c in (cards or []) if c]
    if not cards:
        return []
    card_w = width_mm / 3.0 * mm
    cells = []
    for c in cards:
        cells.append(
            _pdf_card_cell(
                styles,
                c.get("title") or "—",
                c.get("value"),
                c.get("subtitle") or "",
                money_value=bool(c.get("money")),
            )
        )
    while len(cells) % 3:
        cells.append(_pdf_card_cell(styles, "", "", ""))
    rows = [cells[i : i + 3] for i in range(0, len(cells), 3)]
    table = Table(rows, colWidths=[card_w, card_w, card_w], hAlign="CENTER")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), IVORY),
                ("BOX", (0, 0), (-1, -1), 0.35, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return [table, Spacer(1, 9)]


def _draw_page(canvas, doc, *, subtitle: str = ""):
    font_name = _font_name()
    page_w, page_h = canvas._pagesize
    canvas.saveState()
    canvas.setFillColor(WHITE)
    canvas.rect(0, page_h - 18 * mm, page_w, 18 * mm, fill=1, stroke=0)
    canvas.setFillColor(SOFT_PAPER)
    canvas.rect(0, page_h - 18 * mm, page_w, 18 * mm, fill=1, stroke=0)
    canvas.setFillColor(GOLD)
    canvas.rect(0, page_h - 18.2 * mm, page_w, 0.9 * mm, fill=1, stroke=0)
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.4)
    canvas.line(12 * mm, page_h - 20 * mm, page_w - 12 * mm, page_h - 20 * mm)
    canvas.setFillColor(INK)
    canvas.setFont(font_name, 9)
    canvas.drawRightString(page_w - 12 * mm, page_h - 8 * mm, _rtl(COMPANY_FALLBACK))
    canvas.setFillColor(GOLD_DARK)
    canvas.setFont(font_name, 8)
    canvas.drawString(12 * mm, page_h - 8 * mm, _rtl(subtitle or "نظام ركاز — تصدير رسمي"))

    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.4)
    canvas.line(12 * mm, 12 * mm, page_w - 12 * mm, 12 * mm)
    canvas.setFillColor(MUTED_INK)
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
        bottomMargin=18 * mm,
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
    amount_cards: list[dict] | None = None,
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
    story.extend(_cards_block(styles, amount_cards, width_mm=273))
    table = Table(table_data, colWidths=col_widths, repeatRows=1, hAlign="CENTER")
    table.setStyle(_luxury_table_style())
    story.append(table)
    story.extend(_archive_block(styles, width_mm=273))
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
    story.extend(_wide_card_block(styles, "إجمالي الأعمال", metrics["total_work"], "كل الأقسام المالية", width_mm=200))
    story.extend(
        _child_cards_block(
            styles,
            [
                {"title": "نسبة ركاز", "value": metrics["rekaz"], "money": True, "subtitle": f"{pct(metrics['rekaz_pct'])} من المبالغ المدخلة"},
                {"title": "نسبة المقاول الرئيسي", "value": metrics["contractor_ratio"], "money": True, "subtitle": f"{pct(metrics['contractor_pct'])} من المبالغ المدخلة"},
                {"title": "عدد الأعطال", "value": cards["tickets"], "subtitle": f"منفذ/مغلق: {cards['done_tickets']}"},
            ],
            width_mm=200,
        )
    )
    story.extend(
        _wide_card_block(
            styles,
            "إجمالي العمليات والصيانة",
            metrics["operations"],
            "الأعطال وصيانة العدادات والتعزيز وصيانة المحطات والفرق الأولية",
            width_mm=200,
        )
    )
    story.extend(
        _child_cards_block(
            styles,
            [
                {"title": "الأعطال", "value": metrics["tickets"], "money": True, "subtitle": f"{cards['done_tickets']} منفذ / مغلق"},
                {"title": "صيانة العدادات", "value": metrics["metering"], "money": True, "subtitle": f"{cards['metering_count']} معاملة"},
                {"title": "قيمة الفرق الأولية", "value": metrics["primary_teams"], "money": True, "subtitle": f"{cards['primary_team_count']} أمر عمل"},
                {"title": "التعزيز - اسكيمات", "value": metrics["reinforcement"], "money": True, "subtitle": f"{cards['reinforcement_count']} معاملة"},
                {"title": "صيانة المحطات", "value": metrics["stations"], "money": True, "subtitle": f"{cards['station_count']} معاملة"},
            ],
            width_mm=200,
        )
    )
    story.extend(_wide_card_block(styles, "إجمالي الإنشاءات", metrics["construction"], "كل تبويبات ومعاملات الإنشاءات", width_mm=200))
    story.extend(
        _child_cards_block(
            styles,
            [{"title": "معاملات الإنشاءات", "value": cards["construction_count"], "subtitle": "معاملة"}],
            width_mm=200,
        )
    )
    story.extend(_wide_card_block(styles, "إجمالي المشاريع", metrics["projects"], "كل تبويبات وبيانات المشاريع", width_mm=200))
    story.extend(
        _child_cards_block(
            styles,
            [
                {"title": "المشاريع المسجلة", "value": cards["project_count"], "subtitle": "مشروع"},
                {"title": "قيمة المقاول الرئيسي", "value": metrics["contractor"], "money": True, "subtitle": f"{cards['contractor_count']} معاملة"},
            ],
            width_mm=200,
        )
    )

    summary_data = [
        [_p("المؤشر", styles["head"]), _p("القيمة", styles["head"]), _p("المؤشر", styles["head"]), _p("القيمة", styles["head"])],
        [_p("إجمالي الأعمال", styles["body"]), _p(money(metrics["total_work"]), styles["body"]), _p("إجمالي العمليات والصيانة", styles["body"]), _p(money(metrics["operations"]), styles["body"])],
        [_p("نسبة ركاز", styles["body"]), _p(_ratio_with_money(metrics["rekaz_pct"], metrics["rekaz"]), styles["body"]), _p("نسبة المقاول الرئيسي", styles["body"]), _p(_ratio_with_money(metrics["contractor_pct"], metrics["contractor_ratio"]), styles["body"])],
        [_p("الأعطال", styles["body"]), _p(money(metrics["tickets"]), styles["body"]), _p("صيانة العدادات", styles["body"]), _p(money(metrics["metering"]), styles["body"])],
        [_p("التعزيز - اسكيمات", styles["body"]), _p(money(metrics["reinforcement"]), styles["body"]), _p("الفرق الأولية", styles["body"]), _p(money(metrics["primary_teams"]), styles["body"])],
        [_p("صيانة المحطات", styles["body"]), _p(money(metrics["stations"]), styles["body"]), _p("عدد الأعطال", styles["body"]), _p(cards["tickets"], styles["body"])],
        [_p("المستخلصات", styles["body"]), _p(money(metrics["invoices"]), styles["body"]), _p("المحصل", styles["body"]), _p(money(metrics["collected"]), styles["body"])],
        [_p("الوارد مستودع", styles["body"]), _p(f"{metrics['warehouse_inbound']:.2f}", styles["body"]), _p("المنصرف مستودع", styles["body"]), _p(f"{metrics['warehouse_outbound']:.2f}", styles["body"])],
    ]
    table = Table(summary_data, colWidths=[58 * mm, 42 * mm, 58 * mm, 42 * mm], hAlign="CENTER")
    table.setStyle(_luxury_table_style())
    story.append(table)
    story.append(Spacer(1, 12))

    overview_rows = [
        ("العمليات والصيانة", money(metrics["operations"])),
        ("تابع العمليات - الأعطال", money(metrics["tickets"])),
        ("تابع العمليات - صيانة العدادات", money(metrics["metering"])),
        ("تابع العمليات - التعزيز / اسكيمات", money(metrics["reinforcement"])),
        ("تابع العمليات - صيانة المحطات", money(metrics["stations"])),
        ("تابع العمليات - الفرق الأولية", money(metrics["primary_teams"])),
        ("الإنشاءات", money(metrics["construction"])),
        ("المشاريع", money(metrics["projects"])),
        ("المقاولون", money(metrics["contractor"])),
        ("المشتريات الخارجية", money(metrics["purchases"])),
    ]
    all_sections = [
        {"title": "ملخص الأعمال حسب التبويب", "rows": overview_rows},
        *report.get("sections", []),
        {"title": "حالات الأعطال", "rows": [(k, v) for k, v in report["ticket_status"]] or [("لا توجد بيانات", 0)]},
    ]
    for section in all_sections:
        heading = section.get("title") if isinstance(section, dict) else section[0]
        rows = section.get("rows") if isinstance(section, dict) else section[1]
        story.append(_p(heading, styles["h2"]))
        data = [[_p("البند", styles["head"]), _p("القيمة", styles["head"])]]
        data.extend([[_p(a, styles["body"]), _p(b, styles["body"])] for a, b in rows])
        t = Table(data, colWidths=[110 * mm, 50 * mm], hAlign="CENTER")
        t.setStyle(_luxury_table_style())
        story.append(t)
        story.append(Spacer(1, 8))

    story.extend(_archive_block(styles, width_mm=200))
    return _build_pdf(story, pagesize=landscape(A4), subtitle="التقرير العام")
