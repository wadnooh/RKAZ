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
        "إرجاع للكهرباء",
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
    "new_coord_status": ["مسودة", "قيد التنسيق", "بانتظار الرخصة", "تم الإصدار", "مرفوض", "ملغي"],
    "issued_license_status": ["سارية", "منتهية", "ملغاة"],
    "license_types": ["بلدية", "أمانة", "كهرباء", "حفر", "أخرى"],
    "linked_sections": ["الإنشاءات", "العمليات والصيانة", "المشاريع"],
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
    "new_coordinations": """
        CREATE TABLE IF NOT EXISTS new_coordinations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coord_no TEXT,
            request_date TEXT,
            authority TEXT,
            work_desc TEXT,
            location TEXT,
            district TEXT,
            linked_section TEXT,
            ticket_no TEXT,
            project_code TEXT,
            construction_work_no TEXT,
            status TEXT,
            license_no TEXT,
            issue_date TEXT,
            expiry_date TEXT,
            officer TEXT,
            transferred_license_id INTEGER,
            notes TEXT
        )
    """,
    "issued_licenses": """
        CREATE TABLE IF NOT EXISTS issued_licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_no TEXT,
            issue_date TEXT,
            expiry_date TEXT,
            authority TEXT,
            license_type TEXT,
            status TEXT,
            new_coordination_id INTEGER,
            transferred_at TEXT,
            linked_section TEXT,
            ticket_no TEXT,
            project_code TEXT,
            construction_work_no TEXT,
            location TEXT,
            work_desc TEXT,
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
        if "construction_works" in existing or "construction_works" in created:
            if _ensure_column(conn, "construction_works", "ticket_no"):
                created.append("construction_works.ticket_no")
        # ربط معاملات الحفر بالتنسيقات لبدء إجراءات الإخلاء
        n_exc = link_excavation_transactions_to_coordination(conn)
        if n_exc:
            created.append(f"excavation_coordination_link:{n_exc}")
        if "warehouse_tx" in existing or "warehouse_tx" in created:
            if _ensure_column(conn, "warehouse_tx", "rekaz_code"):
                created.append("warehouse_tx.rekaz_code")
            if _ensure_column(conn, "warehouse_tx", "source_section"):
                created.append("warehouse_tx.source_section")
            if _ensure_column(conn, "warehouse_tx", "source_ref"):
                created.append("warehouse_tx.source_ref")
            if _ensure_column(conn, "warehouse_tx", "sender"):
                created.append("warehouse_tx.sender")
            if _ensure_column(conn, "warehouse_tx", "work_order"):
                created.append("warehouse_tx.work_order")
            n = backfill_warehouse_tx_sources(conn)
            if n:
                created.append(f"warehouse_tx.source_backfill:{n}")
            n_wo = backfill_warehouse_tx_work_orders(conn)
            if n_wo:
                created.append(f"warehouse_tx.work_order_backfill:{n_wo}")
            n_scrub = scrub_ticket_numbers_from_warehouse_work_orders(conn)
            if n_scrub:
                created.append(f"warehouse_tx.work_order_scrub:{n_scrub}")
        # إصلاح مضاعفة نسبة الطوارئ على بنود العقد (مرة واحدة في القيمة النهائية فقط)
        if "ticket_boq_lines" in existing or "ticket_boq_lines" in created:
            n_boq = repair_boq_emergency_double_count(conn)
            if n_boq:
                created.append(f"ticket_boq_lines.emergency_once:{n_boq}")
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
            ticket_no TEXT,
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
            sender TEXT,
            ticket_no TEXT,
            rekaz_code TEXT,
            source_section TEXT,
            source_ref TEXT,
            work_order TEXT,
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
    _ensure_column(conn, "warehouse_tx", "sender")
    _ensure_column(conn, "warehouse_tx", "work_order")
    backfill_warehouse_tx_sources(conn)
    backfill_warehouse_tx_work_orders(conn)
    for _boq_table in ("boq_items", "contract_boq_items"):
        for _col in ("short_desc", "long_desc", "line_type", "currency", "payment_type"):
            _ensure_column(conn, _boq_table, _col)

    # تأكيد الجداول المضافة لاحقاً (حتى لو استُعيدت قاعدة قديمة)
    ensure_schema(conn)
    scrub_ticket_numbers_from_warehouse_work_orders(conn)

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
        "nc": ("NC", "nc_next", "nc_prefix"),
        "rl": ("RL", "rl_next", "rl_prefix"),
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
        elif series == "nc":
            taken = conn.execute(
                "SELECT 1 FROM new_coordinations WHERE lower(coord_no)=lower(?) LIMIT 1", (code,)
            ).fetchone()
        elif series == "rl":
            taken = conn.execute(
                "SELECT 1 FROM issued_licenses WHERE lower(license_no)=lower(?) LIMIT 1", (code,)
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


def backfill_warehouse_tx_work_orders(conn=None) -> int:
    """يملأ أمر العمل للحركات الناقصة من العطل / الفرق / المرجع."""
    own = conn is None
    conn = conn or connect()
    _ensure_column(conn, "warehouse_tx", "work_order")
    rows = rows_to_dicts(
        conn.execute(
            """
            SELECT id, ticket_no, source_ref, work_order, source_section
            FROM warehouse_tx
            WHERE coalesce(trim(work_order),'')=''
            """
        ).fetchall()
    )
    n = 0
    for r in rows:
        wo = resolve_tx_work_order(r, conn)
        if not wo:
            continue
        conn.execute("UPDATE warehouse_tx SET work_order=? WHERE id=?", (wo, r["id"]))
        n += 1
    if own:
        conn.commit()
        conn.close()
    return n


def apply_warehouse_tx_work_order(data: dict, conn=None) -> dict:
    """يضع أمر العمل الحقيقي فقط — يرفض رقم العطل / كود ER."""
    if data is None:
        return data
    own = conn is None
    conn = conn or connect()
    try:
        explicit = (data.get("work_order") or "").strip()
        if explicit and _is_ticket_identifier(explicit, conn, data):
            explicit = ""
        # ضع القيمة المنظّفة مؤقتاً حتى لا يعتمد resolve على رقم عطل مخزّن
        data["work_order"] = explicit
        resolved = resolve_tx_work_order(data, conn)
        data["work_order"] = resolved or explicit or ""
        if data["work_order"] and _is_ticket_identifier(data["work_order"], conn, data):
            data["work_order"] = ""
        return data
    finally:
        if own:
            conn.close()


def sync_warehouse_tx_work_order_for_ticket(
    ticket_no: str,
    work_order: str,
    rekaz_code: str = "",
    conn=None,
) -> int:
    """يحدّث أمر العمل في حركات المستودع المرتبطة بالعطل عند تغييره من الصفحة الرئيسية."""
    tno = (ticket_no or "").strip()
    if not tno:
        return 0
    own = conn is None
    conn = conn or connect()
    try:
        _ensure_column(conn, "warehouse_tx", "work_order")
        wo = (work_order or "").strip()
        # لا تكتب رقم العطل في عمود أمر العمل
        if wo and _is_ticket_identifier(wo, conn, {"ticket_no": tno, "rekaz_code": rekaz_code or ""}):
            wo = ""
        code = (rekaz_code or "").strip()
        if code:
            cur = conn.execute(
                """
                UPDATE warehouse_tx
                SET work_order=?
                WHERE ticket_no=?
                   OR (source_section='ops' AND source_ref=?)
                   OR (rekaz_code<>'' AND lower(rekaz_code)=lower(?))
                """,
                (wo, tno, tno, code),
            )
        else:
            cur = conn.execute(
                """
                UPDATE warehouse_tx
                SET work_order=?
                WHERE ticket_no=?
                   OR (source_section='ops' AND source_ref=?)
                """,
                (wo, tno, tno),
            )
        return int(cur.rowcount or 0)
    finally:
        if own:
            conn.commit()
            conn.close()


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
    """إجمالي البند = كمية × سعر فقط.

    نسبة الطوارئ تُحفظ على السطر للعرض، وتُطبَّق مرة واحدة لاحقاً على
    القيمة النهائية للعطل (وليس على إجمالي كل بند).
    """
    q = float(qty or 0)
    p = float(unit_price or 0)
    line_total = q * p
    ratio = float(increase_ratio or 0)
    cls = (work_class or "اعتيادي").strip()
    if cls == "طوارئ":
        if ratio <= 0:
            settings = get_settings()
            ratio = float(settings.get("emergency_ratio") or 0)
    else:
        ratio = 0.0
    if cls not in ("اعتيادي", "طوارئ"):
        cls = "اعتيادي"
    return {
        "line_total": round(line_total, 2),
        "increase_ratio": ratio,
        # النهائي على مستوى البند = الأساس (بدون نسبة) لمنع الحساب المزدوج
        "final_total": round(line_total, 2),
        "work_class": cls,
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


def ticket_emergency_ratio(lines, settings_ratio=None) -> float:
    """نسبة الطوارئ الموحّدة للعطل: تُؤخذ من أي بند طوارئ، وإلا 0."""
    best = None
    fallback = float(settings_ratio or 0)
    for line in lines or []:
        if (line.get("work_class") or "").strip() != "طوارئ":
            continue
        r = float(line.get("increase_ratio") or 0)
        if r <= 0:
            r = fallback
        if best is None or r > best:
            best = r
    return float(best or 0)


def apply_final_value(base, ratio) -> float | None:
    if base is None or base == "":
        return None
    return round(float(base) * (1 + float(ratio or 0)), 2)


def ticket_boq_base_total(
    ticket_id: int | None = None, ticket_no: str | None = None, conn=None
) -> float | None:
    """مجموع إجمالي البنود (كمية × سعر) بدون نسبة الطوارئ."""
    own = conn is None
    conn = conn or connect()
    if ticket_id:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(line_total),0) AS total "
            "FROM ticket_boq_lines WHERE ticket_id=?",
            (ticket_id,),
        ).fetchone()
    elif ticket_no:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(line_total),0) AS total "
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


def ticket_boq_final_total(
    ticket_id: int | None = None, ticket_no: str | None = None, conn=None
) -> float | None:
    """القيمة النهائية للعطل: إجمالي البنود ثم نسبة الطوارئ مرة واحدة إن وُجدت."""
    own = conn is None
    conn = conn or connect()
    lines = list_ticket_boq_lines(ticket_id=ticket_id, ticket_no=ticket_no, conn=conn)
    if own:
        pass  # list_ticket_boq_lines won't close if we passed conn
    if not lines:
        if own:
            conn.close()
        return None
    base = round(sum(float(x.get("line_total") or 0) for x in lines), 2)
    settings = get_settings(conn)
    ratio = ticket_emergency_ratio(lines, settings.get("emergency_ratio"))
    if own:
        conn.close()
    return apply_final_value(base, ratio)


def map_ticket_emergency_ratios(ticket_ids, settings_ratio=0.0, conn=None) -> dict:
    """خريطة ticket_id → نسبة الطوارئ (0 إن لم يوجد بند طوارئ)."""
    if not ticket_ids:
        return {}
    own = conn is None
    conn = conn or connect()
    placeholders = ",".join("?" * len(ticket_ids))
    rows = conn.execute(
        f"""
        SELECT ticket_id,
               MAX(CASE WHEN work_class='طوارئ' THEN 1 ELSE 0 END) AS has_em,
               MAX(CASE WHEN work_class='طوارئ' THEN COALESCE(increase_ratio,0) ELSE 0 END) AS em_ratio
        FROM ticket_boq_lines
        WHERE ticket_id IN ({placeholders})
        GROUP BY ticket_id
        """,
        list(ticket_ids),
    ).fetchall()
    if own:
        conn.close()
    fallback = float(settings_ratio or 0)
    out = {}
    for row in rows:
        tid = row["ticket_id"]
        if int(row["has_em"] or 0):
            r = float(row["em_ratio"] or 0)
            out[tid] = r if r > 0 else fallback
        else:
            out[tid] = 0.0
    return out


def sync_ticket_items_value(ticket_id: int, conn=None) -> float:
    """يحدّث قيمة البنود على العطل من مجموع line_total (بدون نسبة الطوارئ)."""
    own = conn is None
    conn = conn or connect()
    # إصلاح أي أسطر حُسبت عليها النسبة سابقاً على مستوى البند
    conn.execute(
        "UPDATE ticket_boq_lines SET final_total=line_total WHERE ticket_id=?",
        (ticket_id,),
    )
    row = conn.execute(
        "SELECT COALESCE(SUM(line_total),0) AS total FROM ticket_boq_lines WHERE ticket_id=?",
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


def repair_boq_emergency_double_count(conn=None) -> int:
    """إصلاح مضاعفة نسبة الطوارئ: إجمالي البند = كمية×سعر، والنسبة مرة واحدة في القيمة النهائية."""
    own = conn is None
    conn = conn or connect()
    fixed = 0
    rows = conn.execute(
        """
        SELECT id, ticket_id, qty, unit_price, line_total, final_total
        FROM ticket_boq_lines
        """
    ).fetchall()
    touched_tickets = set()
    for row in rows:
        base = round(float(row["qty"] or 0) * float(row["unit_price"] or 0), 2)
        line_total = float(row["line_total"] or 0) if row["line_total"] is not None else None
        final_total = row["final_total"]
        needs = (
            line_total is None
            or abs(line_total - base) > 0.0001
            or final_total is None
            or abs(float(final_total or 0) - base) > 0.0001
        )
        if needs:
            conn.execute(
                "UPDATE ticket_boq_lines SET line_total=?, final_total=? WHERE id=?",
                (base, base, row["id"]),
            )
            fixed += 1
            if row["ticket_id"] is not None:
                touched_tickets.add(row["ticket_id"])

    mismatched = conn.execute(
        """
        SELECT t.id
        FROM tickets t
        JOIN (
          SELECT ticket_id, SUM(line_total) AS base
          FROM ticket_boq_lines
          GROUP BY ticket_id
        ) b ON b.ticket_id = t.id
        WHERE ABS(COALESCE(t.items_value, 0) - COALESCE(b.base, 0)) > 0.0001
        """
    ).fetchall()
    for row in mismatched:
        touched_tickets.add(row["id"])
    for tid in touched_tickets:
        sync_ticket_items_value(tid, conn)
        fixed += 1
    if own:
        conn.commit()
        conn.close()
    return fixed


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


def is_excavation_text(*parts) -> bool:
    """يكتشف ذكر الحفر في أي نص (نوع عمل / وصف بند / ملاحظات…)."""
    blob = " ".join(str(p or "") for p in parts)
    return "حفر" in blob


def is_excavation_work_type(work_type: str | None) -> bool:
    return is_excavation_text(work_type)


def collect_excavation_ticket_nos(conn=None) -> list[str]:
    """كل أرقام الأعطال/المعاملات المرتبطة بحفر وتحتاج تنسيق/إخلاء."""
    own = conn is None
    conn = conn or connect()
    found: set[str] = set()

    def _add(val):
        t = str(val or "").strip()
        if t:
            found.add(t)

    for row in conn.execute(
        """
        SELECT ticket_no FROM contractor_works
        WHERE ticket_no IS NOT NULL AND trim(ticket_no) != ''
          AND (IFNULL(work_type,'') LIKE '%حفر%' OR IFNULL(notes,'') LIKE '%حفر%' OR IFNULL(site,'') LIKE '%حفر%')
        """
    ).fetchall():
        _add(row["ticket_no"])

    for row in conn.execute(
        """
        SELECT ticket_no FROM construction_works
        WHERE ticket_no IS NOT NULL AND trim(ticket_no) != ''
          AND (IFNULL(work_type,'') LIKE '%حفر%' OR IFNULL(notes,'') LIKE '%حفر%' OR IFNULL(site,'') LIKE '%حفر%')
        """
    ).fetchall():
        _add(row["ticket_no"])

    for row in conn.execute(
        """
        SELECT ticket_no FROM tickets
        WHERE ticket_no IS NOT NULL AND trim(ticket_no) != ''
          AND (
            IFNULL(asphalt_clearance,'') = 'نعم'
            OR IFNULL(fault_type,'') LIKE '%حفر%'
            OR IFNULL(notes,'') LIKE '%حفر%'
            OR IFNULL(location,'') LIKE '%حفر%'
          )
        """
    ).fetchall():
        _add(row["ticket_no"])

    for row in conn.execute(
        """
        SELECT DISTINCT ticket_no FROM ticket_boq_lines
        WHERE ticket_no IS NOT NULL AND trim(ticket_no) != ''
          AND (
            IFNULL(description,'') LIKE '%حفر%'
            OR IFNULL(item_no,'') LIKE '%حفر%'
            OR IFNULL(notes,'') LIKE '%حفر%'
          )
        """
    ).fetchall():
        _add(row["ticket_no"])

    for row in conn.execute(
        """
        SELECT DISTINCT ticket_no FROM quantities
        WHERE ticket_no IS NOT NULL AND trim(ticket_no) != ''
          AND (IFNULL(description,'') LIKE '%حفر%' OR IFNULL(notes,'') LIKE '%حفر%')
        """
    ).fetchall():
        _add(row["ticket_no"])

    if own:
        conn.close()
    return sorted(found)


def ticket_has_excavation(ticket_no: str, conn=None) -> bool:
    tno = str(ticket_no or "").strip()
    if not tno:
        return False
    own = conn is None
    conn = conn or connect()
    hit = False
    row = conn.execute(
        """
        SELECT asphalt_clearance, fault_type, notes, location
        FROM tickets WHERE ticket_no=? LIMIT 1
        """,
        (tno,),
    ).fetchone()
    if row and (
        (row["asphalt_clearance"] or "") == "نعم"
        or is_excavation_text(row["fault_type"], row["notes"], row["location"])
    ):
        hit = True
    if not hit:
        hit = bool(
            conn.execute(
                """
                SELECT 1 FROM ticket_boq_lines
                WHERE ticket_no=? AND (
                  IFNULL(description,'') LIKE '%حفر%'
                  OR IFNULL(notes,'') LIKE '%حفر%'
                ) LIMIT 1
                """,
                (tno,),
            ).fetchone()
        )
    if not hit:
        hit = bool(
            conn.execute(
                """
                SELECT 1 FROM quantities
                WHERE ticket_no=? AND (
                  IFNULL(description,'') LIKE '%حفر%' OR IFNULL(notes,'') LIKE '%حفر%'
                ) LIMIT 1
                """,
                (tno,),
            ).fetchone()
        )
    if not hit:
        hit = bool(
            conn.execute(
                """
                SELECT 1 FROM contractor_works
                WHERE ticket_no=? AND (
                  IFNULL(work_type,'') LIKE '%حفر%' OR IFNULL(notes,'') LIKE '%حفر%'
                ) LIMIT 1
                """,
                (tno,),
            ).fetchone()
        )
    if not hit:
        hit = bool(
            conn.execute(
                """
                SELECT 1 FROM construction_works
                WHERE ticket_no=? AND (
                  IFNULL(work_type,'') LIKE '%حفر%' OR IFNULL(notes,'') LIKE '%حفر%'
                ) LIMIT 1
                """,
                (tno,),
            ).fetchone()
        )
    if own:
        conn.close()
    return hit


def ensure_excavation_coordination(
    ticket_no: str,
    *,
    reason: str = "",
    conn=None,
    create_clearance: bool = True,
) -> dict:
    """يربط معاملة الحفر بالتنسيق ويفتح إجراء إخلاء الأسفلت إن لزم."""
    tno = str(ticket_no or "").strip()
    result = {
        "ticket_no": tno,
        "created_coord": False,
        "created_clearance": False,
        "coord_id": None,
        "clearance_id": None,
    }
    if not tno:
        return result
    own = conn is None
    conn = conn or connect()
    today = datetime.now().strftime("%Y-%m-%d")
    note = (reason or "ربط تلقائي — معاملة بها حفر لبدء إجراءات الإخلاء").strip()

    existing = conn.execute(
        "SELECT id, needs_asphalt, status, notes FROM coordination WHERE ticket_no=? ORDER BY id LIMIT 1",
        (tno,),
    ).fetchone()
    if existing:
        result["coord_id"] = existing["id"]
        updates = []
        vals = []
        if (existing["needs_asphalt"] or "").strip() != "نعم":
            updates.append("needs_asphalt=?")
            vals.append("نعم")
        if (existing["status"] or "").strip() in ("", "غير مطلوب"):
            updates.append("status=?")
            vals.append("بانتظار المختبر")
        if note and note not in (existing["notes"] or ""):
            merged = ((existing["notes"] or "").strip() + (" | " if existing["notes"] else "") + note).strip()
            updates.append("notes=?")
            vals.append(merged[:500])
        if updates:
            vals.append(existing["id"])
            conn.execute(
                f"UPDATE coordination SET {', '.join(updates)} WHERE id=?",
                vals,
            )
            result["created_coord"] = True  # updated to enable clearance flow
    else:
        cur = conn.execute(
            """
            INSERT INTO coordination(ticket_no, needs_asphalt, officer, request_date, status, notes)
            VALUES (?,?,?,?,?,?)
            """,
            (tno, "نعم", "", today, "بانتظار المختبر", note[:500]),
        )
        result["coord_id"] = cur.lastrowid
        result["created_coord"] = True

    if create_clearance:
        clr = conn.execute(
            "SELECT id, status FROM quality_clearances WHERE ticket_no=? ORDER BY id LIMIT 1",
            (tno,),
        ).fetchone()
        if clr:
            result["clearance_id"] = clr["id"]
            if (clr["status"] or "").strip() in ("",):
                conn.execute(
                    "UPDATE quality_clearances SET status=? WHERE id=?",
                    ("مطلوب", clr["id"]),
                )
                result["created_clearance"] = True
        else:
            rr = next_series_code("rr", conn)
            cur = conn.execute(
                """
                INSERT INTO quality_clearances(
                  ticket_no, clearance_no, rekaz_code, request_date, status, notes
                ) VALUES (?,?,?,?,?,?)
                """,
                (tno, rr, rr, today, "مطلوب", note[:500]),
            )
            result["clearance_id"] = cur.lastrowid
            result["created_clearance"] = True

    if own:
        conn.commit()
        conn.close()
    return result


def link_excavation_transactions_to_coordination(conn=None) -> int:
    """يربط كل معاملات الحفر الحالية بالتنسيقات وإجراءات الإخلاء."""
    own = conn is None
    conn = conn or connect()
    changed = 0
    for tno in collect_excavation_ticket_nos(conn):
        before_coord = conn.execute(
            "SELECT id, needs_asphalt, status FROM coordination WHERE ticket_no=? LIMIT 1",
            (tno,),
        ).fetchone()
        before_clr = conn.execute(
            "SELECT id FROM quality_clearances WHERE ticket_no=? LIMIT 1",
            (tno,),
        ).fetchone()
        res = ensure_excavation_coordination(
            tno,
            reason="ربط تلقائي من معاملات الحفر",
            conn=conn,
            create_clearance=True,
        )
        if res.get("created_coord") or res.get("created_clearance"):
            changed += 1
        elif not before_coord or not before_clr:
            changed += 1
    if own:
        conn.commit()
        conn.close()
    return changed


def list_excavation_coordination_queue(conn=None, limit: int = 50) -> list[dict]:
    """قائمة معاملات الحفر المرتبطة بالتنسيق/الإخلاء للمتابعة."""
    own = conn is None
    conn = conn or connect()
    tickets = collect_excavation_ticket_nos(conn)
    out = []
    for tno in tickets[: max(int(limit or 50), 1)]:
        coord = conn.execute(
            "SELECT id, status, needs_asphalt, request_date FROM coordination WHERE ticket_no=? ORDER BY id LIMIT 1",
            (tno,),
        ).fetchone()
        clearance = conn.execute(
            "SELECT id, status, rekaz_code, clearance_no FROM quality_clearances WHERE ticket_no=? ORDER BY id LIMIT 1",
            (tno,),
        ).fetchone()
        ticket = conn.execute(
            "SELECT id, district, status, asphalt_clearance FROM tickets WHERE ticket_no=? LIMIT 1",
            (tno,),
        ).fetchone()
        out.append(
            {
                "ticket_no": tno,
                "ticket_id": ticket["id"] if ticket else None,
                "district": (ticket["district"] if ticket else None) or "—",
                "ticket_status": (ticket["status"] if ticket else None) or "—",
                "coord_id": coord["id"] if coord else None,
                "coord_status": (coord["status"] if coord else None) or "—",
                "needs_asphalt": (coord["needs_asphalt"] if coord else None) or "—",
                "clearance_id": clearance["id"] if clearance else None,
                "clearance_status": (clearance["status"] if clearance else None) or "غير مُنشأ",
                "rekaz_code": (clearance["rekaz_code"] if clearance else None)
                or (clearance["clearance_no"] if clearance else None)
                or "—",
            }
        )
    if own:
        conn.close()
    return out


def log_audit(user_name, action, entity, entity_id="", details=""):
    conn = connect()
    conn.execute(
        "INSERT INTO audit_log(user_name, action, entity, entity_id, details) VALUES (?,?,?,?,?)",
        (user_name or "نظام", action, entity, str(entity_id or ""), details or ""),
    )
    conn.commit()
    conn.close()


def normalize_linked_section(value: str | None) -> str:
    v = (value or "").strip()
    key = v.lower()
    aliases = {
        "constructions": "constructions",
        "الإنشاءات": "constructions",
        "ops": "ops",
        "operations": "ops",
        "العمليات": "ops",
        "العمليات والصيانة": "ops",
        "projects": "projects",
        "المشاريع": "projects",
    }
    return aliases.get(v) or aliases.get(key) or ""


def transfer_new_coordination_to_license(
    coord_id: int,
    *,
    license_no: str | None = None,
    issue_date: str | None = None,
    expiry_date: str | None = None,
    linked_section: str | None = None,
    license_type: str | None = None,
    conn=None,
) -> dict:
    """ينقل تنسيقاً جديداً إلى الرخص المصدرة ويربطه بالقسم المستهدف."""
    own = conn is None
    conn = conn or connect()
    row = conn.execute("SELECT * FROM new_coordinations WHERE id=?", (coord_id,)).fetchone()
    if not row:
        if own:
            conn.close()
        raise ValueError("التنسيق غير موجود")
    coord = dict(row)
    if coord.get("transferred_license_id"):
        existing = conn.execute(
            "SELECT * FROM issued_licenses WHERE id=?",
            (coord["transferred_license_id"],),
        ).fetchone()
        if own:
            conn.close()
        return {
            "created": False,
            "license_id": coord.get("transferred_license_id"),
            "license": dict(existing) if existing else None,
            "coord": coord,
        }

    section = normalize_linked_section(linked_section or coord.get("linked_section"))
    if not section:
        # استنتج القسم من الروابط المتوفرة
        if (coord.get("ticket_no") or "").strip():
            section = "ops"
        elif (coord.get("project_code") or "").strip():
            section = "projects"
        elif (coord.get("construction_work_no") or "").strip():
            section = "constructions"
        else:
            section = "constructions"
    section_label = {
        "ops": "العمليات والصيانة",
        "projects": "المشاريع",
        "constructions": "الإنشاءات",
    }.get(section, "الإنشاءات")

    lic_no = (license_no or coord.get("license_no") or "").strip() or next_series_code("rl", conn)
    issued = (issue_date or coord.get("issue_date") or "").strip() or datetime.now().strftime("%Y-%m-%d")
    expiry = (expiry_date or coord.get("expiry_date") or "").strip() or None
    ltype = (license_type or "").strip() or ("حفر" if is_excavation_text(coord.get("work_desc"), coord.get("notes")) else "أخرى")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur = conn.execute(
        """
        INSERT INTO issued_licenses(
          license_no, issue_date, expiry_date, authority, license_type, status,
          new_coordination_id, transferred_at, linked_section,
          ticket_no, project_code, construction_work_no, location, work_desc, notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            lic_no,
            issued,
            expiry,
            coord.get("authority") or "",
            ltype,
            "سارية",
            coord_id,
            now,
            section_label,
            coord.get("ticket_no") or "",
            coord.get("project_code") or "",
            coord.get("construction_work_no") or "",
            coord.get("location") or "",
            coord.get("work_desc") or "",
            (coord.get("notes") or "")[:500],
        ),
    )
    license_id = cur.lastrowid
    conn.execute(
        """
        UPDATE new_coordinations
        SET status=?, license_no=?, issue_date=?, expiry_date=?, linked_section=?, transferred_license_id=?
        WHERE id=?
        """,
        ("تم الإصدار", lic_no, issued, expiry, section_label, license_id, coord_id),
    )
    if own:
        conn.commit()
        conn.close()
    return {
        "created": True,
        "license_id": license_id,
        "license_no": lic_no,
        "linked_section": section,
        "coord_id": coord_id,
    }


def count_issued_licenses(linked_section: str | None = None, conn=None) -> int:
    own = conn is None
    conn = conn or connect()
    section = normalize_linked_section(linked_section) if linked_section else ""
    if section:
        labels = {
            "ops": ("ops", "العمليات", "العمليات والصيانة"),
            "projects": ("projects", "المشاريع"),
            "constructions": ("constructions", "الإنشاءات"),
        }.get(section, (section,))
        placeholders = ",".join("?" * len(labels))
        n = conn.execute(
            f"SELECT COUNT(*) FROM issued_licenses WHERE lower(trim(linked_section)) IN ({placeholders})",
            [x.lower() for x in labels],
        ).fetchone()[0]
    else:
        n = conn.execute("SELECT COUNT(*) FROM issued_licenses").fetchone()[0]
    if own:
        conn.close()
    return int(n or 0)


def warehouse_tx_sign(tx_type: str) -> int:
    """+1 وارد، -1 منصرف/إرجاع، 0 غير معروف."""
    t = tx_type or ""
    if "وارد" in t or "افتتاح" in t:
        return 1
    if "منصرف" in t or "إرجاع" in t:
        return -1
    return 0


def _is_ticket_identifier(value: str, conn, row: dict | None = None) -> bool:
    """True إذا كانت القيمة رقم عطل / كود ER — وليست أمر عمل حقيقي."""
    v = (value or "").strip()
    if not v:
        return False
    if row:
        tno = (row.get("ticket_no") or "").strip()
        code = (row.get("rekaz_code") or "").strip()
        if v == tno or (code and v == code):
            return True
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "tickets" not in tables:
        return False
    hit = conn.execute(
        "SELECT 1 FROM tickets WHERE ticket_no=? OR rekaz_code=? LIMIT 1",
        (v, v),
    ).fetchone()
    if not hit:
        return False
    # أمر عمل فرق أولية قد يطابق نصاً نادراً — لا نعتبره رقم عطل
    if "primary_team_orders" in tables:
        pto = conn.execute(
            "SELECT 1 FROM primary_team_orders WHERE work_order=? LIMIT 1",
            (v,),
        ).fetchone()
        if pto:
            return False
    return True


def resolve_tx_work_order(row: dict, conn=None) -> str:
    """يستخرج أمر العمل الحقيقي فقط — لا يُرجع رقم العطل أو كود ER."""
    own = conn is None
    conn = conn or connect()
    try:
        ref = (row.get("source_ref") or "").strip()
        tno = (row.get("ticket_no") or "").strip()
        stored = (row.get("work_order") or "").strip()

        def _ok(wo: str) -> str:
            wo = (wo or "").strip()
            if not wo or _is_ticket_identifier(wo, conn, row):
                return ""
            return wo

        # 1) أمر العمل من بطاقة العطل المرتبطة
        lookup = tno or ""
        if lookup:
            ticket = conn.execute(
                "SELECT work_order FROM tickets WHERE ticket_no=? OR rekaz_code=? LIMIT 1",
                (lookup, lookup),
            ).fetchone()
            if ticket:
                wo = _ok(ticket["work_order"] or "")
                if wo:
                    return wo

        # 2) المرجع أمر عمل فرق أولية
        if ref:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "primary_team_orders" in tables:
                pto = conn.execute(
                    "SELECT work_order FROM primary_team_orders WHERE work_order=? LIMIT 1",
                    (ref,),
                ).fetchone()
                if pto:
                    return _ok(pto["work_order"] or "")

            # 3) المرجع من الإنشاءات/المشاريع — ليس رقم عطل
            if not _is_ticket_identifier(ref, conn, row):
                return ref

            # المرجع رقم عطل: خذ أمر العمل من ذلك العطل إن وُجد
            ticket_as_ref = conn.execute(
                "SELECT work_order FROM tickets WHERE ticket_no=? OR rekaz_code=? LIMIT 1",
                (ref, ref),
            ).fetchone()
            if ticket_as_ref:
                wo = _ok(ticket_as_ref["work_order"] or "")
                if wo:
                    return wo

        # 4) المخزّن فقط إن لم يكن رقم عطل
        return _ok(stored)
    finally:
        if own:
            conn.close()


def enrich_warehouse_txs_work_order(rows: list[dict], conn=None) -> list[dict]:
    own = conn is None
    conn = conn or connect()
    try:
        for r in rows or []:
            r["work_order"] = resolve_tx_work_order(r, conn)
        return rows
    finally:
        if own:
            conn.close()


def scrub_ticket_numbers_from_warehouse_work_orders(conn=None) -> int:
    """يحذف أرقام الأعطال المخزّنة خطأً في عمود أمر العمل ويعيد التعبئة الصحيحة."""
    own = conn is None
    conn = conn or connect()
    try:
        _ensure_column(conn, "warehouse_tx", "work_order")
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "warehouse_tx" not in tables:
            return 0
        # مسح سريع: أمر العمل = رقم العطل أو كود ER لنفس الصف
        cur1 = conn.execute(
            """
            UPDATE warehouse_tx
            SET work_order=''
            WHERE coalesce(trim(work_order),'')<>''
              AND (
                work_order = ticket_no
                OR (coalesce(trim(rekaz_code),'')<>'' AND work_order = rekaz_code)
              )
            """
        )
        cleared = int(cur1.rowcount or 0)

        if "tickets" in tables:
            # مسح أي قيمة تطابق عطلاً معروفاً وليست أمر عمل فرق أولية
            if "primary_team_orders" in tables:
                dirty_sql = """
                    SELECT w.id, w.ticket_no, w.rekaz_code, w.source_ref, w.source_section, w.work_order
                    FROM warehouse_tx w
                    WHERE coalesce(trim(w.work_order),'')<>''
                      AND EXISTS (
                        SELECT 1 FROM tickets t
                        WHERE t.ticket_no = w.work_order OR t.rekaz_code = w.work_order
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM primary_team_orders p
                        WHERE p.work_order = w.work_order
                      )
                    """
            else:
                dirty_sql = """
                    SELECT w.id, w.ticket_no, w.rekaz_code, w.source_ref, w.source_section, w.work_order
                    FROM warehouse_tx w
                    WHERE coalesce(trim(w.work_order),'')<>''
                      AND EXISTS (
                        SELECT 1 FROM tickets t
                        WHERE t.ticket_no = w.work_order OR t.rekaz_code = w.work_order
                      )
                    """
            dirty = rows_to_dicts(conn.execute(dirty_sql).fetchall())
            for r in dirty:
                conn.execute("UPDATE warehouse_tx SET work_order='' WHERE id=?", (r["id"],))
                cleared += 1

        # إعادة تعبئة أمر العمل الحقيقي من العطل / المرجع غير العطل
        refill = backfill_warehouse_tx_work_orders(conn)
        return cleared + int(refill or 0)
    finally:
        if own:
            conn.commit()
            conn.close()

def get_warehouse_voucher_lines(voucher_no: str, conn=None) -> list[dict]:
    """كل أسطر السند (معاملة المستودع) مرتبة."""
    voucher_no = (voucher_no or "").strip()
    if not voucher_no:
        return []
    own = conn is None
    conn = conn or connect()
    try:
        rows = rows_to_dicts(
            conn.execute(
                "SELECT * FROM warehouse_tx WHERE voucher_no=? ORDER BY id",
                (voucher_no,),
            ).fetchall()
        )
        for r in rows:
            r["sign"] = warehouse_tx_sign(r.get("tx_type") or "")
        return rows
    finally:
        if own:
            conn.close()


def resolve_warehouse_parent(line: dict, conn=None) -> dict:
    """يربط سطر حركة بالمصدر (عطل / فرق أولية / إنشاءات / مشروع)."""
    own = conn is None
    conn = conn or connect()
    try:
        section = (line.get("source_section") or "").strip().lower()
        ref = (line.get("source_ref") or line.get("ticket_no") or "").strip()
        tno = (line.get("ticket_no") or "").strip()
        out = {
            "section": section,
            "ref": ref,
            "label": "",
            "work_order": "",
            "extract_no": "",
            "amount": None,
            "ticket_no": tno,
            "site": "",
            "tab": "",
            "parent_kind": "",
            "parent_id": None,
        }
        if section == "ops" or tno:
            # فرق أولية أولاً إذا المرجع أمر عمل
            if ref:
                pto = conn.execute(
                    "SELECT * FROM primary_team_orders WHERE work_order=? LIMIT 1",
                    (ref,),
                ).fetchone()
                if pto:
                    p = dict(pto)
                    out.update(
                        {
                            "label": _warehouse_section_label("ops"),
                            "tab": "الفرق الأولية",
                            "work_order": p.get("work_order") or "",
                            "extract_no": p.get("extract_no") or "",
                            "amount": p.get("amount"),
                            "parent_kind": "primary_team",
                            "parent_id": p.get("id"),
                            "site": "الفرق الأولية",
                        }
                    )
                    return out
            lookup = tno or ref
            if lookup:
                ticket = conn.execute(
                    "SELECT * FROM tickets WHERE ticket_no=? OR rekaz_code=? LIMIT 1",
                    (lookup, lookup),
                ).fetchone()
                if ticket:
                    t = dict(ticket)
                    out.update(
                        {
                            "label": _warehouse_section_label("ops"),
                            "tab": "الأعطال",
                            "ticket_no": t.get("ticket_no") or lookup,
                            "work_order": t.get("work_order") or "",
                            "site": t.get("district") or t.get("location") or "الأعطال",
                            "parent_kind": "ticket",
                            "parent_id": t.get("id"),
                        }
                    )
                    return out
            out.update(
                {
                    "label": _warehouse_section_label("ops"),
                    "tab": "العمليات والصيانة",
                    "site": "العمليات والصيانة",
                }
            )
            return out
        if section == "constructions" and ref:
            row = conn.execute(
                "SELECT * FROM construction_works WHERE work_no=? LIMIT 1", (ref,)
            ).fetchone()
            if row:
                w = dict(row)
                out.update(
                    {
                        "label": _warehouse_section_label("constructions"),
                        "tab": "الإنشاءات",
                        "site": w.get("site") or "",
                        "parent_kind": "construction",
                        "parent_id": w.get("id"),
                    }
                )
                return out
        if section == "projects" and ref:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_code=? LIMIT 1", (ref,)
            ).fetchone()
            if row:
                p = dict(row)
                out.update(
                    {
                        "label": _warehouse_section_label("projects"),
                        "tab": "المشاريع",
                        "site": p.get("site") or p.get("project_name") or "",
                        "ticket_no": p.get("ticket_no") or "",
                        "parent_kind": "project",
                        "parent_id": p.get("id"),
                    }
                )
                return out
        out["label"] = _warehouse_section_label(section) if section else ""
        out["tab"] = out["label"]
        return out
    finally:
        if own:
            conn.close()


def _warehouse_section_label(section: str) -> str:
    return {
        "ops": "العمليات والصيانة",
        "constructions": "الإنشاءات",
        "projects": "المشاريع",
        "warehouses": "المستودعات",
    }.get(section or "", section or "")


def group_warehouse_txs_by_voucher(txs: list[dict]) -> list[dict]:
    """تجميع أسطر الحركات حسب رقم السند لعرض المعاملة."""
    groups: dict[str, dict] = {}
    order: list[str] = []
    for t in txs or []:
        key = (t.get("voucher_no") or "").strip() or f"__id_{t.get('id')}"
        if key not in groups:
            groups[key] = {
                "voucher_no": t.get("voucher_no") or "",
                "tx_date": t.get("tx_date"),
                "tx_types": [],
                "lines": [],
                "qty_total": 0.0,
            }
            order.append(key)
        g = groups[key]
        g["lines"].append(t)
        if t.get("tx_type") and t.get("tx_type") not in g["tx_types"]:
            g["tx_types"].append(t.get("tx_type"))
        try:
            g["qty_total"] += float(t.get("qty") or 0)
        except (TypeError, ValueError):
            pass
        if not g.get("tx_date") and t.get("tx_date"):
            g["tx_date"] = t.get("tx_date")
    return [groups[k] for k in order]


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
