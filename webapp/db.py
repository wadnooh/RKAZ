import json
import os
import re
import sqlite3
from datetime import datetime
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
    "er_prefix": "ER",
    "er_next": 1,
    "rr_prefix": "RR",
    "rr_next": 1,
    "pr_prefix": "PR",
    "pr_next": 1,
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
    "project_types": ["خاصة", "كهرباء"],
    "project_status": ["جديد", "قيد التنفيذ", "موقوف", "مكتمل", "مغلق"],
    "work_class": ["اعتيادي", "طوارئ"],
}

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
    "projects": """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_code TEXT,
            project_name TEXT,
            project_type TEXT,
            status TEXT,
            start_date TEXT,
            end_date TEXT,
            ticket_no TEXT,
            site TEXT,
            notes TEXT
        )
    """,
    "primary_team_orders": """
        CREATE TABLE IF NOT EXISTS primary_team_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_order TEXT,
            extract_no TEXT,
            amount REAL,
            order_date TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "boq_items": """
        CREATE TABLE IF NOT EXISTS boq_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_no TEXT,
            description TEXT,
            short_desc TEXT,
            long_desc TEXT,
            line_type TEXT,
            unit TEXT,
            unit_price REAL,
            currency TEXT,
            payment_type TEXT,
            category TEXT,
            notes TEXT
        )
    """,
    "contract_boq_files": """
        CREATE TABLE IF NOT EXISTS contract_boq_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            uploaded_by TEXT,
            is_active INTEGER DEFAULT 0,
            item_count INTEGER DEFAULT 0,
            notes TEXT
        )
    """,
    "contract_boq_items": """
        CREATE TABLE IF NOT EXISTS contract_boq_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER,
            item_no TEXT,
            description TEXT,
            short_desc TEXT,
            long_desc TEXT,
            line_type TEXT,
            unit TEXT,
            unit_price REAL,
            currency TEXT,
            amount REAL,
            payment_type TEXT,
            category TEXT,
            notes TEXT
        )
    """,
    "ticket_boq_lines": """
        CREATE TABLE IF NOT EXISTS ticket_boq_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER,
            ticket_no TEXT,
            file_id INTEGER,
            item_no TEXT,
            description TEXT,
            unit TEXT,
            qty REAL,
            unit_price REAL,
            line_total REAL,
            work_class TEXT,
            increase_ratio REAL,
            final_total REAL,
            notes TEXT
        )
    """,
}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl_type: str = "TEXT") -> bool:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column in cols:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
    return True


def ensure_schema(conn: sqlite3.Connection | None = None) -> list[str]:
    """إنشاء أي جداول ناقصة وأعمدة مضافة (مهم بعد استعادة نسخة قديمة على Render)."""
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
        # أعمدة الترقيم والربط
        if "tickets" in existing or "tickets" in created:
            if _ensure_column(conn, "tickets", "rekaz_code"):
                created.append("tickets.rekaz_code")
        if "quality_clearances" in existing or "quality_clearances" in created:
            if _ensure_column(conn, "quality_clearances", "rekaz_code"):
                created.append("quality_clearances.rekaz_code")
        if "warehouse_tx" in existing or "warehouse_tx" in created:
            if _ensure_column(conn, "warehouse_tx", "rekaz_code"):
                created.append("warehouse_tx.rekaz_code")
            if _ensure_column(conn, "warehouse_tx", "source_section"):
                created.append("warehouse_tx.source_section")
            if _ensure_column(conn, "warehouse_tx", "source_ref"):
                created.append("warehouse_tx.source_ref")
            n = backfill_warehouse_tx_sources(conn)
            if n:
                created.append(f"warehouse_tx.source_backfill:{n}")
        # أعمدة دليل بنود العقد الموسّع (قالب Excel ثنائي اللغة)
        for table in ("boq_items", "contract_boq_items"):
            if table in existing or table in created:
                for col, ddl in (
                    ("short_desc", "TEXT"),
                    ("long_desc", "TEXT"),
                    ("line_type", "TEXT"),
                    ("currency", "TEXT"),
                    ("payment_type", "TEXT"),
                ):
                    if _ensure_column(conn, table, col, ddl):
                        created.append(f"{table}.{col}")
        # تعبئة أكواد ER للأعطال القديمة الفارغة
        if "tickets" in existing or "tickets" in created:
            missing = conn.execute(
                "SELECT id FROM tickets WHERE rekaz_code IS NULL OR trim(rekaz_code)='' ORDER BY id"
            ).fetchall()
            for row in missing:
                code = next_series_code("er", conn)
                conn.execute("UPDATE tickets SET rekaz_code=? WHERE id=?", (code, row["id"]))
                created.append(f"tickets.rekaz_code:{code}")
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)",
                (k, json.dumps(v)),
            )
        for k, v in DEFAULT_LISTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO lists(key,value) VALUES (?,?)",
                (k, json.dumps(v, ensure_ascii=False)),
            )
        if created:
            conn.commit()
        else:
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
            rekaz_code TEXT,
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
            rekaz_code TEXT,
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
            rekaz_code TEXT,
            source_section TEXT,
            source_ref TEXT,
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
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_code TEXT,
            project_name TEXT,
            project_type TEXT,
            status TEXT,
            start_date TEXT,
            end_date TEXT,
            ticket_no TEXT,
            site TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS boq_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_no TEXT,
            description TEXT,
            short_desc TEXT,
            long_desc TEXT,
            line_type TEXT,
            unit TEXT,
            unit_price REAL,
            currency TEXT,
            payment_type TEXT,
            category TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS contract_boq_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            uploaded_by TEXT,
            is_active INTEGER DEFAULT 0,
            item_count INTEGER DEFAULT 0,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS contract_boq_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER,
            item_no TEXT,
            description TEXT,
            short_desc TEXT,
            long_desc TEXT,
            line_type TEXT,
            unit TEXT,
            unit_price REAL,
            currency TEXT,
            amount REAL,
            payment_type TEXT,
            category TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS ticket_boq_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER,
            ticket_no TEXT,
            file_id INTEGER,
            item_no TEXT,
            description TEXT,
            unit TEXT,
            qty REAL,
            unit_price REAL,
            line_total REAL,
            work_class TEXT,
            increase_ratio REAL,
            final_total REAL,
            notes TEXT
        );
        """
    )

    # migrations for older DBs
    inv_cols = [r[1] for r in cur.execute("PRAGMA table_info(invoices)").fetchall()]
    if "ticket_no" not in inv_cols:
        cur.execute("ALTER TABLE invoices ADD COLUMN ticket_no TEXT")
    _ensure_column(conn, "tickets", "rekaz_code")
    _ensure_column(conn, "quality_clearances", "rekaz_code")
    _ensure_column(conn, "warehouse_tx", "rekaz_code")
    _ensure_column(conn, "warehouse_tx", "source_section")
    _ensure_column(conn, "warehouse_tx", "source_ref")
    backfill_warehouse_tx_sources(conn)
    for _boq_table in ("boq_items", "contract_boq_items"):
        for _col in ("short_desc", "long_desc", "line_type", "currency", "payment_type"):
            _ensure_column(conn, _boq_table, _col)

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

    # على السحابة مع S3: لا تزرع عطل تجريبي — حتى لا تُحسب القاعدة «ممتلئة» وتُتخطّى الاستعادة
    _seed_demo = os.environ.get("RAKAZ_SEED_DEMO", "").strip().lower() in {"1", "true", "yes", "on"}
    _cloudish = bool(
        os.environ.get("RENDER")
        or os.environ.get("RAKAZ_CLOUD", "").strip()
        or os.environ.get("AWS_S3_BUCKET", "").strip()
    )
    if (_seed_demo or not _cloudish) and cur.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0:
        cur.execute(
            """
            INSERT INTO tickets(
              ticket_no, rekaz_code, receive_date, district, receive_time, agent, station_no, feeder_no,
              location, fault_type, classification, team, dispatch_time, status, items_value, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "43656357",
                "ER-1",
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
        cur.execute(
            "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("er_next", json.dumps(2)),
        )

    if cur.execute("SELECT COUNT(*) FROM boq_items").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO boq_items(item_no, description, unit, unit_price, category, notes) VALUES (?,?,?,?,?,?)",
            [
                ("1.1", "حفر خندق كيبل", "م.ط", 85, "أعمال ترابية", ""),
                ("2.1", "تمديد كيبل متوسط الجهد", "م.ط", 120, "كيابل", ""),
                ("3.1", "استبدال قاطع محطة", "عدد", 2500, "معدات", ""),
                ("4.1", "ردم وتسوية", "م³", 45, "أعمال ترابية", ""),
            ],
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


def next_series_code(series: str, conn=None) -> str:
    """يولد كوداً متسلسلاً: ER-1 / RR-1 / PR-1 ثم يزيد العداد في الإعدادات."""
    series = (series or "").strip().lower()
    prefix_key = f"{series}_prefix"
    next_key = f"{series}_next"
    defaults = {
        "er": ("ER", "er_next", "er_prefix"),
        "rr": ("RR", "rr_next", "rr_prefix"),
        "pr": ("PR", "pr_next", "pr_prefix"),
    }
    if series not in defaults:
        raise ValueError(f"سلسلة غير معروفة: {series}")
    default_prefix, next_key, prefix_key = defaults[series]
    own = conn is None
    conn = conn or connect()
    settings = get_settings(conn)
    prefix = str(settings.get(prefix_key) or default_prefix).strip() or default_prefix
    n = int(settings.get(next_key) or 1)
    # تجنب التصادم إن وُجدت أكواد يدوياً
    for _ in range(5000):
        code = f"{prefix}-{n}"
        taken = False
        if series == "er":
            taken = conn.execute(
                "SELECT 1 FROM tickets WHERE lower(rekaz_code)=lower(?) LIMIT 1", (code,)
            ).fetchone()
        elif series == "rr":
            taken = conn.execute(
                "SELECT 1 FROM quality_clearances WHERE lower(rekaz_code)=lower(?) LIMIT 1", (code,)
            ).fetchone()
        elif series == "pr":
            taken = conn.execute(
                "SELECT 1 FROM projects WHERE lower(project_code)=lower(?) LIMIT 1", (code,)
            ).fetchone()
        if not taken:
            conn.execute(
                "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (next_key, json.dumps(n + 1)),
            )
            if own:
                conn.commit()
                conn.close()
            return code
        n += 1
    if own:
        conn.close()
    raise RuntimeError("تعذر توليد كود جديد")


def count_warehouse_tx_by_source(source: str | None = None, conn=None) -> int:
    """عدد حركات المستودع حسب القسم المصدر (ops/constructions/projects)."""
    own = conn is None
    conn = conn or connect()
    source = (source or "").strip().lower()
    if source in ("ops", "constructions", "projects"):
        n = conn.execute(
            "SELECT COUNT(*) FROM warehouse_tx WHERE lower(coalesce(source_section,''))=?",
            (source,),
        ).fetchone()[0]
    else:
        n = conn.execute("SELECT COUNT(*) FROM warehouse_tx").fetchone()[0]
    if own:
        conn.close()
    return int(n or 0)


def backfill_warehouse_tx_sources(conn=None) -> int:
    """يملأ source_section للسجلات القديمة: عطل → ops."""
    own = conn is None
    conn = conn or connect()
    cur = conn.execute(
        """
        UPDATE warehouse_tx
        SET source_section='ops',
            source_ref=CASE
              WHEN coalesce(trim(source_ref),'')='' THEN coalesce(ticket_no,'')
              ELSE source_ref
            END
        WHERE (source_section IS NULL OR trim(source_section)='')
          AND ticket_no IS NOT NULL AND trim(ticket_no)<>''
        """
    )
    n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    if own:
        conn.commit()
        conn.close()
    return int(n or 0)


def next_warehouse_voucher_no(conn=None) -> str:
    """يولد رقم سند متسلسل بصيغة R-YY-001 (مثال: R-26-001)."""
    own = conn is None
    conn = conn or connect()
    year = datetime.now().strftime("%y")
    prefix = f"R-{year}-"
    rows = conn.execute(
        "SELECT voucher_no FROM warehouse_tx WHERE voucher_no LIKE ?",
        (f"{prefix}%",),
    ).fetchall()
    if own:
        conn.close()
    max_n = 0
    pat = re.compile(rf"^R-{re.escape(year)}-(\d+)$", re.IGNORECASE)
    for row in rows:
        voucher = (row[0] if not isinstance(row, sqlite3.Row) else row["voucher_no"]) or ""
        m = pat.match(str(voucher).strip())
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"{prefix}{max_n + 1:03d}"


def list_ticket_options(conn=None):
    """خيارات اختيار العطل: قيمة = رقم العطل، عرض = رقم العطل + كود ER."""
    own = conn is None
    conn = conn or connect()
    rows = rows_to_dicts(
        conn.execute(
            "SELECT ticket_no, rekaz_code FROM tickets ORDER BY id DESC"
        ).fetchall()
    )
    if own:
        conn.close()
    options = []
    for r in rows:
        tno = r.get("ticket_no") or ""
        if not tno:
            continue
        er = (r.get("rekaz_code") or "").strip()
        label = f"{tno} — {er}" if er else tno
        options.append({"value": tno, "label": label, "rekaz_code": er})
    return options


def resolve_ticket_ref(ref: str, conn=None) -> dict | None:
    """يحلّ رقم العطل أو كود ER إلى سجل العطل."""
    ref = (ref or "").strip()
    if not ref:
        return None
    own = conn is None
    conn = conn or connect()
    row = conn.execute(
        "SELECT * FROM tickets WHERE ticket_no=? OR lower(rekaz_code)=lower(?) LIMIT 1",
        (ref, ref),
    ).fetchone()
    if own:
        conn.close()
    return dict(row) if row else None


def enrich_warehouse_tx_codes(data: dict, conn=None) -> dict:
    """يربط حركة المستودع برقم العطل وكود ER."""
    own = conn is None
    conn = conn or connect()
    tno = (data.get("ticket_no") or "").strip()
    er = (data.get("rekaz_code") or "").strip()
    ticket = None
    if tno:
        ticket = resolve_ticket_ref(tno, conn)
    elif er:
        ticket = resolve_ticket_ref(er, conn)
    if ticket:
        data["ticket_no"] = ticket.get("ticket_no") or tno
        data["rekaz_code"] = ticket.get("rekaz_code") or er
    elif er and not data.get("rekaz_code"):
        data["rekaz_code"] = er
    if own:
        conn.close()
    return data


def is_outbound_warehouse_tx(tx_type: str) -> bool:
    return warehouse_tx_sign(tx_type) < 0


def list_boq_items(conn=None):
    """بنود الدليل النشط — من contract_boq_items إن وُجد، وإلا boq_items."""
    own = conn is None
    conn = conn or connect()
    active = conn.execute(
        "SELECT id FROM contract_boq_files WHERE is_active=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if active:
        rows = rows_to_dicts(
            conn.execute(
                "SELECT * FROM contract_boq_items WHERE file_id=? ORDER BY id",
                (active["id"],),
            ).fetchall()
        )
    else:
        rows = rows_to_dicts(conn.execute("SELECT * FROM boq_items ORDER BY item_no").fetchall())
    if own:
        conn.close()
    return rows


def has_boq_catalog(conn=None) -> bool:
    """هل يوجد أي بند في الدليل النشط (بدون تحميل القائمة كاملة)."""
    own = conn is None
    conn = conn or connect()
    active = conn.execute(
        "SELECT id FROM contract_boq_files WHERE is_active=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if active:
        row = conn.execute(
            "SELECT 1 FROM contract_boq_items WHERE file_id=? LIMIT 1",
            (active["id"],),
        ).fetchone()
    else:
        row = conn.execute("SELECT 1 FROM boq_items LIMIT 1").fetchone()
    if own:
        conn.close()
    return bool(row)


def active_contract_boq_file(conn=None):
    own = conn is None
    conn = conn or connect()
    row = conn.execute(
        "SELECT * FROM contract_boq_files WHERE is_active=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if own:
        conn.close()
    return dict(row) if row else None


def list_contract_boq_files(conn=None):
    own = conn is None
    conn = conn or connect()
    rows = rows_to_dicts(
        conn.execute("SELECT * FROM contract_boq_files ORDER BY id DESC").fetchall()
    )
    if own:
        conn.close()
    return rows


def get_contract_boq_item(item_no: str, conn=None):
    own = conn is None
    conn = conn or connect()
    active = conn.execute(
        "SELECT id FROM contract_boq_files WHERE is_active=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    row = None
    if active:
        row = conn.execute(
            "SELECT * FROM contract_boq_items WHERE file_id=? AND lower(item_no)=lower(?) LIMIT 1",
            (active["id"], item_no or ""),
        ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT * FROM boq_items WHERE lower(item_no)=lower(?) LIMIT 1",
            (item_no or "",),
        ).fetchone()
    if own:
        conn.close()
    return dict(row) if row else None


def boq_display_label(item: dict | None) -> str:
    """نص العرض في قوائم الاختيار: رقم البند + التوصيف المختصر."""
    if not item:
        return ""
    item_no = (item.get("item_no") or "").strip()
    short = (item.get("short_desc") or item.get("description") or "").strip()
    if item_no and short:
        return f"{item_no} — {short}"
    return item_no or short


def enrich_quantity_from_boq(data: dict, conn=None) -> dict:
    """يملأ الوصف والوحدة والسعر من دليل بنود العقد عند إدخال رقم البند."""
    item_no = (data.get("item_no") or "").strip()
    if not item_no:
        return data
    own = conn is None
    conn = conn or connect()
    item = get_contract_boq_item(item_no, conn)
    if own:
        conn.close()
    if item:
        if not data.get("description"):
            data["description"] = (
                item.get("short_desc") or item.get("description") or ""
            )
        if not data.get("unit"):
            data["unit"] = item.get("unit") or ""
        if data.get("unit_price") in (None, "", 0, 0.0):
            data["unit_price"] = item.get("unit_price")
    return data


def calc_boq_line_totals(qty, unit_price, work_class: str, increase_ratio) -> dict:
    q = float(qty or 0)
    p = float(unit_price or 0)
    line_total = q * p
    ratio = float(increase_ratio or 0)
    cls = (work_class or "اعتيادي").strip()
    if cls == "طوارئ":
        if ratio <= 0:
            settings = get_settings()
            ratio = float(settings.get("emergency_ratio") or 0)
        final_total = line_total * (1 + ratio)
    else:
        ratio = 0.0
        final_total = line_total
    return {
        "line_total": round(line_total, 2),
        "increase_ratio": ratio,
        "final_total": round(final_total, 2),
        "work_class": cls if cls in ("اعتيادي", "طوارئ") else "اعتيادي",
    }


def list_ticket_boq_lines(ticket_id: int | None = None, ticket_no: str | None = None, conn=None):
    own = conn is None
    conn = conn or connect()
    if ticket_id:
        rows = rows_to_dicts(
            conn.execute(
                "SELECT * FROM ticket_boq_lines WHERE ticket_id=? ORDER BY id",
                (ticket_id,),
            ).fetchall()
        )
    elif ticket_no:
        rows = rows_to_dicts(
            conn.execute(
                "SELECT * FROM ticket_boq_lines WHERE ticket_no=? ORDER BY id",
                (ticket_no,),
            ).fetchall()
        )
    else:
        rows = []
    if own:
        conn.close()
    return rows


def ticket_boq_final_total(
    ticket_id: int | None = None, ticket_no: str | None = None, conn=None
) -> float | None:
    """مجموع final_total لبنود العقد (القيمة المعتمدة / مبلغ الكميات). None إن لم تُوجد بنود."""
    own = conn is None
    conn = conn or connect()
    if ticket_id:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(final_total),0) AS total "
            "FROM ticket_boq_lines WHERE ticket_id=?",
            (ticket_id,),
        ).fetchone()
    elif ticket_no:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(final_total),0) AS total "
            "FROM ticket_boq_lines WHERE ticket_no=?",
            (str(ticket_no).strip(),),
        ).fetchone()
    else:
        row = None
    if own:
        conn.close()
    if not row or int(row["n"] or 0) <= 0:
        return None
    return round(float(row["total"] or 0), 2)


def sync_ticket_items_value(ticket_id: int, conn=None) -> float:
    """يحدّث قيمة البنود على العطل من مجموع final_total لبنود العقد."""
    own = conn is None
    conn = conn or connect()
    row = conn.execute(
        "SELECT COALESCE(SUM(final_total),0) AS total FROM ticket_boq_lines WHERE ticket_id=?",
        (ticket_id,),
    ).fetchone()
    total = float(row["total"] or 0) if row else 0.0
    conn.execute(
        "UPDATE tickets SET items_value=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (total, ticket_id),
    )
    if own:
        conn.commit()
        conn.close()
    return total


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


def clear_warehouse_balances() -> int:
    """يمسح كل حركات المستودع فيصفر الأرصدة (لا يحذف أصناف المواد)."""
    conn = connect()
    cur = conn.execute("DELETE FROM warehouse_tx")
    deleted = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    conn.commit()
    conn.close()
    return int(deleted)


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
