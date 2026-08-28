from __future__ import annotations

import hashlib
import io
import os
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    flash,
    g,
    redirect,
    request,
    send_file,
    session,
    url_for,
)

from webapp import db
from webapp import media as media_svc
from webapp import backup as backup_svc
from webapp import mailer
from webapp.i18n import _ as i18n_phrase, tv as i18n_tv, tr as i18n_tr, localize_module, localize_section_meta
from webapp.modules_config import MODULES, SECTION_META, modules_for_section
from webapp import permissions
from webapp import whatsapp

# Bump when layout/CSS must force clients past nginx/browser 7d static cache.
_LAYOUT_ASSET_TAG = "delete-auth-methods-1"
DELETE_CODE_TTL_SECONDS = 10 * 60

def lang():
    return session.get("lang") or "ar"


def t(text, **kwargs):
    return i18n_phrase(lang(), text, **kwargs)


def tv(value):
    return i18n_tv(lang(), value)


def tr(key, **kwargs):
    return i18n_tr(lang(), key, **kwargs)


def mod(module):
    return localize_module(module, lang())


def smeta(meta):
    return localize_section_meta(meta, lang())


def current_user_name():
    return session.get("full_name") or session.get("username") or t("مستخدم")


def after_data_change():
    """مزامنة صامتة بعد أي تعديل — بدون أزرار أو رسائل للمستخدم."""
    try:
        backup_svc.silent_backup_after_change()
    except Exception:
        pass


def _delete_scope() -> str:
    parts = [
        request.path or "",
        request.form.get("action") or "",
        request.form.get("id") or "",
        request.form.get("next") or "",
    ]
    return "|".join(str(p) for p in parts)


def _hash_delete_code(code: str) -> str:
    return hashlib.sha256((code or "").strip().encode("utf-8")).hexdigest()


def _static_delete_password() -> str:
    raw = (
        os.environ.get("REKAZ_DELETE_PASSWORD")
        or os.environ.get("DELETE_PASSWORD")
        or str((g.get("settings") or db.get_settings()).get("delete_static_password") or "")
    )
    return raw.strip()


def delete_confirm_methods() -> dict:
    return {
        "email": permissions.can("delete.email_code"),
        "static": permissions.can("delete.static_password"),
        "static_ready": bool(_static_delete_password()),
    }


def _admin_delete_code_recipients() -> list[dict]:
    conn = db.connect()
    users = conn.execute(
        """
        SELECT full_name, username, email, mobile, role
        FROM users
        WHERE active=1 AND (coalesce(email,'')<>'' OR coalesce(mobile,'')<>'')
        ORDER BY id
        """
    ).fetchall()
    conn.close()
    recipients = []
    for user in users:
        if permissions.normalize_role(user["role"]) != "admin":
            continue
        email = (user["email"] or "").strip()
        phone = (user["mobile"] or "").strip()
        if not email and not phone:
            continue
        recipients.append(
            {
                "email": email,
                "phone": phone,
                "name": (user["full_name"] or user["username"] or "admin").strip(),
            }
        )
    return recipients


def _send_delete_code(scope: str) -> bool:
    recipients = _admin_delete_code_recipients()
    cfg = whatsapp.get_whatsapp_config()
    admin_phones = [r["phone"] for r in recipients if r.get("phone")]
    if cfg.get("admin_phone") and cfg["admin_phone"] not in admin_phones:
        admin_phones.append(cfg["admin_phone"])
    email_addrs = [r["email"] for r in recipients if r.get("email")]

    if not email_addrs and not admin_phones:
        g.delete_confirm_message = t("لا يمكن الحذف: لا يوجد بريد إلكتروني أو رقم جوال مسجل لحساب admin نشط.")
        return False
    code = f"{secrets.randbelow(1_000_000):06d}"
    session["delete_email_confirm"] = {
        "scope": scope,
        "code_hash": _hash_delete_code(code),
        "expires_at": time.time() + DELETE_CODE_TTL_SECONDS,
    }
    subject = "كود تأكيد الحذف - ركاز"
    requester = current_user_name()
    body = "\n".join(
        [
            "مرحباً مدير النظام,",
            "",
            "تم طلب تنفيذ عملية حذف في نظام ركاز.",
            f"المستخدم الطالب للحذف: {requester}",
            f"كود تأكيد الحذف: {code}",
            "صلاحية الكود 10 دقائق، ولا يعمل إلا لهذه العملية فقط.",
            "",
            "إذا لم تطلب الحذف فتجاهل هذه الرسالة وراجع مدير النظام.",
        ]
    )
    wa_body = "\n".join(
        [
            "🔐 *كود تأكيد الحذف — نظام ركاز*",
            f"▫️ كود التأكيد: *{code}*",
            f"▫️ الطالب: {requester}",
            "▫️ الصلاحية: 10 دقائق لهذه العملية فقط.",
            "⚠️ إذا لم تطلب الحذف تجاهل هذه الرسالة.",
        ]
    )
    errors = []
    sent_any = False
    if mailer.smtp_configured() and email_addrs:
        ok, err = mailer.send_email(to_addrs=email_addrs, subject=subject, body=body)
        if ok:
            sent_any = True
        else:
            errors.append(t("البريد: {err}", err=err))
    elif not mailer.smtp_configured():
        errors.append(t("البريد غير مفعل"))

    if whatsapp.configured() and admin_phones:
        for p in admin_phones:
            ok, err = whatsapp.send_text(to_phone=p, body=wa_body)
            if ok:
                sent_any = True
            else:
                errors.append(t("واتساب: {err}", err=err))
    elif not whatsapp.configured():
        errors.append(t("واتساب غير مفعل"))

    if sent_any:
        g.delete_confirm_message = t("تم إرسال كود تأكيد الحذف (بالبريد / واتساب). أدخل الكود لإتمام الحذف.")
        g.delete_confirm_category = "ok"
        return False

    session.pop("delete_email_confirm", None)
    if errors:
        g.delete_confirm_message = t("تعذر إرسال كود تأكيد الحذف: {err}", err=" | ".join(errors))
    else:
        g.delete_confirm_message = t("تعذر إرسال كود تأكيد الحذف: {err}", err="لا توجد قناة إرسال مفعلة")
    return False


def delete_password_ok() -> bool:
    """يتحقق من طريقة تأكيد الحذف الممنوحة للمستخدم."""
    code = (request.form.get("delete_code") or request.form.get("delete_password") or "").strip()
    if not session.get("user_id"):
        return False
    methods = delete_confirm_methods()
    if not methods["email"] and not methods["static"]:
        g.delete_confirm_message = t("لا تملك صلاحية طريقة تأكيد الحذف. اطلب منح صلاحية كود البريد أو كلمة المرور الثابتة.")
        return False
    static_password = _static_delete_password()
    if code and methods["static"]:
        if static_password and secrets.compare_digest(code, static_password):
            return True
        if not methods["email"]:
            if not static_password:
                g.delete_confirm_message = t("كلمة مرور الحذف الثابتة غير مضبوطة على السيرفر.")
            else:
                g.delete_confirm_message = t("كلمة مرور الحذف غير صحيحة.")
            return False
    scope = _delete_scope()
    record = session.get("delete_email_confirm") or {}
    if code:
        expires_at = float(record.get("expires_at") or 0)
        if methods["email"] and (
            record.get("scope") == scope
            and expires_at >= time.time()
            and record.get("code_hash") == _hash_delete_code(code)
        ):
            session.pop("delete_email_confirm", None)
            return True
        g.delete_confirm_message = t("بيانات تأكيد الحذف غير صحيحة أو منتهية. تحقق من كلمة المرور أو اطلب كوداً جديداً.")
        return False
    mode = (request.form.get("delete_auth_mode") or "").strip().lower()
    if mode == "static" or (methods["static"] and not methods["email"]):
        if not static_password:
            g.delete_confirm_message = t("كلمة مرور الحذف الثابتة غير مضبوطة على السيرفر.")
        else:
            g.delete_confirm_message = t("أدخل كلمة مرور الحذف الثابتة لإتمام العملية.")
        return False
    if not methods["email"]:
        g.delete_confirm_message = t("لا تملك صلاحية إرسال كود الحذف بالبريد.")
        return False
    return _send_delete_code(scope)


def reject_bad_delete_password(fallback_url: str):
    flash(
        getattr(g, "delete_confirm_message", None) or t("أدخل كود تأكيد الحذف المرسل إلى بريد حسابات admin."),
        getattr(g, "delete_confirm_category", "danger"),
    )
    nxt = (request.form.get("next") or "").strip()
    return redirect(nxt or fallback_url)


def summary_card(title, value, subtitle="", *, money=False, href=None, active=False):
    """بطاقة ملخص بنفس أسلوب إجمالي الكميات في المستودعات."""
    return {
        "title": title,
        "value": value if value is not None else "—",
        "subtitle": subtitle or "",
        "money": bool(money),
        "href": href or None,
        "active": bool(active),
    }


def to_float_safe(val):
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def sum_money_field(rows, *keys):
    total = 0.0
    for r in rows or []:
        for key in keys:
            num = to_float_safe(r.get(key))
            if num is not None:
                total += num
                break
    return total


def missing_amount_flag(raw=None):
    """هل طلب المستخدم فلتر السجلات بدون مبلغ؟"""
    if raw is None:
        raw = request.args.get("missing_amount")
    return str(raw or "").strip().lower() in {"1", "yes", "true", "on"}


def row_missing_amount(row, *keys):
    """True إذا لم يُدخل أي مبلغ في الحقول المحددة."""
    if not keys:
        return False
    for key in keys:
        if to_float_safe(row.get(key)) is not None:
            return False
    return True


def count_missing_amount(rows, *keys):
    if not keys:
        return 0
    return sum(1 for r in rows or [] if row_missing_amount(r, *keys))


def filter_missing_amount_rows(rows, *keys):
    if not keys:
        return list(rows or [])
    return [r for r in (rows or []) if row_missing_amount(r, *keys)]


def request_query_args(*drop, **overrides):
    """يبني dict لمعاملات الرابط مع الحفاظ على الفلاتر الحالية."""
    args = {
        k: v
        for k, v in request.args.to_dict(flat=True).items()
        if v is not None and str(v).strip() != ""
    }
    for key in drop:
        args.pop(key, None)
    for key, val in overrides.items():
        if val is None or str(val).strip() == "":
            args.pop(key, None)
        else:
            args[key] = val
    return args


def url_with_filters(endpoint, *drop, **overrides):
    return url_for(endpoint, **request_query_args(*drop, **overrides))


def missing_amount_card(
    count,
    *,
    endpoint,
    active=False,
    endpoint_kwargs=None,
):
    """بطاقة قابلة للنقر لتصفية السجلات بدون مبلغ."""
    endpoint_kwargs = dict(endpoint_kwargs or {})
    if active:
        href = url_with_filters(endpoint, "missing_amount", **endpoint_kwargs)
        subtitle = t("فلتر نشط — اضغط لإلغاء التصفية")
    else:
        href = url_with_filters(
            endpoint, missing_amount="1", **endpoint_kwargs
        )
        subtitle = t("اضغط لعرض السجلات بدون مبلغ")
    return summary_card(
        t("بدون مبلغ"),
        count,
        subtitle,
        href=href,
        active=active,
    )


def xlsx_sheet_title(title, fallback="Export"):
    """عناوين أوراق Excel لا تقبل \\ / * ? : [ ] وبحد أقصى 31 حرفاً."""
    raw = (title or "").strip() or fallback
    for ch in '\\/*?:[]':
        raw = raw.replace(ch, "-")
    raw = " ".join(part.strip() for part in raw.split()).strip() or fallback
    return raw[:31]


def simple_xlsx_export(title, headers, rows, field_keys, download_name, filters=None, summary_lines=None):
    """تصدير Excel احترافي للصفوف المفلترة بترويسة وهوية كاملة."""
    from openpyxl import Workbook
    from webapp import excel_brand as brand

    wb = Workbook()
    ws = wb.active
    ws.title = xlsx_sheet_title(title)
    ncol = len(headers)

    # حساب إجمالي المبالغ إن وجدت حقول مبالغ
    money_keys = [k for k in field_keys if k in ("final_value", "items_value", "value", "amount", "total", "price", "unit_price", "boq_base_total")]
    total_amt = 0.0
    for r in rows or []:
        if isinstance(r, dict):
            for mk in money_keys:
                v = r.get(mk)
                if v not in (None, ""):
                    try:
                        total_amt += float(str(v).replace(",", "").strip() or 0)
                        break
                    except Exception:
                        pass

    if summary_lines is None:
        summary_lines = []
        if total_amt > 0:
            summary_lines.append(f"إجمالي المبلغ: {total_amt:,.2f} ر.س")
        summary_lines.append(f"عدد السجلات: {len(rows or [])}")

    header_row = brand.apply_brand_header(
        ws,
        title=title,
        ncol=ncol,
        meta_lines=filters,
        summary_lines=summary_lines,
    )
    brand.write_header_row(ws, headers, header_row)
    start = header_row + 1
    for offset, row in enumerate(rows or []):
        r = start + offset
        for col, key in enumerate(field_keys, start=1):
            val = row.get(key) if isinstance(row, dict) else None
            ws.cell(row=r, column=col, value="" if val is None else val)
    end = start + len(rows or []) - 1 if rows else header_row
    if rows:
        brand.style_data_rows(ws, start_row=start, end_row=end, ncol=ncol)
    data = brand.save_workbook_bytes(wb)
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=download_name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def latest_row(rows, *date_keys):
    """أحدث صف حسب أول تاريخ متاح ثم أعلى id."""
    best = None
    best_key = None
    for r in rows or []:
        date_val = ""
        for key in date_keys:
            date_val = (r.get(key) or "").strip()
            if date_val:
                break
        sort_key = (date_val or "", int(r.get("id") or 0))
        if best is None or sort_key > best_key:
            best = r
            best_key = sort_key
    return best


def filter_rows_by_date_range(rows, date_from, date_to, *date_keys):
    """تصفية الصفوف حسب تاريخ من/إلى باستخدام أول حقل تاريخ غير فارغ."""
    date_from = (date_from or "").strip()
    date_to = (date_to or "").strip()
    if not date_from and not date_to:
        return list(rows or [])
    out = []
    for r in rows or []:
        d = ""
        for key in date_keys:
            d = (r.get(key) or "").strip()
            if d:
                break
        if not d:
            continue
        if date_from and d < date_from:
            continue
        if date_to and d > date_to:
            continue
        out.append(r)
    return out


def module_money_keys(name, module=None):
    module = module or MODULES.get(name) or {}
    preferred = {
        "metering": ("approved_value",),
        "invoices": ("value",),
        "quantities": ("total", "unit_price"),
        "external_purchases": ("total",),
        "contractor_supplies": ("total",),
        "primary_team_orders": ("amount",),
        "reinforcement_works": ("value",),
        "construction_works": ("value",),
        "contractor_works": ("value",),
    }.get(name)
    if preferred:
        return preferred
    keys = []
    for key, _label, ftype in module.get("fields") or []:
        if ftype != "number":
            continue
        lk = key.lower()
        if any(tok in lk for tok in ("value", "amount", "price", "collected", "approved", "total")):
            keys.append(key)
    return tuple(keys)


def module_date_keys(name, module=None):
    module = module or MODULES.get(name) or {}
    preferred = {
        "metering": ("approve_date", "submit_date", "start_date"),
        "invoices": ("invoice_date", "support_date", "paid_date"),
        "primary_team_orders": ("order_date",),
        "reinforcement_works": ("work_date",),
        "construction_works": ("work_date",),
        "contractor_works": ("work_date",),
        "contractor_supplies": ("supply_date",),
        "external_purchases": ("purchase_date", "order_date"),
        "projects": ("start_date", "end_date"),
        "warehouse_tx": ("tx_date",),
        "new_coordinations": ("request_date",),
        "issued_licenses": ("issue_date", "expiry_date"),
        "quality_clearances": ("request_date", "clearance_date"),
    }.get(name)
    if preferred:
        return preferred
    keys = []
    for key, _label, ftype in module.get("fields") or []:
        if ftype == "date" or key.endswith("_date") or key in ("tx_date",):
            keys.append(key)
    return tuple(keys)


def module_detail_key(name, module=None):
    module = module or MODULES.get(name) or {}
    preferred = {
        "metering": "ticket_no",
        "invoices": "invoice_id",
        "primary_team_orders": "work_order",
        "reinforcement_works": "work_no",
        "construction_works": "work_no",
        "contractor_works": "work_no",
        "contractor_supplies": "supply_no",
        "projects": "project_code",
        "warehouse_tx": "voucher_no",
        "new_coordinations": "coord_no",
        "issued_licenses": "license_no",
        "quality_clearances": "ticket_no",
        "quantities": "ticket_no",
        "photos": "ticket_no",
        "external_purchases": "purchase_no",
    }.get(name)
    if preferred:
        return preferred
    for key, _label, _ftype in module.get("fields") or []:
        if key in module.get("list_cols") or []:
            return key
    fields = module.get("fields") or []
    return fields[0][0] if fields else "id"


def build_list_summary_cards(
    rows,
    *,
    count_label=None,
    money_keys=(),
    date_keys=(),
    detail_key=None,
    filter_hint=None,
    missing_amount_count=None,
    missing_amount_active=False,
    missing_amount_endpoint=None,
    missing_amount_endpoint_kwargs=None,
):
    """يبني بطاقات ملخص من الصفوف المعروضة (بعد الفلترة)."""
    rows = list(rows or [])
    hint = filter_hint or t("حسب الفلتر الحالي")
    count_label = count_label or t("عدد السجلات")
    cards = [
        summary_card(count_label, len(rows), hint),
    ]
    if money_keys:
        cards.append(
            summary_card(
                t("المبالغ المدخلة"),
                sum_money_field(rows, *money_keys),
                hint,
                money=True,
            )
        )
        if missing_amount_endpoint:
            miss_count = (
                missing_amount_count
                if missing_amount_count is not None
                else count_missing_amount(rows, *money_keys)
            )
            cards.append(
                missing_amount_card(
                    miss_count,
                    endpoint=missing_amount_endpoint,
                    active=missing_amount_active,
                    endpoint_kwargs=missing_amount_endpoint_kwargs,
                )
            )
    latest = latest_row(rows, *date_keys) if date_keys else (rows[0] if rows else None)
    if detail_key:
        detail_val = (latest or {}).get(detail_key) if latest else None
        cards.append(
            summary_card(
                t("آخر سجل"),
                detail_val or "—",
                t("تفاصيل أحدث حركة"),
            )
        )
    if date_keys:
        last_date = ""
        if latest:
            for key in date_keys:
                last_date = (latest.get(key) or "").strip()
                if last_date:
                    break
        cards.append(
            summary_card(
                t("تاريخ آخر حركة"),
                last_date or "—",
                t("أحدث تاريخ في القائمة"),
            )
        )
    return cards


def _ratio_value(value) -> float:
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    return max(0.0, min(100.0, n))


def work_ratio_cards(settings=None, *, base_amount=None):
    """بطاقات مبالغ ركاز والمقاول الرئيسي المحسوبة من نسب المبرمج."""
    settings = settings or getattr(g, "settings", None) or db.get_settings()
    href = url_for("programmer_work_ratios") if db.is_hidden_username(session.get("username")) else None
    try:
        base = float(base_amount or 0)
    except (TypeError, ValueError):
        base = 0.0
    rekaz_ratio = _ratio_value(settings.get("rekaz_ratio"))
    contractor_ratio = _ratio_value(settings.get("main_contractor_ratio"))
    return [
        summary_card(
            t("نسبة ركاز"),
            round(base * rekaz_ratio / 100, 2),
            t("{pct}% من المبالغ المدخلة", pct=f"{rekaz_ratio:.1f}"),
            money=True,
            href=href,
        ),
        summary_card(
            t("نسبة المقاول الرئيسي"),
            round(base * contractor_ratio / 100, 2),
            t("{pct}% من المبالغ المدخلة", pct=f"{contractor_ratio:.1f}"),
            money=True,
            href=href,
        ),
    ]


def response_minutes(dispatch, arrival):
    if not dispatch or not arrival:
        return None
    try:
        h1, m1 = map(int, dispatch.split(":")[:2])
        h2, m2 = map(int, arrival.split(":")[:2])
        diff = h2 * 60 + m2 - (h1 * 60 + m1)
        if diff < 0:
            diff += 24 * 60
        return diff
    except Exception:
        return None


def final_value(items_value, ratio=None):
    """القيمة النهائية = إجمالي البنود × (1 + نسبة الطوارئ) مرة واحدة."""
    if items_value is None or items_value == "":
        return None
    ratio = ratio if ratio is not None else float(g.settings.get("emergency_ratio") or 0)
    return round(float(items_value) * (1 + float(ratio or 0)), 2)


def attach_ticket_final_values(rows, conn=None):
    """يحسب القيمة النهائية مرة واحدة: أساس البنود ثم نسبة الطوارئ إن وُجدت."""
    if not rows:
        return rows
    settings_ratio = float((g.settings or {}).get("emergency_ratio") or 0)
    ids = [r.get("id") for r in rows if r.get("id") is not None]
    ratio_map = db.map_ticket_emergency_ratios(ids, settings_ratio, conn=conn)
    has_boq = set(ratio_map.keys())
    for r in rows:
        tid = r.get("id")
        base = r.get("items_value")
        if tid in has_boq:
            ratio = ratio_map.get(tid, 0.0)
        else:
            # أعطال بلا بنود: إن وُجدت قيمة يدوية تُطبَّق نسبة الإعدادات للتوافق
            ratio = settings_ratio if base not in (None, "") else 0.0
        r["emergency_ratio_applied"] = ratio
        r["final_value"] = final_value(base, ratio=ratio)
    return rows


def link_excavation_if_needed(ticket_no: str, reason: str = "", conn=None) -> dict | None:
    """يربط معاملة الحفر بالتنسيق/الإخلاء عند الحاجة."""
    tno = str(ticket_no or "").strip()
    if not tno:
        return None
    own = conn is None
    conn = conn or db.connect()
    try:
        if not db.ticket_has_excavation(tno, conn):
            return None
        return db.ensure_excavation_coordination(
            tno,
            reason=reason or "ربط تلقائي — معاملة بها حفر",
            conn=conn,
            create_clearance=True,
        )
    finally:
        if own:
            conn.commit()
            conn.close()


def flash_excavation_link(result: dict | None):
    if not result:
        return
    parts = []
    if result.get("created_coord"):
        parts.append(t("تم ربط المعاملة بالتنسيقات"))
    if result.get("created_clearance"):
        parts.append(t("تم فتح إجراء إخلاء الأسفلت"))
    if parts:
        flash(" — ".join(parts), "ok")


LICENSE_EVACUATION_WORKFLOW = "الإخلاء المبدئي"


def redirect_license_evacuations_journey(data: dict | None):
    """عند اختيار «الإخلاء المبدئي» من متابعة التصريح: افتح رحلة الإخلاءات."""
    data = data or {}
    if (data.get("workflow_status") or "").strip() != LICENSE_EVACUATION_WORKFLOW:
        return None
    ticket_no = (data.get("ticket_no") or "").strip()
    if not ticket_no:
        flash(t("لبدء الإخلاء المبدئي اربط الرخصة برقم عطل أولاً."), "danger")
        return None
    try:
        res = db.ensure_excavation_coordination(
            ticket_no,
            reason="الإخلاء المبدئي — من متابعة التصريح",
            create_clearance=True,
        )
        flash_excavation_link(res)
    except Exception as exc:
        flash(t("تعذر بدء الإخلاء المبدئي: {exc}", exc=exc), "danger")
        return None
    flash(t("تم فتح الإخلاء المبدئي من متابعة التصريح."), "ok")
    return redirect(
        url_for("quality_home", tab="evacuations", sub="initial", q=ticket_no)
    )


def linked_section_label(section: str | None) -> str:
    return {
        "ops": t("العمليات والصيانة"),
        "projects": t("المشاريع"),
        "constructions": t("الإنشاءات"),
    }.get(db.normalize_linked_section(section), section or "—")


def static_asset_version() -> str:
    """Cache-bust static CSS/JS so layout updates (e.g. ultra-wide) reach clients despite nginx expires."""
    try:
        css = Path(__file__).resolve().parent / "static" / "styles.css"
        return f"{_LAYOUT_ASSET_TAG}-{int(css.stat().st_mtime)}"
    except OSError:
        return _LAYOUT_ASSET_TAG
