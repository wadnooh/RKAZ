"""حماية مستوى المبرمج: الجهاز الرئيسي + تحقق صارم من جهاز آخر.

عمليات الإدارة الهيكلية فقط (مستخدمون، تبويبات، بنود عقد، …).
إدخال الأعطال والبيانات اليومية للموظفين لا يتأثر.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import flash, redirect, request, session, url_for

from webapp import db
from webapp import permissions
from webapp.i18n import _ as i18n_phrase

COOKIE_NAME = "rekaz_prog_dev"
COOKIE_MAX_AGE = 60 * 60 * 24 * 400  # ~400 يوماً
ELEVATION_MINUTES = 30
APPROVE_TTL_MINUTES = 10
APPROVE_CODE_LEN = 8

# POST يغيّر إعدادات النظام / الهيكل — وليس بيانات تشغيل يومية
PRIVILEGED_POST_ENDPOINTS = frozenset(
    {
        "users_list",
        "app_custom_tabs_manage",
        "ops_custom_tabs_manage",
        "contract_boq_import",
        "contract_boq_activate",
    }
)

MSG_MAIN_ONLY = "التعديل من الجهاز الرئيسي فقط"
MSG_NEED_VERIFY = "يلزم تحقق المبرمج"


def _lang() -> str:
    return session.get("lang") or "ar"


def _t(text: str, **kwargs) -> str:
    return i18n_phrase(_lang(), text, **kwargs)


def bootstrap_code() -> str:
    return (os.environ.get("PROGRAMMER_BOOTSTRAP_CODE") or "").strip()


def change_pin() -> str:
    return (os.environ.get("PROGRAMMER_CHANGE_PIN") or "").strip()


def secrets_configured() -> bool:
    return bool(bootstrap_code() and change_pin())


def is_programmer(role: str | None = None) -> bool:
    return permissions.normalize_role(role if role is not None else session.get("role")) == "admin"


def _hash_secret(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def hash_device_token(token: str) -> str:
    # ربط التجزئة بـ SECRET_KEY حتى لا تُنقل الكوكيز بين بيئات مختلفة بسهولة
    secret = (os.environ.get("SECRET_KEY") or "rakaz-khurais-emergency-2026").encode("utf-8")
    return hmac.new(secret, (token or "").encode("utf-8"), hashlib.sha256).hexdigest()


def new_device_token() -> str:
    return secrets.token_urlsafe(32)


def new_approve_code() -> str:
    # أحرف/أرقام واضحة للطباعة يدوياً
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(APPROVE_CODE_LEN))


def cookie_token() -> str | None:
    raw = (request.cookies.get(COOKIE_NAME) or "").strip()
    return raw or None


def find_trusted_device(token: str | None = None, *, main_only: bool = False) -> dict | None:
    token = token if token is not None else cookie_token()
    if not token:
        return None
    return db.get_programmer_device_by_hash(hash_device_token(token), main_only=main_only)


def is_main_device() -> bool:
    if not is_programmer():
        return False
    device = find_trusted_device(main_only=True)
    if not device:
        return False
    # الجهاز الرئيسي مربوط بحساب المبرمج الذي سجّله
    uid = session.get("user_id")
    if uid and device.get("user_id") and int(device["user_id"]) != int(uid):
        return False
    db.touch_programmer_device(device["id"])
    return True


def elevation_remaining_seconds() -> int:
    until = session.get("programmer_elevated_until")
    if until is None:
        return 0
    try:
        left = float(until) - time.time()
    except (TypeError, ValueError):
        return 0
    return max(0, int(left))


def is_elevated() -> bool:
    return elevation_remaining_seconds() > 0


def can_mutate_control_plane() -> bool:
    """هل يُسمح للمبرمج الحالي بتنفيذ تعديل هيكلي؟"""
    if not session.get("user_id") or not is_programmer():
        return False
    return is_main_device() or is_elevated()


def main_device_registered() -> bool:
    return db.count_programmer_main_devices() > 0


def grant_elevation(minutes: int | None = None) -> None:
    mins = minutes if minutes is not None else ELEVATION_MINUTES
    session["programmer_elevated_until"] = time.time() + (mins * 60)


def clear_elevation() -> None:
    session.pop("programmer_elevated_until", None)


def attach_device_cookie(resp, token: str):
    secure = bool(request.is_secure or os.environ.get("SESSION_COOKIE_SECURE", "").strip() in {"1", "true", "yes", "on"})
    resp.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=secure,
        path="/",
    )
    return resp


def clear_device_cookie(resp):
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


def register_main_device(*, label: str = "", user_agent: str = "", ip: str = "") -> str:
    """يسجّل الجهاز الحالي كجهاز رئيسي ويعيد التوكن الصريح للكوكي."""
    token = new_device_token()
    db.upsert_programmer_main_device(
        user_id=int(session["user_id"]),
        token_hash=hash_device_token(token),
        label=(label or "الجهاز الرئيسي").strip() or "الجهاز الرئيسي",
        user_agent=(user_agent or "")[:400],
        ip=(ip or "")[:80],
    )
    clear_elevation()
    return token


def create_approve_code_record(code: str | None = None) -> tuple[str, datetime]:
    code = code or new_approve_code()
    expires = datetime.utcnow() + timedelta(minutes=APPROVE_TTL_MINUTES)
    # صيغة متوافقة مع مقارنة CURRENT_TIMESTAMP في SQLite
    expires_sql = expires.strftime("%Y-%m-%d %H:%M:%S")
    db.create_programmer_approve_code(_hash_secret(code.upper()), expires_sql)
    return code, expires


def consume_approve_code(code: str) -> bool:
    code = (code or "").strip().upper()
    if not code:
        return False
    return db.consume_programmer_approve_code(_hash_secret(code))


def verify_strict(*, password: str, pin: str, approve_code: str) -> tuple[bool, str]:
    """تحقق صارم من جهاز ثانوي. يعيد (ok, رسالة_خطأ)."""
    if not is_programmer():
        return False, _t("هذه الصفحة للمبرمج (مدير النظام) فقط")
    if not change_pin():
        return False, _t("PROGRAMMER_CHANGE_PIN غير مضبوط على السيرفر — راجع التوثيق")
    if not hmac.compare_digest((pin or "").strip(), change_pin()):
        return False, _t("رمز التغيير غير صحيح")
    # كلمة مرور حساب المبرمج الحالي
    conn = db.connect()
    try:
        user = conn.execute("SELECT * FROM users WHERE id=?", (session.get("user_id"),)).fetchone()
    finally:
        conn.close()
    if not user or (user["password"] or "") != (password or ""):
        return False, _t("كلمة المرور غير صحيحة")
    if not consume_approve_code(approve_code):
        return False, _t("رمز الموافقة من السيرفر غير صالح أو منتهٍ")
    return True, ""


def verify_bootstrap(code: str) -> tuple[bool, str]:
    expected = bootstrap_code()
    if not expected:
        return False, _t("PROGRAMMER_BOOTSTRAP_CODE غير مضبوط على السيرفر — راجع التوثيق")
    if not hmac.compare_digest((code or "").strip(), expected):
        return False, _t("رمز التهيئة غير صحيح")
    return True, ""


def request_needs_programmer_gate() -> bool:
    ep = request.endpoint or ""
    if ep not in PRIVILEGED_POST_ENDPOINTS:
        return False
    if (request.method or "GET").upper() != "POST":
        return False
    return True


def gate_control_plane_mutation():
    """يُستدعى من before_request. يعيد Response أو None."""
    if not request_needs_programmer_gate():
        return None
    if not session.get("user_id"):
        return None
    # غير المبرمج: نظام الصلاحيات يرفض users.manage أصلاً
    if not is_programmer():
        return None
    if can_mutate_control_plane():
        return None
    if not main_device_registered():
        flash(_t("يجب تسجيل الجهاز الرئيسي أولاً قبل أي تعديل إداري."), "danger")
        return redirect(url_for("programmer_device_setup", next=request.path))
    flash(_t(MSG_NEED_VERIFY) + " — " + _t(MSG_MAIN_ONLY), "danger")
    return redirect(url_for("programmer_verify", next=request.path))


def require_programmer_control(fn):
    """Decorator اختياري لمسارات POST الإدارية."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if is_programmer() and (request.method or "").upper() == "POST" and not can_mutate_control_plane():
            if not main_device_registered():
                flash(_t("يجب تسجيل الجهاز الرئيسي أولاً قبل أي تعديل إداري."), "danger")
                return redirect(url_for("programmer_device_setup", next=request.path))
            flash(_t(MSG_NEED_VERIFY) + " — " + _t(MSG_MAIN_ONLY), "danger")
            return redirect(url_for("programmer_verify", next=request.path))
        return fn(*args, **kwargs)

    return wrapper


def template_context() -> dict:
    if not session.get("user_id") or not is_programmer():
        return {
            "programmer_is_admin": False,
            "programmer_main_device": False,
            "programmer_elevated": False,
            "programmer_can_mutate": False,
            "programmer_main_registered": main_device_registered(),
            "programmer_elevation_seconds": 0,
        }
    return {
        "programmer_is_admin": True,
        "programmer_main_device": is_main_device(),
        "programmer_elevated": is_elevated(),
        "programmer_can_mutate": can_mutate_control_plane(),
        "programmer_main_registered": main_device_registered(),
        "programmer_elevation_seconds": elevation_remaining_seconds(),
    }
