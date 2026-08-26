import json
import os
import re
import sqlite3
import secrets
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
    "rekaz_ratio": 0,
    "main_contractor_ratio": 0,
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
    "ticket_status": ["تم الإسناد", "التحديد", "الحفر", "الأعمال الكهربائية", "منفذ", "مغلق", "ملغي", "تم التحويل لقسم آخر"],
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
        "منصرف للمعاملة",
        "إرجاع للكهرباء",
        "إرجاع للمجمعة",
        "وارد من موقع العمل",
        "وارد من مشتريات خارجية",
        "وارد مواد موردة من مقاول",
        "صرف عهدة",
        "إرجاع عهدة",
        "رصيد افتتاحي",
    ],
    "purchase_status": ["جديد", "معتمد", "تم الشراء", "ملغي"],
    "contractor_supply_status": ["جديد", "معتمد", "تم التوريد", "ملغي"],
    "custody_status": ["مسلمة", "مرتجعة", "مفقودة"],
    "vehicle_status": ["عاملة", "صيانة", "متوقفة"],
    "equipment_status": ["جاهزة", "صيانة", "تخريد"],
    "contract_status": ["ساري", "منتهي", "موقوف"],
    "yes_no_active": ["نشط", "موقوف"],
    "hr_departments": ["العمليات", "المستودعات", "الجودة", "السلامة", "المالية", "الموارد البشرية", "الإدارة"],
    "hr_status": ["على رأس العمل", "إجازة", "منتهي"],
    "user_roles": ["admin", "مشرف", "مدخل بيانات", "محاسب", "الموارد البشرية", "مراقبي المواقع"],
    "project_types": ["خاصة", "كهرباء"],
    "project_status": ["جديد", "قيد التنفيذ", "موقوف", "مكتمل", "مغلق"],
    "work_class": ["اعتيادي", "طوارئ"],
    "new_coord_status": ["مسودة", "قيد التنسيق", "بانتظار الرخصة", "تم الإصدار", "مرفوض", "ملغي"],
    "new_coord_kind": ["تنسيق جديد", "بلاغ", "مخطط شامل", "مخالفة"],
    "issued_license_status": ["سارية", "منتهية", "ملغاة", "تم إصدار الرخصة", "جاري العمل"],
    "license_types": ["بلدية", "أمانة", "كهرباء", "حفر", "أخرى"],
    "license_workflow": [
        "متابعة بعد الإصدار",
        "تحت التشييكات",
        "تحت إجراءات الإغلاق",
        "الإخلاء المبدئي",
        "موردي الأسفلت",
    ],
    "clearance_stage": ["إخلاء مبدئي", "إخلاء نهائي", "رخصة ملغاة"],
    "consultant_result": ["مقبول", "ملاحظات", "مرفوض", "بانتظار"],
    "linked_sections": ["الإنشاءات", "العمليات والصيانة", "المشاريع"],
}


def normalize_ticket_status(status):
    return "تم الإسناد" if status == "جديد" else status


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _drop_unique_index_for_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """يلغي فهرس UNIQUE تلقائي/صريح إذا كان مرتبطاً بعمود واحد فقط."""
    for idx in conn.execute(f"PRAGMA index_list('{table}')").fetchall():
        idx_name = idx[1]
        if not idx_name or idx[2] != 1:
            continue
        origin = idx[3] if len(idx) > 3 else ""
        if origin and origin != "c":
            continue
        cols = [r[2] for r in conn.execute(f"PRAGMA index_info('{idx_name}')").fetchall()]
        if cols == [column]:
            try:
                conn.execute(f'DROP INDEX IF EXISTS "{idx_name}"')
                return True
            except sqlite3.OperationalError as exc:
                if "associated with UNIQUE or PRIMARY KEY constraint" in str(exc):
                    return False
                raise
    return False


def _dedupe_duplicate_work_orders(conn: sqlite3.Connection, table: str = "tickets") -> None:
    """يحافظ على أول قيمة work_order من أصل تكرارها ويُفرغ التكرارات الإضافية."""
    if table not in {"tickets", "primary_team_orders"}:
        return
    rows = conn.execute(
        f"""
        SELECT work_order, MIN(id) AS keep_id
        FROM {table}
        WHERE trim(COALESCE(work_order, '')) <> ''
        GROUP BY work_order
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for row in rows:
        work_order = (row[0] or "").strip()
        keep_id = row[1]
        if not work_order:
            continue
        conn.execute(
            f"UPDATE {table} SET work_order='' WHERE trim(COALESCE(work_order, ''))=? AND id != ?",
            (work_order, keep_id),
        )


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
    "external_purchase_lines": """
        CREATE TABLE IF NOT EXISTS external_purchase_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id INTEGER NOT NULL,
            purchase_no TEXT,
            item_no TEXT,
            item_name TEXT,
            unit TEXT,
            qty REAL,
            unit_price REAL,
            line_total REAL,
            warehouse_tx_id INTEGER,
            notes TEXT
        )
    """,
    "contractor_supplies": """
        CREATE TABLE IF NOT EXISTS contractor_supplies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supply_no TEXT,
            supply_date TEXT,
            contractor TEXT,
            ticket_no TEXT,
            work_no TEXT,
            status TEXT,
            notes TEXT,
            received_voucher_no TEXT
        )
    """,
    "contractor_supply_lines": """
        CREATE TABLE IF NOT EXISTS contractor_supply_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supply_id INTEGER NOT NULL,
            supply_no TEXT,
            item_no TEXT,
            item_name TEXT,
            unit TEXT,
            qty REAL,
            unit_price REAL,
            line_total REAL,
            warehouse_tx_id INTEGER,
            notes TEXT
        )
    """,
    "reinforcement_departments": """
        CREATE TABLE IF NOT EXISTS reinforcement_departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dept_name TEXT,
            dept_code TEXT,
            status TEXT,
            notes TEXT
        )
    """,
    "reinforcement_works": """
        CREATE TABLE IF NOT EXISTS reinforcement_works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_no TEXT,
            work_date TEXT,
            department TEXT,
            work_type TEXT,
            station_no TEXT,
            location TEXT,
            ticket_no TEXT,
            status TEXT,
            value REAL,
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
            coord_kind TEXT,
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
            notes TEXT,
            district TEXT,
            work_order TEXT,
            rtc_no TEXT,
            license_length REAL,
            workflow_status TEXT,
            consultant_notes TEXT,
            consultant_submit_date TEXT,
            consultant_submitted TEXT,
            consultant_result TEXT
        )
    """,
    "ops_custom_tabs": """
        CREATE TABLE IF NOT EXISTS ops_custom_tabs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            title_ar TEXT NOT NULL,
            title_en TEXT,
            target_path TEXT,
            sort_order INTEGER DEFAULT 100,
            is_visible INTEGER DEFAULT 1,
            icon TEXT,
            required_perm TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """,
    # تبويبات مخصصة عامة لكل أقسام التطبيق (تعميم ops_custom_tabs)
    "app_custom_tabs": """
        CREATE TABLE IF NOT EXISTS app_custom_tabs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section TEXT NOT NULL,
            slug TEXT NOT NULL,
            title_ar TEXT NOT NULL,
            title_en TEXT,
            target_path TEXT,
            sort_order INTEGER DEFAULT 100,
            is_visible INTEGER DEFAULT 1,
            icon TEXT,
            required_perm TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(section, slug)
        )
    """,
    # أجهزة المبرمج الموثوقة (الجهاز الرئيسي) + رموز موافقة لمرة واحدة
    "programmer_devices": """
        CREATE TABLE IF NOT EXISTS programmer_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            token_hash TEXT NOT NULL UNIQUE,
            label TEXT,
            is_main INTEGER DEFAULT 1,
            user_agent TEXT,
            ip TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "programmer_approve_codes": """
        CREATE TABLE IF NOT EXISTS programmer_approve_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_hash TEXT NOT NULL UNIQUE,
            channel TEXT DEFAULT 'email',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            used_at TEXT
        )
    """,
}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl_type: str = "TEXT") -> bool:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column in cols:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
    return True


# حساب المبرمج المخفي — لا يُعرض في الواجهة أبداً
HIDDEN_PROGRAMMER_USERNAME = "wadnooh"
HIDDEN_PROGRAMMER_PASSWORD = "123123"


def is_hidden_username(username: str | None) -> bool:
    return (username or "").strip().lower() == HIDDEN_PROGRAMMER_USERNAME


def user_is_hidden(row) -> bool:
    if row is None:
        return False
    try:
        if int(row["is_hidden"] or 0) == 1:
            return True
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    try:
        return is_hidden_username(row["username"])
    except (KeyError, IndexError, TypeError):
        return False


def ensure_hidden_programmer_user(conn: sqlite3.Connection | None = None) -> bool:
    """Upsert حساب wadnooh مخفياً بصلاحيات admin كاملة. يعيد True إذا أُنشئ جديداً."""
    own = conn is None
    conn = conn or connect()
    created = False
    try:
        _ensure_column(conn, "users", "email", "TEXT")
        _ensure_column(conn, "users", "mobile", "TEXT")
        _ensure_column(conn, "users", "is_hidden", "INTEGER DEFAULT 0")
        row = conn.execute(
            "SELECT id FROM users WHERE lower(username)=lower(?)",
            (HIDDEN_PROGRAMMER_USERNAME,),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE users
                SET full_name=?, role=?, active=1, password=?, notes=?, is_hidden=1
                WHERE id=?
                """,
                ("المبرمج", "admin", HIDDEN_PROGRAMMER_PASSWORD, "", int(row["id"])),
            )
        else:
            conn.execute(
                """
                INSERT INTO users(username, full_name, role, active, password, notes, is_hidden)
                VALUES (?,?,?,?,?,?,1)
                """,
                (
                    HIDDEN_PROGRAMMER_USERNAME,
                    "المبرمج",
                    "admin",
                    1,
                    HIDDEN_PROGRAMMER_PASSWORD,
                    "",
                ),
            )
            created = True
        if own:
            conn.commit()
        return created
    finally:
        if own:
            conn.close()


def list_visible_users(conn: sqlite3.Connection | None = None) -> list[dict]:
    own = conn is None
    conn = conn or connect()
    try:
        _ensure_column(conn, "users", "email", "TEXT")
        _ensure_column(conn, "users", "mobile", "TEXT")
        _ensure_column(conn, "users", "is_hidden", "INTEGER DEFAULT 0")
        return rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM users
                WHERE coalesce(is_hidden, 0)=0
                  AND lower(coalesce(username,'')) <> lower(?)
                ORDER BY id
                """,
                (HIDDEN_PROGRAMMER_USERNAME,),
            ).fetchall()
        )
    finally:
        if own:
            conn.close()


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
        try:
            from webapp.modules_config import MODULES

            for mod in MODULES.values():
                table = (mod.get("table") or "").strip()
                fields = mod.get("fields") or []
                if not table or not any(f[0] == "attachments" for f in fields):
                    continue
                if table in existing or table in created:
                    if _ensure_column(conn, table, "attachments"):
                        created.append(f"{table}.attachments")
        except Exception:
            pass
        # قناة رمز موافقة المبرمج (email | ssh_emergency)
        if "programmer_approve_codes" in existing or "programmer_approve_codes" in created:
            if _ensure_column(conn, "programmer_approve_codes", "channel", "TEXT DEFAULT 'email'"):
                created.append("programmer_approve_codes.channel")
                conn.execute(
                    "UPDATE programmer_approve_codes SET channel='email' WHERE channel IS NULL OR trim(channel)=''"
                )
        # أعمدة الترقيم والربط
        if "tickets" in existing or "tickets" in created:
            if _ensure_column(conn, "tickets", "rekaz_code"):
                created.append("tickets.rekaz_code")
            # السماح بتكرار رقم العطل؛ لا يُسمح بتكرار أمر العمل
            _drop_unique_index_for_column(conn, "tickets", "ticket_no")
            _dedupe_duplicate_work_orders(conn, "tickets")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_work_order_unique ON tickets(work_order) WHERE trim(COALESCE(work_order, '')) <> ''"
            )
        if "primary_team_orders" in existing or "primary_team_orders" in created:
            _dedupe_duplicate_work_orders(conn, "primary_team_orders")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_primary_team_orders_work_order_unique ON primary_team_orders(work_order) WHERE trim(COALESCE(work_order, '')) <> ''"
            )
        if "quality_clearances" in existing or "quality_clearances" in created:
            if _ensure_column(conn, "quality_clearances", "rekaz_code"):
                created.append("quality_clearances.rekaz_code")
            if _ensure_column(conn, "quality_clearances", "clearance_stage"):
                created.append("quality_clearances.clearance_stage")
                conn.execute(
                    """
                    UPDATE quality_clearances
                    SET clearance_stage='إخلاء مبدئي'
                    WHERE clearance_stage IS NULL OR trim(clearance_stage)=''
                    """
                )
        if "issued_licenses" in existing or "issued_licenses" in created:
            for col in (
                "district",
                "work_order",
                "rtc_no",
                "workflow_status",
                "consultant_notes",
                "consultant_submit_date",
                "consultant_submitted",
                "consultant_result",
            ):
                if _ensure_column(conn, "issued_licenses", col):
                    created.append(f"issued_licenses.{col}")
            if _ensure_column(conn, "issued_licenses", "license_length", "REAL"):
                created.append("issued_licenses.license_length")
            conn.execute(
                """
                UPDATE issued_licenses
                SET workflow_status='متابعة بعد الإصدار'
                WHERE workflow_status IS NULL OR trim(workflow_status)=''
                """
            )
            # تصحيح تسمية خيار متابعة التصريح (كان تبويبة خاطئة)
            conn.execute(
                """
                UPDATE issued_licenses
                SET workflow_status='الإخلاء المبدئي'
                WHERE trim(coalesce(workflow_status,'')) = 'من هنا تبدأ رحلة الإخلاءات'
                """
            )
            refresh_issued_license_expiry_status(conn)
        if "construction_works" in existing or "construction_works" in created:
            if _ensure_column(conn, "construction_works", "ticket_no"):
                created.append("construction_works.ticket_no")
        if "external_purchases" in existing or "external_purchases" in created:
            if _ensure_column(conn, "external_purchases", "received_voucher_no"):
                created.append("external_purchases.received_voucher_no")
            migrated = migrate_external_purchase_lines(conn)
            if migrated:
                created.append(f"external_purchase_lines_migrate:{migrated}")
        if "custody" in existing or "custody" in created:
            for col in (
                "item_no",
                "unit",
                "issued_voucher_no",
                "return_voucher_no",
                "warehouse_tx_id",
                "return_warehouse_tx_id",
            ):
                ddl_type = "INTEGER" if col.endswith("_tx_id") else "TEXT"
                if _ensure_column(conn, "custody", col, ddl_type):
                    created.append(f"custody.{col}")
        if "contractor_supplies" in existing or "contractor_supplies" in created:
            if _ensure_column(conn, "contractor_supplies", "received_voucher_no"):
                created.append("contractor_supplies.received_voucher_no")
        if "reinforcement_departments" in existing or "reinforcement_departments" in created:
            seeded = seed_reinforcement_departments(conn)
            if seeded:
                created.append(f"reinforcement_departments_seed:{seeded}")
        if "reinforcement_works" in existing or "reinforcement_works" in created:
            if _ensure_column(conn, "reinforcement_works", "station_no"):
                created.append("reinforcement_works.station_no")
        if "new_coordinations" in existing or "new_coordinations" in created:
            if _ensure_column(conn, "new_coordinations", "coord_kind"):
                created.append("new_coordinations.coord_kind")
            conn.execute(
                """
                UPDATE new_coordinations
                SET coord_kind='تنسيق جديد'
                WHERE coord_kind IS NULL OR trim(coord_kind)=''
                """
            )
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
            n_units = normalize_warehouse_length_units_once(conn)
            if n_units:
                created.append(f"warehouse.length_units_normalized:{n_units}")
        if "safety_permits" in existing or "safety_permits" in created:
            if _ensure_column(conn, "safety_permits", "work_order"):
                created.append("safety_permits.work_order")
        if "invoices" in existing or "invoices" in created:
            if _ensure_column(conn, "invoices", "work_order"):
                created.append("invoices.work_order")
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
        # ترحيل تبويبات العمليات القديمة → app_custom_tabs (مرة واحدة)
        n_migrated = _migrate_ops_custom_tabs_to_app(conn)
        if n_migrated:
            created.append(f"app_custom_tabs.migrated_from_ops:{n_migrated}")
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
        # حساب المبرمج المخفي (لا يظهر في قوائم المستخدمين)
        if "user_permission_overrides" not in existing and "user_permission_overrides" not in created:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_permission_overrides (
                    user_id INTEGER NOT NULL,
                    perm TEXT NOT NULL,
                    effect TEXT NOT NULL CHECK(effect IN ('allow','deny')),
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id, perm)
                )
                """
            )
            created.append("user_permission_overrides")
        if "users" in existing or "users" in created:
            if _ensure_column(conn, "users", "email", "TEXT"):
                created.append("users.email")
            if _ensure_column(conn, "users", "mobile", "TEXT"):
                created.append("users.mobile")
            if _ensure_column(conn, "users", "is_hidden", "INTEGER DEFAULT 0"):
                created.append("users.is_hidden")
            # مفتاح API للتكاملات الخارجية
            if _ensure_column(conn, "users", "api_key", "TEXT"):
                created.append("users.api_key")
            if _ensure_column(conn, "users", "active_session_token", "TEXT"):
                created.append("users.active_session_token")
            if _ensure_column(conn, "users", "active_session_seen_at", "TEXT"):
                created.append("users.active_session_seen_at")
            if ensure_hidden_programmer_user(conn):
                created.append("users.hidden_programmer")
        if created:
            conn.commit()
        else:
            conn.commit()
        return created
    finally:
        if own:
            conn.close()


def ensure_user_permission_overrides_table(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_permission_overrides (
                user_id INTEGER NOT NULL,
                perm TEXT NOT NULL,
                effect TEXT NOT NULL CHECK(effect IN ('allow','deny')),
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, perm)
            )
            """
        )
        conn.commit()
    finally:
        if own:
            conn.close()


def user_permission_overrides(user_id: int | str | None, conn: sqlite3.Connection | None = None) -> dict[str, str]:
    if not user_id:
        return {}
    own = conn is None
    conn = conn or connect()
    try:
        ensure_user_permission_overrides_table(conn)
        rows = conn.execute(
            "SELECT perm, effect FROM user_permission_overrides WHERE user_id=?",
            (int(user_id),),
        ).fetchall()
        return {row["perm"]: row["effect"] for row in rows}
    finally:
        if own:
            conn.close()


def set_user_permission_override(user_id: int | str, perm: str, effect: str, conn: sqlite3.Connection | None = None) -> None:
    effect = (effect or "").strip().lower()
    if effect not in {"allow", "deny"}:
        raise ValueError("effect must be allow or deny")
    own = conn is None
    conn = conn or connect()
    try:
        ensure_user_permission_overrides_table(conn)
        conn.execute(
            """
            INSERT INTO user_permission_overrides(user_id, perm, effect)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, perm) DO UPDATE SET effect=excluded.effect
            """,
            (int(user_id), perm, effect),
        )
        conn.commit()
    finally:
        if own:
            conn.close()


def clear_user_permission_override(user_id: int | str, perm: str | None = None, conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        ensure_user_permission_overrides_table(conn)
        if perm:
            conn.execute("DELETE FROM user_permission_overrides WHERE user_id=? AND perm=?", (int(user_id), perm))
        else:
            conn.execute("DELETE FROM user_permission_overrides WHERE user_id=?", (int(user_id),))
        conn.commit()
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
            ticket_no TEXT,
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
            work_order TEXT,
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
            item_no TEXT,
            item_name TEXT,
            unit TEXT,
            qty REAL,
            status TEXT,
            return_date TEXT,
            issued_voucher_no TEXT,
            return_voucher_no TEXT,
            warehouse_tx_id INTEGER,
            return_warehouse_tx_id INTEGER,
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
            email TEXT,
            mobile TEXT,
            role TEXT,
            active INTEGER DEFAULT 1,
            password TEXT,
            notes TEXT,
            api_key TEXT,
            active_session_token TEXT,
            active_session_seen_at TEXT,
            is_hidden INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS user_permission_overrides (
            user_id INTEGER NOT NULL,
            perm TEXT NOT NULL,
            effect TEXT NOT NULL CHECK(effect IN ('allow','deny')),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, perm)
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
    for col in (
        "item_no",
        "unit",
        "issued_voucher_no",
        "return_voucher_no",
        "warehouse_tx_id",
        "return_warehouse_tx_id",
    ):
        _ensure_column(conn, "custody", col, "INTEGER" if col.endswith("_tx_id") else "TEXT")
    backfill_warehouse_tx_sources(conn)
    backfill_warehouse_tx_work_orders(conn)
    normalize_warehouse_length_units_once(conn)
    cur.execute(
        "UPDATE warehouse_tx SET tx_type=? WHERE tx_type=?",
        ("منصرف للمعاملة", "منصرف للمقاول"),
    )
    for boq_table in ("boq_items", "contract_boq_items"):
        for col in ("short_desc", "long_desc", "line_type", "currency", "payment_type"):
            _ensure_column(conn, boq_table, col)

    # تأكيد الجداول المضافة لاحقاً (حتى لو استُعيدت قاعدة قديمة)
    ensure_schema(conn)
    scrub_ticket_numbers_from_warehouse_work_orders(conn)

    # seed settings
    for k, v in DEFAULT_SETTINGS.items():
        cur.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, json.dumps(v)))
    for k, v in DEFAULT_LISTS.items():
        cur.execute("INSERT OR IGNORE INTO lists(key,value) VALUES (?,?)", (k, json.dumps(v, ensure_ascii=False)))
    row = cur.execute("SELECT value FROM lists WHERE key='warehouse_tx_types'").fetchone()
    if row:
        try:
            values = json.loads(row["value"] or "[]")
        except Exception:
            values = []
        if isinstance(values, list) and "منصرف للمقاول" in values:
            seen = set()
            updated = []
            for x in values:
                item = "منصرف للمعاملة" if x == "منصرف للمقاول" else x
                if item not in seen:
                    updated.append(item)
                    seen.add(item)
            cur.execute(
                "UPDATE lists SET value=? WHERE key='warehouse_tx_types'",
                (json.dumps(updated, ensure_ascii=False),),
            )

    _ensure_column(conn, "users", "is_hidden", "INTEGER DEFAULT 0")
    _ensure_column(conn, "users", "email", "TEXT")
    _ensure_column(conn, "users", "mobile", "TEXT")
    _ensure_column(conn, "users", "active_session_token", "TEXT")
    _ensure_column(conn, "users", "active_session_seen_at", "TEXT")
    if cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO users(username, full_name, role, active, password, notes, is_hidden) VALUES (?,?,?,?,?,?,?)",
            [
                ("admin", "مدير النظام", "admin", 1, "admin123", "حساب افتراضي", 0),
                ("supervisor", "مشرف المكتب", "مشرف", 1, "1234", "", 0),
                ("dataentry", "مدخل بيانات", "مدخل بيانات", 1, "1234", "", 0),
            ],
        )
    ensure_hidden_programmer_user(conn)

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
        "cs": ("CS", "cs_next", "cs_prefix"),
        "rf": ("RF", "rf_next", "rf_prefix"),
        "cu": ("CU", "cu_next", "cu_prefix"),
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
        elif series == "cs":
            taken = conn.execute(
                "SELECT 1 FROM contractor_supplies WHERE lower(supply_no)=lower(?) LIMIT 1", (code,)
            ).fetchone()
        elif series == "rf":
            taken = conn.execute(
                "SELECT 1 FROM reinforcement_works WHERE lower(work_no)=lower(?) LIMIT 1", (code,)
            ).fetchone()
        elif series == "cu":
            taken = conn.execute(
                "SELECT 1 FROM custody WHERE lower(custody_no)=lower(?) LIMIT 1", (code,)
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
    if source in ("ops", "constructions", "projects", "external", "contractors", "reinforcement"):
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
    return sync_ticket_work_order_to_related(ticket_no, work_order, rekaz_code, conn)


def sync_ticket_work_order_to_related(
    ticket_no: str,
    work_order: str,
    rekaz_code: str = "",
    conn=None,
) -> int:
    """ينسخ أمر العمل من العطل إلى المستودع والمستخلصات والرخص المرتبطة."""
    tno = (ticket_no or "").strip()
    if not tno:
        return 0
    own = conn is None
    conn = conn or connect()
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        wo = (work_order or "").strip()
        # لا تكتب رقم العطل في عمود أمر العمل
        if wo and _is_ticket_identifier(wo, conn, {"ticket_no": tno, "rekaz_code": rekaz_code or ""}):
            wo = ""
        code = (rekaz_code or "").strip()
        total = 0

        if "warehouse_tx" in tables:
            _ensure_column(conn, "warehouse_tx", "work_order")
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
            total += int(cur.rowcount or 0)

        if "invoices" in tables:
            _ensure_column(conn, "invoices", "work_order")
            cur = conn.execute(
                "UPDATE invoices SET work_order=? WHERE ticket_no=?",
                (wo, tno),
            )
            total += int(cur.rowcount or 0)

        if "issued_licenses" in tables:
            _ensure_column(conn, "issued_licenses", "work_order")
            cur = conn.execute(
                "UPDATE issued_licenses SET work_order=? WHERE ticket_no=?",
                (wo, tno),
            )
            total += int(cur.rowcount or 0)

        if "safety_permits" in tables:
            _ensure_column(conn, "safety_permits", "work_order")
            cur = conn.execute(
                "UPDATE safety_permits SET work_order=? WHERE ticket_no=?",
                (wo, tno),
            )
            total += int(cur.rowcount or 0)

        return total
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
    # تُدرج القيم الناقصة بترتيب DEFAULT_LISTS قدر الإمكان
    obsolete = {
        "license_workflow": {"من هنا تبدأ رحلة الإخلاءات"},
        "user_roles": {"مراقب", "المواقع"},
    }
    for key, defaults in DEFAULT_LISTS.items():
        current = data.get(key) or []
        if not isinstance(current, list):
            continue
        drop = obsolete.get(key) or set()
        merged = [v for v in current if v not in drop]
        for i, val in enumerate(defaults):
            if val in merged:
                continue
            insert_at = len(merged)
            for prev in reversed(defaults[:i]):
                if prev in merged:
                    insert_at = merged.index(prev) + 1
                    break
            merged.insert(insert_at, val)
        data[key] = merged
    if own:
        conn.close()
    return data


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


def _migrate_ops_custom_tabs_to_app(conn: sqlite3.Connection) -> int:
    """ترحيل صفوف ops_custom_tabs → app_custom_tabs (section=ops) مرة واحدة."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "app_custom_tabs" not in tables:
        return 0
    if "ops_custom_tabs" not in tables:
        return 0
    existing = conn.execute("SELECT COUNT(*) FROM app_custom_tabs WHERE section='ops'").fetchone()[0]
    if existing:
        return 0
    rows = conn.execute("SELECT * FROM ops_custom_tabs ORDER BY id").fetchall()
    n = 0
    for r in rows:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO app_custom_tabs(
                    section, slug, title_ar, title_en, target_path, sort_order,
                    is_visible, icon, required_perm, notes, created_at, updated_at
                ) VALUES ('ops',?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    r["slug"],
                    r["title_ar"],
                    r["title_en"],
                    r["target_path"],
                    r["sort_order"] if r["sort_order"] is not None else 100,
                    1 if r["is_visible"] is None else int(r["is_visible"]),
                    r["icon"],
                    r["required_perm"],
                    r["notes"],
                    r["created_at"],
                    r["updated_at"],
                ),
            )
            n += 1
        except Exception:
            continue
    return n


def _slugify_ops_tab(text: str) -> str:
    """مُعرّف تبويب لاتيني بسيط (a-z0-9-_)."""
    raw = (text or "").strip().lower()
    raw = re.sub(r"[^a-z0-9\u0600-\u06ff]+", "-", raw)
    ascii_only = re.sub(r"[^a-z0-9]+", "-", raw)
    ascii_only = ascii_only.strip("-_")
    return ascii_only[:64]


_APP_TAB_RESERVED_SLUGS = frozenset({"manage", "new", "edit", "delete", "api", "tabs"})
_OPS_TAB_RESERVED_SLUGS = _APP_TAB_RESERVED_SLUGS

# أقسام التطبيق المسموح ربط تبويب مخصص بها
APP_TAB_SECTIONS = (
    "ops",
    "constructions",
    "projects",
    "contractors",
    "quality",
    "safety",
    "warehouses",
    "external",
    "financial",
    "maintenance",
    "hr",
    "contracts",
    "reinforcement",
)


def list_app_custom_tabs(
    conn=None,
    *,
    section: str | None = None,
    visible_only: bool = False,
) -> list[dict]:
    own = conn is None
    conn = conn or connect()
    try:
        ensure_schema(conn)
        sql = "SELECT * FROM app_custom_tabs WHERE 1=1"
        params: list = []
        if section:
            sql += " AND section=?"
            params.append(section)
        if visible_only:
            sql += " AND IFNULL(is_visible,1)=1"
        sql += " ORDER BY IFNULL(sort_order,100) ASC, id ASC"
        return rows_to_dicts(conn.execute(sql, params).fetchall())
    finally:
        if own:
            conn.close()


def get_app_custom_tab(slug_or_id, conn=None, *, section: str | None = None) -> dict | None:
    own = conn is None
    conn = conn or connect()
    try:
        ensure_schema(conn)
        key = str(slug_or_id or "").strip()
        if not key:
            return None
        if key.isdigit():
            row = conn.execute("SELECT * FROM app_custom_tabs WHERE id=?", (int(key),)).fetchone()
        elif section:
            row = conn.execute(
                "SELECT * FROM app_custom_tabs WHERE section=? AND slug=?",
                (section, key),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM app_custom_tabs WHERE slug=? ORDER BY id LIMIT 1",
                (key,),
            ).fetchone()
        return dict(row) if row else None
    finally:
        if own:
            conn.close()


def save_app_custom_tab(data: dict, conn=None) -> dict:
    """إضافة أو تحديث تبويب مخصص لأي قسم. يعيد الصف المحفوظ."""
    own = conn is None
    conn = conn or connect()
    try:
        ensure_schema(conn)
        tab_id = data.get("id")
        section = (data.get("section") or "ops").strip().lower()
        if section not in APP_TAB_SECTIONS:
            raise ValueError("section_invalid")
        title_ar = (data.get("title_ar") or "").strip()
        title_en = (data.get("title_en") or "").strip()
        if not title_ar:
            raise ValueError("title_ar_required")
        slug = (data.get("slug") or "").strip().lower()
        slug = re.sub(r"[^a-z0-9_-]+", "-", slug).strip("-_")
        if not slug:
            slug = _slugify_ops_tab(title_en) or _slugify_ops_tab(title_ar)
        if not slug:
            slug = f"tab-{int(datetime.now().timestamp())}"
        if slug in _APP_TAB_RESERVED_SLUGS:
            slug = f"{slug}-tab"
        target_path = (data.get("target_path") or "").strip()
        try:
            sort_order = int(data.get("sort_order") if data.get("sort_order") not in (None, "") else 100)
        except (TypeError, ValueError):
            sort_order = 100
        is_visible = 1 if str(data.get("is_visible", "1")).strip() in {"1", "true", "yes", "on"} else 0
        icon = (data.get("icon") or "").strip() or None
        required_perm = (data.get("required_perm") or "").strip() or None
        notes = (data.get("notes") or "").strip() or None
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        clash = conn.execute(
            "SELECT id FROM app_custom_tabs WHERE section=? AND slug=? AND (? IS NULL OR id!=?)",
            (section, slug, tab_id, tab_id),
        ).fetchone()
        if clash:
            base = slug
            n = 2
            while conn.execute(
                "SELECT id FROM app_custom_tabs WHERE section=? AND slug=? AND (? IS NULL OR id!=?)",
                (section, f"{base}-{n}", tab_id, tab_id),
            ).fetchone():
                n += 1
            slug = f"{base}-{n}"

        if tab_id:
            conn.execute(
                """
                UPDATE app_custom_tabs
                SET section=?, slug=?, title_ar=?, title_en=?, target_path=?, sort_order=?,
                    is_visible=?, icon=?, required_perm=?, notes=?, updated_at=?
                WHERE id=?
                """,
                (
                    section,
                    slug,
                    title_ar,
                    title_en or None,
                    target_path or None,
                    sort_order,
                    is_visible,
                    icon,
                    required_perm,
                    notes,
                    now,
                    int(tab_id),
                ),
            )
            row_id = int(tab_id)
        else:
            cur = conn.execute(
                """
                INSERT INTO app_custom_tabs(
                    section, slug, title_ar, title_en, target_path, sort_order,
                    is_visible, icon, required_perm, notes, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    section,
                    slug,
                    title_ar,
                    title_en or None,
                    target_path or None,
                    sort_order,
                    is_visible,
                    icon,
                    required_perm,
                    notes,
                    now,
                    now,
                ),
            )
            row_id = int(cur.lastrowid)
        if own:
            conn.commit()
        return get_app_custom_tab(row_id, conn=conn) or {"id": row_id, "slug": slug, "section": section}
    finally:
        if own:
            conn.close()


def delete_app_custom_tab(tab_id: int, conn=None) -> bool:
    own = conn is None
    conn = conn or connect()
    try:
        ensure_schema(conn)
        cur = conn.execute("DELETE FROM app_custom_tabs WHERE id=?", (int(tab_id),))
        if own:
            conn.commit()
        return cur.rowcount > 0
    finally:
        if own:
            conn.close()


def app_custom_tab_href(tab: dict) -> str:
    """مسار الظهور: رابط صريح أو صفحة نائبة حسب القسم."""
    path = (tab.get("target_path") or "").strip()
    if path.startswith("http://") or path.startswith("https://") or path.startswith("/"):
        return path
    section = (tab.get("section") or "ops").strip()
    slug = (tab.get("slug") or "").strip()
    if not slug:
        return f"/{section}" if section != "ops" else "/ops"
    # توافق روابط العمليات القديمة
    if section == "ops":
        return f"/ops/tabs/{slug}"
    return f"/tabs/{section}/{slug}"


def list_ops_custom_tabs(conn=None, *, visible_only: bool = False) -> list[dict]:
    """توافق خلفي: تبويبات قسم العمليات من الجدول العام."""
    return list_app_custom_tabs(conn=conn, section="ops", visible_only=visible_only)


def get_ops_custom_tab(slug_or_id, conn=None) -> dict | None:
    return get_app_custom_tab(slug_or_id, conn=conn, section="ops")


def save_ops_custom_tab(data: dict, conn=None) -> dict:
    payload = dict(data or {})
    payload["section"] = "ops"
    return save_app_custom_tab(payload, conn=conn)


def delete_ops_custom_tab(tab_id: int, conn=None) -> bool:
    return delete_app_custom_tab(tab_id, conn=conn)


def ops_custom_tab_href(tab: dict) -> str:
    t = dict(tab or {})
    t.setdefault("section", "ops")
    return app_custom_tab_href(t)


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
                  ticket_no, clearance_no, rekaz_code, clearance_stage, request_date, status, notes
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (tno, rr, rr, "إخلاء مبدئي", today, "مطلوب", note[:500]),
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

    # أمر العمل الحقيقي من العطل إن وُجد — لا تستخدم رقم العطل كأمر عمل
    wo = ""
    tno = (coord.get("ticket_no") or "").strip()
    if tno:
        ticket = resolve_ticket_ref(tno, conn)
        if ticket:
            wo = (ticket.get("work_order") or "").strip()
            if wo and _is_ticket_identifier(wo, conn, {"ticket_no": tno, "rekaz_code": ticket.get("rekaz_code") or ""}):
                wo = ""
    if not wo:
        cref = (coord.get("construction_work_no") or "").strip()
        if cref and (not tno or cref != tno):
            wo = cref

    cur = conn.execute(
        """
        INSERT INTO issued_licenses(
          license_no, issue_date, expiry_date, authority, license_type, status,
          new_coordination_id, transferred_at, linked_section,
          ticket_no, project_code, construction_work_no, location, work_desc, notes,
          district, work_order, rtc_no, workflow_status
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            coord.get("district") or "",
            wo,
            lic_no,
            "متابعة بعد الإصدار",
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


def linked_section_label(section_key: str | None) -> str:
    key = normalize_linked_section(section_key)
    return {
        "ops": "العمليات والصيانة",
        "projects": "المشاريع",
        "constructions": "الإنشاءات",
    }.get(key, "الإنشاءات")


def quality_workflow_for_ref(
    *,
    ticket_no: str | None = None,
    construction_work_no: str | None = None,
    project_code: str | None = None,
    linked_section: str | None = None,
    conn=None,
) -> dict:
    """حالة مسار الجودة للمعاملة: تنسيقات → متابعة تصاريح → إخلاءات."""
    own = conn is None
    conn = conn or connect()
    tno = (ticket_no or "").strip()
    work_no = (construction_work_no or "").strip()
    pr_code = (project_code or "").strip()
    section = normalize_linked_section(linked_section)
    if not section:
        if tno:
            section = "ops"
        elif pr_code:
            section = "projects"
        elif work_no:
            section = "constructions"

    where = ["1=0"]
    params: list = []
    if tno:
        where.append("ticket_no=?")
        params.append(tno)
    if work_no:
        where.append("construction_work_no=?")
        params.append(work_no)
    if pr_code:
        where.append("project_code=?")
        params.append(pr_code)
    clause = " OR ".join(where)

    coords = rows_to_dicts(
        conn.execute(
            f"SELECT * FROM new_coordinations WHERE {clause} ORDER BY id DESC LIMIT 20",
            params,
        ).fetchall()
    ) if params else []
    licenses = rows_to_dicts(
        conn.execute(
            f"SELECT * FROM issued_licenses WHERE {clause} ORDER BY id DESC LIMIT 20",
            params,
        ).fetchall()
    ) if params else []
    clearances = []
    if tno:
        clearances = rows_to_dicts(
            conn.execute(
                "SELECT * FROM quality_clearances WHERE ticket_no=? ORDER BY id DESC LIMIT 20",
                (tno,),
            ).fetchall()
        )

    latest_coord = coords[0] if coords else None
    latest_license = licenses[0] if licenses else None
    if latest_coord and latest_coord.get("transferred_license_id") and not latest_license:
        lic = conn.execute(
            "SELECT * FROM issued_licenses WHERE id=?",
            (latest_coord["transferred_license_id"],),
        ).fetchone()
        if lic:
            latest_license = dict(lic)
            licenses = [latest_license] + licenses
    latest_clearance = clearances[0] if clearances else None

    if latest_clearance:
        stage = "evacuations"
    elif latest_license:
        stage = "permits"
    elif latest_coord:
        stage = "coords"
    else:
        stage = "none"

    out = {
        "ticket_no": tno,
        "construction_work_no": work_no,
        "project_code": pr_code,
        "linked_section": section,
        "linked_section_label": linked_section_label(section),
        "stage": stage,
        "coords": coords,
        "licenses": licenses,
        "clearances": clearances,
        "latest_coord": latest_coord,
        "latest_license": latest_license,
        "latest_clearance": latest_clearance,
        "has_coord": bool(latest_coord),
        "has_license": bool(latest_license),
        "has_clearance": bool(latest_clearance),
        "coord_transferred": bool(latest_coord and latest_coord.get("transferred_license_id")),
    }
    if own:
        conn.close()
    return out


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


def _parse_iso_date(value: str | None):
    if not value:
        return None
    text = str(value).strip()[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def license_remaining_days(expiry_date: str | None) -> int | None:
    d = _parse_iso_date(expiry_date)
    if not d:
        return None
    return (d - datetime.now().date()).days


def enrich_issued_licenses(rows: list[dict]) -> list[dict]:
    today = datetime.now().date()
    for r in rows:
        rem = license_remaining_days(r.get("expiry_date"))
        r["remaining_days"] = rem
        issue = _parse_iso_date(r.get("issue_date"))
        r["days_since_issue"] = (today - issue).days if issue else None
        if rem is None:
            r["days_bucket"] = "unknown"
        elif rem < 7:
            r["days_bucket"] = "lt7"
        elif rem <= 15:
            r["days_bucket"] = "7_15"
        elif rem <= 25:
            r["days_bucket"] = "15_25"
        else:
            r["days_bucket"] = "gt25"
        submitted = (r.get("consultant_submitted") or "").strip()
        if not submitted and (r.get("consultant_submit_date") or "").strip():
            submitted = "نعم"
        r["consultant_submitted_flag"] = submitted in ("نعم", "1", "true", "True", "yes", "Yes")
    return rows


def refresh_issued_license_expiry_status(conn=None) -> int:
    """حدّث حالة الرخص المنتهية تلقائياً حسب تاريخ الانتهاء."""
    own = conn is None
    conn = conn or connect()
    today = datetime.now().strftime("%Y-%m-%d")
    cur = conn.execute(
        """
        UPDATE issued_licenses
        SET status='منتهية'
        WHERE expiry_date IS NOT NULL AND trim(expiry_date) != ''
          AND substr(expiry_date, 1, 10) < ?
          AND coalesce(status, '') NOT IN ('ملغاة', 'منتهية')
        """,
        (today,),
    )
    n = cur.rowcount or 0
    if own:
        conn.commit()
        conn.close()
    return int(n)


def _year_month_clause(column: str, year: str | None, month: str | None):
    clauses: list[str] = []
    params: list[str] = []
    y = (year or "").strip()
    m = (month or "").strip()
    if y and y.isdigit():
        clauses.append(f"substr(coalesce({column}, ''), 1, 4)=?")
        params.append(y)
    if m and m.isdigit():
        mm = m.zfill(2)
        clauses.append(f"substr(coalesce({column}, ''), 6, 2)=?")
        params.append(mm)
    return clauses, params


def _license_workflow_match(sub: str) -> tuple[str, list]:
    """شرط تصفية تبويبات متابعة التصاريح."""
    key = (sub or "active").strip().lower()
    if key in ("active", "valid", "سارية", "الرخص السارية"):
        return (
            """
            coalesce(status, '') NOT IN ('منتهية', 'ملغاة')
            AND (
              coalesce(workflow_status, '') IN ('', 'متابعة بعد الإصدار')
              OR workflow_status IS NULL
            )
            """,
            [],
        )
    if key in ("checks", "تشييكات", "تحت التشييكات"):
        return ("coalesce(workflow_status, '') = 'تحت التشييكات'", [])
    if key in ("closing", "إغلاق", "تحت إجراءات الإغلاق"):
        return (
            """
            coalesce(workflow_status, '') IN (
              'تحت إجراءات الإغلاق',
              'الإخلاء المبدئي',
              'من هنا تبدأ رحلة الإخلاءات'
            )
            """,
            [],
        )
    if key in ("asphalt", "أسفلت", "موردي الأسفلت"):
        return (
            """
            (
              coalesce(workflow_status, '') = 'موردي الأسفلت'
              OR coalesce(license_type, '') = 'حفر'
            )
            """,
            [],
        )
    if key in ("expired", "منتهية", "الرخص المنتهية"):
        return ("coalesce(status, '') = 'منتهية'", [])
    if key in ("cancelled", "ملغاة", "الرخص الملغاة"):
        return ("coalesce(status, '') = 'ملغاة'", [])
    return ("1=1", [])


def list_issued_licenses_for_hub(
    conn=None,
    *,
    sub: str = "active",
    year: str | None = None,
    month: str | None = None,
    q: str | None = None,
    limit: int = 500,
) -> list[dict]:
    own = conn is None
    conn = conn or connect()
    refresh_issued_license_expiry_status(conn)
    where = ["1=1"]
    params: list = []
    wf_sql, wf_params = _license_workflow_match(sub)
    where.append(f"({wf_sql})")
    params.extend(wf_params)
    ym_clauses, ym_params = _year_month_clause("issue_date", year, month)
    where.extend(ym_clauses)
    params.extend(ym_params)
    query = (q or "").strip()
    if query:
        like = f"%{query}%"
        where.append(
            """
            (
              license_no LIKE ? OR work_order LIKE ? OR rtc_no LIKE ?
              OR ticket_no LIKE ? OR project_code LIKE ? OR construction_work_no LIKE ?
              OR district LIKE ? OR location LIKE ? OR authority LIKE ?
              OR status LIKE ? OR workflow_status LIKE ? OR cast(id as text) LIKE ?
            )
            """
        )
        params.extend([like] * 12)
    sql = f"SELECT * FROM issued_licenses WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    rows = rows_to_dicts(conn.execute(sql, params).fetchall())
    enrich_issued_licenses(rows)
    if own:
        conn.close()
    return rows


def count_issued_licenses_by_hub_sub(conn=None) -> dict:
    own = conn is None
    conn = conn or connect()
    refresh_issued_license_expiry_status(conn)
    keys = ("active", "checks", "closing", "asphalt", "expired", "cancelled", "all")
    out = {}
    for key in keys:
        if key == "all":
            out[key] = int(conn.execute("SELECT COUNT(*) FROM issued_licenses").fetchone()[0] or 0)
            continue
        wf_sql, wf_params = _license_workflow_match(key)
        out[key] = int(
            conn.execute(f"SELECT COUNT(*) FROM issued_licenses WHERE ({wf_sql})", wf_params).fetchone()[0] or 0
        )
    if own:
        conn.close()
    return out


def license_days_buckets(rows: list[dict]) -> dict:
    buckets = {"lt7": 0, "7_15": 0, "15_25": 0, "gt25": 0, "unknown": 0}
    submitted = 0
    waiting = 0
    for r in rows:
        buckets[r.get("days_bucket") or "unknown"] = buckets.get(r.get("days_bucket") or "unknown", 0) + 1
        if r.get("consultant_submitted_flag"):
            submitted += 1
        else:
            waiting += 1
    buckets["submitted"] = submitted
    buckets["waiting"] = waiting
    buckets["total"] = len(rows)
    return buckets


_COORD_KIND_BY_SUB = {
    "coords": "تنسيق جديد",
    "reports": "بلاغ",
    "master_plan": "مخطط شامل",
    "violations": "مخالفة",
    "تنسيق جديد": "تنسيق جديد",
    "بلاغ": "بلاغ",
    "بلاغات": "بلاغ",
    "مخطط شامل": "مخطط شامل",
    "مخالفة": "مخالفة",
    "المخالفات": "مخالفة",
}


def coord_kind_for_sub(sub: str | None) -> str | None:
    key = (sub or "coords").strip().lower()
    if key in ("", "coords", "list", "new"):
        return "تنسيق جديد"
    return _COORD_KIND_BY_SUB.get(key) or _COORD_KIND_BY_SUB.get(sub or "")


def list_new_coordinations_for_hub(
    conn=None,
    *,
    kind: str | None = "تنسيق جديد",
    year: str | None = None,
    month: str | None = None,
    q: str | None = None,
    limit: int = 500,
) -> list[dict]:
    own = conn is None
    conn = conn or connect()
    where = ["1=1"]
    params: list = []
    kind_label = (kind or "").strip()
    if kind_label:
        if kind_label == "تنسيق جديد":
            where.append("coalesce(nullif(trim(coord_kind), ''), 'تنسيق جديد') = ?")
        else:
            where.append("coalesce(coord_kind, '') = ?")
        params.append(kind_label)
    ym_clauses, ym_params = _year_month_clause("request_date", year, month)
    where.extend(ym_clauses)
    params.extend(ym_params)
    query = (q or "").strip()
    if query:
        like = f"%{query}%"
        where.append(
            """
            (
              coord_no LIKE ? OR authority LIKE ? OR ticket_no LIKE ?
              OR project_code LIKE ? OR construction_work_no LIKE ?
              OR district LIKE ? OR location LIKE ? OR license_no LIKE ?
              OR status LIKE ? OR coord_kind LIKE ? OR cast(id as text) LIKE ?
            )
            """
        )
        params.extend([like] * 11)
    sql = f"SELECT * FROM new_coordinations WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    rows = rows_to_dicts(conn.execute(sql, params).fetchall())
    if own:
        conn.close()
    return rows


def count_new_coordinations_by_kind(conn=None) -> dict:
    """عدادات تبويبات التنسيقات الجديدة (تنسيق/بلاغ/مخطط/مخالفة)."""
    own = conn is None
    conn = conn or connect()
    out = {"coords": 0, "reports": 0, "master_plan": 0, "violations": 0}
    rows = conn.execute(
        """
        SELECT coalesce(nullif(trim(coord_kind), ''), 'تنسيق جديد') AS kind, COUNT(*) AS n
        FROM new_coordinations
        GROUP BY 1
        """
    ).fetchall()
    for r in rows:
        label = (r["kind"] or "تنسيق جديد").strip()
        n = int(r["n"] or 0)
        if label == "بلاغ":
            out["reports"] += n
        elif label == "مخطط شامل":
            out["master_plan"] += n
        elif label == "مخالفة":
            out["violations"] += n
        else:
            out["coords"] += n
    if own:
        conn.close()
    return out


def list_clearances_for_hub(
    conn=None,
    *,
    stage: str = "initial",
    year: str | None = None,
    month: str | None = None,
    q: str | None = None,
    limit: int = 500,
) -> list[dict]:
    own = conn is None
    conn = conn or connect()
    stage_key = (stage or "initial").strip().lower()
    stage_map = {
        "initial": "إخلاء مبدئي",
        "final": "إخلاء نهائي",
        "cancelled": "رخصة ملغاة",
        "إخلاء مبدئي": "إخلاء مبدئي",
        "إخلاء نهائي": "إخلاء نهائي",
        "رخصة ملغاة": "رخصة ملغاة",
    }
    stage_label = stage_map.get(stage_key, "إخلاء مبدئي")
    where = ["coalesce(clearance_stage, 'إخلاء مبدئي') = ?"]
    params: list = [stage_label]
    ym_clauses, ym_params = _year_month_clause("request_date", year, month)
    where.extend(ym_clauses)
    params.extend(ym_params)
    query = (q or "").strip()
    if query:
        like = f"%{query}%"
        where.append(
            """
            (
              ticket_no LIKE ? OR clearance_no LIKE ? OR rekaz_code LIKE ?
              OR contractor LIKE ? OR status LIKE ? OR cast(id as text) LIKE ?
            )
            """
        )
        params.extend([like] * 6)
    sql = f"SELECT * FROM quality_clearances WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    rows = rows_to_dicts(conn.execute(sql, params).fetchall())
    if own:
        conn.close()
    return rows


def count_clearances_by_stage(conn=None) -> dict:
    own = conn is None
    conn = conn or connect()
    out = {"initial": 0, "final": 0, "cancelled": 0}
    rows = conn.execute(
        """
        SELECT coalesce(nullif(trim(clearance_stage), ''), 'إخلاء مبدئي') AS stage, COUNT(*) AS n
        FROM quality_clearances
        GROUP BY 1
        """
    ).fetchall()
    for r in rows:
        label = (r["stage"] or "إخلاء مبدئي").strip()
        if label == "إخلاء نهائي":
            out["final"] += int(r["n"] or 0)
        elif label == "رخصة ملغاة":
            out["cancelled"] += int(r["n"] or 0)
        else:
            out["initial"] += int(r["n"] or 0)
    # الرخص الملغاة من جدول الرخص أيضاً
    cancelled_lic = conn.execute(
        "SELECT COUNT(*) FROM issued_licenses WHERE coalesce(status, '') = 'ملغاة'"
    ).fetchone()[0]
    out["cancelled_licenses"] = int(cancelled_lic or 0)
    if own:
        conn.close()
    return out


def quality_hub_year_options(conn=None) -> list[str]:
    own = conn is None
    conn = conn or connect()
    years = set()
    for sql in (
        "SELECT DISTINCT substr(issue_date,1,4) FROM issued_licenses WHERE length(coalesce(issue_date,'')) >= 4",
        "SELECT DISTINCT substr(request_date,1,4) FROM new_coordinations WHERE length(coalesce(request_date,'')) >= 4",
        "SELECT DISTINCT substr(request_date,1,4) FROM quality_clearances WHERE length(coalesce(request_date,'')) >= 4",
    ):
        for (y,) in conn.execute(sql).fetchall():
            if y and str(y).isdigit():
                years.add(str(y))
    years.add(str(datetime.now().year))
    if own:
        conn.close()
    return sorted(years, reverse=True)


def normalize_warehouse_unit(unit) -> str:
    value = str(unit or "").strip()
    if not value:
        return ""
    compact = (
        value.casefold()
        .replace(" ", "")
        .replace(".", "")
        .replace("-", "")
        .replace("_", "")
    )
    kilometer_words = {
        "km",
        "kms",
        "kilometer",
        "kilometers",
        "kilometre",
        "kilometres",
    }
    meter_words = {"m", "meter", "meters", "metre", "metres"}
    if compact in kilometer_words or ("\u0643\u064a\u0644\u0648" in value and "\u0645\u062a\u0631" in value):
        return "M"
    if compact in meter_words or value in {"\u0645", "\u0645\u062a\u0631"}:
        return "M"
    return value


def normalize_warehouse_length_units_once(conn=None) -> int:
    own = conn is None
    conn = conn or connect()
    changed = 0
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in (
            "warehouse_items",
            "warehouse_tx",
            "boq_items",
            "contract_boq_items",
            "ticket_boq_lines",
            "external_purchase_lines",
            "contractor_supply_lines",
        ):
            if table not in tables:
                continue
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "unit" not in cols:
                continue
            cur = conn.execute(
                f"""
                UPDATE {table}
                SET unit='M'
                WHERE lower(trim(coalesce(unit,''))) IN (
                    'km', 'k.m', 'kms', 'kilometer', 'kilometers', 'kilometre', 'kilometres'
                )
                   OR unit LIKE '%\u0643\u064a\u0644\u0648%\u0645\u062a\u0631%'
                """
            )
            changed += int(cur.rowcount or 0)
        if own:
            conn.commit()
        return changed
    finally:
        if own:
            conn.close()


def warehouse_tx_sign(tx_type: str) -> int:
    """+1 وارد، -1 منصرف/إرجاع، 0 غير معروف."""
    t = tx_type or ""
    if "إرجاع عهدة" in t:
        return 1
    if "وارد" in t or "افتتاح" in t:
        return 1
    if "منصرف" in t or "إرجاع" in t or "صرف عهدة" in t:
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
        """
        SELECT id, voucher_no, tx_date, tx_type, item_no, item_name, unit, qty,
               ticket_no, rekaz_code, source_section, source_ref, work_order,
               sender, recipient, notes
        FROM warehouse_tx
        WHERE lower(item_no)=lower(?)
        ORDER BY tx_date DESC, id DESC
        """,
        (item_no or "",),
    ).fetchall()
    conn.close()
    inbound = outbound = 0.0
    tickets = set()
    movements = []
    for r in rows:
        qty = float(r["qty"] or 0)
        sign = warehouse_tx_sign(r["tx_type"])
        if sign > 0:
            inbound += qty
        elif sign < 0:
            outbound += qty
        if r["ticket_no"]:
            tickets.add(r["ticket_no"])
        movements.append(
            {
                **dict(r),
                "unit": normalize_warehouse_unit(r["unit"]),
                "sign": sign,
                "signed_qty": qty * sign,
            }
        )
    return {
        "balance": inbound - outbound,
        "inbound": inbound,
        "outbound": outbound,
        "tx_count": len(rows),
        "tickets": sorted(tickets),
        "movements": movements,
    }


def warehouse_movements_totals(source_section: str | None = None, conn=None) -> dict:
    """إجمالي كميات الوارد / المنصرف / المتبقي بدون تفصيل المواد."""
    own = conn is None
    conn = conn or connect()
    params: list = []
    where = "1=1"
    section = (source_section or "").strip().lower()
    if section in ("ops", "constructions", "projects", "external", "custody", "contractors", "reinforcement"):
        where = "lower(coalesce(source_section,''))=?"
        params.append(section)
    rows = conn.execute(
        f"SELECT tx_type, qty FROM warehouse_tx WHERE {where}",
        params,
    ).fetchall()
    if own:
        conn.close()
    inbound = outbound = 0.0
    for r in rows:
        qty = float(r["qty"] or 0)
        sign = warehouse_tx_sign(r["tx_type"])
        if sign > 0:
            inbound += qty
        elif sign < 0:
            outbound += qty
    return {
        "inbound": inbound,
        "outbound": outbound,
        "balance": inbound - outbound,
        "tx_count": len(rows),
        "source_section": section or "all",
    }


def warehouse_movements_totals_by_source(conn=None) -> list[dict]:
    """إجماليات الكميات مجمّعة حسب القسم المصدر."""
    own = conn is None
    conn = conn or connect()
    sections = [
        ("reinforcement", "التعزيز - اسكيمات"),
        ("ops", "العمليات والصيانة"),
        ("constructions", "الإنشاءات"),
        ("projects", "المشاريع"),
        ("external", "المشتريات الخارجية"),
        ("custody", "العهد"),
        ("contractors", "مواد موردة من مقاول"),
        ("", "غير مصنّف"),
    ]
    out = []
    for key, label in sections:
        if key:
            totals = warehouse_movements_totals(key, conn=conn)
        else:
            rows = conn.execute(
                """
                SELECT tx_type, qty FROM warehouse_tx
                WHERE trim(coalesce(source_section,''))=''
                   OR lower(trim(source_section)) NOT IN ('ops','constructions','projects','external','custody','contractors','reinforcement')
                """
            ).fetchall()
            inbound = outbound = 0.0
            for r in rows:
                qty = float(r["qty"] or 0)
                sign = warehouse_tx_sign(r["tx_type"])
                if sign > 0:
                    inbound += qty
                elif sign < 0:
                    outbound += qty
            totals = {
                "inbound": inbound,
                "outbound": outbound,
                "balance": inbound - outbound,
                "tx_count": len(rows),
                "source_section": "other",
            }
        if totals["tx_count"] <= 0 and key == "":
            continue
        totals["label"] = label
        totals["key"] = key or "other"
        out.append(totals)
    if own:
        conn.close()
    return out


def seed_reinforcement_departments(conn=None) -> int:
    """بذور أقسام افتراضية إن كان الجدول فارغاً — يمكن إضافة المزيد يدوياً."""
    own = conn is None
    conn = conn or connect()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "reinforcement_departments" not in tables:
        if own:
            conn.close()
        return 0
    n = conn.execute("SELECT COUNT(*) FROM reinforcement_departments").fetchone()[0]
    if int(n or 0) > 0:
        if own:
            conn.close()
        return 0
    seeds = [
        ("صيانة العدادات", "METER", "نشط", "قسم افتراضي — يمكن تعديله أو إضافة أقسام أخرى"),
        ("صيانة المحطات", "STATION", "نشط", "قسم افتراضي — يمكن تعديله أو إضافة أقسام أخرى"),
    ]
    for name, code, status, notes in seeds:
        conn.execute(
            "INSERT INTO reinforcement_departments(dept_name, dept_code, status, notes) VALUES (?,?,?,?)",
            (name, code, status, notes),
        )
    if own:
        conn.commit()
        conn.close()
    return len(seeds)


def list_reinforcement_departments(active_only: bool = True, conn=None) -> list[dict]:
    own = conn is None
    conn = conn or connect()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "reinforcement_departments" not in tables:
        if own:
            conn.close()
        return []
    sql = "SELECT * FROM reinforcement_departments"
    params: list = []
    if active_only:
        sql += " WHERE status IS NULL OR trim(status)='' OR status='نشط' OR status='نعم'"
    sql += " ORDER BY dept_name"
    rows = rows_to_dicts(conn.execute(sql, params).fetchall())
    if own:
        conn.close()
    return rows


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


def migrate_external_purchase_lines(conn=None) -> int:
    """ينقل أصناف الطلبات القديمة (صنف واحد في الرأس) إلى جدول الأسطر."""
    own = conn is None
    conn = conn or connect()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "external_purchases" not in tables:
        if own:
            conn.close()
        return 0
    if "external_purchase_lines" not in tables:
        conn.execute(EXTRA_TABLE_DDL["external_purchase_lines"])
    moved = 0
    rows = rows_to_dicts(conn.execute("SELECT * FROM external_purchases").fetchall())
    for po in rows:
        pid = po.get("id")
        has_lines = conn.execute(
            "SELECT 1 FROM external_purchase_lines WHERE purchase_id=? LIMIT 1",
            (pid,),
        ).fetchone()
        if has_lines:
            continue
        name = (po.get("item_name") or "").strip()
        qty = po.get("qty")
        price = po.get("unit_price")
        if not name and qty in (None, "", 0) and price in (None, "", 0):
            continue
        try:
            q = float(qty or 0)
        except (TypeError, ValueError):
            q = 0.0
        try:
            p = float(price or 0)
        except (TypeError, ValueError):
            p = 0.0
        conn.execute(
            """
            INSERT INTO external_purchase_lines(
              purchase_id, purchase_no, item_no, item_name, unit, qty, unit_price, line_total
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                pid,
                po.get("purchase_no") or "",
                "",
                name or "صنف قديم",
                "",
                q,
                p,
                round(q * p, 2),
            ),
        )
        moved += 1
    if own:
        conn.commit()
        conn.close()
    return moved


def list_purchase_lines(purchase_id: int, conn=None) -> list[dict]:
    own = conn is None
    conn = conn or connect()
    rows = rows_to_dicts(
        conn.execute(
            "SELECT * FROM external_purchase_lines WHERE purchase_id=? ORDER BY id",
            (purchase_id,),
        ).fetchall()
    )
    if own:
        conn.close()
    return rows


def purchase_lines_summary(purchase_ids: list[int] | None = None, conn=None) -> dict[int, dict]:
    """ملخص أصناف الطلبات: العدد والإجمالي وأول اسم مادة."""
    own = conn is None
    conn = conn or connect()
    out: dict[int, dict] = {}
    if purchase_ids is not None and not purchase_ids:
        if own:
            conn.close()
        return out
    sql = """
        SELECT purchase_id,
               COUNT(*) AS line_count,
               COALESCE(SUM(COALESCE(line_total, qty * unit_price)), 0) AS total,
               MIN(item_name) AS first_item
        FROM external_purchase_lines
    """
    params: list = []
    if purchase_ids is not None:
        placeholders = ",".join("?" * len(purchase_ids))
        sql += f" WHERE purchase_id IN ({placeholders})"
        params.extend(purchase_ids)
    sql += " GROUP BY purchase_id"
    for r in rows_to_dicts(conn.execute(sql, params).fetchall()):
        out[int(r["purchase_id"])] = {
            "line_count": int(r["line_count"] or 0),
            "total": float(r["total"] or 0),
            "first_item": r.get("first_item") or "",
        }
    if own:
        conn.close()
    return out


def add_purchase_line(
    purchase_id: int,
    *,
    item_no: str,
    qty: float,
    unit_price: float | None = None,
    notes: str = "",
    conn=None,
) -> dict:
    own = conn is None
    conn = conn or connect()
    po = conn.execute("SELECT * FROM external_purchases WHERE id=?", (purchase_id,)).fetchone()
    if not po:
        if own:
            conn.close()
        raise ValueError("طلب الشراء غير موجود")
    if (po["received_voucher_no"] if "received_voucher_no" in po.keys() else None):
        if own:
            conn.close()
        raise ValueError("تم ترحيل الطلب للمستودع — لا يمكن تعديل الأصناف")
    item_no = (item_no or "").strip()
    if not item_no:
        if own:
            conn.close()
        raise ValueError("اختر مادة من المستودع")
    item = conn.execute(
        "SELECT * FROM warehouse_items WHERE lower(item_no)=lower(?)",
        (item_no,),
    ).fetchone()
    if not item:
        if own:
            conn.close()
        raise ValueError(f"رقم المادة «{item_no}» غير موجود في المستودع")
    try:
        q = float(qty)
    except (TypeError, ValueError):
        q = 0.0
    if q <= 0:
        if own:
            conn.close()
        raise ValueError("أدخل كمية صحيحة")
    try:
        price = float(unit_price) if unit_price not in (None, "") else 0.0
    except (TypeError, ValueError):
        price = 0.0
    line_total = round(q * price, 2)
    cur = conn.execute(
        """
        INSERT INTO external_purchase_lines(
          purchase_id, purchase_no, item_no, item_name, unit, qty, unit_price, line_total, notes
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            purchase_id,
            po["purchase_no"] or "",
            item["item_no"],
            item["item_name"] or "",
            normalize_warehouse_unit(item["unit"] or ""),
            q,
            price,
            line_total,
            (notes or "").strip(),
        ),
    )
    # حدّث ملخص الرأس للتوافق مع القوائم القديمة
    conn.execute(
        """
        UPDATE external_purchases
        SET item_name=?, qty=?, unit_price=?
        WHERE id=?
        """,
        (item["item_name"] or "", q, price, purchase_id),
    )
    line_id = cur.lastrowid
    if own:
        conn.commit()
        conn.close()
    return {"id": line_id, "item_no": item["item_no"], "qty": q, "line_total": line_total}


def delete_purchase_line(line_id: int, conn=None) -> None:
    own = conn is None
    conn = conn or connect()
    line = conn.execute("SELECT * FROM external_purchase_lines WHERE id=?", (line_id,)).fetchone()
    if not line:
        if own:
            conn.close()
        raise ValueError("السطر غير موجود")
    po = conn.execute("SELECT * FROM external_purchases WHERE id=?", (line["purchase_id"],)).fetchone()
    if po and (dict(po).get("received_voucher_no") or "").strip():
        if own:
            conn.close()
        raise ValueError("تم ترحيل الطلب للمستودع — لا يمكن حذف الأصناف")
    if line["warehouse_tx_id"]:
        if own:
            conn.close()
        raise ValueError("الصنف مرحّل للمستودع")
    conn.execute("DELETE FROM external_purchase_lines WHERE id=?", (line_id,))
    if own:
        conn.commit()
        conn.close()


def receive_purchase_to_warehouse(purchase_id: int, conn=None) -> dict:
    """يرحّل أصناف طلب الشراء كوارد للمستودع (مرة واحدة)."""
    own = conn is None
    conn = conn or connect()
    po = conn.execute("SELECT * FROM external_purchases WHERE id=?", (purchase_id,)).fetchone()
    if not po:
        if own:
            conn.close()
        raise ValueError("طلب الشراء غير موجود")
    po = dict(po)
    if (po.get("received_voucher_no") or "").strip():
        if own:
            conn.close()
        return {"already": True, "voucher_no": po["received_voucher_no"], "created": 0}
    lines = list_purchase_lines(purchase_id, conn=conn)
    if not lines:
        if own:
            conn.close()
        raise ValueError("أضف صنفاً واحداً على الأقل قبل الترحيل للمستودع")
    missing = [ln for ln in lines if not (ln.get("item_no") or "").strip()]
    if missing:
        if own:
            conn.close()
        raise ValueError("كل الأصناف يجب أن تكون مربوطة برقم مادة من المستودع")
    voucher = next_warehouse_voucher_no(conn)
    tx_date = (po.get("purchase_date") or "").strip() or datetime.now().strftime("%Y-%m-%d")
    tx_type = "وارد من مشتريات خارجية"
    created = 0
    for ln in lines:
        if ln.get("warehouse_tx_id"):
            continue
        cur = conn.execute(
            """
            INSERT INTO warehouse_tx(
              voucher_no, tx_date, tx_type, item_no, item_name, unit, qty,
              recipient, sender, ticket_no, rekaz_code, source_section, source_ref, work_order, region, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                voucher,
                tx_date,
                tx_type,
                ln.get("item_no") or "",
                ln.get("item_name") or "",
                normalize_warehouse_unit(ln.get("unit") or ""),
                float(ln.get("qty") or 0),
                "المستودع",
                po.get("supplier") or "مشتريات خارجية",
                po.get("ticket_no") or "",
                "",
                "external",
                po.get("purchase_no") or str(purchase_id),
                "",
                "",
                (ln.get("notes") or po.get("notes") or "").strip(),
            ),
        )
        conn.execute(
            "UPDATE external_purchase_lines SET warehouse_tx_id=? WHERE id=?",
            (cur.lastrowid, ln["id"]),
        )
        created += 1
    conn.execute(
        """
        UPDATE external_purchases
        SET received_voucher_no=?, status=CASE
              WHEN status IS NULL OR trim(status)='' OR status='جديد' OR status='معتمد' THEN 'تم الشراء'
              ELSE status END
        WHERE id=?
        """,
        (voucher, purchase_id),
    )
    if own:
        conn.commit()
        conn.close()
    return {"already": False, "voucher_no": voucher, "created": created}


def _custody_row(custody_id: int, conn) -> dict:
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM custody WHERE id=?", (custody_id,)).fetchone()
    if not row:
        raise ValueError("سجل العهدة غير موجود")
    return dict(row)


def issue_custody_to_warehouse(custody_id: int, conn=None) -> dict:
    """يصرف العهدة من رصيد المستودع بسند مستقل مصدره custody."""
    own = conn is None
    conn = conn or connect()
    try:
        row = _custody_row(custody_id, conn)
        if (row.get("issued_voucher_no") or "").strip():
            return {"already": True, "voucher_no": row["issued_voucher_no"], "tx_id": row.get("warehouse_tx_id")}
        item_no = (row.get("item_no") or "").strip()
        if not item_no:
            raise ValueError("اختر رقم المادة من المستودع قبل صرف العهدة")
        item = conn.execute(
            "SELECT * FROM warehouse_items WHERE lower(item_no)=lower(?)",
            (item_no,),
        ).fetchone()
        if not item:
            raise ValueError(f"رقم المادة «{item_no}» غير موجود في المستودع")
        try:
            qty = float(row.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty <= 0:
            raise ValueError("أدخل كمية صحيحة قبل صرف العهدة")
        available = float(warehouse_balance(item_no) or 0)
        if available < qty:
            raise ValueError(f"رصيد المادة غير كافٍ. المتاح: {available:.2f}")
        custody_no = (row.get("custody_no") or "").strip() or next_series_code("cu", conn)
        tx_date = (row.get("custody_date") or "").strip() or datetime.now().strftime("%Y-%m-%d")
        voucher = next_warehouse_voucher_no(conn)
        cur = conn.execute(
            """
            INSERT INTO warehouse_tx(
              voucher_no, tx_date, tx_type, item_no, item_name, unit, qty,
              recipient, sender, ticket_no, rekaz_code, source_section, source_ref, work_order, region, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                voucher,
                tx_date,
                "صرف عهدة",
                item["item_no"],
                item["item_name"] or row.get("item_name") or "",
                normalize_warehouse_unit(item["unit"] or row.get("unit") or ""),
                qty,
                row.get("employee") or "مستلم عهدة",
                "المستودع",
                "",
                "",
                "custody",
                custody_no,
                custody_no,
                "",
                (row.get("notes") or "").strip(),
            ),
        )
        conn.execute(
            """
            UPDATE custody
            SET custody_no=?, item_no=?, item_name=?, unit=?, status='مسلمة',
                issued_voucher_no=?, warehouse_tx_id=?
            WHERE id=?
            """,
            (
                custody_no,
                item["item_no"],
                item["item_name"] or row.get("item_name") or "",
                normalize_warehouse_unit(item["unit"] or row.get("unit") or ""),
                voucher,
                cur.lastrowid,
                custody_id,
            ),
        )
        if own:
            conn.commit()
        return {"already": False, "voucher_no": voucher, "tx_id": cur.lastrowid}
    finally:
        if own:
            conn.close()


def return_custody_to_warehouse(custody_id: int, conn=None) -> dict:
    """يرجع العهدة للمستودع بسند وارد مستقل مصدره custody."""
    own = conn is None
    conn = conn or connect()
    try:
        row = _custody_row(custody_id, conn)
        if not (row.get("issued_voucher_no") or "").strip():
            raise ValueError("لا يمكن إرجاع عهدة لم تُصرف من المستودع بعد")
        if (row.get("return_voucher_no") or "").strip():
            return {"already": True, "voucher_no": row["return_voucher_no"], "tx_id": row.get("return_warehouse_tx_id")}
        item_no = (row.get("item_no") or "").strip()
        if not item_no:
            raise ValueError("رقم المادة غير موجود في سجل العهدة")
        try:
            qty = float(row.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty <= 0:
            raise ValueError("كمية العهدة غير صالحة")
        item = conn.execute(
            "SELECT * FROM warehouse_items WHERE lower(item_no)=lower(?)",
            (item_no,),
        ).fetchone()
        item_name = (item["item_name"] if item else None) or row.get("item_name") or ""
        unit = normalize_warehouse_unit((item["unit"] if item else None) or row.get("unit") or "")
        custody_no = (row.get("custody_no") or "").strip() or str(custody_id)
        tx_date = (row.get("return_date") or "").strip() or datetime.now().strftime("%Y-%m-%d")
        voucher = next_warehouse_voucher_no(conn)
        cur = conn.execute(
            """
            INSERT INTO warehouse_tx(
              voucher_no, tx_date, tx_type, item_no, item_name, unit, qty,
              recipient, sender, ticket_no, rekaz_code, source_section, source_ref, work_order, region, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                voucher,
                tx_date,
                "إرجاع عهدة",
                item_no,
                item_name,
                unit,
                qty,
                "المستودع",
                row.get("employee") or "مستلم عهدة",
                "",
                "",
                "custody",
                custody_no,
                custody_no,
                "",
                (row.get("notes") or "").strip(),
            ),
        )
        conn.execute(
            """
            UPDATE custody
            SET status='مرتجعة', return_date=?, return_voucher_no=?, return_warehouse_tx_id=?
            WHERE id=?
            """,
            (tx_date, voucher, cur.lastrowid, custody_id),
        )
        if own:
            conn.commit()
        return {"already": False, "voucher_no": voucher, "tx_id": cur.lastrowid}
    finally:
        if own:
            conn.close()


def list_contractor_supply_lines(supply_id: int, conn=None) -> list[dict]:
    own = conn is None
    conn = conn or connect()
    rows = rows_to_dicts(
        conn.execute(
            "SELECT * FROM contractor_supply_lines WHERE supply_id=? ORDER BY id",
            (supply_id,),
        ).fetchall()
    )
    if own:
        conn.close()
    return rows


def contractor_supply_lines_summary(supply_ids: list[int] | None = None, conn=None) -> dict[int, dict]:
    own = conn is None
    conn = conn or connect()
    out: dict[int, dict] = {}
    if supply_ids is not None and not supply_ids:
        if own:
            conn.close()
        return out
    sql = """
        SELECT supply_id,
               COUNT(*) AS line_count,
               COALESCE(SUM(COALESCE(line_total, qty * unit_price)), 0) AS total,
               COALESCE(SUM(COALESCE(qty, 0)), 0) AS qty_total,
               MIN(item_name) AS first_item
        FROM contractor_supply_lines
    """
    params: list = []
    if supply_ids is not None:
        placeholders = ",".join("?" * len(supply_ids))
        sql += f" WHERE supply_id IN ({placeholders})"
        params.extend(supply_ids)
    sql += " GROUP BY supply_id"
    for r in rows_to_dicts(conn.execute(sql, params).fetchall()):
        out[int(r["supply_id"])] = {
            "line_count": int(r["line_count"] or 0),
            "total": float(r["total"] or 0),
            "qty_total": float(r["qty_total"] or 0),
            "first_item": r.get("first_item") or "",
        }
    if own:
        conn.close()
    return out


def add_contractor_supply_line(
    supply_id: int,
    *,
    item_no: str,
    qty: float,
    unit_price: float | None = None,
    notes: str = "",
    conn=None,
) -> dict:
    own = conn is None
    conn = conn or connect()
    row = conn.execute("SELECT * FROM contractor_supplies WHERE id=?", (supply_id,)).fetchone()
    if not row:
        if own:
            conn.close()
        raise ValueError("سجل التوريد غير موجود")
    if (dict(row).get("received_voucher_no") or "").strip():
        if own:
            conn.close()
        raise ValueError("تم ترحيل التوريد للمستودع — لا يمكن تعديل الأصناف")
    item_no = (item_no or "").strip()
    if not item_no:
        if own:
            conn.close()
        raise ValueError("اختر مادة من المستودع")
    item = conn.execute(
        "SELECT * FROM warehouse_items WHERE lower(item_no)=lower(?)",
        (item_no,),
    ).fetchone()
    if not item:
        if own:
            conn.close()
        raise ValueError(f"رقم المادة «{item_no}» غير موجود في المستودع")
    try:
        q = float(qty)
    except (TypeError, ValueError):
        q = 0.0
    if q <= 0:
        if own:
            conn.close()
        raise ValueError("أدخل كمية صحيحة")
    try:
        price = float(unit_price) if unit_price not in (None, "") else 0.0
    except (TypeError, ValueError):
        price = 0.0
    line_total = round(q * price, 2)
    cur = conn.execute(
        """
        INSERT INTO contractor_supply_lines(
          supply_id, supply_no, item_no, item_name, unit, qty, unit_price, line_total, notes
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            supply_id,
            row["supply_no"] or "",
            item["item_no"],
            item["item_name"] or "",
            normalize_warehouse_unit(item["unit"] or ""),
            q,
            price,
            line_total,
            (notes or "").strip(),
        ),
    )
    line_id = cur.lastrowid
    if own:
        conn.commit()
        conn.close()
    return {"id": line_id, "item_no": item["item_no"], "qty": q, "line_total": line_total}


def delete_contractor_supply_line(line_id: int, conn=None) -> None:
    own = conn is None
    conn = conn or connect()
    line = conn.execute("SELECT * FROM contractor_supply_lines WHERE id=?", (line_id,)).fetchone()
    if not line:
        if own:
            conn.close()
        raise ValueError("السطر غير موجود")
    header = conn.execute("SELECT * FROM contractor_supplies WHERE id=?", (line["supply_id"],)).fetchone()
    if header and (dict(header).get("received_voucher_no") or "").strip():
        if own:
            conn.close()
        raise ValueError("تم ترحيل التوريد للمستودع — لا يمكن حذف الأصناف")
    if line["warehouse_tx_id"]:
        if own:
            conn.close()
        raise ValueError("الصنف مرحّل للمستودع")
    conn.execute("DELETE FROM contractor_supply_lines WHERE id=?", (line_id,))
    if own:
        conn.commit()
        conn.close()


def receive_contractor_supply_to_warehouse(supply_id: int, conn=None) -> dict:
    """يرحّل مواد موردة من مقاول كوارد للمستودع (مرة واحدة)."""
    own = conn is None
    conn = conn or connect()
    row = conn.execute("SELECT * FROM contractor_supplies WHERE id=?", (supply_id,)).fetchone()
    if not row:
        if own:
            conn.close()
        raise ValueError("سجل التوريد غير موجود")
    header = dict(row)
    if (header.get("received_voucher_no") or "").strip():
        if own:
            conn.close()
        return {"already": True, "voucher_no": header["received_voucher_no"], "created": 0}
    lines = list_contractor_supply_lines(supply_id, conn=conn)
    if not lines:
        if own:
            conn.close()
        raise ValueError("أضف صنفاً واحداً على الأقل قبل الترحيل للمستودع")
    missing = [ln for ln in lines if not (ln.get("item_no") or "").strip()]
    if missing:
        if own:
            conn.close()
        raise ValueError("كل الأصناف يجب أن تكون مربوطة برقم مادة من المستودع")
    voucher = next_warehouse_voucher_no(conn)
    tx_date = (header.get("supply_date") or "").strip() or datetime.now().strftime("%Y-%m-%d")
    tx_type = "وارد مواد موردة من مقاول"
    created = 0
    for ln in lines:
        if ln.get("warehouse_tx_id"):
            continue
        cur = conn.execute(
            """
            INSERT INTO warehouse_tx(
              voucher_no, tx_date, tx_type, item_no, item_name, unit, qty,
              recipient, sender, ticket_no, rekaz_code, source_section, source_ref, work_order, region, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                voucher,
                tx_date,
                tx_type,
                ln.get("item_no") or "",
                ln.get("item_name") or "",
                normalize_warehouse_unit(ln.get("unit") or ""),
                float(ln.get("qty") or 0),
                "المستودع",
                header.get("contractor") or "مقاول",
                header.get("ticket_no") or "",
                "",
                "contractors",
                header.get("supply_no") or str(supply_id),
                header.get("work_no") or "",
                "",
                (ln.get("notes") or header.get("notes") or "").strip(),
            ),
        )
        conn.execute(
            "UPDATE contractor_supply_lines SET warehouse_tx_id=? WHERE id=?",
            (cur.lastrowid, ln["id"]),
        )
        created += 1
    conn.execute(
        """
        UPDATE contractor_supplies
        SET received_voucher_no=?, status=CASE
              WHEN status IS NULL OR trim(status)='' OR status='جديد' OR status='معتمد' THEN 'تم التوريد'
              ELSE status END
        WHERE id=?
        """,
        (voucher, supply_id),
    )
    if own:
        conn.commit()
        conn.close()
    return {"already": False, "voucher_no": voucher, "created": created}


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
    data["unit"] = normalize_warehouse_unit(data.get("unit"))
    return data


def get_user_by_api_key(api_key: str, conn=None) -> dict | None:
    """البحث عن مستخدم نشط عبر مفتاح API."""
    key = (api_key or "").strip()
    if not key:
        return None
    own = conn is None
    conn = conn or connect()
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM users WHERE api_key=? AND active=1", (key,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        if own:
            conn.close()


def regenerate_api_key(user_id: int, conn=None) -> str:
    """يولّد مفتاح API جديداً للمستخدم ويحفظه."""
    own = conn is None
    conn = conn or connect()
    try:
        ensure_schema(conn)
        new_key = f"rkz_{secrets.token_urlsafe(32)}"
        conn.execute("UPDATE users SET api_key=? WHERE id=?", (new_key, int(user_id)))
        conn.commit()
        return new_key
    finally:
        if own:
            conn.close()

# ---- أجهزة المبرمج / رموز الموافقة ----

def count_programmer_main_devices(conn=None) -> int:
    own = conn is None
    conn = conn or connect()
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT COUNT(*) FROM programmer_devices WHERE is_main=1"
        ).fetchone()
        return int(row[0] if row else 0)
    except Exception:
        return 0
    finally:
        if own:
            conn.close()


def get_programmer_device_by_hash(token_hash: str, *, main_only: bool = False, conn=None) -> dict | None:
    if not token_hash:
        return None
    own = conn is None
    conn = conn or connect()
    try:
        ensure_schema(conn)
        sql = "SELECT * FROM programmer_devices WHERE token_hash=?"
        if main_only:
            sql += " AND is_main=1"
        row = conn.execute(sql, (token_hash,)).fetchone()
        return rows_to_dicts([row])[0] if row else None
    finally:
        if own:
            conn.close()


def touch_programmer_device(device_id: int, conn=None) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        conn.execute(
            "UPDATE programmer_devices SET last_seen=CURRENT_TIMESTAMP WHERE id=?",
            (int(device_id),),
        )
        conn.commit()
    finally:
        if own:
            conn.close()


def upsert_programmer_main_device(
    *,
    user_id: int,
    token_hash: str,
    label: str = "الجهاز الرئيسي",
    user_agent: str = "",
    ip: str = "",
    conn=None,
) -> None:
    """جهاز رئيسي واحد فقط: يحذف الأجهزة الرئيسية السابقة ثم يضيف الجديد."""
    own = conn is None
    conn = conn or connect()
    try:
        ensure_schema(conn)
        conn.execute("DELETE FROM programmer_devices WHERE is_main=1")
        conn.execute(
            """
            INSERT INTO programmer_devices(user_id, token_hash, label, is_main, user_agent, ip)
            VALUES (?,?,?,1,?,?)
            """,
            (int(user_id), token_hash, label, user_agent, ip),
        )
        conn.commit()
    finally:
        if own:
            conn.close()


def list_programmer_devices(conn=None) -> list[dict]:
    own = conn is None
    conn = conn or connect()
    try:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM programmer_devices ORDER BY is_main DESC, id DESC"
        ).fetchall()
        return rows_to_dicts(rows)
    finally:
        if own:
            conn.close()


def clear_programmer_devices(conn=None) -> int:
    own = conn is None
    conn = conn or connect()
    try:
        ensure_schema(conn)
        cur = conn.execute("DELETE FROM programmer_devices")
        conn.commit()
        return cur.rowcount or 0
    finally:
        if own:
            conn.close()


def create_programmer_approve_code(
    code_hash: str, expires_at: str, *, channel: str = "email", conn=None
) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        ensure_schema(conn)
        ch = (channel or "email").strip().lower() or "email"
        conn.execute(
            """
            DELETE FROM programmer_approve_codes
            WHERE used_at IS NOT NULL OR expires_at < CURRENT_TIMESTAMP
            """
        )
        conn.execute(
            """
            INSERT INTO programmer_approve_codes(code_hash, expires_at, channel)
            VALUES (?,?,?)
            """,
            (code_hash, expires_at, ch),
        )
        conn.commit()
    finally:
        if own:
            conn.close()


def consume_programmer_approve_code(
    code_hash: str, *, allowed_channels: list[str] | None = None, conn=None
) -> bool:
    if not code_hash:
        return False
    own = conn is None
    conn = conn or connect()
    try:
        ensure_schema(conn)
        channels = [c.strip().lower() for c in (allowed_channels or ["email"]) if c and c.strip()]
        if not channels:
            channels = ["email"]
        placeholders = ",".join("?" * len(channels))
        row = conn.execute(
            f"""
            SELECT id, channel FROM programmer_approve_codes
            WHERE code_hash=? AND used_at IS NULL AND expires_at >= CURRENT_TIMESTAMP
              AND lower(COALESCE(channel,'email')) IN ({placeholders})
            ORDER BY id DESC LIMIT 1
            """,
            (code_hash, *channels),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE programmer_approve_codes SET used_at=CURRENT_TIMESTAMP WHERE id=?",
            (row["id"],),
        )
        conn.commit()
        return True
    finally:
        if own:
            conn.close()
