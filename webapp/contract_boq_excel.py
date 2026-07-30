"""استيراد بنود العقد الموحد من Excel — دليل BOQ النشط للعقود."""

from __future__ import annotations

from datetime import datetime

from openpyxl import Workbook, load_workbook

from webapp import db
from webapp import excel_brand as brand

BOQ_HEADERS = [
    "رقم البند",
    "الوصف",
    "الوحدة",
    "سعر الوحدة",
    "المبلغ",
    "التصنيف",
    "ملاحظات",
]

_ALIASES = {
    "رقم البند": "item_no",
    "كود البند": "item_no",
    "رقم البند بالعقد": "item_no",
    "بند": "item_no",
    "item_no": "item_no",
    "item no": "item_no",
    "item code": "item_no",
    "code": "item_no",
    "الوصف": "description",
    "وصف البند": "description",
    "بيان البند": "description",
    "البند": "description",
    "description": "description",
    "item description": "description",
    "الوحدة": "unit",
    "وحدة القياس": "unit",
    "unit": "unit",
    "سعر الوحدة": "unit_price",
    "سعر البند": "unit_price",
    "السعر": "unit_price",
    "سعر": "unit_price",
    "unit_price": "unit_price",
    "unit price": "unit_price",
    "rate": "unit_price",
    "المبلغ": "amount",
    "الإجمالي": "amount",
    "القيمة": "amount",
    "amount": "amount",
    "total": "amount",
    "التصنيف": "category",
    "القسم": "category",
    "category": "category",
    "ملاحظات": "notes",
    "notes": "notes",
}


def _norm_header(value) -> str:
    return str(value or "").strip().lower().replace("ـ", "").replace("_", " ")


def _map_headers(row_values) -> dict[int, str]:
    mapping = {}
    for idx, raw in enumerate(row_values):
        key = _ALIASES.get(_norm_header(raw)) or _ALIASES.get(str(raw or "").strip())
        if key:
            mapping[idx] = key
    return mapping


def _to_float(val):
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "").replace("٬", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def build_boq_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "بنود العقد"
    header_row = brand.apply_brand_header(
        ws,
        title="قالب بنود العقد الموحد",
        ncol=len(BOQ_HEADERS),
        subtitle="ارفع هذا الملف من صفحة إدارة العقود بعد تعبئة البنود",
    )
    brand.write_header_row(
        ws,
        BOQ_HEADERS,
        header_row,
        widths={"رقم البند": 14, "الوصف": 36, "الوحدة": 12, "سعر الوحدة": 14, "المبلغ": 14, "التصنيف": 16, "ملاحظات": 24},
    )
    sample_row = header_row + 1
    samples = ["1.1", "حفر خندق كيبل", "م.ط", 85, "", "أعمال ترابية", ""]
    for col, val in enumerate(samples, 1):
        ws.cell(sample_row, col, val)
    brand.style_data_rows(ws, start_row=sample_row, end_row=sample_row, ncol=len(BOQ_HEADERS))
    brand.write_instructions_sheet(
        wb,
        [
            "الأعمدة المطلوبة: رقم البند، الوصف، الوحدة، سعر الوحدة.",
            "المبلغ والتصنيف والملاحظات اختيارية.",
            "عند الرفع يُستبدل الدليل النشط بالملف الجديد مع الاحتفاظ بسجل الملفات السابقة.",
        ],
    )
    return brand.save_workbook_bytes(wb)


def import_boq_from_excel(file_storage, *, uploaded_by: str = "") -> dict:
    """يفسّر Excel ويحفظه كدليل بنود عقد نشط. يعيد إحصاءات الاستيراد."""
    wb = load_workbook(file_storage, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header_map = None
    header_row_idx = 0
    for i, row in enumerate(rows_iter, 1):
        mapped = _map_headers(row)
        if "item_no" in mapped.values() and (
            "description" in mapped.values() or "unit_price" in mapped.values()
        ):
            header_map = mapped
            header_row_idx = i
            break
    if not header_map:
        raise ValueError(
            "لم يُعثر على صف رؤوس. المتوقع: رقم البند، الوصف، الوحدة، سعر الوحدة (أو مرادفاتها)."
        )

    items = []
    for row in ws.iter_rows(min_row=header_row_idx + 1, values_only=True):
        data = {field: None for field in ("item_no", "description", "unit", "unit_price", "amount", "category", "notes")}
        for idx, field in header_map.items():
            if idx >= len(row):
                continue
            val = row[idx]
            if field in ("unit_price", "amount"):
                data[field] = _to_float(val)
            else:
                data[field] = str(val).strip() if val is not None and str(val).strip() else ""
        item_no = (data.get("item_no") or "").strip()
        if not item_no:
            continue
        # تجاهل صفوف العناوين الفرعية إن لم يكن لها سعر ولا وصف مفيد قصير جداً
        desc = (data.get("description") or "").strip()
        price = data.get("unit_price")
        if price is None and data.get("amount") is not None and not desc:
            continue
        items.append(
            {
                "item_no": item_no,
                "description": desc,
                "unit": (data.get("unit") or "").strip(),
                "unit_price": price,
                "amount": data.get("amount"),
                "category": (data.get("category") or "").strip(),
                "notes": (data.get("notes") or "").strip(),
            }
        )

    if not items:
        raise ValueError("الملف لا يحتوي بنوداً صالحة بعد صف الرؤوس.")

    filename = getattr(file_storage, "filename", None) or "بنود_العقد.xlsx"
    conn = db.connect()
    try:
        conn.execute("UPDATE contract_boq_files SET is_active=0 WHERE is_active=1")
        cur = conn.execute(
            """
            INSERT INTO contract_boq_files(filename, uploaded_at, uploaded_by, is_active, item_count, notes)
            VALUES (?,?,?,?,?,?)
            """,
            (
                filename,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                uploaded_by or "",
                1,
                len(items),
                "",
            ),
        )
        file_id = cur.lastrowid
        conn.executemany(
            """
            INSERT INTO contract_boq_items(file_id, item_no, description, unit, unit_price, amount, category, notes)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            [
                (
                    file_id,
                    it["item_no"],
                    it["description"],
                    it["unit"],
                    it["unit_price"],
                    it["amount"],
                    it["category"],
                    it["notes"],
                )
                for it in items
            ],
        )
        # مزامنة جدول boq_items القديم كمرآة للدليل النشط (للتمتير/الكميات)
        conn.execute("DELETE FROM boq_items")
        conn.executemany(
            "INSERT INTO boq_items(item_no, description, unit, unit_price, category, notes) VALUES (?,?,?,?,?,?)",
            [
                (it["item_no"], it["description"], it["unit"], it["unit_price"], it["category"], it["notes"])
                for it in items
            ],
        )
        conn.commit()
    finally:
        conn.close()

    return {"ok": len(items), "file_id": file_id, "filename": filename}
