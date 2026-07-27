"""استيراد وتصدير أعطال من/إلى Excel."""

from __future__ import annotations

import io
from datetime import datetime, time

from openpyxl import Workbook, load_workbook

from webapp import db

TICKET_HEADERS = [
    "رقم العطل",
    "تاريخ الاستلام",
    "الحي",
    "وقت الاستلام",
    "المندوب",
    "رقم المحطة",
    "رقم الفيدر",
    "الموقع",
    "نوع العطل",
    "تصنيف العطل",
    "الفرقة",
    "وقت التوجيه",
    "وقت الوصول",
    "حالة التنفيذ",
    "تاريخ التنفيذ",
    "تم التصوير",
    "الكميات مكتملة",
    "إخلاء الأسفلت",
    "حالة التمتير",
    "اعتماد الاستشاري",
    "حالة المستخلص",
    "أمر العمل",
    "رقم الفاتورة",
    "حالة SAP",
    "قيمة البنود",
    "ملاحظات",
]

TICKET_FIELDS = [
    "ticket_no",
    "receive_date",
    "district",
    "receive_time",
    "agent",
    "station_no",
    "feeder_no",
    "location",
    "fault_type",
    "classification",
    "team",
    "dispatch_time",
    "arrival_time",
    "status",
    "execution_date",
    "photographed",
    "quantities_done",
    "asphalt_clearance",
    "metering_status",
    "consultant_approval",
    "invoice_status",
    "work_order",
    "invoice_no",
    "sap_status",
    "items_value",
    "notes",
]

_DATE_FIELDS = {"receive_date", "execution_date"}
_TIME_FIELDS = {"receive_time", "dispatch_time", "arrival_time"}

_TICKET_ALIASES = {
    "رقم العطل": "ticket_no",
    "رقم البلاغ": "ticket_no",  # توافق مع القوالب القديمة
    "البلاغ": "ticket_no",
    "ticket_no": "ticket_no",
    "ticket no": "ticket_no",
    "fault no": "ticket_no",
    "fault_no": "ticket_no",
    "تاريخ الاستلام": "receive_date",
    "تاريخ العطل": "receive_date",
    "تاريخ البلاغ": "receive_date",  # توافق
    "receive_date": "receive_date",
    "الحي": "district",
    "district": "district",
    "وقت الاستلام": "receive_time",
    "receive_time": "receive_time",
    "المندوب": "agent",
    "agent": "agent",
    "رقم المحطة": "station_no",
    "المحطة": "station_no",
    "station_no": "station_no",
    "رقم الفيدر": "feeder_no",
    "الفيدر": "feeder_no",
    "feeder_no": "feeder_no",
    "الموقع": "location",
    "الموقع (رابط خرائط)": "location",
    "location": "location",
    "نوع العطل": "fault_type",
    "العطل": "fault_type",
    "fault_type": "fault_type",
    "تصنيف العطل": "classification",
    "تصنيف البلاغ": "classification",  # توافق
    "التصنيف": "classification",
    "classification": "classification",
    "الفرقة": "team",
    "team": "team",
    "وقت التوجيه": "dispatch_time",
    "dispatch_time": "dispatch_time",
    "وقت الوصول": "arrival_time",
    "arrival_time": "arrival_time",
    "حالة التنفيذ": "status",
    "الحالة": "status",
    "status": "status",
    "تاريخ التنفيذ": "execution_date",
    "execution_date": "execution_date",
    "تم التصوير": "photographed",
    "التصوير": "photographed",
    "photographed": "photographed",
    "الكميات مكتملة": "quantities_done",
    "quantities_done": "quantities_done",
    "إخلاء الأسفلت": "asphalt_clearance",
    "asphalt_clearance": "asphalt_clearance",
    "حالة التمتير": "metering_status",
    "metering_status": "metering_status",
    "اعتماد الاستشاري": "consultant_approval",
    "consultant_approval": "consultant_approval",
    "حالة المستخلص": "invoice_status",
    "invoice_status": "invoice_status",
    "أمر العمل": "work_order",
    "work_order": "work_order",
    "رقم الفاتورة": "invoice_no",
    "invoice_no": "invoice_no",
    "حالة sap": "sap_status",
    "حالة SAP": "sap_status",
    "sap_status": "sap_status",
    "قيمة البنود": "items_value",
    "items_value": "items_value",
    "ملاحظات": "notes",
    "notes": "notes",
}


def _norm_header(value) -> str:
    return str(value or "").strip().lower().replace("ـ", "")


def _map_headers(row_values) -> dict[int, str]:
    mapping = {}
    for idx, raw in enumerate(row_values):
        key = str(raw or "").strip()
        field = _TICKET_ALIASES.get(key) or _TICKET_ALIASES.get(key.lower())
        if not field:
            field = _TICKET_ALIASES.get(_norm_header(key))
        if field:
            mapping[idx] = field
    return mapping


def _format_date(val) -> str:
    if val is None or val == "":
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    if hasattr(val, "strftime") and not isinstance(val, time):
        try:
            return val.strftime("%Y-%m-%d")
        except Exception:
            pass
    s = str(val).strip()
    if " " in s and len(s) >= 10:
        s = s.split(" ")[0]
    if "/" in s and len(s.split("/")) == 3:
        parts = s.split("/")
        if len(parts[2]) == 4:
            # D/M/Y or M/D/Y — prefer D/M/Y for Arabic sheets
            d, m, y = parts[0].zfill(2), parts[1].zfill(2), parts[2]
            return f"{y}-{m}-{d}"
    return s


def _format_time(val) -> str:
    if val is None or val == "":
        return ""
    if isinstance(val, datetime):
        return val.strftime("%H:%M")
    if isinstance(val, time):
        return val.strftime("%H:%M")
    s = str(val).strip()
    if " " in s and ":" in s:
        # "2026-01-01 14:30:00" or similar
        part = s.split(" ")[-1]
        return part[:5] if len(part) >= 5 else part
    if len(s) >= 5 and s[2] == ":":
        return s[:5]
    return s


def _to_float(val):
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _cell(row, idx, field: str | None = None):
    if idx is None or idx >= len(row):
        return ""
    val = row[idx]
    if val is None:
        return ""
    if field in _DATE_FIELDS:
        return _format_date(val)
    if field in _TIME_FIELDS:
        return _format_time(val)
    if field == "items_value":
        return _to_float(val)
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    return str(val).strip()


def build_tickets_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "الأعطال"
    ws.append(TICKET_HEADERS)
    today = datetime.now().strftime("%Y-%m-%d")
    ws.append(
        [
            "T-1001",
            today,
            "خريص",
            "08:30",
            "مندوب 1",
            "ST-01",
            "F-12",
            "",
            "عطل كيبل",
            "طارئ",
            "فرقة 1",
            "09:00",
            "09:25",
            "جديد",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            0,
            "صف مثال — احذفه أو عدّله",
        ]
    )
    tip = wb.create_sheet("تعليمات")
    tip.append(["عمود رقم العطل مطلوب وفريد."])
    tip.append(["عند تكرار رقم العطل يتم تحديث السجل الموجود (upsert)."])
    tip.append(["التواريخ بصيغة YYYY-MM-DD والأوقات HH:MM."])
    tip.append(["ارفع الملف من صفحة الأعطال ← استيراد من Excel."])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def import_tickets_from_excel(file_storage) -> dict:
    wb = load_workbook(file_storage, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {"ok": 0, "updated": 0, "errors": ["الملف فارغ"]}

    mapping = _map_headers(rows[0])
    if "ticket_no" not in mapping.values():
        return {"ok": 0, "updated": 0, "errors": ["لم يُعثر على عمود رقم العطل"]}

    inv = {v: k for k, v in mapping.items()}
    created = updated = 0
    errors: list[str] = []
    conn = db.connect()

    for i, row in enumerate(rows[1:], start=2):
        if not row or all(c is None or str(c).strip() == "" for c in row):
            continue
        ticket_no = _cell(row, inv.get("ticket_no"), "ticket_no")
        if not ticket_no:
            errors.append(f"صف {i}: رقم العطل فارغ")
            continue

        data = {}
        for field in TICKET_FIELDS:
            if field == "ticket_no":
                data[field] = ticket_no
                continue
            data[field] = _cell(row, inv.get(field), field) if field in inv else ""

        if data.get("items_value") == "":
            data["items_value"] = None
        if not data.get("status"):
            data["status"] = "جديد"

        try:
            existing = conn.execute(
                "SELECT id FROM tickets WHERE ticket_no=?",
                (ticket_no,),
            ).fetchone()
            cols = ", ".join(TICKET_FIELDS)
            if existing:
                sets = ", ".join([f"{f}=?" for f in TICKET_FIELDS])
                conn.execute(
                    f"UPDATE tickets SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    [data[f] for f in TICKET_FIELDS] + [existing["id"]],
                )
                updated += 1
            else:
                placeholders = ", ".join(["?"] * len(TICKET_FIELDS))
                conn.execute(
                    f"INSERT INTO tickets({cols}) VALUES ({placeholders})",
                    [data[f] for f in TICKET_FIELDS],
                )
                created += 1
        except Exception as exc:
            errors.append(f"صف {i} ({ticket_no}): {exc}")

    conn.commit()
    conn.close()
    return {"ok": created, "updated": updated, "errors": errors}
