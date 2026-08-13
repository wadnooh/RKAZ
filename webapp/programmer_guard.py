"""حماية مستوى المبرمج: الجهاز الرئيسي + تحقق صارم عبر بريد المبرمج.

عمليات الإدارة الهيكلية فقط (مستخدمون، تبويبات، بنود عقد، …).
إدخال الأعطال والبيانات اليومية للموظفين لا يتأثر.

بريد الموافقة المعتمد فقط:
  wadnooh@gmail.com
  wadnooh@wadnooh.com
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import redirect, request, session, url_for

from webapp import db
from webapp import mailer
from webapp import permissions
from webapp.i18n import _ as i18n_phrase

COOKIE_NAME = "rekaz_prog_dev"
COOKIE_MAX_AGE = 60 * 60 * 24 * 400  # ~400 يوماً
ELEVATION_MINUTES = 30
APPROVE_TTL_MINUTES = 10
APPROVE_CODE_LEN = 8
OTP_SEND_COOLDOWN_SEC = 60

# بريد المبرمج فقط — لا يُقبل غيره للموافقة
DEFAULT_PROGRAMMER_EMAILS = (
    "wadnooh@gmail.com",
    "wadnooh@wadnooh.com",
)

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

def _lang() -> str:
    return session.get("lang") or "ar"


def _t(text: str, **kwargs) -> str:
    return i18n_phrase(_lang(), text, **kwargs)


def bootstrap_code() -> str:
    return (os.environ.get("PROGRAMMER_BOOTSTRAP_CODE") or "").strip()


def change_pin() -> str:
    return (os.environ.get("PROGRAMMER_CHANGE_PIN") or "").strip()


def programmer_emails() -> list[str]:
    raw = (os.environ.get("PROGRAMMER_EMAILS") or "").strip()
    if raw:
        emails = [e.strip().lower() for e in raw.replace(";", ",").split(",") if e.strip()]
    else:
        emails = list(DEFAULT_PROGRAMMER_EMAILS)
    # لا تسمح بتجاوز القائمة الافتراضية بأخرى فقط — ادمج واحتفظ بالمعتمدة
    allowed = {e.lower() for e in DEFAULT_PROGRAMMER_EMAILS}
    out = []
    for e in emails:
        el = e.lower()
        if el in allowed and el not in out:
            out.append(el)
    return out or list(DEFAULT_PROGRAMMER_EMAILS)


def masked_programmer_emails() -> list[str]:
    """عرض جزئي للواجهة بدون تسريب كامل إن لزم — هنا العناوين معروفة للمالك."""
    return programmer_emails()


def secrets_configured() -> bool:
    return bool(bootstrap_code() and change_pin())


def smtp_ready() -> bool:
    return mailer.smtp_configured()


def is_programmer(role: str | None = None) -> bool:
    """مدير النظام أو الحساب المخفي wadnooh فقط."""
    if db.is_hidden_username(session.get("username")):
        return True
    return permissions.normalize_role(role if role is not None else session.get("role")) == "admin"


def can_access_programmer_device_ui() -> bool:
    """إظهار تبويب/روابط جهاز المبرمج — admin أو wadnooh فقط."""
    return bool(session.get("user_id")) and is_programmer()


def is_hidden_programmer() -> bool:
    """حساب المبرمج المخفي (wadnooh) — الهوية الرئيسية للتحكم."""
    return db.is_hidden_username(session.get("username"))


def _hash_secret(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def hash_device_token(token: str) -> str:
    secret = (os.environ.get("SECRET_KEY") or "rakaz-khurais-emergency-2026").encode("utf-8")
    return hmac.new(secret, (token or "").encode("utf-8"), hashlib.sha256).hexdigest()


def new_device_token() -> str:
    return secrets.token_urlsafe(32)


def new_approve_code() -> str:
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
    uid = session.get("user_id")
    # الحساب المخفي الرئيسي يُعامل كمالك الجهاز المعتمد حتى لو سُجّل الجهاز بحساب admin آخر
    if (
        uid
        and device.get("user_id")
        and int(device["user_id"]) != int(uid)
        and not is_hidden_programmer()
    ):
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


def create_approve_code_record(
    code: str | None = None, *, channel: str = "email"
) -> tuple[str, datetime]:
    code = code or new_approve_code()
    expires = datetime.utcnow() + timedelta(minutes=APPROVE_TTL_MINUTES)
    expires_sql = expires.strftime("%Y-%m-%d %H:%M:%S")
    ch = (channel or "email").strip().lower() or "email"
    db.create_programmer_approve_code(_hash_secret(code.upper()), expires_sql, channel=ch)
    return code, expires


def allowed_approve_channels() -> list[str]:
    """عند عمل SMTP: رمز البريد فقط. عند تعطّله: رمز طوارئ SSH فقط."""
    if smtp_ready():
        return ["email"]
    return ["ssh_emergency"]


def consume_approve_code(code: str) -> bool:
    code = (code or "").strip().upper()
    if not code:
        return False
    return db.consume_programmer_approve_code(
        _hash_secret(code), allowed_channels=allowed_approve_channels()
    )


def _otp_send_wait_seconds() -> int:
    last = session.get("programmer_otp_sent_at")
    if last is None:
        return 0
    try:
        left = OTP_SEND_COOLDOWN_SEC - (time.time() - float(last))
    except (TypeError, ValueError):
        return 0
    return max(0, int(left))


def send_email_otp(*, next_path: str = "") -> tuple[bool, str]:
    """يولّد رمزاً ويرسله لبريد المبرمج المعتمد فقط (قناة email)."""
    if not is_programmer():
        return False, _t("هذه الصفحة للمبرمج (مدير النظام) فقط")
    wait = _otp_send_wait_seconds()
    if wait > 0:
        return False, _t("انتظر {sec} ثانية قبل إعادة إرسال رمز التحقق.", sec=wait)
    if not smtp_ready():
        return False, _t(
            "البريد غير جاهز. لا يمكن التحقق من جهاز ثانوي حتى يعمل SMTP، أو استخدم طوارئ SSH فقط عند تعطّل البريد."
        )
    code, expires = create_approve_code_record(channel="email")
    emails = programmer_emails()
    base = (os.environ.get("APP_BASE_URL") or request.url_root or "").rstrip("/")
    verify_url = f"{base}{url_for('programmer_verify', next=next_path or '/')}"
    subject = "رمز تحقق مبرمج ركاز — لمرة واحدة"
    body = (
        "رمز التحقق لمرة واحدة لتعديل إداري من جهاز غير رئيسي:\n\n"
        f"  {code}\n\n"
        f"صالح حتى (UTC): {expires.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"أدخله في صفحة التحقق مع كلمة المرور ورمز التغيير:\n{verify_url}\n\n"
        "لا يُقبل أي رمز إلا الوارد في هذا البريد أثناء عمل خدمة البريد.\n"
        "إن لم تطلب هذا الرمز فتجاهل الرسالة.\n"
    )
    ok, err = mailer.send_email(to_addrs=emails, subject=subject, body=body)
    if not ok:
        return False, _t("تعذّر إرسال البريد: {err}", err=err)
    session["programmer_otp_sent_at"] = time.time()
    return True, _t("تم إرسال رمز التحقق إلى بريد المبرمج المعتمد.")


def verify_strict(*, password: str, pin: str, approve_code: str) -> tuple[bool, str]:
    """تحقق صارم من جهاز ثانوي: كلمة المرور + PIN + رمز البريد (إلزامي عند عمل SMTP)."""
    if not is_programmer():
        return False, _t("هذه الصفحة للمبرمج (مدير النظام) فقط")
    if not change_pin():
        return False, _t("PROGRAMMER_CHANGE_PIN غير مضبوط على السيرفر — راجع التوثيق")
    if not hmac.compare_digest((pin or "").strip(), change_pin()):
        return False, _t("رمز التغيير غير صحيح")
    conn = db.connect()
    try:
        user = conn.execute("SELECT * FROM users WHERE id=?", (session.get("user_id"),)).fetchone()
    finally:
        conn.close()
    if not user or (user["password"] or "") != (password or ""):
        return False, _t("كلمة المرور غير صحيحة")
    if not (approve_code or "").strip():
        if smtp_ready():
            return False, _t("يلزم رمز التحقق من بريد المبرمج")
        return False, _t("البريد معطّل — استخدم رمز طوارئ SSH ثم أعد المحاولة")
    if not consume_approve_code(approve_code):
        if smtp_ready():
            return False, _t("رمز التحقق من البريد غير صالح أو منتهٍ — أعد إرسال الرمز للبريد المعتمد")
        return False, _t("رمز الطوارئ غير صالح أو منتهٍ")
    return True, ""


def create_ssh_emergency_code() -> tuple[bool, str, datetime | None]:
    """طوارئ SSH فقط عندما SMTP غير جاهز. عند عمل البريد يُرفض التوليد."""
    if smtp_ready():
        return (
            False,
            "SMTP يعمل — مرفوض. التحقق من جهاز ثانوي عبر رمز البريد فقط (wadnooh@gmail.com / wadnooh@wadnooh.com).",
            None,
        )
    code, expires = create_approve_code_record(channel="ssh_emergency")
    return True, code, expires


def otp_send_wait_seconds() -> int:
    return _otp_send_wait_seconds()


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
    if not request_needs_programmer_gate():
        return None
    if not session.get("user_id"):
        return None
    if not is_programmer():
        return None
    if can_mutate_control_plane():
        return None
    if not main_device_registered():
        return redirect(url_for("programmer_device_setup", next=request.path))
    return redirect(url_for("programmer_verify", next=request.path))


def require_programmer_control(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if is_programmer() and (request.method or "").upper() == "POST" and not can_mutate_control_plane():
            if not main_device_registered():
                return redirect(url_for("programmer_device_setup", next=request.path))
            return redirect(url_for("programmer_verify", next=request.path))
        return fn(*args, **kwargs)

    return wrapper


def template_context() -> dict:
    show_device = can_access_programmer_device_ui()
    if not session.get("user_id") or not is_programmer():
        return {
            "programmer_is_admin": False,
            "programmer_show_device_tab": False,
            "programmer_main_device": False,
            "programmer_elevated": False,
            "programmer_can_mutate": False,
            "programmer_main_registered": main_device_registered(),
            "programmer_elevation_seconds": 0,
            "programmer_emails": [],
            "programmer_smtp_ready": smtp_ready(),
        }
    return {
        "programmer_is_admin": True,
        "programmer_show_device_tab": show_device,
        "programmer_main_device": is_main_device(),
        "programmer_elevated": is_elevated(),
        "programmer_can_mutate": can_mutate_control_plane(),
        "programmer_main_registered": main_device_registered(),
        "programmer_elevation_seconds": elevation_remaining_seconds(),
        "programmer_emails": masked_programmer_emails(),
        "programmer_smtp_ready": smtp_ready(),
    }
