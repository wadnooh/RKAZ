import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _resolve_db_path() -> Path:
    data_dir = os.environ.get("RAKAZ_DATA_DIR", "").strip()
    if data_dir:
        return Path(data_dir) / "rakaz.db"
    return ROOT / "instance" / "rakaz.db"


DB_PATH = _resolve_db_path()

DEFAULT_SETTINGS = {
    "office_name": "مكتب خدمات خريص",
    "company_name": "شركة ركاز الإنجاز للمقاولات",
    "teams_count": 2,
    "daily_tickets": 2,
    "work_days": 30,
    "monthly_expenses": 334000,
    "cash_delay_months": 4,
    "emergency_ratio": 0.45,
    "target_avg": 5000,
    "reject_limit": 0.05,
    "response_target": 30,
    "invoice_days": 7,
}

DEFAULT_LISTS = {
    "execution_status": ["جديد", "تم التوجيه", "قيد التنفيذ", "منفذ", "مغلق", "مرفوض/إعادة عمل"],
    "photo_status": ["غير مطلوب", "ناقص", "مكتمل", "مرفوض"],
    "coordination_status": ["غير مطلوب", "بانتظار المختبر", "مستخرج", "مرفوض"],
    "metering_status": ["لم يبدأ", "قيد الإعداد", "عند الاستشاري", "معتمد", "مرفوض"],
    "invoice_status": ["لم يرفع", "مرفوع للمساندة", "صدر أمر عمل", "فاتورة صادرة", "مرفوع SAP", "مدفوع"],
    "sap_status": ["لم يرفع", "مرفوع", "مقبول", "مرفوض"],
    "ticket_class": ["خفيف", "متوسط", "ثقيل"],
    "teams": ["فرقة 1 مدثر", "فرقة 2 ردوي", "فرقة 3", "فرقة 4"],
    "agents": ["التجاني"],
    "coordination_officers": ["مسؤول تنسيقات 1"],
    "invoice_officers": ["محمود العكل"],
    "consultants": ["التصميم الفني"],
    "yes_no": ["نعم", "لا"],
    "construction_types": ["تمديد كيبل", "تركيب قاطع", "حفر", "استبدال معدات", "أخرى"],
    "clearance_status": ["مطلوب", "قيد الإصدار", "صادر", "مرفوض"],
    "inspect_result": ["مطابق", "ملاحظات", "غير مطابق"],
    "permit_status": ["ساري", "مغلق", "ملغي"],
    "incident_types": ["إصابة", "قرب حادث", "مخالفة سلامة", "أخرى"],
    "severity_levels": ["منخفض", "متوسط", "عالي"],
    "warehouse_categories": ["عهد", "مواد كهربائية", "كيابل", "عدد"],
    "warehouse_tx_types": [
        "وارد من الكهرباء",
        "منصرف للمقاول",
        "إرجاع للمجمعة",
        "وارد من موقع العمل",
        "رصيد افتتاحي",
    ],
    "purchase_status": ["جديد", "معتمد", "تم الشراء", "ملغي"],
    "custody_status": ["مسلمة", "مرتجعة", "مفقودة"],
    "vehicle_status": ["عاملة", "صيانة", "متوقفة"],
    "equipment_status": ["جاهزة", "صيانة", "تخريد"],
    "contract_status": ["ساري", "منتهي", "موقوف"],
    "hr_departments": ["العمليات", "المستودعات", "الجودة", "السلامة", "المالية", "الموارد البشرية", "الإدارة"],
    "hr_status": ["على رأس العمل", "إجازة", "منتهي"],
    "user_roles": ["admin", "مشرف", "مدخل بيانات", "مراقب"],
    "followup_status": ["مفتوح", "قيد المتابعة", "مكتمل", "ملغي"],
    "followup_priority": ["عاجل", "عالي", "متوسط", "منخفض"],
    "review_result": ["معتمد", "ملاحظات", "مرفوض", "يحتاج استكمال"],
}

SOP_ROWS = [
    ("استلام البلاغ", "مكتب الطوارئ", "رقم العطل، الموقع، نوع العطل", "تسجيل البلاغ بالتاريخ", "15 دقيقة", "سجل البلاغات الرسمي"),
    ("توجيه الفرقة", "المندوب / المراقب", "بيانات البلاغ", "تعيين فرقة مناسبة", "15 دقيقة", "وقت التوجيه"),
    ("تنفيذ الأعمال", "الفرقة الميدانية", "وصف العطل والمواد، الموقع", "إصلاح العطل", "حسب العطل", "صور قبل/أثناء/بعد"),
    ("رفع الكميات", "الفرقة + المندوب", "صور الكميات، البنود المنفذة", "جدول الكميات", "24 ساعة", "ورقة الكميات المكتملة"),
    ("التنسيقات الفنية", "مسؤول التنسيقات", "المتطلبات حسب نوع العمل", "تقرير/إخلاء أسفلت", "حسب التنسيق", "رقم تقرير المختبر إن وجد"),
    ("التمتير", "مسؤول المستخلصات", "الكميات، الصور، التنسيقات", "مستخلص معتمد", "3 أيام", "مستخلص BOQ"),
    ("اعتماد الاستشاري", "الاستشاري", "مستخلص التمتير", "اعتماد/ملاحظات", "7 أيام", "خطاب اعتماد رسمي"),
    ("رفع المستخلص", "مسؤول المستخلصات", "المستخلص المعتمد", "أمر عمل المستخلص", "2 يوم", "رقم الأمر"),
    ("إصدار فاتورة المستخلص", "المحاسبة", "المستخلص المعتمد", "رقم فاتورة + المبالغ", "حسب العقد", "فاتورة رسمية للمستخلص"),
    ("رفع SAP", "المحاسبة", "الفاتورة، المستخلص، الأمر", "حالة رفع SAP", "حسب دورة المستحقات", "حالة SAP"),
]


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# جداول أُضيفت لاحقاً — تُضمن صراحة حتى بعد استعادة حفظة قديمة
EXTRA_TABLE_DDL = {
    "contractor_works": """
        CREATE TABLE IF NOT EXISTS contractor_works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_no TEXT,
            work_date TEXT,
            contractor TEXT,
            ticket_no TEXT,
            work_type TEXT,
            site TEXT,
            status TEXT,
            value REAL,
            notes TEXT
        )
    """,
    "hr_employees": """
        CREATE TABLE IF NOT EXISTS hr_employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_no TEXT,
            full_name TEXT,
            job_title TEXT,
            department TEXT,
            phone TEXT,
            status TEXT,
            join_date TEXT,
            notes TEXT
        )
    """,
}


def ensure_schema(conn: sqlite3.Connection | None = None) -> list[str]:
    """إنشاء أي جداول ناقصة (مهم بعد استعادة نسخة قديمة على Render)."""
    own = conn is None
    conn = conn or connect()
    created: list[str] = []
    try:
        existing = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for name, ddl in EXTRA_TABLE_DDL.items():
            if name not in existing:
                conn.execute(ddl)
                created.append(name)
        if created:
            conn.commit()
        return created
    finally:
        if own:
            conn.close()


def init_db():
    conn = connect()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS lists (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            leader TEXT,
            technicians INTEGER,
            driver TEXT,
            vehicle TEXT,
            area TEXT,
            status TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_no TEXT UNIQUE,
            receive_date TEXT,
            district TEXT,
            receive_time TEXT,
            agent TEXT,
            station_no TEXT,
            feeder_no TEXT,
            location TEXT,
            fault_type TEXT,
            classification TEXT,
            team TEXT,
            dispatch_time TEXT,
            arrival_time TEXT,
            status TEXT,
            execution_date TEXT,
            photographed TEXT,
            quantities_done TEXT,
            asphalt_clearance TEXT,
            metering_status TEXT,
            consultant_approval TEXT,
            invoice_status TEXT,
            work_order TEXT,
            invoice_no TEXT,
            sap_status TEXT,
            items_value REAL,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS quantities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_no TEXT,
            item_no TEXT,
            description TEXT,
            unit TEXT,
            qty REAL,
            unit_price REAL,
            ref TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_no TEXT,
            before_shot TEXT,
            during_shot TEXT,
            after_shot TEXT,
            quantities_shot TEXT,
            location_shot TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS coordination (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_no TEXT,
            needs_asphalt TEXT,
            officer TEXT,
            request_date TEXT,
            lab TEXT,
            report_no TEXT,
            receive_date TEXT,
            status TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS metering (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_no TEXT,
            officer TEXT,
            start_date TEXT,
            submit_date TEXT,
            approve_date TEXT,
            status TEXT,
            approved_value REAL,
            reject_ratio REAL,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id TEXT,
            period TEXT,
            value REAL,
            support_date TEXT,
            work_order TEXT,
            work_order_date TEXT,
            invoice_no TEXT,
            invoice_date TEXT,
            sap_date TEXT,
            sap_status TEXT,
            due_date TEXT,
            paid_date TEXT,
            collected REAL,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS cash_actual (
            month_index INTEGER PRIMARY KEY,
            amount REAL
        );
        CREATE TABLE IF NOT EXISTS sop (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage TEXT,
            owner TEXT,
            inputs TEXT,
            outputs TEXT,
            duration TEXT,
            evidence TEXT
        );
        CREATE TABLE IF NOT EXISTS construction_works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_no TEXT,
            work_date TEXT,
            site TEXT,
            district TEXT,
            work_type TEXT,
            station_no TEXT,
            status TEXT,
            supervisor TEXT,
            value REAL,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS quality_clearances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_no TEXT,
            clearance_no TEXT,
            request_date TEXT,
            issue_date TEXT,
            contractor TEXT,
            status TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS quality_inspections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_no TEXT,
            inspect_date TEXT,
            inspector TEXT,
            result TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS safety_permits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            permit_no TEXT,
            ticket_no TEXT,
            permit_date TEXT,
            location TEXT,
            issuer TEXT,
            receiver TEXT,
            work_desc TEXT,
            status TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS safety_incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_no TEXT,
            incident_date TEXT,
            location TEXT,
            incident_type TEXT,
            severity TEXT,
            status TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS warehouse_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_no TEXT,
            item_name TEXT,
            unit TEXT,
            category TEXT,
            min_qty REAL,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS warehouse_tx (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voucher_no TEXT,
            tx_date TEXT,
            tx_type TEXT,
            item_no TEXT,
            item_name TEXT,
            unit TEXT,
            qty REAL,
            recipient TEXT,
            ticket_no TEXT,
            region TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS external_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_no TEXT,
            purchase_date TEXT,
            supplier TEXT,
            item_name TEXT,
            qty REAL,
            unit_price REAL,
            status TEXT,
            ticket_no TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS custody (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            custody_no TEXT,
            custody_date TEXT,
            employee TEXT,
            item_name TEXT,
            qty REAL,
            status TEXT,
            return_date TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS workshop_cars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plate_no TEXT,
            car_type TEXT,
            driver TEXT,
            team TEXT,
            status TEXT,
            last_service TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS workshop_equipment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equip_no TEXT,
            equip_name TEXT,
            status TEXT,
            location TEXT,
            last_service TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS contractor_works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_no TEXT,
            work_date TEXT,
            contractor TEXT,
            ticket_no TEXT,
            work_type TEXT,
            site TEXT,
            status TEXT,
            value REAL,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS hr_employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_no TEXT,
            full_name TEXT,
            job_title TEXT,
            department TEXT,
            phone TEXT,
            status TEXT,
            join_date TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_no TEXT,
            contract_name TEXT,
            party TEXT,
            start_date TEXT,
            end_date TEXT,
            value REAL,
            status TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            full_name TEXT,
            role TEXT,
            active INTEGER DEFAULT 1,
            password TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT,
            action TEXT,
            entity TEXT,
            entity_id TEXT,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            ticket_no TEXT,
            section TEXT,
            priority TEXT,
            due_date TEXT,
            assignee TEXT,
            status TEXT,
            notes TEXT,
            created_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            closed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_no TEXT,
            review_date TEXT,
            reviewer TEXT,
            result TEXT,
            score INTEGER,
            checklist_json TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # migrations for older DBs
    inv_cols = [r[1] for r in cur.execute("PRAGMA table_info(invoices)").fetchall()]
    if "ticket_no" not in inv_cols:
        cur.execute("ALTER TABLE invoices ADD COLUMN ticket_no TEXT")

    # تأكيد الجداول المضافة لاحقاً (حتى لو استُعيدت قاعدة قديمة)
    ensure_schema(conn)

    # seed settings
    for k, v in DEFAULT_SETTINGS.items():
        cur.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, json.dumps(v)))
    for k, v in DEFAULT_LISTS.items():
        cur.execute("INSERT OR IGNORE INTO lists(key,value) VALUES (?,?)", (k, json.dumps(v, ensure_ascii=False)))

    if cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO users(username, full_name, role, active, password, notes) VALUES (?,?,?,?,?,?)",
            [
                ("admin", "مدير النظام", "admin", 1, "admin123", "حساب افتراضي"),
                ("supervisor", "مشرف المكتب", "مشرف", 1, "1234", ""),
                ("dataentry", "مدخل بيانات", "مدخل بيانات", 1, "1234", ""),
            ],
        )

    if cur.execute("SELECT COUNT(*) FROM warehouse_items").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO warehouse_items(item_no, item_name, unit, category, min_qty, notes) VALUES (?,?,?,?,?,?)",
            [
                ("900271001", "MCCB 400A", "EA", "مواد كهربائية", 2, ""),
                ("320272001", "قاطع محطة", "EA", "مواد كهربائية", 1, ""),
            ],
        )

    if cur.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 0:
        cur.execute(
            "INSERT INTO contracts(contract_no, contract_name, party, start_date, end_date, value, status, notes) VALUES (?,?,?,?,?,?,?,?)",
            ("C-KHR-2026", "عقد خدمات خريص", "الشركة السعودية للكهرباء", "2026-01-01", "2026-12-31", 12000000, "ساري", ""),
        )

    if cur.execute("SELECT COUNT(*) FROM workshop_cars").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO workshop_cars(plate_no, car_type, driver, team, status, last_service, notes) VALUES (?,?,?,?,?,?,?)",
            [
                ("أ ب ج 1234", "دينا غمارتين", "ارفين", "فرقة 1 مدثر", "عاملة", "2026-06-01", ""),
                ("د هـ و 5678", "دينا غمارتين", "امير عبد الله", "فرقة 2 ردوي", "عاملة", "2026-05-15", ""),
            ],
        )

    if cur.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO teams(name,leader,technicians,driver,vehicle,area,status,notes) VALUES (?,?,?,?,?,?,?,?)",
            [
                ("فرقة 1", "ارفين", 4, "ارفين", "دينا غمارتين", "خريص", "نشطة", ""),
                ("فرقة 2", "ناتيش", 4, "امير عبد الله", "دينا غمارتين", "خريص", "نشطة", ""),
                ("فرقة 3", "حفر", 6, "مصطفى فخري", "", "خريص", "نشطة", ""),
            ],
        )

    if cur.execute("SELECT COUNT(*) FROM sop").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO sop(stage,owner,inputs,outputs,duration,evidence) VALUES (?,?,?,?,?,?)",
            SOP_ROWS,
        )

    if cur.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0:
        cur.execute(
            """
            INSERT INTO tickets(
              ticket_no, receive_date, district, receive_time, agent, station_no, feeder_no,
              location, fault_type, classification, team, dispatch_time, status, items_value, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "43656357",
                "2026-07-14",
                "النهضة",
                "14:20",
                "التجاني",
                "28514",
                "4",
                "https://maps.app.goo.gl/MwWrBsZmqCY1XHvf7",
                "قاطع محطة عطلان",
                "متوسط",
                "فرقة 1 مدثر",
                "14:25",
                "جديد",
                None,
                "",
            ),
        )

    for i in range(12):
        cur.execute("INSERT OR IGNORE INTO cash_actual(month_index, amount) VALUES (?, NULL)", (i,))

    conn.commit()
    conn.close()


def get_settings(conn=None):
    own = conn is None
    conn = conn or connect()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    data = {**DEFAULT_SETTINGS}
    for r in rows:
        try:
            data[r["key"]] = json.loads(r["value"])
        except Exception:
            data[r["key"]] = r["value"]
    if own:
        conn.close()
    return data


def save_settings(data):
    conn = connect()
    for k, v in data.items():
        conn.execute(
            "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, json.dumps(v)),
        )
    conn.commit()
    conn.close()


def get_lists(conn=None):
    own = conn is None
    conn = conn or connect()
    rows = conn.execute("SELECT key, value FROM lists").fetchall()
    data = {**DEFAULT_LISTS}
    for r in rows:
        try:
            data[r["key"]] = json.loads(r["value"])
        except Exception:
            pass
    # دمج القيم الافتراضية الجديدة (مثل رصيد افتتاحي) دون حذف تخصيص المستخدم
    for key, defaults in DEFAULT_LISTS.items():
        current = data.get(key) or []
        if not isinstance(current, list):
            continue
        merged = list(current)
        for val in defaults:
            if val not in merged:
                merged.append(val)
        data[key] = merged
    if own:
        conn.close()
    return data


def save_lists(data):
    conn = connect()
    for k, v in data.items():
        conn.execute(
            "INSERT INTO lists(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, json.dumps(v, ensure_ascii=False)),
        )
    conn.commit()
    conn.close()


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


def log_audit(user_name, action, entity, entity_id="", details=""):
    conn = connect()
    conn.execute(
        "INSERT INTO audit_log(user_name, action, entity, entity_id, details) VALUES (?,?,?,?,?)",
        (user_name or "نظام", action, entity, str(entity_id or ""), details or ""),
    )
    conn.commit()
    conn.close()


def warehouse_tx_sign(tx_type: str) -> int:
    """+1 وارد، -1 منصرف، 0 غير معروف."""
    t = tx_type or ""
    if "وارد" in t or "افتتاح" in t:
        return 1
    if "منصرف" in t or "إرجاع" in t:
        return -1
    return 0


def warehouse_balance(item_no):
    """رصيد المادة = الوارد - المنصرف حسب نوع الحركة."""
    return warehouse_balance_detail(item_no)["balance"]


def warehouse_balance_detail(item_no):
    conn = connect()
    rows = conn.execute(
        "SELECT tx_type, qty, ticket_no FROM warehouse_tx WHERE lower(item_no)=lower(?)",
        (item_no or "",),
    ).fetchall()
    conn.close()
    inbound = outbound = 0.0
    tickets = set()
    for r in rows:
        qty = float(r["qty"] or 0)
        sign = warehouse_tx_sign(r["tx_type"])
        if sign > 0:
            inbound += qty
        elif sign < 0:
            outbound += qty
        if r["ticket_no"]:
            tickets.add(r["ticket_no"])
    return {
        "balance": inbound - outbound,
        "inbound": inbound,
        "outbound": outbound,
        "tx_count": len(rows),
        "tickets": sorted(tickets),
    }


def list_warehouse_items():
    conn = connect()
    rows = rows_to_dicts(conn.execute("SELECT * FROM warehouse_items ORDER BY item_no").fetchall())
    conn.close()
    return rows


def enrich_warehouse_tx_from_item(data: dict) -> dict:
    """يربط حركة المستودع ببيانات الصنف (اسم/وحدة) من رقم المادة."""
    item_no = (data.get("item_no") or "").strip()
    if not item_no:
        return data
    conn = connect()
    item = conn.execute(
        "SELECT * FROM warehouse_items WHERE lower(item_no)=lower(?)",
        (item_no,),
    ).fetchone()
    conn.close()
    if item:
        if not data.get("item_name"):
            data["item_name"] = item["item_name"]
        if not data.get("unit"):
            data["unit"] = item["unit"]
    return data
