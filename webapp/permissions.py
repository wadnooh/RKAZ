"""نظام صلاحيات المستخدمين — أدوار ومصفوفة صلاحيات لكل التطبيق."""

from __future__ import annotations

from functools import wraps

from flask import flash, redirect, request, session, url_for

from webapp.modules_config import MODULES
from webapp.i18n import _ as i18n_phrase

# ---- أكواد الصلاحيات ----
PERM_LABELS = {
    "section.constructions": "الإنشاءات",
    "section.projects": "المشاريع",
    "section.ops": "العمليات والصيانة",
    "section.contractors": "المقاولين",
    "section.quality": "التنسيقات والجودة",
    "section.safety": "السلامة",
    "section.warehouses": "المستودعات",
    "section.external": "المشتريات والعهد",
    "section.financial": "المتابعات المالية",
    "section.maintenance": "الورشة",
    "section.hr": "الموارد البشرية",
    "section.contracts": "إدارة العقود",
    "section.reinforcement": "التعزيز - اسكيمات",
    "tickets.read": "عرض الأعطال",
    # يتحكم بتعديل بيانات العطل وبنود العقد والكميات والصور/التمتير المرتبطة بالعطل
    "tickets.write": "إضافة/تعديل الأعطال والبنود",
    "tickets.delete": "حذف الأعطال",
    "modules.read": "عرض سجلات الأقسام",
    "modules.write": "إضافة/تعديل السجلات",
    "modules.delete": "حذف السجلات",
    "cashflow.read": "عرض التدفق النقدي",
    "cashflow.write": "تعديل التدفق النقدي",
    "teams.write": "إدارة فرق المهام",
    "users.manage": "إدارة المستخدمين",
    "ops.tabs.manage": "إدارة تبويبات العمليات",  # توافق قديم — يُفضّل app.tabs.manage
    "app.tabs.manage": "إدارة التبويبات",
    "audit.read": "سجل النشاط",
    "export": "تصدير Excel",
    "search": "البحث العام",
}

ALL_PERMS = set(PERM_LABELS)

# وحدات مرتبطة بالعطل: أي إضافة/تعديل/حذف لها يتطلب tickets.write (بالإضافة إلى modules.*)
TICKET_LINKED_WRITE_MODULES = frozenset({"quantities", "photos", "metering"})

# كل أقسام العرض
SECTION_PERMS = {
    "constructions": "section.constructions",
    "projects": "section.projects",
    "ops": "section.ops",
    "contractors": "section.contractors",
    "quality": "section.quality",
    "safety": "section.safety",
    "warehouses": "section.warehouses",
    "external": "section.external",
    "financial": "section.financial",
    "maintenance": "section.maintenance",
    "hr": "section.hr",
    "contracts": "section.contracts",
    "reinforcement": "section.reinforcement",
}

_READ_ALL_SECTIONS = set(SECTION_PERMS.values())

_ROLE_PERMS: dict[str, set[str]] = {
    "admin": set(ALL_PERMS),
    "مشرف": (
        _READ_ALL_SECTIONS
        | {
            "tickets.read",
            "tickets.write",
            "tickets.delete",
            "modules.read",
            "modules.write",
            "modules.delete",
            "cashflow.read",
            "cashflow.write",
            "teams.write",
            "audit.read",
            "export",
            "search",
            # بدون users.manage / app.tabs.manage / ops.tabs.manage — خاص بمدير النظام (المضيف)
        }
    ),
    "مدخل بيانات": {
        "section.ops",
        "section.constructions",
        "section.projects",
        "section.contractors",
        "section.quality",
        "section.safety",
        "section.warehouses",
        "section.external",
        "section.financial",
        "section.maintenance",
        "section.hr",
        "section.reinforcement",
        "tickets.read",
        "tickets.write",
        "modules.read",
        "modules.write",
        "cashflow.read",
        "export",
        "search",
    },
    "مراقب": _READ_ALL_SECTIONS
    | {
        "tickets.read",
        "modules.read",
        "cashflow.read",
        "export",
        "search",
        "audit.read",
    },
}

# مرادفات أدوار إن وُجدت بصيغ أخرى
_ROLE_ALIASES = {
    "administrator": "admin",
    "Admin": "admin",
    "ADMIN": "admin",
    "مدير": "admin",
    "مدير النظام": "admin",
    "supervisor": "مشرف",
    "dataentry": "مدخل بيانات",
    "viewer": "مراقب",
    "readonly": "مراقب",
}


def normalize_role(role: str | None) -> str:
    role = (role or "").strip()
    if not role:
        return "مراقب"
    if role in _ROLE_PERMS:
        return role
    return _ROLE_ALIASES.get(role, role if role in _ROLE_PERMS else "مراقب")


def perms_for_role(role: str | None) -> set[str]:
    return set(_ROLE_PERMS.get(normalize_role(role), set()))


def current_role() -> str:
    return normalize_role(session.get("role"))


def has_perm(perm: str, role: str | None = None) -> bool:
    if not perm:
        return True
    r = normalize_role(role if role is not None else session.get("role"))
    if r == "admin":
        return True
    return perm in _ROLE_PERMS.get(r, set())


def can(*perms: str, role: str | None = None) -> bool:
    """True إذا توفرت كل الصلاحيات المطلوبة."""
    return all(has_perm(p, role) for p in perms)


def role_matrix() -> list[dict]:
    """لعرض مصفوفة الصلاحيات في صفحة المستخدمين."""
    roles = ["admin", "مشرف", "مدخل بيانات", "مراقب"]
    rows = []
    for perm, label in PERM_LABELS.items():
        rows.append(
            {
                "perm": perm,
                "label": i18n_phrase(session.get("lang") or "ar", label),
                "roles": {r: has_perm(perm, r) for r in roles},
            }
        )
    return rows


def deny_ticket_mutate(message: str | None = None):
    """رفض تعديل عطل/بند بدون صلاحية tickets.write."""
    return deny_redirect(
        message
        or i18n_phrase(session.get("lang") or "ar", "ليس لديك صلاحية لتعديل العطل أو بنوده. يلزم: إضافة/تعديل الأعطال والبنود.")
    )


def deny_redirect(message: str | None = None):
    flash(message or i18n_phrase(session.get("lang") or "ar", "ليس لديك صلاحية للوصول إلى هذه الصفحة."), "danger")
    # وجّه لأول قسم مسموح
    for section, perm in (
        ("ops", "section.ops"),
        ("constructions", "section.constructions"),
        ("projects", "section.projects"),
        ("contractors", "section.contractors"),
        ("quality", "section.quality"),
        ("safety", "section.safety"),
        ("warehouses", "section.warehouses"),
        ("external", "section.external"),
        ("financial", "section.financial"),
        ("maintenance", "section.maintenance"),
        ("hr", "section.hr"),
        ("contracts", "section.contracts"),
        ("reinforcement", "section.reinforcement"),
    ):
        if has_perm(perm):
            endpoint = {
                "ops": "ops_home",
                "constructions": "constructions_home",
                "projects": "projects_home",
                "contractors": "contractors_home",
                "quality": "quality_home",
                "safety": "safety_home",
                "warehouses": "warehouses_home",
                "external": "external_purchases_home",
                "financial": "financial_home",
                "maintenance": "maintenance_home",
                "hr": "hr_home",
                "contracts": "contracts_admin_home",
                "reinforcement": "reinforcement_home",
            }[section]
            return redirect(url_for(endpoint))
    return redirect(url_for("logout"))


def require_perm(*perms: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("login", next=request.path))
            if not can(*perms):
                return deny_redirect()
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def _module_section(name: str | None) -> str | None:
    if not name:
        return None
    mod = MODULES.get(name) or {}
    return mod.get("section")


def required_perm_for_request() -> str | None:
    """
    يحدد صلاحية واحدة مطلوبة للطلب الحالي.
    None = مسموح لأي مستخدم مسجّل.
    """
    ep = request.endpoint or ""
    method = (request.method or "GET").upper()
    args = request.view_args or {}

    # الوحدات العامة
    if ep in {"module_list", "module_new", "module_edit", "module_delete"}:
        mod_name = args.get("name")
        section = _module_section(mod_name)
        section_perm = SECTION_PERMS.get(section or "")
        # الرخص المصدرة: يُسمح بعرضها من القسم المرتبط (عمليات/مشاريع/إنشاءات)
        if mod_name == "issued_licenses" and ep == "module_list":
            linked = (request.args.get("linked_section") or "").strip().lower()
            linked_perm = {
                "ops": "section.ops",
                "projects": "section.projects",
                "constructions": "section.constructions",
            }.get(linked)
            if linked_perm and has_perm(linked_perm) and has_perm("modules.read"):
                return None
        if section_perm and not has_perm(section_perm):
            return section_perm
        if ep == "module_list":
            return "modules.read" if not has_perm("modules.read") else None
        # كميات/صور/تمتير مرتبطة بالعطل: لا تُعدَّل دون tickets.write
        if mod_name in TICKET_LINKED_WRITE_MODULES and not has_perm("tickets.write"):
            return "tickets.write"
        if ep in {"module_new", "module_edit"}:
            return None if has_perm("modules.write") else "modules.write"
        if ep == "module_delete":
            return None if has_perm("modules.delete") else "modules.delete"

    if ep == "new_coordination_transfer":
        if not has_perm("section.quality"):
            return "section.quality"
        return None if has_perm("modules.write") else "modules.write"

    if ep == "media_serve":
        # صور العمليات: يحتاج قراءة وحدات أو قراءة أعطال + قسم العمليات
        if has_perm("modules.read") or (has_perm("tickets.read") and has_perm("section.ops")):
            return None
        return "modules.read"

    # الأعطال
    ticket_map = {
        "tickets_list": "tickets.read",
        "ticket_view": "tickets.read",
        "ticket_print": "tickets.read",
        "ticket_new": "tickets.write",
        "ticket_edit": "tickets.write",
        "ticket_delete": "tickets.delete",
        "tickets_template": "tickets.write",
        "tickets_import": "tickets.write",
    }
    if ep in ticket_map:
        need = ticket_map[ep]
        # القسم أيضاً
        if not has_perm("section.ops") and need.startswith("tickets."):
            return "section.ops"
        return None if has_perm(need) else need

    # أقسام رئيسية
    section_endpoints = {
        "dashboard": "section.ops",
        "ops_home": "section.ops",
        "constructions_home": "section.constructions",
        "projects_home": "section.projects",
        "contractors_home": "section.contractors",
        "new_coords_home": "section.quality",
        "quality_home": "section.quality",
        "quality_workflow_go": "section.quality",
        "safety_home": "section.safety",
        "warehouses_home": "section.warehouses",
        "warehouse_balances": "section.warehouses",
        "warehouse_movements_summary": "section.warehouses",
        "external_purchases_home": "section.external",
        "purchase_line_add": "section.external",
        "purchase_line_delete": "section.external",
        "purchase_receive_warehouse": "section.external",
        "contractor_supply_line_add": "section.contractors",
        "contractor_supply_line_delete": "section.contractors",
        "contractor_supply_receive_warehouse": "section.contractors",
        "financial_home": "section.financial",
        "maintenance_home": "section.maintenance",
        "hr_home": "section.hr",
        "contracts_admin_home": "section.contracts",
        "reinforcement_home": "section.reinforcement",
    }
    if ep in section_endpoints:
        need = section_endpoints[ep]
        return None if has_perm(need) else need

    # المتابعات المالية — التدفق النقدي مخفي من الواجهة ويُعاد توجيهه
    if ep == "cashflow":
        if not has_perm("section.financial"):
            return "section.financial"
        return None

    if ep == "teams_page":
        if not has_perm("section.ops"):
            return "section.ops"
        if method == "POST":
            return None if has_perm("teams.write") else "teams.write"
        return None if has_perm("section.ops") else "section.ops"

    if ep in {
        "warehouse_items_template",
        "warehouse_items_template_legacy",
        "warehouse_tx_template",
        "warehouse_items_import",
        "warehouse_items_import_legacy",
        "warehouse_tx_import",
        "warehouse_balances_clear",
    }:
        if not has_perm("section.warehouses"):
            return "section.warehouses"
        if ep.endswith(("_import", "_clear", "_import_legacy")) and not has_perm("modules.write"):
            return "modules.write"
        return None

    if ep in {"contract_boq_template", "contract_boq_import", "contract_boq_activate"}:
        if not has_perm("section.contracts"):
            return "section.contracts"
        if ep in {"contract_boq_import", "contract_boq_activate"} and not has_perm("modules.write"):
            return "modules.write"
        return None

    if ep in {"ticket_boq_add", "ticket_boq_delete"}:
        if not has_perm("section.ops"):
            return "section.ops"
        return None if has_perm("tickets.write") else "tickets.write"

    if ep in {"users_home", "users_list"}:
        return None if has_perm("users.manage") else "users.manage"

    if ep in {"app_custom_tabs_manage", "ops_custom_tabs_manage"}:
        # الإدارة من داخل إدارة العقود — للمضيف فقط
        if not has_perm("section.contracts"):
            return "section.contracts"
        if has_perm("app.tabs.manage") or has_perm("ops.tabs.manage"):
            return None
        return "app.tabs.manage"

    if ep == "ops_custom_tab_view":
        if not has_perm("section.ops"):
            return "section.ops"
        return None

    if ep == "app_custom_tab_view":
        # يُتحقق من صلاحية القسم داخل المسار نفسه
        return None

    if ep in {"audit_log_home", "audit_log_page"}:
        return None if has_perm("audit.read") else "audit.read"

    if ep == "export_tickets_excel":
        return None if has_perm("export") else "export"

    if ep == "global_search":
        return None if has_perm("search") else "search"

    if ep == "api_jump_destinations":
        return None  # يُفلتر المحتوى حسب الصلاحيات

    if ep == "api_boq_item":
        return None  # بحث قراءة بعد تسجيل الدخول

    return None


def filter_jump_items(items: list[dict]) -> list[dict]:
    """تصفية وجهات القفز السريع حسب الصلاحيات."""
    out = []
    for item in items:
        path = item.get("path") or item.get("href") or item.get("url") or ""
        perm = _perm_for_path(path)
        if perm is None or has_perm(perm):
            # عطل جديد يحتاج كتابة
            if path.rstrip("/").endswith("/tickets/new") and not has_perm("tickets.write"):
                continue
            out.append(item)
    return out


def _perm_for_path(path: str) -> str | None:
    if not path:
        return None
    if path.startswith("/module/"):
        name = path[len("/module/") :].split("/")[0].split("?")[0]
        section = _module_section(name)
        return SECTION_PERMS.get(section or "", "modules.read")
    rules = (
        ("/users", "users.manage"),
        ("/admin/audit", "audit.read"),
        ("/constructions", "section.constructions"),
        ("/new-coordinations", "section.quality"),
        ("/projects", "section.projects"),
        ("/contractors", "section.contractors"),
        ("/quality", "section.quality"),
        ("/safety", "section.safety"),
        ("/warehouses", "section.warehouses"),
        ("/external-purchases", "section.external"),
        ("/financial", "section.financial"),
        ("/maintenance", "section.maintenance"),
        ("/hr", "section.hr"),
        ("/contracts-admin/tabs", "app.tabs.manage"),
        ("/contracts-admin", "section.contracts"),
        ("/reinforcement", "section.reinforcement"),
        ("/tickets", "tickets.read"),
        ("/cashflow", "section.financial"),
        ("/teams", "section.ops"),
        ("/export", "export"),
        ("/ops/tabs/manage", "app.tabs.manage"),
        ("/ops", "section.ops"),
        ("/search", "search"),
    )
    for prefix, perm in rules:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?"):
            return perm
    return None


def nav_sections_for_role(role: str | None = None) -> list[str]:
    """مفاتيح الأقسام المسموحة للتبويبات."""
    allowed = []
    for key, perm in SECTION_PERMS.items():
        if has_perm(perm, role):
            allowed.append(key)
    return allowed
