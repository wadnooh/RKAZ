"""نظام صلاحيات المستخدمين — أدوار ومصفوفة صلاحيات لكل التطبيق."""

from __future__ import annotations

from functools import wraps

from flask import flash, redirect, request, session, url_for

from webapp.modules_config import MODULES

# ---- أكواد الصلاحيات ----
PERM_LABELS = {
    "section.ops": "العمليات والصيانة",
    "section.constructions": "الإنشاءات",
    "section.quality": "التنسيقات والجودة",
    "section.safety": "السلامة",
    "section.warehouses": "المستودعات",
    "section.external": "المشتريات والعهد",
    "section.maintenance": "الورشة",
    "section.contracts": "إدارة العقود",
    "section.review": "المتابعة والمراجعة",
    "tickets.read": "عرض البلاغات",
    "tickets.write": "إضافة/تعديل البلاغات",
    "tickets.delete": "حذف البلاغات",
    "modules.read": "عرض سجلات الأقسام",
    "modules.write": "إضافة/تعديل السجلات",
    "modules.delete": "حذف السجلات",
    "cashflow.read": "عرض التدفق النقدي",
    "cashflow.write": "تعديل التدفق النقدي",
    "teams.write": "إدارة فرق المهام",
    "lists.write": "إدارة القوائم",
    "settings.write": "إعدادات المكتب",
    "users.manage": "إدارة المستخدمين",
    "audit.read": "سجل النشاط",
    "export": "تصدير Excel",
    "search": "البحث العام",
    "sop.read": "إجراءات العمل",
}

ALL_PERMS = set(PERM_LABELS)

# كل أقسام العرض
SECTION_PERMS = {
    "ops": "section.ops",
    "constructions": "section.constructions",
    "quality": "section.quality",
    "safety": "section.safety",
    "warehouses": "section.warehouses",
    "external": "section.external",
    "maintenance": "section.maintenance",
    "contracts": "section.contracts",
    "review": "section.review",
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
            "lists.write",
            "settings.write",
            "audit.read",
            "export",
            "search",
            "sop.read",
            # بدون users.manage — خاص بمدير النظام
        }
    ),
    "مدخل بيانات": {
        "section.ops",
        "section.constructions",
        "section.quality",
        "section.safety",
        "section.warehouses",
        "section.external",
        "section.maintenance",
        "section.review",
        "tickets.read",
        "tickets.write",
        "modules.read",
        "modules.write",
        "cashflow.read",
        "export",
        "search",
        "sop.read",
    },
    "مراقب": _READ_ALL_SECTIONS
    | {
        "tickets.read",
        "modules.read",
        "cashflow.read",
        "export",
        "search",
        "sop.read",
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
                "label": label,
                "roles": {r: has_perm(perm, r) for r in roles},
            }
        )
    return rows


def deny_redirect(message: str | None = None):
    flash(message or "ليس لديك صلاحية للوصول إلى هذه الصفحة.", "danger")
    # وجّه لأول قسم مسموح
    for section, perm in (
        ("ops", "section.ops"),
        ("review", "section.review"),
        ("constructions", "section.constructions"),
        ("quality", "section.quality"),
        ("safety", "section.safety"),
        ("warehouses", "section.warehouses"),
        ("external", "section.external"),
        ("maintenance", "section.maintenance"),
        ("contracts", "section.contracts"),
    ):
        if has_perm(perm):
            endpoint = {
                "ops": "ops_home",
                "review": "review_home",
                "constructions": "constructions_home",
                "quality": "quality_home",
                "safety": "safety_home",
                "warehouses": "warehouses_home",
                "external": "external_purchases_home",
                "maintenance": "maintenance_home",
                "contracts": "contracts_admin_home",
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
        section = _module_section(args.get("name"))
        section_perm = SECTION_PERMS.get(section or "")
        if section_perm and not has_perm(section_perm):
            return section_perm
        if ep == "module_list":
            return "modules.read" if not has_perm("modules.read") else None
        if ep in {"module_new", "module_edit"}:
            return None if has_perm("modules.write") else "modules.write"
        if ep == "module_delete":
            return None if has_perm("modules.delete") else "modules.delete"

    # البلاغات
    ticket_map = {
        "tickets_list": "tickets.read",
        "ticket_view": "tickets.read",
        "ticket_print": "tickets.read",
        "ticket_new": "tickets.write",
        "ticket_edit": "tickets.write",
        "ticket_delete": "tickets.delete",
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
        "quality_home": "section.quality",
        "safety_home": "section.safety",
        "warehouses_home": "section.warehouses",
        "warehouse_balances": "section.warehouses",
        "external_purchases_home": "section.external",
        "maintenance_home": "section.maintenance",
        "contracts_admin_home": "section.contracts",
        "review_home": "section.review",
        "ticket_review": "section.review",
        "review_followup_save": "section.review",
    }
    if ep in section_endpoints:
        need = section_endpoints[ep]
        # المتابعة / حفظ المراجعة تحتاج صلاحية كتابة
        if ep == "review_followup_save" and method == "POST":
            if not has_perm("section.review"):
                return "section.review"
            if not has_perm("tickets.write") and not has_perm("modules.write"):
                return "tickets.write"
            return None
        if ep == "ticket_review" and method == "POST":
            if not has_perm("section.review"):
                return "section.review"
            if not has_perm("tickets.write") and not has_perm("modules.write"):
                return "tickets.write"
            return None
        return None if has_perm(need) else need

    # أدوات العمليات
    if ep == "cashflow":
        if not has_perm("section.ops"):
            return "section.ops"
        if method == "POST":
            return None if has_perm("cashflow.write") else "cashflow.write"
        return None if has_perm("cashflow.read") else "cashflow.read"

    if ep == "teams_page":
        if not has_perm("section.ops"):
            return "section.ops"
        if method == "POST":
            return None if has_perm("teams.write") else "teams.write"
        return None if has_perm("section.ops") else "section.ops"

    if ep == "lists_page":
        if method == "POST":
            return None if has_perm("lists.write") else "lists.write"
        return None if has_perm("lists.write") or has_perm("section.ops") else "section.ops"

    if ep == "sop_page":
        return None if has_perm("sop.read") else "sop.read"

    if ep == "settings_page":
        return None if has_perm("settings.write") else "settings.write"

    if ep in {
        "warehouse_items_template",
        "warehouse_tx_template",
        "warehouse_items_import",
        "warehouse_tx_import",
    }:
        if not has_perm("section.warehouses"):
            return "section.warehouses"
        if ep.endswith("_import") and not has_perm("modules.write"):
            return "modules.write"
        return None

    if ep in {"users_home", "users_list"}:
        return None if has_perm("users.manage") else "users.manage"

    if ep in {"audit_log_home", "audit_log_page"}:
        return None if has_perm("audit.read") else "audit.read"

    if ep == "export_tickets_excel":
        return None if has_perm("export") else "export"

    if ep == "global_search":
        return None if has_perm("search") else "search"

    if ep == "api_jump_destinations":
        return None  # يُفلتر المحتوى حسب الصلاحيات

    # aliases قديمة
    if ep in {"contractors_home", "financial_home", "hr_home"}:
        return None if has_perm("section.ops") else "section.ops"

    return None


def filter_jump_items(items: list[dict]) -> list[dict]:
    """تصفية وجهات القفز السريع حسب الصلاحيات."""
    out = []
    for item in items:
        path = item.get("path") or item.get("href") or item.get("url") or ""
        perm = _perm_for_path(path)
        if perm is None or has_perm(perm):
            # بلاغ جديد يحتاج كتابة
            if path.rstrip("/").endswith("/tickets/new") and not has_perm("tickets.write"):
                continue
            if path.rstrip("/").endswith("/lists") and not (has_perm("lists.write") or has_perm("section.ops")):
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
        ("/settings", "settings.write"),
        ("/admin/audit", "audit.read"),
        ("/review", "section.review"),
        ("/constructions", "section.constructions"),
        ("/quality", "section.quality"),
        ("/safety", "section.safety"),
        ("/warehouses", "section.warehouses"),
        ("/external-purchases", "section.external"),
        ("/maintenance", "section.maintenance"),
        ("/contracts-admin", "section.contracts"),
        ("/tickets", "tickets.read"),
        ("/cashflow", "cashflow.read"),
        ("/teams", "section.ops"),
        ("/lists", "section.ops"),
        ("/sop", "sop.read"),
        ("/export", "export"),
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
