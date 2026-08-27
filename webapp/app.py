from __future__ import annotations

import io
import json
import os
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import RequestEntityTooLarge

from webapp import db
from webapp.i18n import tr as i18n_tr, _ as i18n_phrase, tv as i18n_tv, localize_module, localize_section_meta, localize_jump
from webapp.modules_config import MODULES, SECTION_META, modules_for_section
from webapp import review_engine
from webapp import permissions
from webapp import warehouse_excel
from webapp import tickets_excel
from webapp.tickets_routes import tickets_bp
from webapp import backup as backup_svc
from webapp.api_routes import api_bp
from webapp import media as media_svc
from webapp import mailer
from webapp import programmer_guard as prog_guard
from webapp import reports as reports_svc
from webapp import helpers

_t = helpers.t
_lang = helpers.lang
_tv = helpers.tv
_mod = helpers.mod
_smeta = helpers.smeta
current_user_name = helpers.current_user_name
_after_data_change = helpers.after_data_change
_missing_amount_flag = helpers.missing_amount_flag
_count_missing_amount = helpers.count_missing_amount
_filter_missing_amount_rows = helpers.filter_missing_amount_rows
_filter_rows_by_date_range = helpers.filter_rows_by_date_range
_sum_money_field = helpers.sum_money_field
_module_money_keys = helpers.module_money_keys
_module_date_keys = helpers.module_date_keys
_module_detail_key = helpers.module_detail_key
_latest_row = helpers.latest_row
_simple_xlsx_export = helpers.simple_xlsx_export
_link_excavation_if_needed = helpers.link_excavation_if_needed
_flash_excavation_link = helpers.flash_excavation_link
_redirect_license_evacuations_journey = helpers.redirect_license_evacuations_journey
_linked_section_label = helpers.linked_section_label
_summary_card = helpers.summary_card
build_list_summary_cards = helpers.build_list_summary_cards
request_query_args = helpers.request_query_args
_url_with_filters = helpers.url_with_filters


def money(value):
    if value is None or value == "":
        return "—"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return value
    return f"{amount:,.2f} ر.س"

app = Flask(__name__, instance_relative_config=True)
# يجب أن يبقى SECRET_KEY ثابتاً بين إعادة التشغيل — تغييره يُبطل جلسات الجميع
# في بيئة الإنتاج، يجب تعيين هذا المتغير عبر متغيرات البيئة (environment variable)
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise ValueError("متغير البيئة SECRET_KEY غير معين. هذا المتغير مطلوب لتشغيل التطبيق بأمان.")
app.config["TEMPLATES_AUTO_RELOAD"] = True
IDLE_TIMEOUT_SECONDS = int(os.environ.get("RAKAZ_IDLE_TIMEOUT_SECONDS", "240") or "240")
app.permanent_session_lifetime = timedelta(seconds=IDLE_TIMEOUT_SECONDS)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
# HTTPS خلف nginx/Render: SESSION_COOKIE_SECURE=1 أو RENDER أو FORCE_HTTPS
_secure_cookie = os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower()
if _secure_cookie in {"1", "true", "yes", "on"}:
    app.config["SESSION_COOKIE_SECURE"] = True
elif _secure_cookie in {"0", "false", "no", "off"}:
    app.config["SESSION_COOKIE_SECURE"] = False
else:
    app.config["SESSION_COOKIE_SECURE"] = bool(
        os.environ.get("RENDER")
        or os.environ.get("FORCE_HTTPS", "").strip().lower() in {"1", "true", "yes", "on"}
    )
# صور الجوال قد تكون كبيرة، خصوصا عند رفع أكثر من مرحلة في نفس العملية.
app.config["MAX_CONTENT_LENGTH"] = 128 * 1024 * 1024
app.url_map.strict_slashes = False
# Render / reverse proxies
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

_DB_READY = False
PUBLIC_ENDPOINTS = {
    "login",
    "forgot_password",
    "set_lang",
    "static",
    "health",
    "backups_ui_removed",
    "api_backups_latest",
    "api_backups_auto_run",
    "api_backups_sync_status",
}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_EXEMPT_ENDPOINT_PREFIXES = ("api.",)


def _new_session_token() -> str:
    return secrets.token_urlsafe(32)


def _session_expired_by_idle(now: float | None = None) -> bool:
    last = session.get("last_activity_at")
    if not last:
        return False
    try:
        return (now or time.time()) - float(last) > IDLE_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        return True


def _clear_session_keep_lang(message: str | None = None):
    lang = session.get("lang")
    session.clear()
    if lang in ("ar", "en"):
        session["lang"] = lang
    if message:
        flash(message, "danger")
    return redirect(url_for("login"))


def _csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _csrf_ok() -> bool:
    if request.method not in UNSAFE_METHODS:
        return True
    endpoint = request.endpoint or ""
    if endpoint in PUBLIC_ENDPOINTS or any(endpoint.startswith(p) for p in CSRF_EXEMPT_ENDPOINT_PREFIXES):
        return True
    token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
    return bool(token and secrets.compare_digest(str(token), str(session.get("_csrf_token") or "")))


def _is_api_endpoint() -> bool:
    endpoint = request.endpoint or ""
    return endpoint.startswith("api.")


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.full_path))
        return fn(*args, **kwargs)

    return wrapper


def create_app():
    global _DB_READY
    db.init_db()
    _DB_READY = True
    # استعادة تلقائية من AWS S3 إن كانت القاعدة فارغة/بذرة فقط
    try:
        backup_svc.maybe_restore_from_s3_on_boot()
        db.ensure_schema()
    except Exception:
        pass
    # ابدأ الحفظ التلقائي في الخلفية (محلي + رفع إلى S3)
    try:
        backup_svc.start_auto_backup_scheduler(app)
        app.register_blueprint(tickets_bp)
        app.register_blueprint(api_bp)
        _register_legacy_ticket_endpoints()
    except Exception:
        pass
    return app


@app.errorhandler(RequestEntityTooLarge)
def request_entity_too_large(_exc):
    flash(_t("حجم الصور كبير جدا. ارفع صورة أو صورتين في كل مرة أو خفف دقة الكاميرا."), "danger")
    if request.path == "/field-upload":
        return render_template("field_upload.html", form=request.form), 413
    return redirect(request.referrer or url_for("ops_home"))


def _register_legacy_ticket_endpoints() -> None:
    """Keep old template endpoint names working after moving tickets to a blueprint."""
    aliases = [
        ("tickets_list", "/tickets/", "tickets.list_all", ["GET"]),
        ("tickets_template", "/tickets/template.xlsx", "tickets.template", ["GET"]),
        ("tickets_import", "/tickets/import", "tickets.import_from_excel", ["POST"]),
        ("ticket_new", "/tickets/new", "tickets.new", ["GET", "POST"]),
        ("ticket_view", "/tickets/<int:ticket_id>", "tickets.view", ["GET"]),
        ("ticket_print", "/tickets/<int:ticket_id>/print", "tickets.print_view", ["GET"]),
        ("ticket_edit", "/tickets/<int:ticket_id>/edit", "tickets.edit", ["GET", "POST"]),
        ("ticket_delete", "/tickets/<int:ticket_id>/delete", "tickets.delete", ["POST"]),
        ("ticket_boq_add", "/tickets/<int:ticket_id>/boq/add", "tickets.boq_add", ["POST"]),
        ("ticket_boq_delete", "/tickets/<int:ticket_id>/boq/<int:line_id>/delete", "tickets.boq_delete", ["POST"]),
        ("export_tickets_excel", "/tickets/export.xlsx", "tickets.export_excel", ["GET"]),
    ]
    for endpoint, rule, target, methods in aliases:
        if endpoint in app.view_functions or target not in app.view_functions:
            continue
        app.add_url_rule(rule, endpoint=endpoint, view_func=app.view_functions[target], methods=methods)


@app.before_request
def _load_context():
    global _DB_READY
    if not _DB_READY:
        db.init_db()
        _DB_READY = True
    else:
        # بعد استعادة حفظة قديمة قد تختفي جداول جديدة
        try:
            db.ensure_schema()
        except Exception:
            pass
    g.settings = db.get_settings()
    g.lists = db.get_lists()
    g.year = datetime.now().year
    g.lang = session.get("lang") or "ar"
    if _is_api_endpoint():
        return None
    # حماية الصفحات — يتطلب تسجيل دخول
    if request.endpoint and request.endpoint not in PUBLIC_ENDPOINTS and not session.get("user_id"):
        if request.endpoint != "static":
            return redirect(url_for("login", next=request.full_path))
        return None
    if session.get("user_id") and request.endpoint not in PUBLIC_ENDPOINTS:
        now = time.time()
        if _session_expired_by_idle(now):
            db.log_audit(current_user_name(), "خروج تلقائي", "نظام", session.get("user_id"), "خمول أكثر من 4 دقائق")
            return _clear_session_keep_lang(_t("تم تسجيل الخروج تلقائياً بسبب عدم النشاط لمدة 4 دقائق."))
        if not _csrf_ok():
            abort(400)
        active_token = (session.get("session_token") or "").strip()
        if not active_token:
            return _clear_session_keep_lang(_t("انتهت الجلسة الأمنية. سجل الدخول مرة أخرى."))
        conn = db.connect()
        try:
            user = conn.execute(
                "SELECT id, active, active_session_token FROM users WHERE id=?",
                (session.get("user_id"),),
            ).fetchone()
            stored_token = (user["active_session_token"] if user else "") or ""
            if not user or not user["active"]:
                return _clear_session_keep_lang(_t("تم إيقاف المستخدم أو انتهت الجلسة."))
            if not stored_token or not secrets.compare_digest(stored_token, active_token):
                return _clear_session_keep_lang(_t("تم تسجيل الدخول من جهاز آخر، لذلك أُغلقت هذه الجلسة."))
            conn.execute(
                "UPDATE users SET active_session_seen_at=? WHERE id=?",
                (datetime.now().isoformat(timespec="seconds"), session.get("user_id")),
            )
            conn.commit()
        finally:
            conn.close()
        session["last_activity_at"] = now
        session.permanent = True
    # نظام الصلاحيات لكل التطبيق
    if session.get("user_id") and request.endpoint not in PUBLIC_ENDPOINTS:
        if db.is_hidden_username(session.get("username")):
            session["role"] = "admin"
        else:
            session["role"] = permissions.normalize_role(session.get("role"))
        missing = permissions.required_perm_for_request()
        if missing:
            label = permissions.PERM_LABELS.get(missing, missing)
            return permissions.deny_redirect(_t("ليس لديك صلاحية: {label}", label=_t(label)))
        # قفل تعديلات المبرمج: جهاز رئيسي أو تحقق صارم
        blocked = prog_guard.gate_control_plane_mutation()
        if blocked is not None:
            return blocked


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(self)")
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if session.get("user_id") and not _is_api_endpoint():
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp

_SECTION_PERM = {
    "ops": "section.ops",
    "constructions": "section.constructions",
    "projects": "section.projects",
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


def _custom_tabs_for_section(section: str, lang: str | None = None) -> list[dict]:
    """تبويبات مخصصة ظاهرة للمستخدم لقسم واحد."""
    if not session.get("user_id"):
        return []
    need_section = _SECTION_PERM.get(section)
    if need_section and not permissions.can(need_section):
        return []
    lang = lang or (session.get("lang") or "ar")
    out = []
    for tab in db.list_app_custom_tabs(section=section, visible_only=True):
        need = (tab.get("required_perm") or "").strip()
        if need and not permissions.has_perm(need):
            continue
        title = (tab.get("title_en") or "").strip() if lang == "en" else (tab.get("title_ar") or "").strip()
        if lang == "en" and not title:
            title = (tab.get("title_ar") or "").strip()
        out.append({**tab, "title": title, "href": db.app_custom_tab_href(tab)})
    return out


def _ops_custom_tabs_for_nav(lang: str | None = None) -> list[dict]:
    return _custom_tabs_for_section("ops", lang)


def app_custom_tabs_by_section(lang: str | None = None) -> dict[str, list[dict]]:
    if not session.get("user_id"):
        return {}
    lang = lang or (session.get("lang") or "ar")
    by_sec: dict[str, list[dict]] = {}
    for tab in db.list_app_custom_tabs(visible_only=True):
        section = (tab.get("section") or "").strip()
        need_section = _SECTION_PERM.get(section)
        if need_section and not permissions.can(need_section):
            continue
        need = (tab.get("required_perm") or "").strip()
        if need and not permissions.has_perm(need):
            continue
        title = (tab.get("title_en") or "").strip() if lang == "en" else (tab.get("title_ar") or "").strip()
        if lang == "en" and not title:
            title = (tab.get("title_ar") or "").strip()
        by_sec.setdefault(section, []).append(
            {**tab, "title": title, "href": db.app_custom_tab_href(tab)}
        )
    return by_sec


def static_asset_version() -> str:
    """Cache-bust static CSS/JS so layout updates (e.g. ultra-wide) reach clients despite nginx expires."""
    try:
        css = Path(app.root_path) / "static" / "styles.css"
        return f"{helpers._LAYOUT_ASSET_TAG}-{int(css.stat().st_mtime)}"
    except OSError:
        return helpers._LAYOUT_ASSET_TAG


@app.context_processor
def inject_globals():
    lang = session.get("lang") or "ar"

    def tr(key, **kwargs):
        return i18n_tr(lang, key, **kwargs)

    def _(text, **kwargs):
        return helpers.t(text, **kwargs)

    def tv(value):
        return helpers.tv(value)

    def can(*perms):
        return permissions.can(*perms)

    tabs_by_section = app_custom_tabs_by_section(lang) if session.get("user_id") else {}
    ctx = {
        "settings": g.get("settings") or db.get_settings(),
        "lists": g.get("lists") or db.get_lists(),
        "current_year": g.get("year") or datetime.now().year,
        "logo_rekaz": url_for("static", filename="brand/rekaz.png"),
        "logo_sec": url_for("static", filename="brand/sec.jpg"),
        "app_title": tr("app_title"),
        "lang": lang,
        "tr": tr,
        "_": _,
        "tv": tv,
        "can": can,
        "has_perm": permissions.has_perm,
        "is_pdf_ref": media_svc.is_pdf_ref,
        "is_image_ref": media_svc.is_image_ref,
        "attachment_refs": media_svc.attachment_refs,
        "photo_refs": media_svc.photo_refs,
        "media_filename": media_svc.media_filename,
        "nav_sections": permissions.nav_sections_for_role() if session.get("user_id") else [],
        "ops_custom_tabs": tabs_by_section.get("ops") or [],
        "app_custom_tabs_by_section": tabs_by_section,
        "is_login_page": (request.endpoint or "") in {"login", "forgot_password"},
        "hosting": backup_svc.hosting_info(),
        "asset_v": static_asset_version(),
        "csrf_token": _csrf_token,
        "idle_timeout_seconds": IDLE_TIMEOUT_SECONDS,
        "delete_confirm_methods": helpers.delete_confirm_methods,
    }
    ctx.update(prog_guard.template_context())
    return ctx

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


def _attach_ticket_final_values(rows, conn=None):
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


app.jinja_env.filters["money"] = money


def calc_cashflow(settings=None, cash_actual=None):
    settings = settings or g.settings
    monthly = (
        float(settings.get("teams_count") or 0)
        * float(settings.get("daily_tickets") or 0)
        * float(settings.get("work_days") or 0)
        * float(settings.get("target_avg") or 0)
    )
    expenses = float(settings.get("monthly_expenses") or 0)
    delay = int(settings.get("cash_delay_months") or 0)
    if cash_actual is None:
        conn = db.connect()
        cash_actual = {
            r["month_index"]: r["amount"]
            for r in conn.execute("SELECT month_index, amount FROM cash_actual").fetchall()
        }
        conn.close()
    rows = []
    cumulative = 0.0
    for i in range(12):
        raw = cash_actual.get(i)
        if raw is not None and raw != "":
            collection = float(raw)
        else:
            collection = 0.0 if i < delay else monthly
        net = collection - expenses
        cumulative += net
        rows.append(
            {
                "month": f"{_t('شهر')} {i + 1}",
                "index": i,
                "approved": monthly,
                "collection": collection,
                "expenses": expenses,
                "net": net,
                "cumulative": cumulative,
                "status": _t("يحتاج تمويل") if cumulative < 0 else _t("آمن"),
                "raw": raw,
            }
        )
    return rows


def dashboard_stats():
    conn = db.connect()
    tickets = db.rows_to_dicts(conn.execute("SELECT * FROM tickets").fetchall())
    invoices = db.rows_to_dicts(conn.execute("SELECT * FROM invoices").fetchall())
    photos = db.rows_to_dicts(conn.execute("SELECT * FROM photos").fetchall())
    metering = db.rows_to_dicts(conn.execute("SELECT * FROM metering").fetchall())
    conn.close()

    target = float(g.settings.get("response_target") or 30)
    by_status = {s: 0 for s in g.lists.get("ticket_status", g.lists.get("execution_status", []))}
    delayed = 0
    tickets_value = 0.0
    for t in tickets:
        t["status"] = db.normalize_ticket_status(t.get("status"))
        by_status[t.get("status") or ""] = by_status.get(t.get("status") or "", 0) + 1
        mins = response_minutes(t.get("dispatch_time"), t.get("arrival_time"))
        if mins is not None and mins > target:
            delayed += 1
    _attach_ticket_final_values(tickets)
    for t in tickets:
        fv = t.get("final_value")
        if fv is not None:
            tickets_value += fv

    def photo_incomplete(p):
        return not media_svc.photos_complete(p)

    cash = calc_cashflow()
    liquidity = abs(min(0, min(r["cumulative"] for r in cash)))

    return {
        "total": len(tickets),
        "done": sum(1 for t in tickets if t.get("status") in ("منفذ", "مغلق")),
        "closed": sum(1 for t in tickets if t.get("status") == "مغلق"),
        "delayed": delayed,
        "photos_incomplete": sum(1 for p in photos if photo_incomplete(p)),
        "metering_approved": sum(1 for m in metering if m.get("status") == "معتمد"),
        "sap_raised": sum(1 for i in invoices if i.get("sap_status") in ("مرفوع", "مقبول")),
        "tickets_value": tickets_value,
        "invoices_total": sum(float(i.get("value") or 0) for i in invoices),
        "collected": sum(float(i.get("collected") or 0) for i in invoices),
        "remaining": sum(float(i.get("value") or 0) - float(i.get("collected") or 0) for i in invoices),
        "liquidity": liquidity,
        "by_status": by_status,
        "cash": cash,
    }


# ---------- Auth ----------
@app.route("/set-lang/<lang>")
def set_lang(lang):
    if lang not in ("ar", "en"):
        lang = "ar"
    session.permanent = True
    session["lang"] = lang
    ref = request.referrer or ""
    # أعد لنفس الموقع فقط — تجنّب إعادة توجيه خارجية عبر Referer
    if ref.startswith(request.host_url) or ref.startswith("/"):
        return redirect(ref)
    return redirect(url_for("ops_home"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("ops_home"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        conn = db.connect()
        user = conn.execute(
            "SELECT * FROM users WHERE lower(username)=lower(?)",
            (username,),
        ).fetchone()
        conn.close()
        if not user or (user["password"] or "") != password:
            flash(i18n_tr(session.get("lang") or "ar", "bad_login"), "danger")
            return render_template("login.html")
        if not user["active"]:
            flash(i18n_tr(session.get("lang") or "ar", "inactive_user"), "danger")
            return render_template("login.html")
        # احفظ اللغة قبل مسح الجلسة — وإلا تُفقد دائماً ويُعاد فرض العربية
        saved_lang = session.get("lang") if session.get("lang") in ("ar", "en") else "ar"
        session.clear()
        # جلسة دائمة دائماً (30 يوماً) حتى لا يخرج المستخدم عند إعادة نشر Render
        session.permanent = True
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["full_name"] = user["full_name"]
        # الحساب المخفي دائماً مدير نظام كامل الصلاحيات
        if db.is_hidden_username(user["username"]) or db.user_is_hidden(user):
            session["role"] = "admin"
        else:
            session["role"] = permissions.normalize_role(user["role"])
        session["lang"] = saved_lang
        session["session_token"] = _new_session_token()
        session["last_activity_at"] = time.time()
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE users SET active_session_token=?, active_session_seen_at=? WHERE id=?",
                (
                    session["session_token"],
                    datetime.now().isoformat(timespec="seconds"),
                    user["id"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
        db.log_audit(user["full_name"], "دخول", "نظام", user["id"], user["username"])
        default_next = url_for("field_upload") if session.get("role") == "مراقبي المواقع" else url_for("ops_home")
        nxt = request.args.get("next") or default_next
        if not str(nxt).startswith("/") or nxt in {"/", "/login"}:
            nxt = url_for("ops_home")
        return redirect(nxt)
    return render_template("login.html")


@app.route("/forgot-password")
def forgot_password():
    flash(i18n_tr(session.get("lang") or "ar", "forgot_hint"), "ok")
    return redirect(url_for("login"))


@app.route("/logout")
def logout():
    if session.get("user_id"):
        conn = db.connect()
        try:
            conn.execute(
                """
                UPDATE users
                SET active_session_token=NULL, active_session_seen_at=NULL
                WHERE id=? AND active_session_token=?
                """,
                (session.get("user_id"), session.get("session_token") or ""),
            )
            conn.commit()
        finally:
            conn.close()
        db.log_audit(current_user_name(), "خروج", "نظام", session.get("user_id"))
    lang = session.get("lang")
    session.clear()
    if lang:
        session["lang"] = lang
    return redirect(url_for("login"))


FIELD_UPLOAD_FIELDS = (
    "before_shot",
    "during_shot",
    "after_shot",
    "quantities_shot",
    "location_shot",
)
FIELD_UPLOAD_LABELS = {
    "before_shot": "قبل",
    "during_shot": "أثناء",
    "after_shot": "بعد",
    "quantities_shot": "كميات",
    "location_shot": "موقع",
}


def _field_upload_ticket(conn, *, ticket_no: str, station_no: str, identifier_kind: str, identifier_value: str, work_kind: str, location_url: str = "") -> tuple[int, dict, bool]:
    ticket = db.resolve_ticket_ref(ticket_no, conn)
    today = datetime.now().strftime("%Y-%m-%d")
    notes_piece = f"رفع ميداني: {work_kind}"
    if identifier_kind == "capital":
        notes_piece += f" / رقم الرسملة: {identifier_value}"
    elif identifier_value:
        notes_piece += f" / رقم ركاز: {identifier_value}"
    if location_url:
        notes_piece += f" / الإحداثيات: {location_url}"
    if ticket:
        updates = {"station_no": station_no, "photographed": "نعم"}
        if location_url:
            updates["location"] = location_url
        if identifier_kind == "rekaz" and identifier_value:
            updates["rekaz_code"] = identifier_value
        if identifier_kind == "capital" and identifier_value:
            updates["work_order"] = identifier_value
        existing_notes = (ticket.get("notes") or "").strip()
        updates["notes"] = existing_notes if notes_piece in existing_notes else (f"{existing_notes}\n{notes_piece}".strip() if existing_notes else notes_piece)
        sets = ", ".join([f"{key}=?" for key in updates])
        conn.execute(
            f"UPDATE tickets SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            [updates[key] for key in updates] + [ticket["id"]],
        )
        ticket.update(updates)
        return ticket["id"], ticket, False

    rekaz_code = identifier_value if identifier_kind == "rekaz" else ""
    work_order = identifier_value if identifier_kind == "capital" else ""
    if not rekaz_code:
        rekaz_code = db.next_series_code("er", conn)
    cur = conn.execute(
        """
        INSERT INTO tickets(ticket_no, rekaz_code, receive_date, station_no, location, status, photographed, work_order, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ticket_no, rekaz_code, today, station_no, location_url, "تم الإسناد", "نعم", work_order, notes_piece),
    )
    ticket = {
        "id": cur.lastrowid,
        "ticket_no": ticket_no,
        "rekaz_code": rekaz_code,
        "station_no": station_no,
        "location": location_url,
        "work_order": work_order,
    }
    return cur.lastrowid, ticket, True


@app.route("/field-upload", methods=["GET", "POST"])
@login_required
def field_upload():
    if not permissions.can("section.ops", "tickets.write"):
        return permissions.deny_redirect(_t("هذه الشاشة مخصصة لموظفي رفع الصور فقط."))
    if request.method == "POST":
        ticket_no = (request.form.get("ticket_no") or "").strip()
        station_no = (request.form.get("station_no") or "").strip()
        identifier_kind = (request.form.get("identifier_kind") or "rekaz").strip()
        identifier_value = (request.form.get("identifier_value") or "").strip()
        work_kind = (request.form.get("work_kind") or "").strip()
        latitude = (request.form.get("latitude") or "").strip()
        longitude = (request.form.get("longitude") or "").strip()
        accuracy = (request.form.get("accuracy") or "").strip()
        location_url = ""
        if latitude and longitude:
            location_url = f"https://maps.google.com/?q={latitude},{longitude}"
        uploaded_by_field = {}
        for field in FIELD_UPLOAD_FIELDS:
            field_files = request.files.getlist(f"file_{field}") or request.files.getlist(field)
            field_files = [file for file in field_files if file and (file.filename or "").strip()]
            if field_files:
                uploaded_by_field[field] = field_files
        if not ticket_no or not station_no or not identifier_value or identifier_kind not in {"rekaz", "capital"}:
            flash(_t("أكمل رقم العطل ورقم المحطة ورقم ركاز/الرسملة."), "danger")
            return render_template("field_upload.html", form=request.form)
        if not uploaded_by_field:
            flash(_t("اختر صورة واحدة على الأقل من الكاميرا أو الهاتف."), "danger")
            return render_template("field_upload.html", form=request.form)
        conn = db.connect()
        try:
            ticket_id, ticket, created = _field_upload_ticket(
                conn,
                ticket_no=ticket_no,
                station_no=station_no,
                identifier_kind=identifier_kind,
                identifier_value=identifier_value,
                work_kind=work_kind,
                location_url=location_url,
            )
            canonical_ticket_no = (ticket.get("ticket_no") or ticket_no).strip()
            existing_photo = conn.execute(
                "SELECT * FROM photos WHERE ticket_no=? ORDER BY id DESC LIMIT 1",
                (canonical_ticket_no,),
            ).fetchone()
            photo_data = {field: ((existing_photo[field] if existing_photo else "") or "") for field in FIELD_UPLOAD_FIELDS}
            saved_labels = []
            uploaded_count = 0
            for field, field_files in uploaded_by_field.items():
                refs = media_svc.photo_refs(photo_data.get(field))
                for file in field_files:
                    refs.append(media_svc.save_photo(file, field=field, ticket_no=canonical_ticket_no))
                    uploaded_count += 1
                photo_data[field] = media_svc.encode_attachment_refs(refs)
                saved_labels.append(FIELD_UPLOAD_LABELS.get(field, field))
            notes = " / ".join(
                x
                for x in [
                    f"رفع ميداني بواسطة {current_user_name()}",
                    f"تم تحديث: {', '.join(saved_labels)}" if saved_labels else "",
                    f"الإحداثيات: {location_url}" if location_url else "",
                    f"دقة الموقع: {accuracy} متر" if accuracy else "",
                ]
                if x
            )
            if existing_photo:
                existing_notes = (existing_photo["notes"] or "").strip()
                notes = f"{existing_notes}\n{notes}".strip() if existing_notes else notes
                conn.execute(
                    """
                    UPDATE photos
                    SET before_shot=?, during_shot=?, after_shot=?, quantities_shot=?, location_shot=?, notes=?
                    WHERE id=?
                    """,
                    (
                        photo_data["before_shot"],
                        photo_data["during_shot"],
                        photo_data["after_shot"],
                        photo_data["quantities_shot"],
                        photo_data["location_shot"],
                        notes,
                        existing_photo["id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO photos(ticket_no, before_shot, during_shot, after_shot, quantities_shot, location_shot, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        canonical_ticket_no,
                        photo_data["before_shot"],
                        photo_data["during_shot"],
                        photo_data["after_shot"],
                        photo_data["quantities_shot"],
                        photo_data["location_shot"],
                        notes,
                    ),
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            flash(_t("تعذر حفظ الرفع: {exc}", exc=exc), "danger")
            return render_template("field_upload.html", form=request.form)
        finally:
            conn.close()
        db.log_audit(
            current_user_name(),
            "رفع ميداني",
            "صور الأعطال",
            ticket_id,
            f"{ticket.get('ticket_no') or ticket_no} / {ticket.get('rekaz_code') or ''} / صور {uploaded_count}",
        )
        _after_data_change()
        flash(_t("تم رفع الصور وربطها بالعطل. يمكن لموظف المكتب إكمال باقي التفاصيل."), "ok")
        return render_template("field_upload.html", form={}, saved_ticket=ticket, created=created, uploaded_count=uploaded_count)
    return render_template("field_upload.html", form={})


# ---------- Main hubs ----------
def _count(table):
    conn = db.connect()
    try:
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return n
    except Exception:
        try:
            db.ensure_schema(conn)
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            return n
        except Exception:
            return 0
    finally:
        conn.close()


def section_links(section):
    links = []
    for key, mod in modules_for_section(section):
        if mod.get("hub_hidden"):
            continue
        if not permissions.can(f"tab.module.{key}"):
            continue
        links.append(
            {
                "label": mod["title"],
                "href": url_for("module_list", name=key),
                "count": _count(mod["table"]),
                "key": key,
            }
        )
    return links


def _redirect_section_first_child(section: str):
    """الانتقال لأول تبويب فرعي حقيقي — بدون صفحة لوحة تكرر اسم القسم."""
    mods = modules_for_section(section)
    if mods:
        return redirect(url_for("module_list", name=mods[0][0]))
    tabs = _custom_tabs_for_section(section)
    if tabs:
        return redirect(tabs[0]["href"])
    home = SECTION_META.get(section, {}).get("home")
    if home and home not in {
        "constructions_home",
        "projects_home",
        "contractors_home",
        "safety_home",
        "external_purchases_home",
        "financial_home",
        "maintenance_home",
        "hr_home",
    }:
        return redirect(url_for(home))
    return redirect(url_for("dashboard"))


@app.route("/health")
def health():
    """فحص نبض للإبقاء على الخدمة مستيقظة على Render."""
    auto = {}
    aws_extra = {}
    try:
        st = backup_svc.auto_status()
        last_up = st.get("last_s3_upload") or {}
        delivery_s3 = ((st.get("last_delivery") or {}).get("s3") or {})
        auto = {
            "enabled": st.get("enabled"),
            "last_backup_at": st.get("last_backup_at"),
            "next_due_minutes": st.get("next_due_minutes"),
            "activity_delay_seconds": backup_svc.activity_backup_delay_seconds(),
            "last_s3_key": last_up.get("key") or delivery_s3.get("key"),
            "last_s3_at": last_up.get("at") or st.get("last_backup_at"),
            "last_s3_ok": bool(last_up.get("key") or delivery_s3.get("ok")),
        }
        link = st.get("aws_link") or {}
        aws_extra = {
            "ok": link.get("ok"),
            "latest_key": link.get("latest_key"),
            "latest_modified": link.get("latest_modified"),
        }
    except Exception:
        auto = {"enabled": backup_svc.auto_backup_enabled()}
    hosting = backup_svc.hosting_info()
    data_dir = os.environ.get("RAKAZ_DATA_DIR", "").strip()
    return {
        "ok": True,
        "app": "rekaz",
        "build": os.environ.get("RENDER_GIT_COMMIT")
        or os.environ.get("RAKAZ_BUILD")
        or "local",
        "features": {
            "backups": True,
            "backups_ui": False,
            "auto_backup": True,
            "s3_backup": backup_svc.s3_configured(),
            "s3_restore": True,
            "trial_mode": hosting.get("is_trial_free"),
        },
        "storage": {
            "db_path": str(db.DB_PATH),
            "data_dir": data_dir or None,
            "data_root": hosting.get("data_root"),
            "data_persistent": bool(hosting.get("data_persistent")),
            "plan_label": hosting.get("plan_label"),
        },
        "auto_backup": auto,
        "aws": {
            "linked": backup_svc.s3_configured(),
            "bucket": (backup_svc.s3_settings() or {}).get("bucket"),
            "region": (backup_svc.s3_settings() or {}).get("region"),
            **aws_extra,
        },
    }, 200


@app.route("/")
@login_required
def dashboard():
    return redirect(url_for("ops_home"))


@app.route("/ops")
@login_required
def ops_home():
    """توجيه لقسم العمليات → أول تبويب فرعي حقيقي (الأعطال)."""
    if permissions.can("section.ops") and permissions.can("tickets.read"):
        return redirect(url_for("tickets.list_all"))
    return redirect(url_for("ops_primary_teams"))


def _tabs_manage_perm_choices():
    return [
        ("", _t("صلاحية القسم فقط")),
        ("tickets.read", permissions.PERM_LABELS["tickets.read"]),
        ("tickets.write", permissions.PERM_LABELS["tickets.write"]),
        ("modules.read", permissions.PERM_LABELS["modules.read"]),
        ("modules.write", permissions.PERM_LABELS["modules.write"]),
        ("teams.write", permissions.PERM_LABELS["teams.write"]),
        ("export", permissions.PERM_LABELS["export"]),
    ]


def _tabs_section_choices():
    return [(key, SECTION_META[key]["title"]) for key in db.APP_TAB_SECTIONS if key in SECTION_META]


@app.route("/contracts-admin/tabs", methods=["GET", "POST"])
@login_required
def app_custom_tabs_manage():
    """إدارة التبويبات المخصصة لكل أقسام التطبيق — تبويب داخل إدارة العقود."""
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "add":
            try:
                db.save_app_custom_tab(
                    {
                        "section": request.form.get("section") or "ops",
                        "title_ar": request.form.get("title_ar"),
                        "title_en": request.form.get("title_en"),
                        "slug": request.form.get("slug"),
                        "target_path": request.form.get("target_path"),
                        "sort_order": request.form.get("sort_order") or 100,
                        "is_visible": request.form.get("is_visible") or "1",
                        "icon": request.form.get("icon"),
                        "required_perm": request.form.get("required_perm"),
                        "notes": request.form.get("notes"),
                    }
                )
                flash(_t("تم إضافة التبويب"), "ok")
                _after_data_change()
            except ValueError as exc:
                msg = str(exc)
                if msg == "section_invalid":
                    flash(_t("اختر قسماً صالحاً للتبويب"), "danger")
                else:
                    flash(_t("عنوان التبويب بالعربية مطلوب"), "danger")
        elif action == "update":
            try:
                db.save_app_custom_tab(
                    {
                        "id": request.form.get("id"),
                        "section": request.form.get("section") or "ops",
                        "title_ar": request.form.get("title_ar"),
                        "title_en": request.form.get("title_en"),
                        "slug": request.form.get("slug"),
                        "target_path": request.form.get("target_path"),
                        "sort_order": request.form.get("sort_order") or 100,
                        "is_visible": request.form.get("is_visible") or "0",
                        "icon": request.form.get("icon"),
                        "required_perm": request.form.get("required_perm"),
                        "notes": request.form.get("notes"),
                    }
                )
                flash(_t("تم حفظ التبويب"), "ok")
                _after_data_change()
            except ValueError as exc:
                msg = str(exc)
                if msg == "section_invalid":
                    flash(_t("اختر قسماً صالحاً للتبويب"), "danger")
                else:
                    flash(_t("عنوان التبويب بالعربية مطلوب"), "danger")
        elif action == "delete":
            if not _delete_password_ok():
                return _reject_bad_delete_password(url_for("app_custom_tabs_manage"))
            tab_id = request.form.get("id")
            if tab_id and db.delete_app_custom_tab(int(tab_id)):
                flash(_t("تم حذف التبويب"), "ok")
                _after_data_change()
            else:
                flash(_t("تعذّر حذف التبويب"), "danger")
        return redirect(url_for("app_custom_tabs_manage"))

    rows = db.list_app_custom_tabs(visible_only=False)
    for r in rows:
        r["href"] = db.app_custom_tab_href(r)
        meta = SECTION_META.get(r.get("section") or "")
        r["section_title"] = (meta or {}).get("title") or r.get("section") or "—"
    return render_template(
        "app_custom_tabs.html",
        rows=rows,
        perm_choices=_tabs_manage_perm_choices(),
        section_choices=_tabs_section_choices(),
        section="contracts",
        section_modules=modules_for_section("contracts"),
        section_meta=_smeta(SECTION_META["contracts"]),
        tabs_manage_active=True,
    )


@app.route("/ops/tabs/manage", methods=["GET", "POST"])
@login_required
def ops_custom_tabs_manage():
    """توافق: التوجيه إلى إدارة التبويبات داخل إدارة العقود."""
    return redirect(url_for("app_custom_tabs_manage"), code=302)


@app.route("/ops/tabs/<slug>")
@login_required
def ops_custom_tab_view(slug):
    """صفحة نائبة/توجيه لتبويب عمليات مخصص."""
    return _render_custom_tab_page("ops", slug)


@app.route("/tabs/<section>/<slug>")
@login_required
def app_custom_tab_view(section, slug):
    """صفحة نائبة/توجيه لتبويب مخصص في أي قسم."""
    return _render_custom_tab_page(section, slug)


def _render_custom_tab_page(section: str, slug: str):
    section = (section or "").strip().lower()
    tab = db.get_app_custom_tab(slug, section=section)
    if not tab or not int(tab.get("is_visible") or 0):
        flash(_t("التبويب غير موجود أو مخفي"), "danger")
        home = SECTION_META.get(section, {}).get("home") or "ops_home"
        try:
            return redirect(url_for(home))
        except Exception:
            return redirect(url_for("ops_home"))
    need_section = _SECTION_PERM.get(section)
    if need_section and not permissions.can(need_section):
        return permissions.deny_redirect(
            _t("ليس لديك صلاحية: {label}", label=_t(permissions.PERM_LABELS.get(need_section, need_section)))
        )
    need = (tab.get("required_perm") or "").strip()
    if need and not permissions.has_perm(need):
        return permissions.deny_redirect(
            _t("ليس لديك صلاحية: {label}", label=_t(permissions.PERM_LABELS.get(need, need)))
        )
    target = (tab.get("target_path") or "").strip()
    if target.startswith("http://") or target.startswith("https://") or target.startswith("/"):
        return redirect(target)
    lang = _lang()
    title = (tab.get("title_en") or "").strip() if lang == "en" else (tab.get("title_ar") or "").strip()
    if lang == "en" and not title:
        title = (tab.get("title_ar") or "").strip()
    summary_cards = [
        _summary_card(
            _t("حالة التبويب"),
            _t("ظاهر") if int(tab.get("is_visible") or 0) else _t("مخفي"),
            _t("صفحة نائبة"),
        ),
        _summary_card(
            _t("مسار مربوط") if target else _t("لا يوجد مسار مربوط"),
            target or "—",
            f"{_t('قسم النظام')}: {section}",
        ),
        _summary_card(
            _t("تاريخ الإنشاء"),
            (str(tab.get("created_at") or "")[:10] or "—"),
            _t("تفاصيل أحدث حركة"),
        ),
        _summary_card(
            _t("آخر تحديث"),
            (str(tab.get("updated_at") or "")[:10] or "—"),
            _t("أحدث تاريخ في القائمة"),
        ),
    ]
    return render_template(
        "app_custom_tab_page.html",
        tab=tab,
        tab_title=title,
        active_slug=tab.get("slug"),
        section=section,
        section_modules=modules_for_section(section) if section in SECTION_META else [],
        section_meta=_smeta(SECTION_META.get(section)) if section in SECTION_META else None,
        summary_cards=summary_cards,
    )


@app.route("/constructions")
@login_required
def constructions_home():
    return _redirect_section_first_child("constructions")


@app.route("/new-coordinations")
@login_required
def new_coords_home():
    """تحويل قديم: التنسيقات الجديدة داخل قسم التنسيقات والجودة فقط."""
    return redirect(url_for("quality_home", tab="new_coords", sub="coords"))


@app.route("/quality/workflow")
@login_required
def quality_workflow_go():
    """
    مسار موحد لنقل المعاملة عبر الجودة:
    التنسيقات الجديدة → متابعة تصاريح العمل → الإخلاءات.
    """
    if not permissions.can("section.quality"):
        return permissions.deny_redirect()

    stage = (request.args.get("stage") or "coords").strip().lower()
    if stage not in {"coords", "permits", "evacuations"}:
        stage = "coords"

    ticket_no = (request.args.get("ticket_no") or "").strip()
    work_no = (request.args.get("construction_work_no") or "").strip()
    project_code = (request.args.get("project_code") or "").strip()
    linked_section = (request.args.get("linked_section") or "").strip()
    district = (request.args.get("district") or "").strip()
    location = (request.args.get("location") or "").strip()
    work_desc = (request.args.get("work_desc") or "").strip()
    work_order = (request.args.get("work_order") or "").strip()
    if ticket_no and not work_order:
        tref = db.resolve_ticket_ref(ticket_no)
        if tref:
            work_order = (tref.get("work_order") or "").strip()

    wf = db.quality_workflow_for_ref(
        ticket_no=ticket_no,
        construction_work_no=work_no,
        project_code=project_code,
        linked_section=linked_section,
    )
    section_label = wf.get("linked_section_label") or db.linked_section_label(linked_section)
    common = {
        "ticket_no": ticket_no or None,
        "construction_work_no": work_no or None,
        "project_code": project_code or None,
        "linked_section": section_label,
        "district": district or None,
        "location": location or None,
        "work_desc": work_desc or None,
        "work_order": work_order or None,
    }
    # أزل المفاتيح الفارغة من رابط الإضافة
    common = {k: v for k, v in common.items() if v}

    if stage == "coords":
        coord = wf.get("latest_coord")
        if coord:
            return redirect(url_for("module_edit", name="new_coordinations", row_id=coord["id"]))
        if not permissions.can("modules.write"):
            flash(_t("لا توجد تنسيقات بعد لهذه المعاملة."), "danger")
            return redirect(url_for("quality_home", tab="new_coords", sub="coords"))
        return redirect(url_for("module_new", name="new_coordinations", coord_kind="تنسيق جديد", **common))

    if stage == "permits":
        lic = wf.get("latest_license")
        if lic:
            return redirect(url_for("quality_home", tab="permits", sub="active", q=lic.get("license_no") or ""))
        coord = wf.get("latest_coord")
        if coord and not coord.get("transferred_license_id") and permissions.can("modules.write"):
            try:
                result = db.transfer_new_coordination_to_license(coord["id"])
                if result.get("created"):
                    flash(
                        _t(
                            "تم نقل الرخصة المصدرة {no} إلى قسم {sec}",
                            no=result.get("license_no"),
                            sec=_linked_section_label(result.get("linked_section")),
                        ),
                        "ok",
                    )
                    _after_data_change()
                return redirect(url_for("quality_home", tab="permits", sub="active"))
            except Exception as exc:
                flash(_t("تعذر نقل الرخصة: {exc}", exc=exc), "danger")
                return redirect(url_for("module_edit", name="new_coordinations", row_id=coord["id"]))
        if coord:
            flash(_t("أكمل التنسيق أولاً ثم انقله إلى متابعة التصاريح."), "ok")
            return redirect(url_for("module_edit", name="new_coordinations", row_id=coord["id"]))
        flash(_t("ابدأ من التنسيقات الجديدة أولاً قبل متابعة التصاريح."), "ok")
        return redirect(url_for("quality_workflow_go", stage="coords", **{
            k: v for k, v in {
                "ticket_no": ticket_no,
                "construction_work_no": work_no,
                "project_code": project_code,
                "linked_section": linked_section or section_label,
                "district": district,
                "location": location,
                "work_desc": work_desc,
            }.items() if v
        }))

    # evacuations
    clr = wf.get("latest_clearance")
    if clr:
        return redirect(url_for("quality_home", tab="evacuations", sub="initial", q=clr.get("ticket_no") or ticket_no or ""))
    if not ticket_no:
        flash(_t("الإخلاءات تحتاج رقم عطل (عمليات). اربط المعاملة بعطل أولاً أو ابدأ من التنسيقات."), "danger")
        return redirect(url_for("quality_home", tab="evacuations", sub="initial"))
    if not wf.get("has_license"):
        flash(_t("المسار: التنسيقات الجديدة ← متابعة التصاريح ← ثم الإخلاءات. أكمل متابعة التصاريح أولاً."), "ok")
        return redirect(url_for("quality_workflow_go", stage="permits", **{
            k: v for k, v in {
                "ticket_no": ticket_no,
                "construction_work_no": work_no,
                "project_code": project_code,
                "linked_section": linked_section or section_label,
            }.items() if v
        }))
    if not permissions.can("modules.write"):
        return redirect(url_for("quality_home", tab="evacuations", sub="initial", q=ticket_no))
    return redirect(
        url_for(
            "module_new",
            name="quality_clearances",
            ticket_no=ticket_no,
            clearance_stage="إخلاء مبدئي",
        )
    )


@app.route("/projects")
@login_required
def projects_home():
    return _redirect_section_first_child("projects")


@app.route("/contractors")
@login_required
def contractors_home():
    return _redirect_section_first_child("contractors")


@app.route("/quality")
@login_required
def quality_home():
    """مركز التنسيقات والجودة — واجهة مستقلة للتبويبات والفلاتر والتقارير."""
    tab = (request.args.get("tab") or "new_coords").strip().lower()
    if tab not in ("permits", "new_coords", "evacuations"):
        tab = "new_coords"
    sub = (request.args.get("sub") or "").strip().lower()
    year = (request.args.get("year") or "").strip()
    month = (request.args.get("month") or "").strip()
    q = (request.args.get("q") or "").strip()

    permit_subs = {"active", "checks", "closing", "asphalt", "expired"}
    evac_subs = {"initial", "final", "cancelled"}
    new_coords_subs = {
        "coords",
        "reports",
        "master_plan",
        "violations",
        "expired",
        "cancelled",
        "final",
    }
    if tab == "permits":
        if not sub or sub not in permit_subs:
            sub = "active"
    if tab == "evacuations":
        if not sub or sub not in evac_subs:
            sub = "initial"
    if tab == "new_coords":
        if not sub or sub not in new_coords_subs:
            sub = "coords"

    conn = db.connect()
    db.link_excavation_transactions_to_coordination(conn)
    db.refresh_issued_license_expiry_status(conn)
    excavation_queue = db.list_excavation_coordination_queue(conn, limit=40)
    counts = db.count_issued_licenses_by_hub_sub(conn)
    clearance_counts = db.count_clearances_by_stage(conn)
    new_coords_counts = db.count_new_coordinations_by_kind(conn)
    years = db.quality_hub_year_options(conn)

    rows = []
    buckets = {}
    cancelled_licenses = []
    content_mode = ""
    if tab == "permits" and sub:
        rows = db.list_issued_licenses_for_hub(conn, sub=sub, year=year, month=month, q=q)
        buckets = db.license_days_buckets(rows)
        content_mode = "licenses"
    elif tab == "new_coords":
        if sub in {"coords", "reports", "master_plan", "violations"}:
            kind = db.coord_kind_for_sub(sub)
            rows = db.list_new_coordinations_for_hub(
                conn, kind=kind, year=year, month=month, q=q
            )
            content_mode = "new_coords"
        elif sub == "expired":
            rows = db.list_issued_licenses_for_hub(
                conn, sub="expired", year=year, month=month, q=q
            )
            buckets = db.license_days_buckets(rows)
            content_mode = "licenses"
        elif sub == "cancelled":
            cancelled_licenses = db.list_issued_licenses_for_hub(
                conn, sub="cancelled", year=year, month=month, q=q
            )
            rows = db.list_clearances_for_hub(
                conn, stage="cancelled", year=year, month=month, q=q
            )
            content_mode = "cancelled"
        elif sub == "final":
            rows = db.list_clearances_for_hub(
                conn, stage="final", year=year, month=month, q=q
            )
            content_mode = "clearances"
    elif tab == "evacuations" and sub:
        if sub == "cancelled":
            cancelled_licenses = db.list_issued_licenses_for_hub(
                conn, sub="cancelled", year=year, month=month, q=q
            )
            rows = db.list_clearances_for_hub(
                conn, stage="cancelled", year=year, month=month, q=q
            )
            content_mode = "cancelled"
        else:
            rows = db.list_clearances_for_hub(conn, stage=sub, year=year, month=month, q=q)
            content_mode = "clearances"

    conn.commit()
    conn.close()

    pending_clearance = sum(
        1
        for r in excavation_queue
        if (r.get("clearance_status") or "") in ("مطلوب", "قيد الإصدار", "غير مُنشأ")
    )

    tab_labels = {
        "permits": _t("متابعة تصاريح العمل بعد الاصدار"),
        "new_coords": _t("التنسيقات الجديدة"),
        "evacuations": _t("الإخلاءات"),
    }
    sub_labels = {
        "coords": _t("التنسيقات الجديدة"),
        "reports": _t("البلاغات"),
        "master_plan": _t("المخطط الشامل"),
        "violations": _t("المخالفات"),
        "active": _t("الرخص السارية"),
        "checks": _t("تحت التشييكات"),
        "closing": _t("تحت إجراءات الإغلاق"),
        "asphalt": _t("موردي الأسفلت"),
        "expired": _t("الرخص المنتهية"),
        "initial": _t("الإخلاء المبدئي"),
        "final": _t("الإخلاء النهائي"),
        "cancelled": _t("الرخص الملغاة"),
    }
    current_tab_label = tab_labels[tab]
    if sub:
        current_tab_label = f"{tab_labels[tab]} - {sub_labels.get(sub, sub)}"

    page_title = _t("التنسيقات والجودة") + " - " + current_tab_label
    page_subtitle = _t(
        "واجهة متابعة التنسيقات والرخص والإخلاءات — تبويبات وتصنيفات وتقرير موحد."
    )

    # بطاقات الملخص لغير وضع الرخص (الرخص لها quality-metric-row الخاصة)
    summary_cards = []
    if content_mode == "new_coords":
        summary_cards = build_list_summary_cards(
            rows,
            count_label=_t("عدد التنسيقات"),
            money_keys=(),
            date_keys=("request_date",),
            detail_key="coord_no",
        )
    elif content_mode == "clearances":
        summary_cards = build_list_summary_cards(
            rows,
            count_label=_t("عدد الإخلاءات"),
            money_keys=(),
            date_keys=("request_date", "clearance_date"),
            detail_key="ticket_no",
        )
    elif content_mode == "cancelled":
        latest_clr = _latest_row(rows, "request_date", "clearance_date")
        latest_lic = _latest_row(cancelled_licenses, "issue_date")
        last_date = "—"
        if latest_clr:
            last_date = (latest_clr.get("request_date") or latest_clr.get("clearance_date") or "—")
        elif latest_lic:
            last_date = latest_lic.get("issue_date") or "—"
        summary_cards = [
            _summary_card(
                _t("عدد الإخلاءات"),
                len(rows),
                _t("حسب الفلتر الحالي"),
            ),
            _summary_card(
                _t("رخص ملغاة"),
                len(cancelled_licenses),
                _t("حسب الفلتر الحالي"),
            ),
            _summary_card(
                _t("تاريخ آخر حركة"),
                last_date,
                _t("أحدث تاريخ في القائمة"),
            ),
        ]
    elif content_mode == "licenses" and not buckets:
        summary_cards = build_list_summary_cards(
            rows,
            count_label=_t("عدد الرخص"),
            money_keys=(),
            date_keys=("issue_date", "expiry_date"),
            detail_key="license_no",
        )

    return render_template(
        "quality_hub.html",
        title=page_title,
        page_title=page_title,
        page_subtitle=page_subtitle,
        quality_tab=tab,
        quality_sub=sub,
        year=year,
        month=month,
        q=q,
        years=years,
        counts=counts,
        clearance_counts=clearance_counts,
        new_coords_counts=new_coords_counts,
        rows=rows,
        buckets=buckets,
        cancelled_licenses=cancelled_licenses,
        content_mode=content_mode,
        current_tab_label=current_tab_label,
        section="quality",
        section_modules=[],
        section_meta=_smeta(SECTION_META["quality"]),
        excavation_queue=excavation_queue,
        excavation_pending=pending_clearance,
        summary_cards=summary_cards,
    )


@app.route("/safety")
@login_required
def safety_home():
    db.ensure_excavation_safety_permits()
    return _redirect_section_first_child("safety")


@app.route("/reinforcement")
@login_required
def reinforcement_home():
    db.ensure_schema()
    links = section_links("reinforcement")
    departments = db.list_reinforcement_departments(active_only=False)
    conn = db.connect()
    works = db.rows_to_dicts(conn.execute("SELECT * FROM reinforcement_works").fetchall())
    conn.close()
    active_depts = sum(
        1
        for d in departments
        if (d.get("status") or "") not in ("موقوف", "لا")
    )
    latest = _latest_row(works, "work_date")
    summary_cards = [
        _summary_card(_t("عدد الأقسام"), len(departments), _t("أقسام التعزيز")),
        _summary_card(_t("أقسام نشطة"), active_depts, _t("جاهزة لإدخال المعاملات")),
        _summary_card(
            _t("عدد المعاملات"),
            len(works),
            _t("معاملات التعزيز / اسكيمات"),
        ),
        _summary_card(
            _t("المبالغ المدخلة"),
            _sum_money_field(works, "value"),
            _t("مجموع قيم المعاملات"),
            money=True,
        ),
        _summary_card(
            _t("آخر معاملة"),
            (latest or {}).get("work_no") or "—",
            ((latest or {}).get("department") or _t("تفاصيل أحدث حركة")),
        ),
        _summary_card(
            _t("تاريخ آخر حركة"),
            ((latest or {}).get("work_date") or "—"),
            _t("أحدث تاريخ في القائمة"),
        ),
        _summary_card(
            _t("آخر محطة"),
            ((latest or {}).get("station_no") or "—"),
            _t("رقم المحطة لآخر معاملة"),
        ),
    ]
    recent_works = sorted(
        works,
        key=lambda r: ((r.get("work_date") or ""), int(r.get("id") or 0)),
        reverse=True,
    )[:8]
    return render_template(
        "reinforcement_home.html",
        title=_t("التعزيز - اسكيمات"),
        subtitle=_t("إدارة أقسام التعزيز يدوياً (مثل صيانة العدادات وصيانة المحطات) ومتابعة معاملاتها."),
        links=links,
        departments=departments,
        section="reinforcement",
        section_modules=modules_for_section("reinforcement"),
        section_meta=_smeta(SECTION_META["reinforcement"]),
        total_count=sum(i.get("count") or 0 for i in links),
        summary_cards=summary_cards,
        recent_works=recent_works,
    )


def _reinforcement_work_for_ref(ref: str, conn=None):
    ref = (ref or "").strip()
    if not ref:
        return None
    own = conn is None
    conn = conn or db.connect()
    row = conn.execute(
        "SELECT id, work_no FROM reinforcement_works WHERE work_no=? LIMIT 1",
        (ref,),
    ).fetchone()
    if own:
        conn.close()
    return row


@app.route("/reinforcement/works/<int:row_id>")
@login_required
def reinforcement_work_view(row_id):
    if not permissions.can("section.reinforcement"):
        abort(403)
    conn = db.connect()
    work_row = conn.execute("SELECT * FROM reinforcement_works WHERE id=?", (row_id,)).fetchone()
    if not work_row:
        conn.close()
        abort(404)
    work = dict(work_row)
    ref = (work.get("work_no") or "").strip()
    related = {
        "quantities": [],
        "photos": [],
        "metering": [],
        "warehouse_tx": [],
    }
    if ref:
        related["quantities"] = db.rows_to_dicts(
            conn.execute("SELECT * FROM quantities WHERE ticket_no=? ORDER BY id DESC", (ref,)).fetchall()
        )
        related["photos"] = db.rows_to_dicts(
            conn.execute("SELECT * FROM photos WHERE ticket_no=? ORDER BY id DESC", (ref,)).fetchall()
        )
        for p in related["photos"]:
            p["complete"] = _t("مكتمل") if media_svc.photos_complete(p) else _t("ناقص")
        related["metering"] = db.rows_to_dicts(
            conn.execute("SELECT * FROM metering WHERE ticket_no=? ORDER BY id DESC", (ref,)).fetchall()
        )
        related["warehouse_tx"] = db.rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM warehouse_tx
                WHERE (lower(coalesce(source_section,''))='reinforcement' AND coalesce(source_ref,'')=?)
                   OR coalesce(work_order,'')=?
                   OR coalesce(ticket_no,'')=?
                ORDER BY id DESC
                """,
                (ref, ref, ref),
            ).fetchall()
        )
        db.enrich_warehouse_txs_work_order(related["warehouse_tx"], conn)
    qty_total = 0.0
    for q in related["quantities"]:
        qty_total += float(q.get("qty") or 0) * float(q.get("unit_price") or 0)
    conn.close()
    return render_template(
        "reinforcement_work_view.html",
        work=work,
        related=related,
        qty_total=qty_total,
        voucher_groups=db.group_warehouse_txs_by_voucher(related["warehouse_tx"]),
        can_mutate=permissions.can("modules.write") and permissions.can("section.reinforcement"),
        can_warehouse=permissions.can("section.warehouses"),
        focus=(request.args.get("focus") or "").strip(),
        section="reinforcement",
        section_meta=_smeta(SECTION_META.get("reinforcement")),
        section_modules=modules_for_section("reinforcement"),
    )


@app.route("/warehouses")
@login_required
def warehouses_home():
    """توجيه للمستودعات → إجمالي الكميات (أول تبويب فرعي حقيقي)."""
    return redirect(url_for("warehouse_movements_summary"))


@app.route("/warehouses/summary")
@login_required
def warehouse_movements_summary():
    """صفحة إجمالي كميات الوارد والمنصرف والمتبقي بدون تفصيل الحركات."""
    db.backfill_warehouse_tx_sources()
    source = (request.args.get("source") or "").strip().lower()
    if source not in ("", "ops", "constructions", "projects", "external", "custody", "contractors", "reinforcement"):
        source = ""
    totals = db.warehouse_movements_totals(source or None)
    by_source = db.warehouse_movements_totals_by_source()
    summary_cards = [
        _summary_card(
            _t("إجمالي الكمية الواردة"),
            f"{float(totals.get('inbound') or 0):.2f}",
            _t("مجموع حركات الوارد"),
        ),
        _summary_card(
            _t("إجمالي الكمية المنصرفة"),
            f"{float(totals.get('outbound') or 0):.2f}",
            _t("مجموع حركات المنصرف / الإرجاع"),
        ),
        _summary_card(
            _t("المتبقي"),
            f"{float(totals.get('balance') or 0):.2f}",
            _t("الوارد − المنصرف"),
        ),
        _summary_card(
            _t("عدد الحركات"),
            totals.get("tx_count") or 0,
            _t("سجل حركة في الفلتر الحالي"),
        ),
    ]
    return render_template(
        "warehouse_movements_summary.html",
        totals=totals,
        by_source=by_source,
        source_filter=source,
        warehouse_active="summary",
        summary_cards=summary_cards,
    )


def _warehouse_tx_count_map(source: str, conn) -> dict:
    """خريطة مرجع المعاملة → عدد حركات المستودع."""
    rows = conn.execute(
        """
        SELECT coalesce(nullif(trim(source_ref),''), nullif(trim(ticket_no),''), '') AS ref, COUNT(*) AS n
        FROM warehouse_tx
        WHERE lower(coalesce(source_section,''))=?
        GROUP BY 1
        """,
        (source,),
    ).fetchall()
    out = {}
    for r in rows:
        ref = (r["ref"] if hasattr(r, "keys") else r[0]) or ""
        n = r["n"] if hasattr(r, "keys") else r[1]
        if ref:
            out[str(ref)] = int(n or 0)
    return out


def _warehouse_specialty_page(source: str, active: str, title: str, subtitle: str, list_endpoint: str):
    db.backfill_warehouse_tx_sources()
    db.ensure_schema()
    view = (request.args.get("view") or "").strip().lower()
    # التبويبات الداخلية حسب التخصص (أعطال / الفرق الأولية / حركات)
    if view == "work_orders":
        view = "teams"
    if source == "ops" and view not in ("tickets", "teams", "reinforcement", "movements"):
        view = "tickets"
    if source == "constructions" and view not in ("works", "movements"):
        view = "works"
    if source == "projects" and view not in ("projects", "movements"):
        view = "projects"
    if source == "reinforcement" and view not in ("works", "movements"):
        view = "works"

    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    department_filter = (request.args.get("department") or "").strip()
    conn = db.connect()
    tx_count = db.count_warehouse_tx_by_source(source, conn)
    tx_rows = []
    rows = []
    reinforcement_departments = []
    if view == "reinforcement" or source == "reinforcement":
        reinforcement_departments = db.list_reinforcement_departments(active_only=False, conn=conn)

    if view == "movements":
        tx_rows = db.rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM warehouse_tx
                WHERE lower(coalesce(source_section,''))=?
                ORDER BY id DESC
                """,
                (source,),
            ).fetchall()
        )
        db.enrich_warehouse_txs_work_order(tx_rows, conn)
        if q:
            ql = q.lower()
            tx_rows = [
                r
                for r in tx_rows
                if ql in (r.get("voucher_no") or "").lower()
                or ql in (r.get("item_no") or "").lower()
                or ql in (r.get("item_name") or "").lower()
                or ql in (r.get("source_ref") or "").lower()
                or ql in (r.get("ticket_no") or "").lower()
                or ql in (r.get("work_order") or "").lower()
            ]
    elif view == "teams":
        # الفرق الأولية = أوامر عمل الكهرباء (منفصلة تماماً عن الأعطال و tickets.team)
        rows = db.rows_to_dicts(
            conn.execute("SELECT * FROM primary_team_orders ORDER BY id DESC").fetchall()
        )
        if q:
            ql = q.lower()
            rows = [
                r
                for r in rows
                if ql in (r.get("work_order") or "").lower()
                or ql in (r.get("extract_no") or "").lower()
                or ql in (str(r.get("amount") or "")).lower()
                or ql in (r.get("notes") or "").lower()
            ]
        cmap = _warehouse_tx_count_map("ops", conn)
        for r in rows:
            r["wh_count"] = cmap.get(str(r.get("work_order") or ""), 0)
    elif view == "tickets":
        sql = "SELECT * FROM tickets WHERE 1=1"
        params = []
        if q:
            sql += " AND (ticket_no LIKE ? OR rekaz_code LIKE ? OR work_order LIKE ? OR district LIKE ? OR fault_type LIKE ? OR team LIKE ?)"
            like = f"%{q}%"
            params.extend([like, like, like, like, like, like])
        if status:
            status = db.normalize_ticket_status(status)
            if status == "تم الإسناد":
                sql += " AND status IN (?, ?)"
                params.extend(["تم الإسناد", "جديد"])
            else:
                sql += " AND status=?"
                params.append(status)
        sql += " ORDER BY id DESC"
        rows = db.rows_to_dicts(conn.execute(sql, params).fetchall())
        cmap = _warehouse_tx_count_map("ops", conn)
        for r in rows:
            r["status"] = db.normalize_ticket_status(r.get("status"))
            r["wh_count"] = cmap.get(str(r.get("ticket_no") or ""), 0)
    elif view == "reinforcement":
        sql = "SELECT * FROM reinforcement_works WHERE 1=1"
        params = []
        if department_filter:
            sql += " AND department=?"
            params.append(department_filter)
        if q:
            sql += " AND (work_no LIKE ? OR department LIKE ? OR location LIKE ? OR work_type LIKE ? OR ticket_no LIKE ? OR status LIKE ?)"
            like = f"%{q}%"
            params.extend([like, like, like, like, like, like])
        sql += " ORDER BY id DESC"
        rows = db.rows_to_dicts(conn.execute(sql, params).fetchall())
        cmap = _warehouse_tx_count_map("reinforcement", conn)
        for r in rows:
            r["wh_count"] = cmap.get(str(r.get("work_no") or ""), 0)
    elif view in ("works", "reinforcement"):
        table = "reinforcement_works" if source == "reinforcement" else "construction_works"
        rows = db.rows_to_dicts(conn.execute(f"SELECT * FROM {table} ORDER BY id DESC").fetchall())
        if q:
            ql = q.lower()
            rows = [
                r
                for r in rows
                if ql in (r.get("work_no") or "").lower()
                or ql in (r.get("department") or "").lower()
                or ql in (r.get("location") or "").lower()
                or ql in (r.get("site") or "").lower()
                or ql in (r.get("work_type") or "").lower()
            ]
        cmap = _warehouse_tx_count_map(source, conn)
        for r in rows:
            r["wh_count"] = cmap.get(str(r.get("work_no") or ""), 0)
    elif view == "projects":
        rows = db.rows_to_dicts(conn.execute("SELECT * FROM projects ORDER BY id DESC").fetchall())
        if q:
            ql = q.lower()
            rows = [
                r
                for r in rows
                if ql in (r.get("project_code") or "").lower()
                or ql in (r.get("project_name") or "").lower()
                or ql in (r.get("ticket_no") or "").lower()
            ]
        cmap = _warehouse_tx_count_map("projects", conn)
        for r in rows:
            r["wh_count"] = cmap.get(str(r.get("project_code") or ""), 0)
    conn.close()

    summary_cards = []
    if view == "tickets":
        summary_cards = [
            _summary_card(_t("عدد الأعطال"), len(rows), _t("حسب الفلتر الحالي")),
            _summary_card(
                _t("حركات المواد"),
                sum(int(r.get("wh_count") or 0) for r in rows),
                _t("مرتبطة بالأعطال المعروضة"),
            ),
            _summary_card(
                _t("آخر عطل"),
                ((_latest_row(rows, "receive_date") or {}).get("ticket_no") or "—"),
                _t("تفاصيل أحدث عطل"),
            ),
            _summary_card(
                _t("تاريخ آخر عطل"),
                ((_latest_row(rows, "receive_date") or {}).get("receive_date") or "—"),
                _t("أحدث تاريخ استلام"),
            ),
        ]
    elif view == "teams":
        summary_cards = build_list_summary_cards(
            rows,
            count_label=_t("عدد الأوامر"),
            money_keys=("amount",),
            date_keys=("order_date",),
            detail_key="work_order",
        )
    elif view in ("works", "reinforcement"):
        summary_cards = build_list_summary_cards(
            rows,
            count_label=_t("عدد المعاملات"),
            money_keys=("value",),
            date_keys=("work_date",),
            detail_key="work_no",
        )
        if view == "reinforcement":
            active_depts = sum(
                1
                for d in reinforcement_departments
                if (d.get("status") or "") not in ("موقوف", "لا")
            )
            summary_cards = [
                _summary_card(_t("عدد الأقسام"), len(reinforcement_departments), _t("أقسام التعزيز")),
                _summary_card(_t("أقسام نشطة"), active_depts, _t("جاهزة لحركات المستودع")),
                _summary_card(_t("عدد المعاملات"), len(rows), _t("حسب الفلتر الحالي")),
                _summary_card(
                    _t("حركات المواد"),
                    sum(int(r.get("wh_count") or 0) for r in rows),
                    _t("مرتبطة بمعاملات التعزيز المعروضة"),
                ),
                _summary_card(
                    _t("آخر معاملة"),
                    ((_latest_row(rows, "work_date") or {}).get("work_no") or "—"),
                    ((_latest_row(rows, "work_date") or {}).get("department") or _t("تفاصيل أحدث حركة")),
                ),
                _summary_card(
                    _t("تاريخ آخر حركة"),
                    ((_latest_row(rows, "work_date") or {}).get("work_date") or "—"),
                    _t("أحدث تاريخ في القائمة"),
                ),
            ]
    elif view == "projects":
        summary_cards = build_list_summary_cards(
            rows,
            count_label=_t("عدد المشاريع"),
            money_keys=(),
            date_keys=("start_date", "end_date"),
            detail_key="project_code",
        )
    elif view == "movements":
        summary_cards = [
            _summary_card(_t("عدد الحركات"), len(tx_rows), _t("حسب الفلتر الحالي")),
            _summary_card(
                _t("إجمالي الكميات"),
                f"{sum(float(r.get('qty') or 0) for r in tx_rows):.2f}",
                _t("مجموع كميات الحركات المعروضة"),
            ),
            _summary_card(
                _t("آخر سجل"),
                ((_latest_row(tx_rows, "tx_date") or {}).get("voucher_no") or "—"),
                _t("تفاصيل أحدث حركة"),
            ),
            _summary_card(
                _t("تاريخ آخر حركة"),
                ((_latest_row(tx_rows, "tx_date") or {}).get("tx_date") or "—"),
                _t("أحدث تاريخ في القائمة"),
            ),
        ]

    return render_template(
        "warehouse_specialty.html",
        active=active,
        source=source,
        title=title,
        subtitle=subtitle,
        view=view,
        q=q,
        status=status,
        department_filter=department_filter,
        reinforcement_departments=reinforcement_departments,
        rows=rows,
        tx_rows=tx_rows,
        tx_count=tx_count,
        list_endpoint=list_endpoint,
        summary_cards=summary_cards,
        wh_from={
            "ops": "wh_ops",
            "constructions": "wh_constructions",
            "projects": "wh_projects",
            "reinforcement": "wh_reinforcement",
        }.get(source, "warehouses"),
    )


def _warehouse_specialty_pdf_payload(source: str):
    db.backfill_warehouse_tx_sources()
    db.ensure_schema()
    source = (source or "").strip().lower()
    view = (request.args.get("view") or "").strip().lower()
    if view == "work_orders":
        view = "teams"
    if source == "ops" and view not in ("tickets", "teams", "reinforcement", "movements"):
        view = "tickets"
    if source == "constructions" and view not in ("works", "movements"):
        view = "works"
    if source == "projects" and view not in ("projects", "movements"):
        view = "projects"
    if source not in ("ops", "constructions", "projects"):
        abort(404)

    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    department_filter = (request.args.get("department") or "").strip()
    conn = db.connect()
    try:
        rows = []
        if view == "movements":
            rows = db.rows_to_dicts(
                conn.execute(
                    """
                    SELECT * FROM warehouse_tx
                    WHERE lower(coalesce(source_section,''))=?
                    ORDER BY id DESC
                    """,
                    (source,),
                ).fetchall()
            )
            db.enrich_warehouse_txs_work_order(rows, conn)
            if q:
                ql = q.lower()
                rows = [
                    r
                    for r in rows
                    if ql in (r.get("voucher_no") or "").lower()
                    or ql in (r.get("item_no") or "").lower()
                    or ql in (r.get("item_name") or "").lower()
                    or ql in (r.get("source_ref") or "").lower()
                    or ql in (r.get("ticket_no") or "").lower()
                    or ql in (r.get("work_order") or "").lower()
                ]
            headers = [_t("السند"), _t("التاريخ"), _t("النوع"), _t("رقم أمر العمل"), _t("المادة"), _t("الكمية"), _t("رقم العطل"), _t("المستلم"), _t("المسلم")]
            fields = ["voucher_no", "tx_date", "tx_type", "work_order", "item_name", "qty", "ticket_no", "recipient", "sender"]
            title = _t("حركات المواد")
        elif view == "teams":
            rows = db.rows_to_dicts(conn.execute("SELECT * FROM primary_team_orders ORDER BY id DESC").fetchall())
            if q:
                ql = q.lower()
                rows = [
                    r
                    for r in rows
                    if ql in (r.get("work_order") or "").lower()
                    or ql in (r.get("extract_no") or "").lower()
                    or ql in (str(r.get("amount") or "")).lower()
                    or ql in (r.get("notes") or "").lower()
                ]
            headers = [_t("أمر العمل"), _t("رقم المستخلص"), _t("المبلغ"), _t("التاريخ"), _t("ملاحظات")]
            fields = ["work_order", "extract_no", "amount", "order_date", "notes"]
            title = _t("الفرق الأولية")
        elif view == "tickets":
            sql = "SELECT * FROM tickets WHERE 1=1"
            params = []
            if q:
                sql += " AND (ticket_no LIKE ? OR rekaz_code LIKE ? OR work_order LIKE ? OR district LIKE ? OR fault_type LIKE ? OR team LIKE ?)"
                like = f"%{q}%"
                params.extend([like, like, like, like, like, like])
            if status:
                status = db.normalize_ticket_status(status)
                if status == "تم الإسناد":
                    sql += " AND status IN (?, ?)"
                    params.extend(["تم الإسناد", "جديد"])
                else:
                    sql += " AND status=?"
                    params.append(status)
            sql += " ORDER BY id DESC"
            rows = db.rows_to_dicts(conn.execute(sql, params).fetchall())
            for r in rows:
                r["status"] = db.normalize_ticket_status(r.get("status"))
            headers = [_t("رقم العطل"), _t("كود ER"), _t("رقم أمر العمل"), _t("التاريخ"), _t("الحي"), _t("العطل"), _t("الفرقة"), _t("الحالة")]
            fields = ["ticket_no", "rekaz_code", "work_order", "receive_date", "district", "fault_type", "team", "status"]
            title = _t("الأعطال")
        elif view == "reinforcement":
            sql = "SELECT * FROM reinforcement_works WHERE 1=1"
            params = []
            if department_filter:
                sql += " AND department=?"
                params.append(department_filter)
            if q:
                sql += " AND (work_no LIKE ? OR department LIKE ? OR location LIKE ? OR work_type LIKE ? OR ticket_no LIKE ? OR status LIKE ?)"
                like = f"%{q}%"
                params.extend([like, like, like, like, like, like])
            sql += " ORDER BY id DESC"
            rows = db.rows_to_dicts(conn.execute(sql, params).fetchall())
            headers = [_t("رقم أمر العمل"), _t("التاريخ"), _t("القسم"), _t("نوع العمل"), _t("الموقع"), _t("رقم العطل"), _t("الحالة"), _t("القيمة")]
            fields = ["work_no", "work_date", "department", "work_type", "location", "ticket_no", "status", "value"]
            title = _t("التعزيز - اسكيمات")
        elif view == "works":
            rows = db.rows_to_dicts(conn.execute("SELECT * FROM construction_works ORDER BY id DESC").fetchall())
            if q:
                ql = q.lower()
                rows = [
                    r
                    for r in rows
                    if ql in (r.get("work_no") or "").lower()
                    or ql in (r.get("site") or "").lower()
                    or ql in (r.get("work_type") or "").lower()
                ]
            headers = [_t("رقم أمر العمل"), _t("التاريخ"), _t("الموقع"), _t("نوع العمل"), _t("الحالة"), _t("القيمة")]
            fields = ["work_no", "work_date", "site", "work_type", "status", "value"]
            title = _t("الإنشاءات")
        else:
            rows = db.rows_to_dicts(conn.execute("SELECT * FROM projects ORDER BY id DESC").fetchall())
            if q:
                ql = q.lower()
                rows = [
                    r
                    for r in rows
                    if ql in (r.get("project_code") or "").lower()
                    or ql in (r.get("project_name") or "").lower()
                    or ql in (r.get("ticket_no") or "").lower()
                ]
            headers = [_t("كود المشروع"), _t("اسم المشروع"), _t("النوع"), _t("الحالة"), _t("رقم العطل")]
            fields = ["project_code", "project_name", "project_type", "status", "ticket_no"]
            title = _t("المشاريع")
    finally:
        conn.close()
    filters = []
    if q:
        filters.append(f"{_t('بحث')}: {q}")
    if status:
        filters.append(f"{_t('الحالة')}: {status}")
    if department_filter:
        filters.append(f"{_t('القسم')}: {department_filter}")
    return {
        "title": f"{_t('المستودعات')} - {title}",
        "headers": headers,
        "fields": fields,
        "rows": rows,
        "filters": filters,
        "view": view,
        "amount_field": "amount" if view == "teams" else ("value" if view in ("reinforcement", "works") else ""),
    }


@app.route("/warehouses/<source>/export.pdf")
@login_required
def warehouse_specialty_pdf(source):
    payload = _warehouse_specialty_pdf_payload(source)
    data = reports_svc.build_table_pdf(
        title_text=payload["title"],
        headers=payload["headers"],
        rows=payload["rows"],
        field_keys=payload["fields"],
        filters=payload["filters"],
        amount_cards=(
            [
                {
                    "title": _t("إجمالي المبالغ"),
                    "value": _sum_money_field(payload["rows"], payload["amount_field"]),
                    "money": True,
                    "subtitle": _t("حسب الفلترة الحالية"),
                },
                *helpers.work_ratio_cards(base_amount=_sum_money_field(payload["rows"], payload["amount_field"])),
            ]
            if payload.get("amount_field") else None
        ),
    )
    stamp = datetime.now().strftime("%Y%m%d")
    suffix = "-مفلتر" if payload["filters"] else ""
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=f"warehouses-{source}-{payload['view']}{suffix}-{stamp}.pdf",
        mimetype="application/pdf",
    )


@app.route("/warehouses/constructions")
@login_required
def warehouse_constructions():
    return _warehouse_specialty_page(
        "constructions",
        "constructions",
        _t("الإنشاءات"),
        _t("عرض معاملات الإنشاءات داخل المستودع — بدون الانتقال للصفحة الرئيسية"),
        "warehouse_constructions",
    )


@app.route("/warehouses/ops")
@login_required
def warehouse_ops():
    return _warehouse_specialty_page(
        "ops",
        "ops",
        _t("العمليات والصيانة"),
        _t("عرض الأعطال والفرق الأولية (أوامر عمل الكهرباء) داخل المستودع — بدون الانتقال للصفحة الرئيسية"),
        "warehouse_ops",
    )


@app.route("/warehouses/projects")
@login_required
def warehouse_projects():
    return _warehouse_specialty_page(
        "projects",
        "projects",
        _t("المشاريع"),
        _t("عرض المشاريع داخل المستودع — بدون الانتقال للصفحة الرئيسية"),
        "warehouse_projects",
    )


@app.route("/warehouses/reinforcement")
@login_required
def warehouse_reinforcement():
    return redirect(url_for("warehouse_ops", view="reinforcement"))
    # Legacy route kept for old links; the visible tab now lives under operations.
    return _warehouse_specialty_page(
        "reinforcement",
        "reinforcement",
        _t("التعزيز - اسكيمات"),
        _t("عرض معاملات التعزيز/الاسكيمات داخل المستودع مع ربط تلقائي برقم المعاملة."),
        "warehouse_reinforcement",
    )


def _warehouse_tx_option_urls(form_from: str, *, ticket_no: str = "", source_ref: str = "", source: str = "ops") -> dict:
    """روابط خيارات وارد / صرف / إرجاع لسياق المستودع."""
    base = {"from": form_from}
    if ticket_no:
        base["ticket_no"] = ticket_no
    if source_ref:
        base["source_ref"] = source_ref
    src = (source or "ops").strip().lower()
    if src == "ops":
        in_type, out_type, ret_type = "وارد من الكهرباء", "منصرف للمعاملة", "إرجاع للكهرباء"
    else:
        in_type, out_type, ret_type = "وارد من موقع العمل", "منصرف للمعاملة", "إرجاع للمجمعة"
    return {
        "in_url": url_for("module_new", name="warehouse_tx", tx_type=in_type, **base),
        "out_url": url_for("module_new", name="warehouse_tx", tx_type=out_type, **base),
        "return_url": url_for("module_new", name="warehouse_tx", tx_type=ret_type, **base),
    }


@app.route("/warehouses/ops/ticket/<int:ticket_id>")
@login_required
def warehouse_ticket_detail(ticket_id):
    """تفاصيل عطل للعرض داخل المستودع فقط (لا يفتح تبويب العمليات)."""
    conn = db.connect()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close()
        flash(_t("العطل غير موجود"), "danger")
        return redirect(url_for("warehouse_ops", view="tickets"))
    ticket = dict(row)
    tno = ticket.get("ticket_no") or ""
    txs = db.rows_to_dicts(
        conn.execute(
            """
            SELECT * FROM warehouse_tx
            WHERE ticket_no=? OR (source_ref<>'' AND source_ref=?)
               OR (rekaz_code<>'' AND lower(rekaz_code)=lower(?))
            ORDER BY id DESC
            """,
            (tno, tno, ticket.get("rekaz_code") or ""),
        ).fetchall()
    )
    conn.close()
    opts = _warehouse_tx_option_urls("wh_ops", ticket_no=tno, source="ops") if tno else {}
    return render_template(
        "warehouse_record_detail.html",
        warehouse_active="ops",
        kind="ticket",
        title=_t("تفاصيل العطل"),
        record=ticket,
        txs=txs,
        voucher_groups=db.group_warehouse_txs_by_voucher(txs),
        back_url=url_for("warehouse_ops", view="tickets"),
        issue_url=opts.get("out_url"),
        **opts,
    )


@app.route("/warehouses/ops/primary-team/<int:row_id>")
@login_required
def warehouse_primary_team_detail(row_id):
    """تفاصيل أمر عمل الفرق الأولية (كهرباء) داخل المستودع فقط."""
    conn = db.connect()
    row = conn.execute("SELECT * FROM primary_team_orders WHERE id=?", (row_id,)).fetchone()
    if not row:
        conn.close()
        flash(_t("أمر العمل غير موجود"), "danger")
        return redirect(url_for("warehouse_ops", view="teams"))
    order = dict(row)
    ref = (order.get("work_order") or "").strip()
    txs = (
        db.rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM warehouse_tx
                WHERE source_ref=? OR (source_section='ops' AND source_ref=?)
                ORDER BY id DESC
                """,
                (ref, ref),
            ).fetchall()
        )
        if ref
        else []
    )
    conn.close()
    opts = _warehouse_tx_option_urls("wh_ops", source_ref=ref, source="ops") if ref else {}
    return render_template(
        "warehouse_record_detail.html",
        warehouse_active="ops",
        kind="primary_team",
        title=_t("تفاصيل الفرق الأولية"),
        record=order,
        txs=txs,
        voucher_groups=db.group_warehouse_txs_by_voucher(txs),
        back_url=url_for("warehouse_ops", view="teams"),
        issue_url=opts.get("out_url"),
        **opts,
    )


@app.route("/warehouses/constructions/<int:row_id>")
@login_required
def warehouse_construction_detail(row_id):
    conn = db.connect()
    row = conn.execute("SELECT * FROM construction_works WHERE id=?", (row_id,)).fetchone()
    if not row:
        conn.close()
        flash(_t("المعاملة غير موجودة"), "danger")
        return redirect(url_for("warehouse_constructions"))
    work = dict(row)
    ref = work.get("work_no") or ""
    txs = db.rows_to_dicts(
        conn.execute(
            """
            SELECT * FROM warehouse_tx
            WHERE source_ref=? OR (source_section='constructions' AND source_ref=?)
            ORDER BY id DESC
            """,
            (ref, ref),
        ).fetchall()
    ) if ref else []
    conn.close()
    opts = _warehouse_tx_option_urls("wh_constructions", source_ref=ref, source="constructions") if ref else {}
    return render_template(
        "warehouse_record_detail.html",
        warehouse_active="constructions",
        kind="construction",
        title=_t("تفاصيل معاملة الإنشاءات"),
        record=work,
        txs=txs,
        voucher_groups=db.group_warehouse_txs_by_voucher(txs),
        back_url=url_for("warehouse_constructions", view="works"),
        issue_url=opts.get("out_url"),
        **opts,
    )


@app.route("/warehouses/projects/<int:row_id>")
@login_required
def warehouse_project_detail(row_id):
    conn = db.connect()
    row = conn.execute("SELECT * FROM projects WHERE id=?", (row_id,)).fetchone()
    if not row:
        conn.close()
        flash(_t("المشروع غير موجود"), "danger")
        return redirect(url_for("warehouse_projects"))
    project = dict(row)
    ref = project.get("project_code") or ""
    txs = db.rows_to_dicts(
        conn.execute(
            """
            SELECT * FROM warehouse_tx
            WHERE source_ref=? OR (source_section='projects' AND source_ref=?)
            ORDER BY id DESC
            """,
            (ref, ref),
        ).fetchall()
    ) if ref else []
    conn.close()
    opts = _warehouse_tx_option_urls("wh_projects", source_ref=ref, source="projects") if ref else {}
    return render_template(
        "warehouse_record_detail.html",
        warehouse_active="projects",
        kind="project",
        title=_t("تفاصيل المشروع"),
        record=project,
        txs=txs,
        voucher_groups=db.group_warehouse_txs_by_voucher(txs),
        back_url=url_for("warehouse_projects", view="projects"),
        issue_url=opts.get("out_url"),
        **opts,
    )


@app.route("/warehouses/voucher/<path:voucher_no>")
@login_required
def warehouse_voucher_detail(voucher_no):
    """عرض المعاملة (سند المستودع) مع مواد الوارد/المنصرف/الإرجاع — لجميع التخصصات."""
    voucher_no = (voucher_no or "").strip()
    mat_view = (request.args.get("mat") or "all").strip().lower()
    if mat_view not in ("in", "out", "return", "all"):
        mat_view = "all"
    lines = db.get_warehouse_voucher_lines(voucher_no)
    if not lines:
        flash(_t("المعاملة غير موجودة"), "danger")
        return redirect(url_for("warehouse_movements_summary"))

    for ln in lines:
        try:
            ln["item_balance"] = db.warehouse_balance(ln.get("item_no") or "")
        except Exception:
            ln["item_balance"] = None
        t = ln.get("tx_type") or ""
        ln["is_return"] = "إرجاع" in t

    head = lines[0]
    parent = db.resolve_warehouse_parent(head)
    work_order = db.resolve_tx_work_order(head) or (parent.get("work_order") or "")
    section = (head.get("source_section") or "").strip().lower()
    warehouse_active = {
        "ops": "ops",
        "constructions": "constructions",
        "projects": "projects",
        "reinforcement": "ops",
    }.get(section, "summary")

    inbound = [r for r in lines if r.get("sign", 0) > 0]
    returns = [r for r in lines if r.get("is_return")]
    outbound_issue = [r for r in lines if r.get("sign", 0) < 0 and not r.get("is_return")]
    if mat_view == "in":
        shown = inbound
    elif mat_view == "out":
        shown = outbound_issue
    elif mat_view == "return":
        shown = returns
    else:
        shown = lines

    qty_in = sum(float(r.get("qty") or 0) for r in inbound)
    qty_out_issue = sum(float(r.get("qty") or 0) for r in outbound_issue)
    qty_return = sum(float(r.get("qty") or 0) for r in returns)
    qty_total = sum(float(r.get("qty") or 0) for r in lines)
    tx_types = []
    for r in lines:
        t = r.get("tx_type") or ""
        if t and t not in tx_types:
            tx_types.append(t)

    back_url = url_for("warehouse_movements_summary")
    if section == "ops":
        back_url = url_for("warehouse_ops", view="movements")
    elif section == "constructions":
        back_url = url_for("warehouse_constructions", view="movements")
    elif section == "projects":
        back_url = url_for("warehouse_projects", view="movements")
    elif section == "reinforcement":
        ref = (head.get("source_ref") or head.get("work_order") or "").strip()
        work = _reinforcement_work_for_ref(ref)
        back_url = (
            url_for("reinforcement_work_view", row_id=work["id"]) + "#section-warehouse"
            if work
            else url_for("warehouse_movements_summary", source="reinforcement")
        )

    form_from = {
        "ops": "wh_ops",
        "constructions": "wh_constructions",
        "projects": "wh_projects",
        "reinforcement": "wh_reinforcement",
    }.get(section, "warehouses")

    base_args = {"from": form_from, "voucher_no": voucher_no, "reuse_voucher": "1"}
    if head.get("ticket_no"):
        base_args["ticket_no"] = head.get("ticket_no")
    if head.get("source_ref"):
        base_args["source_ref"] = head.get("source_ref")

    return render_template(
        "warehouse_voucher_detail.html",
        warehouse_active=warehouse_active,
        voucher_no=voucher_no,
        head=head,
        parent=parent,
        work_order=work_order,
        lines=lines,
        shown=shown,
        mat_view=mat_view,
        inbound_count=len(inbound),
        outbound_issue_count=len(outbound_issue),
        return_count=len(returns),
        qty_in=qty_in,
        qty_out_issue=qty_out_issue,
        qty_return=qty_return,
        qty_total=qty_total,
        tx_types=tx_types,
        back_url=back_url,
        form_from=form_from,
        in_url=url_for("module_new", name="warehouse_tx", tx_type="وارد من الكهرباء", **base_args),
        out_url=url_for("module_new", name="warehouse_tx", tx_type="منصرف للمعاملة", **base_args),
        return_url=url_for("module_new", name="warehouse_tx", tx_type="إرجاع للكهرباء", **base_args),
        parent_url=_warehouse_parent_url(parent),
    )


def _warehouse_mirror_tx_type(tx_type: str, source: str = "") -> str | None:
    """نوع الحركة المقابل للنسخ التلقائي (وارد↔منصرف) — بدون الإرجاع."""
    t = (tx_type or "").strip()
    if not t or "إرجاع" in t:
        return None
    if "وارد" in t or "افتتاح" in t:
        return "منصرف للمعاملة"
    if "منصرف" in t:
        if (source or "").strip().lower() == "ops":
            return "وارد من الكهرباء"
        return "وارد من موقع العمل"
    return None


def _insert_warehouse_mirror_zeros(conn, prepared_rows: list[dict], form_ctx: str = "") -> int:
    """ينشئ سطور صفرية في الاتجاه المقابل لكل مادة مُضافة (إن لم تكن موجودة)."""
    if not prepared_rows:
        return 0
    keys = [f[0] for f in MODULES["warehouse_tx"]["fields"]]
    fallback_source = _warehouse_source_from_ctx(form_ctx) if form_ctx else ""
    mirrored = 0
    for data in prepared_rows:
        source = (data.get("source_section") or "").strip() or fallback_source
        opposite = _warehouse_mirror_tx_type(data.get("tx_type") or "", source)
        if not opposite:
            continue
        voucher = (data.get("voucher_no") or "").strip()
        item_no = (data.get("item_no") or "").strip()
        if not voucher or not item_no:
            continue
        exists = conn.execute(
            """
            SELECT id FROM warehouse_tx
            WHERE voucher_no=? AND lower(item_no)=lower(?) AND tx_type=?
            LIMIT 1
            """,
            (voucher, item_no, opposite),
        ).fetchone()
        if exists:
            continue
        mirror = dict(data)
        mirror["tx_type"] = opposite
        mirror["qty"] = 0
        if not (mirror.get("source_section") or "").strip() and source:
            mirror["source_section"] = source
        # لا نعيد اشتراط الربط عبر التحضير لكمية صفرية؛ ننسخ الحقول كما هي
        mirror["notes"] = (mirror.get("notes") or "").strip()
        auto_note = "نسخ تلقائي — كمية صفرية"
        if auto_note not in (mirror["notes"] or ""):
            mirror["notes"] = f"{mirror['notes']} | {auto_note}".strip(" |") if mirror["notes"] else auto_note
        conn.execute(
            f"INSERT INTO warehouse_tx({', '.join(keys)}) VALUES ({', '.join(['?']*len(keys))})",
            [mirror.get(k) for k in keys],
        )
        mirrored += 1
    return mirrored


@app.route("/warehouses/tx/multi", methods=["GET", "POST"])
@login_required
def warehouse_tx_multi():
    """إدخال وارد/صرف/إرجاع متعدد المواد في سند واحد."""
    form_ctx = _warehouse_form_ctx()
    if form_ctx not in _warehouse_create_contexts():
        flash(
            _t(
                "إدخال معاملات المستودع يتم من صفحات المستودعات فقط، والصفحات الرئيسية للعرض فقط."
            ),
            "danger",
        )
        return redirect(url_for("warehouses_home"))

    source = _warehouse_source_from_ctx(form_ctx)
    cancel_url = {
        "wh_ops": url_for("warehouse_ops"),
        "ops": url_for("warehouse_ops"),
        "wh_constructions": url_for("warehouse_constructions"),
        "constructions": url_for("warehouse_constructions"),
        "wh_projects": url_for("warehouse_projects"),
        "projects": url_for("warehouse_projects"),
        "wh_reinforcement": url_for("warehouse_ops", view="reinforcement"),
        "reinforcement": url_for("module_list", name="reinforcement_works"),
    }.get(form_ctx, url_for("warehouses_home"))

    conn = db.connect()
    ticket_options = db.list_ticket_options(conn)
    warehouse_items = db.list_warehouse_items()

    header = {
        "voucher_no": "",
        "tx_date": datetime.now().strftime("%Y-%m-%d"),
        "tx_type": (request.form.get("tx_type") or request.args.get("tx_type") or "").strip() or "منصرف للمعاملة",
        "recipient": "",
        "sender": "",
        "ticket_no": (request.form.get("ticket_no") or request.args.get("ticket_no") or "").strip(),
        "rekaz_code": "",
        "source_section": source,
        "source_ref": (request.form.get("source_ref") or request.args.get("source_ref") or "").strip(),
        "work_order": (request.form.get("work_order") or request.args.get("work_order") or "").strip(),
        "region": "",
        "notes": "",
    }
    if header["ticket_no"]:
        ticket = db.resolve_ticket_ref(header["ticket_no"], conn)
        if ticket:
            header["rekaz_code"] = ticket.get("rekaz_code") or ""
            if not header["work_order"]:
                header["work_order"] = (ticket.get("work_order") or "").strip()
        if not header["source_ref"] and source == "ops":
            header["source_ref"] = header["ticket_no"]
    # لا تنسخ رقم العطل إلى أمر العمل — فقط المرجع غير العطل (فرق/إنشاءات/مشاريع)
    if not header["work_order"] and header["source_ref"]:
        if header["source_ref"] != (header.get("ticket_no") or "").strip():
            header["work_order"] = header["source_ref"]
    header = db.apply_warehouse_tx_work_order(header, conn)

    reuse = str(request.form.get("reuse_voucher") or request.args.get("reuse_voucher") or "").strip() in {"1", "on", "yes", "true"}
    existing_voucher = (request.form.get("voucher_no") or request.args.get("voucher_no") or "").strip()
    if reuse and existing_voucher:
        header["voucher_no"] = existing_voucher
        prev = conn.execute(
            "SELECT * FROM warehouse_tx WHERE voucher_no=? ORDER BY id LIMIT 1",
            (existing_voucher,),
        ).fetchone()
        if prev:
            prev = dict(prev)
            for k in (
                "tx_date",
                "tx_type",
                "ticket_no",
                "rekaz_code",
                "source_section",
                "source_ref",
                "work_order",
                "region",
                "recipient",
                "sender",
                "notes",
            ):
                if not (request.form.get(k) or "").strip():
                    if prev.get(k) not in (None, "") and not (header.get(k) or "").strip():
                        header[k] = prev.get(k)
            if request.form.get("tx_type") or request.args.get("tx_type"):
                header["tx_type"] = (request.form.get("tx_type") or request.args.get("tx_type") or "").strip()
    else:
        header["voucher_no"] = db.next_warehouse_voucher_no(conn)

    lines = [{"item_no": "", "item_name": "", "unit": "", "qty": "", "line_recipient": ""}]

    if request.method == "POST":
        if not permissions.can("modules.write"):
            conn.close()
            flash(_t("لا تملك صلاحية الإضافة."), "danger")
            return redirect(cancel_url)
        header.update(
            {
                "tx_date": (request.form.get("tx_date") or "").strip(),
                "tx_type": (request.form.get("tx_type") or "").strip(),
                "recipient": (request.form.get("recipient") or "").strip(),
                "sender": (request.form.get("sender") or "").strip(),
                "ticket_no": (request.form.get("ticket_no") or "").strip(),
                "rekaz_code": (request.form.get("rekaz_code") or "").strip(),
                "source_section": source,
                "source_ref": (request.form.get("source_ref") or "").strip(),
                "work_order": (request.form.get("work_order") or "").strip(),
                "region": (request.form.get("region") or "").strip(),
                "notes": (request.form.get("notes") or "").strip(),
            }
        )
        if not header["source_ref"] and source == "ops" and header["ticket_no"]:
            header["source_ref"] = header["ticket_no"]
        if not header["work_order"] and header["source_ref"]:
            if header["source_ref"] != (header.get("ticket_no") or "").strip():
                header["work_order"] = header["source_ref"]
        header = db.apply_warehouse_tx_work_order(header, conn)
        if reuse and existing_voucher:
            header["voucher_no"] = existing_voucher # Keep existing voucher on reuse
        else:
            # لا تعيد استخدام سند موجود إلا عند reuse صريح
            voucher = (request.form.get("voucher_no") or "").strip() or header["voucher_no"]
            if not voucher or (
                not reuse
                and conn.execute(
                    "SELECT 1 FROM warehouse_tx WHERE voucher_no=? LIMIT 1", (voucher,)
                ).fetchone()
            ):
                voucher = db.next_warehouse_voucher_no(conn)
            header["voucher_no"] = voucher

        item_nos = request.form.getlist("item_no[]")
        item_names = request.form.getlist("item_name[]")
        units = request.form.getlist("unit[]")
        qtys = request.form.getlist("qty[]")
        line_recipients = request.form.getlist("line_recipient[]")
        parsed = []
        for i, item_no in enumerate(item_nos):
            item_no = (item_no or "").strip()
            qty_raw = (qtys[i] if i < len(qtys) else "") or ""
            if not item_no and not str(qty_raw).strip():
                continue
            try:
                qty = float(qty_raw)
            except (TypeError, ValueError):
                qty = None
            parsed.append(
                {
                    "item_no": item_no,
                    "item_name": (item_names[i] if i < len(item_names) else "") or "",
                    "unit": (units[i] if i < len(units) else "") or "",
                    "qty": qty if qty is not None else "",
                    "line_recipient": (line_recipients[i] if i < len(line_recipients) else "")
                    or "",
                }
            )

        if not parsed:
            flash(_t("أضف مادة واحدة على الأقل"), "danger")
            lines = [{"item_no": "", "item_name": "", "unit": "", "qty": "", "line_recipient": ""}]
        else:
            errors = []
            prepared_rows = []
            for idx, line in enumerate(parsed, start=1):
                if not line["item_no"]:
                    errors.append(_t("السطر {n}: اختر المادة", n=idx))
                    continue
                if line["qty"] in ("", None) or float(line["qty"] or 0) <= 0:
                    errors.append(_t("السطر {n}: أدخل كمية صحيحة", n=idx))
                    continue
                data = {
                    "voucher_no": header["voucher_no"],
                    "tx_date": header["tx_date"],
                    "tx_type": header["tx_type"],
                    "item_no": line["item_no"],
                    "item_name": line["item_name"],
                    "unit": line["unit"],
                    "qty": line["qty"],
                    "recipient": line["line_recipient"] or header["recipient"],
                    "sender": header.get("sender") or "",
                    "ticket_no": header["ticket_no"],
                    "rekaz_code": header["rekaz_code"],
                    "source_section": header["source_section"],
                    "source_ref": header["source_ref"],
                    "work_order": header.get("work_order") or "",
                    "region": header["region"],
                    "notes": header["notes"],
                }
                prepared, err = _prepare_warehouse_tx_create(data, form_ctx, conn)
                if err:
                    errors.append(_t("السطر {n}: {err}", n=idx, err=err))
                else:
                    prepared_rows.append(prepared)

            if errors:
                flash(" | ".join(errors), "danger")
                lines = parsed or lines
            elif not prepared_rows:
                flash(_t("أضف مادة واحدة على الأقل"), "danger")
                lines = parsed or lines
            else:
                keys = [f[0] for f in MODULES["warehouse_tx"]["fields"]]
                for data in prepared_rows:
                    conn.execute(
                        f"INSERT INTO warehouse_tx({', '.join(keys)}) VALUES ({', '.join(['?']*len(keys))})",
                        [data.get(k) for k in keys],
                    )
                mirrored = _insert_warehouse_mirror_zeros(conn, prepared_rows, form_ctx)
                conn.commit()
                db.log_audit(
                    current_user_name(),
                    "إضافة",
                    "معاملات المستودع",
                    header["voucher_no"],
                    _t("{n} مواد", n=len(prepared_rows))
                    + (f" / {_t('{n} نسخ صفري', n=mirrored)}" if mirrored else ""),
                )
                conn.close()
                if mirrored:
                    flash(
                        _t(
                            "تم حفظ {n} مواد في السند {v} مع {m} سطراً صفرياً في الاتجاه المقابل",
                            n=len(prepared_rows),
                            v=header["voucher_no"],
                            m=mirrored,
                        ),
                        "ok",
                    )
                else:
                    flash(
                        _t("تم حفظ {n} مواد في السند {v}", n=len(prepared_rows), v=header["voucher_no"]),
                        "ok",
                    )
                _after_data_change()
                return redirect(url_for("warehouse_voucher_detail", voucher_no=header["voucher_no"]))

    conn.close()
    title_map = {
        "وارد من الكهرباء": _t("وارد متعدد"),
        "وارد من موقع العمل": _t("وارد متعدد"),
        "منصرف للمعاملة": _t("صرف متعدد"),
        "إرجاع للكهرباء": _t("إرجاع متعدد"),
        "إرجاع للمجمعة": _t("إرجاع متعدد"),
    }
    items_payload = [
        {
            "item_no": it.get("item_no") or "",
            "item_name": it.get("item_name") or "",
            "unit": it.get("unit") or "",
        }
        for it in (warehouse_items or [])
    ]
    initial_lines = [
        {
            "item_no": ln.get("item_no") or "",
            "item_name": ln.get("item_name") or "",
            "unit": ln.get("unit") or "",
            "qty": ln.get("qty") if ln.get("qty") not in ("", None) else "",
        }
        for ln in (lines or [])
        if (ln.get("item_no") or "").strip()
    ]
    return render_template(
        "warehouse_tx_multi.html",
        title=title_map.get(header.get("tx_type") or "", _t("حركة متعددة")),
        header=header,
        lines=lines,
        warehouse_items=warehouse_items,
        ticket_options=ticket_options,
        form_ctx=form_ctx,
        reuse_voucher=reuse,
        cancel_url=cancel_url,
        source_label=_warehouse_source_label(source),
        items_json=json.dumps(items_payload, ensure_ascii=False),
        initial_lines_json=json.dumps(initial_lines, ensure_ascii=False),
    )


def _delete_password_ok() -> bool:
    return helpers.delete_password_ok()


def _reject_bad_delete_password(fallback_url: str):
    return helpers.reject_bad_delete_password(fallback_url)


@app.route("/warehouses/tx/<int:row_id>/delete", methods=["POST"])
@login_required
def warehouse_tx_delete(row_id):
    """حذف سطر مادة من حركة مستودع مع الرجوع لعرض المعاملة أو القائمة."""
    if not permissions.can("modules.write"):
        flash(_t("لا تملك صلاحية الحذف."), "danger")
        return redirect(request.form.get("next") or url_for("warehouses_home"))
    if not _delete_password_ok():
        return _reject_bad_delete_password(url_for("warehouses_home"))
    conn = db.connect()
    row = conn.execute("SELECT * FROM warehouse_tx WHERE id=?", (row_id,)).fetchone()
    if not row:
        conn.close()
        flash(_t("السجل غير موجود"), "danger")
        return redirect(request.form.get("next") or url_for("warehouses_home"))
    voucher = (row["voucher_no"] or "").strip() if hasattr(row, "keys") else ""
    conn.execute("DELETE FROM warehouse_tx WHERE id=?", (row_id,))
    conn.commit()
    conn.close()
    db.log_audit(current_user_name(), "حذف", "معاملات المستودع", row_id, voucher)
    flash(_t("تم حذف مادة الحركة"), "ok")
    _after_data_change()
    nxt = (request.form.get("next") or "").strip()
    if nxt:
        return redirect(nxt)
    if voucher:
        # إن بقي السند افتحه، وإلا قائمة الحركات
        left = db.get_warehouse_voucher_lines(voucher)
        if left:
            return redirect(url_for("warehouse_voucher_detail", voucher_no=voucher))
    return redirect(url_for("warehouse_ops", view="movements"))


def _warehouse_parent_url(parent: dict):
    kind = (parent or {}).get("parent_kind")
    pid = (parent or {}).get("parent_id")
    if not kind or not pid:
        return None
    if kind == "ticket":
        return url_for("warehouse_ticket_detail", ticket_id=pid)
    if kind == "primary_team":
        return url_for("warehouse_primary_team_detail", row_id=pid)
    if kind == "construction":
        return url_for("warehouse_construction_detail", row_id=pid)
    if kind == "project":
        return url_for("warehouse_project_detail", row_id=pid)
    return None


@app.route("/external-purchases")
@login_required
def external_purchases_home():
    return _redirect_section_first_child("external")


@app.route("/module/custody/<int:row_id>/issue", methods=["POST"])
@login_required
def custody_issue_warehouse(row_id):
    if not permissions.can("section.external") or not permissions.can("modules.write"):
        return permissions.deny_redirect()
    try:
        result = db.issue_custody_to_warehouse(row_id)
        if result.get("already"):
            flash(_t("العهدة مصروفة مسبقاً بسند {no}", no=result.get("voucher_no")), "ok")
        else:
            flash(_t("تم صرف العهدة من المستودع بسند {no}", no=result.get("voucher_no")), "ok")
            db.log_audit(current_user_name(), "صرف عهدة من المستودع", "العهد", row_id, result.get("voucher_no") or "")
            _after_data_change()
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("module_edit", name="custody", row_id=row_id))


@app.route("/module/custody/<int:row_id>/return", methods=["POST"])
@login_required
def custody_return_warehouse(row_id):
    if not permissions.can("section.external") or not permissions.can("modules.write"):
        return permissions.deny_redirect()
    try:
        result = db.return_custody_to_warehouse(row_id)
        if result.get("already"):
            flash(_t("العهدة مرتجعة مسبقاً بسند {no}", no=result.get("voucher_no")), "ok")
        else:
            flash(_t("تم إرجاع العهدة للمستودع بسند {no}", no=result.get("voucher_no")), "ok")
            db.log_audit(current_user_name(), "إرجاع عهدة للمستودع", "العهد", row_id, result.get("voucher_no") or "")
            _after_data_change()
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("module_edit", name="custody", row_id=row_id))


@app.route("/module/custody/<int:row_id>/lines/add", methods=["POST"])
@login_required
def custody_line_add(row_id):
    if not permissions.can("section.external") or not permissions.can("modules.write"):
        return permissions.deny_redirect()
    source_type = (request.form.get("source_type") or "warehouse").strip()
    if source_type == "external":
        item_no = (request.form.get("external_item_no") or request.form.get("item_no") or "").strip()
        item_name = (request.form.get("external_item_name") or "").strip()
        unit = (request.form.get("external_unit") or "").strip()
    else:
        item_no = (request.form.get("item_no") or "").strip()
        item_name = ""
        unit = ""
    qty_raw = (request.form.get("qty") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    try:
        qty = float(qty_raw) if qty_raw != "" else 0.0
    except ValueError:
        flash(_t("الكمية غير صالحة"), "danger")
        return redirect(url_for("module_edit", name="custody", row_id=row_id))
    try:
        db.add_custody_line(row_id, item_no=item_no, item_name=item_name, unit=unit, qty=qty, notes=notes, source_type=source_type)
        flash(_t("تمت إضافة بند العهدة"), "ok")
        db.log_audit(current_user_name(), "إضافة بند عهدة", "العهد", row_id, f"{item_no} × {qty}")
        _after_data_change()
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("module_edit", name="custody", row_id=row_id))


@app.route("/module/custody/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def custody_line_delete(line_id):
    if not permissions.can("section.external") or not permissions.can("modules.write"):
        return permissions.deny_redirect()
    conn = db.connect()
    line = conn.execute("SELECT custody_id FROM custody_lines WHERE id=?", (line_id,)).fetchone()
    custody_id = line["custody_id"] if line else None
    conn.close()
    if not custody_id:
        flash(_t("السطر غير موجود"), "danger")
        return redirect(url_for("module_list", name="custody"))
    if not _delete_password_ok():
        return _reject_bad_delete_password(url_for("module_edit", name="custody", row_id=custody_id))
    try:
        db.delete_custody_line(line_id)
        flash(_t("تم حذف بند العهدة"), "ok")
        db.log_audit(current_user_name(), "حذف بند عهدة", "العهد", custody_id, str(line_id))
        _after_data_change()
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("module_edit", name="custody", row_id=custody_id))


@app.route("/module/external_purchases/<int:row_id>/lines/add", methods=["POST"])
@login_required
def purchase_line_add(row_id):
    if not permissions.can("section.external") or not permissions.can("modules.write"):
        return permissions.deny_redirect()
    item_no = (request.form.get("item_no") or "").strip()
    qty_raw = (request.form.get("qty") or "").strip()
    price_raw = (request.form.get("unit_price") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    try:
        qty = float(qty_raw) if qty_raw != "" else 0.0
    except ValueError:
        flash(_t("الكمية غير صالحة"), "danger")
        return redirect(url_for("module_edit", name="external_purchases", row_id=row_id))
    try:
        unit_price = float(price_raw) if price_raw != "" else 0.0
    except ValueError:
        unit_price = 0.0
    try:
        db.add_purchase_line(row_id, item_no=item_no, qty=qty, unit_price=unit_price, notes=notes)
        flash(_t("تمت إضافة الصنف"), "ok")
        db.log_audit(current_user_name(), "إضافة صنف شراء", "المشتريات الخارجية", row_id, f"{item_no} × {qty}")
        _after_data_change()
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("module_edit", name="external_purchases", row_id=row_id))


@app.route("/module/external_purchases/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def purchase_line_delete(line_id):
    if not permissions.can("section.external") or not permissions.can("modules.write"):
        return permissions.deny_redirect()
    conn = db.connect()
    line = conn.execute("SELECT purchase_id FROM external_purchase_lines WHERE id=?", (line_id,)).fetchone()
    purchase_id = line["purchase_id"] if line else None
    conn.close()
    if not purchase_id:
        flash(_t("السطر غير موجود"), "danger")
        return redirect(url_for("module_list", name="external_purchases"))
    if not _delete_password_ok():
        return _reject_bad_delete_password(url_for("module_edit", name="external_purchases", row_id=purchase_id))
    try:
        db.delete_purchase_line(line_id)
        flash(_t("تم حذف الصنف"), "ok")
        db.log_audit(current_user_name(), "حذف صنف شراء", "المشتريات الخارجية", purchase_id, str(line_id))
        _after_data_change()
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("module_edit", name="external_purchases", row_id=purchase_id))


@app.route("/module/external_purchases/<int:row_id>/receive", methods=["POST"])
@login_required
def purchase_receive_warehouse(row_id):
    if not permissions.can("section.external") or not permissions.can("modules.write"):
        return permissions.deny_redirect()
    try:
        result = db.receive_purchase_to_warehouse(row_id)
        if result.get("already"):
            flash(_t("الطلب مرحّل مسبقاً بسند {no}", no=result.get("voucher_no")), "ok")
        else:
            flash(
                _t(
                    "تم ترحيل {n} صنفاً للمستودع بسند {no}",
                    n=result.get("created") or 0,
                    no=result.get("voucher_no"),
                ),
                "ok",
            )
            db.log_audit(
                current_user_name(),
                "ترحيل شراء للمستودع",
                "المشتريات الخارجية",
                row_id,
                result.get("voucher_no") or "",
            )
            _after_data_change()
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("module_edit", name="external_purchases", row_id=row_id))


@app.route("/module/contractor_supplies/<int:row_id>/lines/add", methods=["POST"])
@login_required
def contractor_supply_line_add(row_id):
    if not permissions.can("section.contractors") or not permissions.can("modules.write"):
        return permissions.deny_redirect()
    item_no = (request.form.get("item_no") or "").strip()
    qty_raw = (request.form.get("qty") or "").strip()
    price_raw = (request.form.get("unit_price") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    try:
        qty = float(qty_raw) if qty_raw != "" else 0.0
    except ValueError:
        flash(_t("الكمية غير صالحة"), "danger")
        return redirect(url_for("module_edit", name="contractor_supplies", row_id=row_id))
    try:
        unit_price = float(price_raw) if price_raw != "" else 0.0
    except ValueError:
        unit_price = 0.0
    try:
        db.add_contractor_supply_line(row_id, item_no=item_no, qty=qty, unit_price=unit_price, notes=notes)
        flash(_t("تمت إضافة الصنف"), "ok")
        db.log_audit(current_user_name(), "إضافة مادة موردة", "مواد موردة من مقاول", row_id, f"{item_no} × {qty}")
        _after_data_change()
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("module_edit", name="contractor_supplies", row_id=row_id))


@app.route("/module/contractor_supplies/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def contractor_supply_line_delete(line_id):
    if not permissions.can("section.contractors") or not permissions.can("modules.write"):
        return permissions.deny_redirect()
    conn = db.connect()
    line = conn.execute("SELECT supply_id FROM contractor_supply_lines WHERE id=?", (line_id,)).fetchone()
    supply_id = line["supply_id"] if line else None
    conn.close()
    if not supply_id:
        flash(_t("السطر غير موجود"), "danger")
        return redirect(url_for("module_list", name="contractor_supplies"))
    if not _delete_password_ok():
        return _reject_bad_delete_password(url_for("module_edit", name="contractor_supplies", row_id=supply_id))
    try:
        db.delete_contractor_supply_line(line_id)
        flash(_t("تم حذف الصنف"), "ok")
        db.log_audit(current_user_name(), "حذف مادة موردة", "مواد موردة من مقاول", supply_id, str(line_id))
        _after_data_change()
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("module_edit", name="contractor_supplies", row_id=supply_id))


@app.route("/module/contractor_supplies/<int:row_id>/receive", methods=["POST"])
@login_required
def contractor_supply_receive_warehouse(row_id):
    if not permissions.can("section.contractors") or not permissions.can("modules.write"):
        return permissions.deny_redirect()
    try:
        result = db.receive_contractor_supply_to_warehouse(row_id)
        if result.get("already"):
            flash(_t("التوريد مرحّل مسبقاً بسند {no}", no=result.get("voucher_no")), "ok")
        else:
            flash(
                _t(
                    "تم ترحيل {n} صنفاً للمستودع بسند {no}",
                    n=result.get("created") or 0,
                    no=result.get("voucher_no"),
                ),
                "ok",
            )
            db.log_audit(
                current_user_name(),
                "ترحيل مواد مقاول للمستودع",
                "مواد موردة من مقاول",
                row_id,
                result.get("voucher_no") or "",
            )
            _after_data_change()
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("module_edit", name="contractor_supplies", row_id=row_id))


@app.route("/financial")
@login_required
def financial_home():
    return _redirect_section_first_child("financial")


@app.route("/reports")
@login_required
def reports_home():
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    phone = (request.args.get("phone") or "").strip()
    report = reports_svc.build_general_report(date_from=date_from, date_to=date_to)
    pdf_url = url_for("general_report_pdf", date_from=date_from or None, date_to=date_to or None)
    pdf_share_url = url_for("general_report_pdf", date_from=date_from or None, date_to=date_to or None, _external=True, _scheme="https")
    page_url = url_for("reports_home", date_from=date_from or None, date_to=date_to or None, _external=True)
    whatsapp_url = reports_svc.whatsapp_url(report, page_url, pdf_share_url, phone)
    summary_cards = [
        _summary_card(_t("إجمالي الأعمال"), report["metrics"]["total_work"], _t("كل الأقسام المالية"), money=True),
        *helpers.work_ratio_cards(report["settings"], base_amount=report["metrics"]["total_work"]),
        _summary_card(_t("عدد الأعطال"), report["cards"]["tickets"], _t("منفذ/مغلق: {n}", n=report["cards"]["done_tickets"])),
    ]
    return render_template(
        "general_report.html",
        report=report,
        date_from=date_from,
        date_to=date_to,
        phone=phone,
        pdf_url=pdf_url,
        whatsapp_url=whatsapp_url,
        summary_cards=summary_cards,
        section="contracts",
        section_modules=modules_for_section("contracts"),
        section_meta=_smeta(SECTION_META["contracts"]),
    )


@app.route("/reports/general.pdf")
@app.route("/reports/export.pdf")
@login_required
def general_report_pdf():
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    report = reports_svc.build_general_report(date_from=date_from, date_to=date_to)
    data = reports_svc.build_general_report_pdf(report)
    stamp = datetime.now().strftime("%Y%m%d")
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=f"تقرير-ركاز-العام-{stamp}.pdf",
        mimetype="application/pdf",
    )


@app.route("/reports/whatsapp")
@login_required
def general_report_whatsapp():
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    phone = (request.args.get("phone") or "").strip()
    report = reports_svc.build_general_report(date_from=date_from, date_to=date_to)
    pdf_url = url_for("general_report_pdf", date_from=date_from or None, date_to=date_to or None, _external=True)
    page_url = url_for("reports_home", date_from=date_from or None, date_to=date_to or None, _external=True)
    return redirect(reports_svc.whatsapp_url(report, page_url, pdf_url, phone))


@app.route("/maintenance")
@login_required
def maintenance_home():
    return _redirect_section_first_child("maintenance")


@app.route("/hr")
@login_required
def hr_home():
    return _redirect_section_first_child("hr")


@app.route("/contracts-admin")
@login_required
def contracts_admin_home():
    links = section_links("contracts")
    if permissions.can("reports.view"):
        links.append(
            {
                "label": _t("التقارير"),
                "href": url_for("reports_home"),
                "key": "reports",
            }
        )
    boq_file = db.active_contract_boq_file()
    boq_files = db.list_contract_boq_files()
    boq_count = int((boq_file or {}).get("item_count") or 0)
    return render_template(
        "contracts_hub.html",
        title=_t("إدارة العقود"),
        subtitle=_t("عقود المكتب وبنود العقد الموحد — ارفع ملف Excel ليصبح الدليل النشط للمعاملات."),
        links=links,
        section="contracts",
        section_modules=modules_for_section("contracts"),
        section_meta=_smeta(SECTION_META["contracts"]),
        total_count=sum(i.get("count") or 0 for i in links),
        boq_file=boq_file,
        boq_files=boq_files,
        boq_count=boq_count,
        emergency_ratio=float((g.settings or {}).get("emergency_ratio") or 0),
    )


@app.route("/contracts-admin/boq/template.xlsx")
@login_required
def contract_boq_template():
    from webapp import contract_boq_excel

    data = contract_boq_excel.build_boq_template()
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name="قالب_بنود_العقد.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/contracts-admin/boq/import", methods=["POST"])
@login_required
def contract_boq_import():
    if not (permissions.can("modules.write") or permissions.can("section.contracts")):
        return permissions.deny_redirect()
    f = request.files.get("file")
    if not f or not f.filename:
        flash(_t("اختر ملف Excel لبنود العقد"), "danger")
        return redirect(url_for("contracts_admin_home"))
    try:
        from webapp import contract_boq_excel

        result = contract_boq_excel.import_boq_from_excel(f, uploaded_by=current_user_name())
        flash(_t("تم رفع بنود العقد: {ok} بند — الملف النشط: {filename}", ok=result["ok"], filename=result["filename"]), "ok")
        db.log_audit(
            current_user_name(),
            "رفع بنود عقد",
            "بنود العقد",
            result.get("file_id"),
            f"{result.get('filename')} ({result.get('ok')} بند)",
        )
        _after_data_change()
    except Exception as exc:
        flash(_t("تعذر استيراد بنود العقد: {exc}", exc=exc), "danger")
    return redirect(url_for("contracts_admin_home"))


@app.route("/contracts-admin/boq/<int:file_id>/activate", methods=["POST"])
@login_required
def contract_boq_activate(file_id):
    if not permissions.can("modules.write"):
        return permissions.deny_redirect()
    conn = db.connect()
    row = conn.execute("SELECT * FROM contract_boq_files WHERE id=?", (file_id,)).fetchone()
    if not row:
        conn.close()
        flash(_t("الملف غير موجود"), "danger")
        return redirect(url_for("contracts_admin_home"))
    conn.execute("UPDATE contract_boq_files SET is_active=0")
    conn.execute("UPDATE contract_boq_files SET is_active=1 WHERE id=?", (file_id,))
    items = db.rows_to_dicts(
        conn.execute("SELECT * FROM contract_boq_items WHERE file_id=?", (file_id,)).fetchall()
    )
    conn.execute("DELETE FROM boq_items")
    for it in items:
        conn.execute(
            """
            INSERT INTO boq_items(
              item_no, description, short_desc, long_desc, line_type,
              unit, unit_price, currency, payment_type, category, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                it.get("item_no"),
                it.get("description") or it.get("short_desc") or "",
                it.get("short_desc") or "",
                it.get("long_desc") or "",
                it.get("line_type") or "",
                it.get("unit"),
                it.get("unit_price"),
                it.get("currency") or "",
                it.get("payment_type") or "",
                it.get("category"),
                it.get("notes"),
            ),
        )
    conn.commit()
    conn.close()
    flash(_t("تم تفعيل دليل بنود العقد"), "ok")
    _after_data_change()
    return redirect(url_for("contracts_admin_home"))


@app.route("/users")
@login_required
def users_home():
    return redirect(url_for("users_list"))


def _safe_next_path(raw: str | None, fallback: str) -> str:
    nxt = (raw or "").strip()
    if nxt.startswith("/") and not nxt.startswith("//") and nxt not in {"/", "/login"}:
        return nxt
    return fallback


@app.route("/admin/programmer/device", methods=["GET", "POST"])
@login_required
def programmer_device_setup():
    """تسجيل الجهاز الرئيسي للمبرمج (مرة واحدة / بعد إعادة التعيين عبر SSH)."""
    if not prog_guard.can_access_programmer_device_ui():
        return permissions.deny_redirect(_t("هذه الصفحة للمبرمج المعتمد فقط"))
    nxt = _safe_next_path(request.values.get("next"), url_for("users_list"))
    already = prog_guard.main_device_registered()
    is_this_main = prog_guard.is_main_device()

    if request.method == "POST":
        action = (request.form.get("action") or "register").strip()
        # إرسال رمز التهيئة بالبريد من صفحة التسجيل نفسها
        if action in {"send_bootstrap", "resend_bootstrap"}:
            if already and not is_this_main:
                flash(
                    _t("الجهاز الرئيسي مسجّل مسبقاً. استخدم إرسال رمز التحقق من جهاز آخر."),
                    "danger",
                )
                return redirect(url_for("programmer_device_setup", next=nxt))
            ok, msg = prog_guard.send_bootstrap_email()
            flash(msg, "ok" if ok else "danger")
            if ok:
                db.log_audit(current_user_name(), "إرسال رمز تهيئة مبرمج", "أمان", session.get("user_id"))
            return redirect(url_for("programmer_device_setup", next=nxt))
        # مسار OTP متاح من صفحة الجهاز أيضاً حتى لا يُحصر المستخدم في التسجيل فقط
        if action in {"send_otp", "resend_otp"}:
            if not already:
                flash(_t("سجّل الجهاز الرئيسي أولاً، أو أرسل رمز التهيئة بالبريد من هذه الصفحة."), "danger")
                return redirect(url_for("programmer_device_setup", next=nxt))
            if is_this_main:
                flash(_t("أنت على الجهاز الرئيسي — التعديل مسموح مباشرة."), "ok")
                return redirect(nxt)
            ok, msg = prog_guard.send_email_otp(next_path=nxt)
            flash(msg, "ok" if ok else "danger")
            if ok:
                db.log_audit(current_user_name(), "إرسال OTP مبرمج", "أمان", session.get("user_id"))
            return redirect(url_for("programmer_device_setup", next=nxt))
        if action == "verify_secondary":
            if not already:
                return redirect(url_for("programmer_device_setup", next=nxt))
            if is_this_main or prog_guard.is_elevated():
                return redirect(nxt)
            ok, err = prog_guard.verify_strict(
                password=request.form.get("password") or "",
                pin=request.form.get("change_pin") or "",
                approve_code=request.form.get("approve_code") or "",
            )
            if not ok:
                flash(err, "danger")
                return redirect(url_for("programmer_device_setup", next=nxt))
            prog_guard.grant_elevation()
            db.log_audit(current_user_name(), "تحقق مبرمج (جهاز ثانوي)", "أمان", session.get("user_id"))
            flash(
                _t(
                    "تم التحقق — يمكنك إجراء تعديلات برمجية لمدة {mins} دقيقة من هذا الجهاز.",
                    mins=prog_guard.ELEVATION_MINUTES,
                ),
                "ok",
            )
            return redirect(nxt)
        if action == "register":
            if already and not is_this_main:
                flash(
                    _t("الجهاز الرئيسي مسجّل مسبقاً. من جهاز آخر استخدم تحقق المبرمج، أو أعد التعيين عبر SSH."),
                    "danger",
                )
                return redirect(url_for("programmer_device_setup", next=nxt))
            ok, err = prog_guard.verify_bootstrap(request.form.get("bootstrap_code") or "")
            if not ok:
                flash(err, "danger")
                return redirect(url_for("programmer_device_setup", next=nxt))
            token = prog_guard.register_main_device(
                label=(request.form.get("label") or "").strip() or "الجهاز الرئيسي",
                user_agent=request.headers.get("User-Agent") or "",
                ip=request.headers.get("X-Forwarded-For", request.remote_addr) or "",
            )
            db.log_audit(current_user_name(), "تسجيل جهاز رئيسي", "أمان", session.get("user_id"))
            flash(_t("تم ربط هذا الجهاز كجهاز المبرمج الرئيسي."), "ok")
            resp = redirect(nxt)
            return prog_guard.attach_device_cookie(resp, token)
        if action == "logout_device":
            # إزالة كوكي الجهاز من هذا المتصفح فقط (لا يمسح السجل على السيرفر)
            flash(_t("تم إلغاء اعتماد هذا المتصفح محلياً."), "ok")
            resp = redirect(url_for("programmer_device_setup"))
            return prog_guard.clear_device_cookie(resp)

    devices = db.list_programmer_devices() if already else []
    show_bootstrap_mail = bool((not already) or is_this_main)
    show_otp = bool(already and not is_this_main and not prog_guard.is_elevated())
    mail_wait = 0
    if show_bootstrap_mail or show_otp:
        mail_wait = prog_guard.otp_send_wait_seconds()
    return render_template(
        "programmer_device.html",
        next_url=nxt,
        already_registered=already,
        is_this_main=is_this_main,
        devices=devices,
        secrets_ok=prog_guard.secrets_configured(),
        smtp_ready=prog_guard.smtp_ready(),
        show_bootstrap_mail=show_bootstrap_mail,
        show_otp=show_otp,
        otp_wait_seconds=mail_wait,
        programmer_emails=prog_guard.masked_programmer_emails(),
        can_mutate=prog_guard.can_mutate_control_plane(),
        elevated_seconds=prog_guard.elevation_remaining_seconds(),
    )


@app.route("/admin/programmer/verify", methods=["GET", "POST"])
@login_required
def programmer_verify():
    """تحقق صارم لتعديل برمجي من جهاز غير رئيسي (OTP + رمز التغيير عبر بريد المبرمج)."""
    if not prog_guard.can_access_programmer_device_ui():
        return permissions.deny_redirect(_t("هذه الصفحة للمبرمج المعتمد فقط"))
    nxt = _safe_next_path(request.values.get("next"), url_for("users_list"))

    if prog_guard.is_main_device():
        flash(_t("أنت على الجهاز الرئيسي — التعديل مسموح مباشرة."), "ok")
        return redirect(nxt)
    if prog_guard.is_elevated():
        flash(
            _t(
                "التحقق ساري لمدة {mins} دقيقة تقريباً.",
                mins=max(1, prog_guard.elevation_remaining_seconds() // 60),
            ),
            "ok",
        )
        return redirect(nxt)

    if not prog_guard.main_device_registered():
        return redirect(url_for("programmer_device_setup", next=nxt))

    if request.method == "POST":
        action = (request.form.get("action") or "verify").strip()
        if action in {"send_otp", "resend_otp"}:
            ok, msg = prog_guard.send_email_otp(next_path=nxt)
            flash(msg, "ok" if ok else "danger")
            if ok:
                db.log_audit(current_user_name(), "إرسال OTP مبرمج", "أمان", session.get("user_id"))
            return redirect(url_for("programmer_verify", next=nxt))
        ok, err = prog_guard.verify_strict(
            password=request.form.get("password") or "",
            pin=request.form.get("change_pin") or "",
            approve_code=request.form.get("approve_code") or "",
        )
        if not ok:
            flash(err, "danger")
            return redirect(url_for("programmer_verify", next=nxt))
        prog_guard.grant_elevation()
        db.log_audit(current_user_name(), "تحقق مبرمج (جهاز ثانوي)", "أمان", session.get("user_id"))
        flash(
            _t(
                "تم التحقق — يمكنك إجراء تعديلات برمجية لمدة {mins} دقيقة من هذا الجهاز.",
                mins=prog_guard.ELEVATION_MINUTES,
            ),
            "ok",
        )
        return redirect(nxt)

    otp_wait = prog_guard.otp_send_wait_seconds()
    return render_template(
        "programmer_verify.html",
        next_url=nxt,
        elevation_minutes=prog_guard.ELEVATION_MINUTES,
        secrets_ok=prog_guard.secrets_configured(),
        smtp_ready=prog_guard.smtp_ready(),
        programmer_emails=prog_guard.masked_programmer_emails(),
        otp_wait_seconds=otp_wait,
    )


@app.route("/admin/programmer/magic/<token>")
@login_required
def programmer_magic(token):
    """الروابط السريعة أُلغيت — التحقق يتم فقط بإدخال رمز البريد في النموذج."""
    if not prog_guard.can_access_programmer_device_ui():
        return permissions.deny_redirect(_t("هذه الصفحة للمبرمج المعتمد فقط"))
    flash(
        _t("يلزم إدخال رمز التحقق من البريد يدوياً مع كلمة المرور ورمز التغيير."),
        "danger",
    )
    return redirect(url_for("programmer_verify"))


def _parse_percent_setting(raw, label: str) -> float:
    text = str(raw or "").strip().replace("%", "").replace(",", ".")
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise ValueError(_t("{label} يجب أن تكون رقماً بين 0 و 100", label=label))
    if value < 0 or value > 100:
        raise ValueError(_t("{label} يجب أن تكون بين 0 و 100", label=label))
    return round(value, 2)


@app.route("/admin/programmer/work-ratios", methods=["GET", "POST"])
@login_required
def programmer_work_ratios():
    if not prog_guard.can_access_programmer_device_ui():
        return permissions.deny_redirect(_t("هذه الصفحة للمبرمج المعتمد فقط"))
    if request.method == "POST":
        if not prog_guard.can_mutate_control_plane():
            if not prog_guard.main_device_registered():
                return redirect(url_for("programmer_device_setup", next=request.path))
            return redirect(url_for("programmer_verify", next=request.path))
        try:
            rekaz_ratio = _parse_percent_setting(request.form.get("rekaz_ratio"), _t("نسبة ركاز"))
            contractor_ratio = _parse_percent_setting(request.form.get("main_contractor_ratio"), _t("نسبة المقاول الرئيسي"))
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("programmer_work_ratios"))
        db.save_settings(
            {
                "rekaz_ratio": rekaz_ratio,
                "main_contractor_ratio": contractor_ratio,
            }
        )
        db.log_audit(
            current_user_name(),
            "تعديل",
            "نسب ركاز والمقاول",
            details=f"rekaz={rekaz_ratio}, contractor={contractor_ratio}",
        )
        flash(_t("تم حفظ نسب ركاز والمقاول الرئيسي."), "ok")
        _after_data_change()
        return redirect(url_for("programmer_work_ratios"))
    return render_template(
        "programmer_work_ratios.html",
        settings=db.get_settings(),
        can_save_ratios=prog_guard.can_mutate_control_plane(),
    )


@app.route("/admin/audit-log")
@login_required
def audit_log_home():
    return redirect(url_for("audit_log_page"))

@app.route("/api/boq-item")
@login_required
def api_boq_item():
    """بحث سريع عن بند في دليل العقد برقم البند (لإدخال نصي)."""
    item_no = (request.args.get("item_no") or "").strip()
    if not item_no:
        return jsonify({"ok": False, "error": _t("أدخل رقم البند")})
    item = db.get_contract_boq_item(item_no)
    if not item:
        return jsonify({"ok": False, "error": _t("رقم البند «{item_no}» غير موجود في دليل العقد النشط", item_no=item_no)})
    desc = (item.get("short_desc") or item.get("description") or "").strip()
    return jsonify(
        {
            "ok": True,
            "item_no": item.get("item_no"),
            "description": desc,
            "unit": item.get("unit") or "",
            "unit_price": item.get("unit_price"),
        }
    )

# ---------- Generic CRUD helpers ----------
# MODULES imported from webapp.modules_config


def _module_form_data(module):
    data = {}
    for key, _label, ftype in module["fields"]:
        val = (request.form.get(key) or "").strip()
        if ftype == "number":
            data[key] = float(val) if val != "" else None
        elif ftype in ("image", "attachment"):
            # تُعالَج لاحقاً عبر media_svc.apply_photo_uploads
            data[key] = val
        else:
            data[key] = val
    return data


def _metering_boq_approved_total(ticket_no, conn=None) -> float | None:
    """القيمة المعتمدة من بنود العقد (مبلغ الكميات النهائي بعد التصنيف/الطوارئ)."""
    tno = str(ticket_no or "").strip()
    if not tno:
        return None
    return db.ticket_boq_final_total(ticket_no=tno, conn=conn)


def _apply_metering_approved_from_boq(data: dict, conn=None) -> float | None:
    """يربط قيمة التمتير المعتمدة بمبلغ الكميات من بنود العقد عند وجودها."""
    total = _metering_boq_approved_total(data.get("ticket_no"), conn=conn)
    if total is not None:
        data["approved_value"] = total
    return total


def _apply_photos_from_request(data: dict) -> None:
    clear_flags = {
        f: str(request.form.get(f"clear_{f}") or "").strip() in {"1", "on", "yes", "true"}
        for f in media_svc.PHOTO_FIELDS
    }
    media_svc.apply_photo_uploads(
        data,
        request.files,
        ticket_no=data.get("ticket_no"),
        clear_flags=clear_flags,
    )


def _apply_attachments_from_request(name: str, data: dict) -> None:
    if "attachments" not in data:
        return
    clear = str(request.form.get("clear_attachments") or "").strip().lower() in {"1", "on", "yes", "true"}
    ref = (
        data.get("ticket_no")
        or data.get("coord_no")
        or data.get("license_no")
        or data.get("work_no")
        or data.get("project_code")
        or data.get("voucher_no")
        or data.get("purchase_no")
        or data.get("supply_no")
        or data.get("permit_no")
        or data.get("contract_no")
        or data.get("emp_no")
        or data.get("dept_code")
    )
    media_svc.apply_attachment_uploads(
        data,
        request.files,
        scope=name,
        record_ref=ref,
        clear=clear,
    )


def _warehouse_form_ctx():
    """سياق النموذج: من الصفحة الرئيسية أو من داخل المستودع المستقل."""
    ctx = (request.values.get("from") or "").strip().lower()
    if ctx in ("ticket", "ops"):
        return "ops"
    if ctx in ("wh_ops", "warehouse_ops"):
        return "wh_ops"
    if ctx in ("wh_constructions", "warehouse_constructions"):
        return "wh_constructions"
    if ctx in ("wh_projects", "warehouse_projects"):
        return "wh_projects"
    if ctx in ("wh_reinforcement", "warehouse_reinforcement"):
        return "wh_reinforcement"
    if ctx in ("constructions", "projects", "warehouses", "contractors", "reinforcement"):
        return ctx
    return "warehouses"


def _warehouse_source_from_ctx(form_ctx: str) -> str:
    """يحول سياق النموذج إلى source_section المخزّن."""
    return {
        "ops": "ops",
        "wh_ops": "ops",
        "constructions": "constructions",
        "wh_constructions": "constructions",
        "projects": "projects",
        "wh_projects": "projects",
        "contractors": "contractors",
        "wh_contractors": "contractors",
        "reinforcement": "reinforcement",
        "wh_reinforcement": "reinforcement",
    }.get((form_ctx or "").strip().lower(), "")


def _warehouse_create_contexts():
    """السياقات المسموح منها إنشاء حركة: صفحات المعاملة أو المستودع."""
    return (
        "ops",
        "wh_ops",
        "wh_constructions",
        "wh_projects",
        "wh_contractors",
        "wh_reinforcement",
    )


def _warehouse_main_sections():
    return ("ops", "constructions", "projects", "contractors", "reinforcement")


def _warehouse_source_label(section: str) -> str:
    return {
        "ops": _t("العمليات والصيانة"),
        "constructions": _t("الإنشاءات"),
        "projects": _t("المشاريع"),
        "contractors": _t("مواد موردة من مقاول"),
        "external": _t("المشتريات الخارجية"),
        "custody": _t("العهد"),
        "reinforcement": _t("التعزيز - اسكيمات"),
        "warehouses": _t("المستودعات"),
    }.get(section or "", section or "")


def _redirect_after_module(name, data, form_ctx=None):
    """بعد حفظ سجل مرتبط بعطل: العودة لصفحة العطل في نفس الخطوة (بدون نقل تلقائي)."""
    if name == "warehouse_items":
        return redirect(url_for("warehouse_balances", view="items"))
    if name == "primary_team_orders":
        return redirect(url_for("ops_primary_teams"))
    tno = str((data or {}).get("ticket_no") or "").strip()
    form_ctx = form_ctx or _warehouse_form_ctx()

    if name == "warehouse_tx":
        if form_ctx in ("reinforcement", "wh_reinforcement"):
            ref = (data.get("source_ref") or data.get("work_order") or tno or "").strip()
            conn = db.connect()
            work = _reinforcement_work_for_ref(ref, conn)
            conn.close()
            if work:
                flash(_t("تم الحفظ"), "ok")
                return redirect(url_for("reinforcement_work_view", row_id=work["id"], focus="warehouse") + "#section-warehouse")
        # من داخل المستودع: ابقَ في المستودع دائماً (بدون تحويل للصفحات الرئيسية)
        voucher = (data.get("voucher_no") or "").strip()
        if voucher and form_ctx in ("wh_ops", "wh_constructions", "wh_projects", "wh_reinforcement", "warehouses", "ops", "constructions", "projects", "reinforcement"):
            # بعد الحفظ افتح عرض المعاملة (سند)
            if form_ctx in ("wh_ops", "wh_constructions", "wh_projects", "wh_reinforcement", "warehouses") or not (
                form_ctx == "ops"
                and (data.get("ticket_no") or "").strip()
                and permissions.can("tickets.read")
                and permissions.can("section.ops")
            ):
                return redirect(url_for("warehouse_voucher_detail", voucher_no=voucher))
        if form_ctx == "wh_ops":
            src_ref = (data.get("source_ref") or "").strip()
            tno_tx = (data.get("ticket_no") or "").strip()
            if src_ref and not tno_tx:
                return redirect(url_for("warehouse_ops", view="teams"))
            return redirect(url_for("warehouse_ops", view="tickets" if tno_tx else "movements"))
        if form_ctx == "wh_constructions":
            return redirect(url_for("warehouse_constructions"))
        if form_ctx == "wh_projects":
            return redirect(url_for("warehouse_projects"))
        if form_ctx == "wh_reinforcement":
            return redirect(url_for("warehouse_ops", view="reinforcement"))
        if form_ctx == "warehouses":
            source = (request.values.get("source") or data.get("source_section") or "").strip().lower()
            if source == "ops":
                return redirect(url_for("warehouse_ops", view="movements"))
            if source == "constructions":
                return redirect(url_for("warehouse_constructions", view="movements"))
            if source == "projects":
                return redirect(url_for("warehouse_projects", view="movements"))
            if source == "reinforcement":
                return redirect(url_for("warehouse_ops", view="reinforcement"))
            return redirect(url_for("warehouses_home"))
        # من الصفحة الرئيسية (معالج العطل) — ابقَ في خطوة المستودع بدون قفز تلقائي
        if (
            form_ctx == "ops"
            and tno
            and permissions.can("tickets.read")
            and permissions.can("section.ops")
        ):
            conn = db.connect()
            row = conn.execute("SELECT id FROM tickets WHERE ticket_no=? LIMIT 1", (tno,)).fetchone()
            conn.close()
            if row:
                flash(_t("تم الحفظ"), "ok")
                return redirect(url_for("tickets.view", ticket_id=row["id"], edit=1, step="warehouse" if permissions.can("section.warehouses") else "done") + "#step-warehouse")
        if form_ctx == "constructions":
            return redirect(url_for("module_list", name="construction_works"))
        if form_ctx == "projects":
            return redirect(url_for("module_list", name="projects"))
        if form_ctx == "reinforcement":
            return redirect(url_for("module_list", name="reinforcement_works"))
        if form_ctx == "ops":
            return redirect(url_for("warehouse_ops"))
        return redirect(url_for("warehouses_home"))

    # بعد حفظ سجلات مرتبطة بالعطل: ارجع لنفس الخطوة بدون انتقال تلقائي
    stay_after = {
        "quantities": "boq",
        "photos": "photos",
        "metering": "metering",
    }
    if tno and name in stay_after:
        conn = db.connect()
        work = _reinforcement_work_for_ref(tno, conn)
        if work:
            conn.close()
            focus = {
                "quantities": "quantities",
                "photos": "photos",
                "metering": "metering",
            }.get(name, "")
            flash(_t("تم الحفظ"), "ok")
            return redirect(url_for("reinforcement_work_view", row_id=work["id"], focus=focus) + f"#section-{focus}")
        row = conn.execute("SELECT id FROM tickets WHERE ticket_no=? LIMIT 1", (tno,)).fetchone()
        conn.close()
        if row:
            stay = stay_after[name]
            allowed = {"data", "boq", "photos", "metering", "warehouse", "done"}
            if stay not in allowed:
                stay = "data"
            flash(_t("تم الحفظ"), "ok")
            return redirect(url_for("tickets.view", ticket_id=row["id"], edit=1, step=stay) + f"#step-{stay}")
    if tno:
        return redirect(url_for("module_list", name=name, ticket_no=tno))
    return redirect(url_for("module_list", name=name))


def _prepare_warehouse_tx_create(data: dict, form_ctx: str, conn) -> tuple:
    """يملأ مصدر الحركة — مسموح من صفحات المستودعات فقط."""
    if form_ctx not in _warehouse_create_contexts():
        return None, _t(
            "إدخال معاملات المستودع يتم من صفحات المستودعات فقط، والصفحات الرئيسية للعرض فقط."
        )

    source = _warehouse_source_from_ctx(form_ctx)
    data["source_section"] = source
    source_ref = (data.get("source_ref") or request.values.get("source_ref") or "").strip()
    tno = (data.get("ticket_no") or "").strip()
    if source == "ops":
        data["source_ref"] = source_ref or tno
    else:
        data["source_ref"] = source_ref

    data = db.enrich_warehouse_tx_from_item(data)
    data = db.enrich_warehouse_tx_codes(data, conn)
    data = db.apply_warehouse_tx_work_order(data, conn)

    linked = (
        (data.get("ticket_no") or "").strip()
        or (data.get("rekaz_code") or "").strip()
        or (data.get("source_ref") or "").strip()
        or (data.get("work_order") or "").strip()
    )
    if db.is_outbound_warehouse_tx(data.get("tx_type") or "") and not linked:
        return None, _t(
            "الصرف يتطلب ربطاً بمعاملة (عطل / إنشاءات / مشروع)."
        )
    return data, None


def _render_warehouse_tx_form(module, name, data, tickets, ticket_options, mode, form_ctx, extra=None):
    section = module.get("section")
    ctx = {
        "name": name,
        "module": _mod(module),
        "row": data,
        "tickets": tickets,
        "ticket_options": ticket_options,
        "warehouse_items": db.list_warehouse_items(),
        "boq_items": [],
        "mode": mode,
        "section": section,
        "section_meta": _smeta(SECTION_META.get(section)),
        "section_modules": modules_for_section(section) if section else [],
        "form_ctx": form_ctx,
    }
    if extra:
        ctx.update(extra)
    return render_template("module_form.html", **ctx)


@app.route("/media/<storage>/<path:key>")
@login_required
def media_serve(storage, key):
    """عرض صورة مرفوعة (S3 أو محلي) لمستخدم مسجّل."""
    try:
        stream, mime, filename = media_svc.load_media(storage, key)
    except FileNotFoundError:
        abort(404)
    except PermissionError:
        abort(403)
    except Exception:
        abort(404)
    return send_file(stream, mimetype=mime, download_name=filename, as_attachment=False)

@app.route("/module/<name>")
@login_required
def module_list(name):
    module = MODULES.get(name)
    if not module:
        flash(_t("القسم غير موجود"), "danger")
        return redirect(url_for("ops_home"))
    if name == "warehouse_items":
        return redirect(url_for("warehouse_balances", view="items"))
    if name == "primary_team_orders":
        return redirect(url_for("ops_primary_teams"))
    if name == "warehouse_tx":
        source_filter = (request.args.get("source") or "").strip().lower()
        if source_filter == "constructions":
            return redirect(url_for("warehouse_constructions", view="movements"))
        if source_filter == "ops":
            return redirect(url_for("warehouse_ops", view="movements"))
        if source_filter == "projects":
            return redirect(url_for("warehouse_projects", view="movements"))
        if source_filter == "reinforcement":
            return redirect(url_for("warehouse_ops", view="reinforcement"))
        if not (request.args.get("ticket_no") or request.args.get("item_no")):
            return redirect(url_for("warehouses_home"))
    if name == "safety_permits":
        db.ensure_excavation_safety_permits()
    packed = _load_module_list_rows(name, module)
    rows = packed["rows"]
    money_keys = packed["money_keys"]
    missing_amount = packed["missing_amount"]
    missing_count = packed["missing_count"]
    item_filter = packed["item_filter"]
    ticket_filter = packed["ticket_filter"]
    source_filter = packed["source_filter"]
    dept_filter = packed["dept_filter"]
    date_from = packed["date_from"]
    date_to = packed["date_to"]
    date_keys = packed["date_keys"]
    excavation_filter = packed["excavation_filter"]
    linked_section_filter = packed["linked_section_filter"]
    tickets = packed["tickets"]
    section = module.get("section")
    # لا نكرر بطاقات المستودع الخاصة بإجمالي الكميات على صفحات الحركات التفصيلية
    skip_summary = name in ("warehouse_items",)
    if skip_summary:
        summary_cards = []
    else:
        count_labels = {
            "metering": _t("عدد سجلات التمتير"),
            "invoices": _t("عدد المستخلصات"),
            "construction_works": _t("عدد المعاملات"),
            "contractor_works": _t("عدد المعاملات"),
            "reinforcement_works": _t("عدد المعاملات"),
            "projects": _t("عدد المشاريع"),
            "quantities": _t("عدد البنود"),
            "photos": _t("عدد سجلات الصور"),
            "warehouse_tx": _t("عدد الحركات"),
            "external_purchases": _t("عدد المشتريات"),
            "contractor_supplies": _t("عدد التوريدات"),
            "custody": _t("عدد العهد"),
            "new_coordinations": _t("عدد التنسيقات"),
            "issued_licenses": _t("عدد الرخص"),
            "quality_clearances": _t("عدد الإخلاءات"),
            "reinforcement_departments": _t("عدد الأقسام"),
        }
        summary_cards = build_list_summary_cards(
            rows,
            count_label=count_labels.get(name),
            money_keys=money_keys,
            date_keys=_module_date_keys(name, module),
            detail_key=_module_detail_key(name, module),
            missing_amount_count=missing_count,
            missing_amount_active=missing_amount,
            missing_amount_endpoint="module_list" if money_keys else None,
            missing_amount_endpoint_kwargs={"name": name} if money_keys else None,
        )
        if section in ("constructions", "projects", "maintenance"):
            summary_cards.extend(
                helpers.work_ratio_cards(
                    base_amount=_sum_money_field(rows, *money_keys) if money_keys else 0,
                )
            )
    return render_template(
        "module_list.html",
        name=name,
        module=_mod(module),
        rows=rows,
        tickets=tickets,
        item_filter=item_filter,
        ticket_filter=ticket_filter,
        excavation_filter=excavation_filter,
        linked_section_filter=linked_section_filter,
        source_filter=source_filter if name == "warehouse_tx" else "",
        date_from=date_from,
        date_to=date_to,
        has_date_filter=bool(date_keys),
        section=section,
        section_meta=_smeta(SECTION_META.get(section)),
        section_modules=modules_for_section(section) if section else [],
        warehouse_source=source_filter if name == "warehouse_tx" else None,
        department_filter=dept_filter if name == "reinforcement_works" else "",
        reinforcement_departments=db.list_reinforcement_departments(active_only=False) if name == "reinforcement_works" else [],
        missing_amount=missing_amount,
        export_href=_url_with_filters("module_export_excel", name=name) if money_keys else None,
        export_pdf_href=_url_with_filters("module_export_pdf", name=name),
        summary_cards=summary_cards,
    )


def _load_module_list_rows(name, module):
    """تحميل صفوف الوحدة مع نفس فلاتر صفحة القائمة (بما فيها بدون مبلغ)."""
    conn = db.connect()
    rows = db.rows_to_dicts(conn.execute(f"SELECT * FROM {module['table']} ORDER BY id DESC").fetchall())
    tickets = [r["ticket_no"] for r in conn.execute("SELECT ticket_no FROM tickets ORDER BY id DESC").fetchall()]
    if name == "reinforcement_works":
        refs = sorted(
            {
                ref
                for r in rows
                for ref in ((r.get("work_no") or "").strip(), (r.get("ticket_no") or "").strip())
                if ref
            }
        )
        qty_totals = {}
        if refs:
            placeholders = ",".join("?" for _ in refs)
            qty_rows = conn.execute(
                f"""
                SELECT ticket_no, COALESCE(SUM(COALESCE(qty,0) * COALESCE(unit_price,0)),0) AS total
                FROM quantities
                WHERE ticket_no IN ({placeholders})
                GROUP BY ticket_no
                """,
                refs,
            ).fetchall()
            qty_totals = {r["ticket_no"]: float(r["total"] or 0) for r in qty_rows}
        for r in rows:
            direct = helpers.to_float_safe(r.get("value"))
            work_total = qty_totals.get((r.get("work_no") or "").strip(), 0)
            ticket_total = qty_totals.get((r.get("ticket_no") or "").strip(), 0)
            linked_total = work_total or ticket_total
            if (direct is None or direct == 0) and linked_total:
                r["value"] = linked_total
                r["value_source"] = "quantities"
    conn.close()
    if name == "quantities":
        for r in rows:
            r["total"] = (float(r.get("qty") or 0) * float(r.get("unit_price") or 0))
    if name == "photos":
        for r in rows:
            r["complete"] = _t("مكتمل") if media_svc.photos_complete(r) else _t("ناقص")
    if name == "invoices":
        for r in rows:
            r["remaining"] = float(r.get("value") or 0) - float(r.get("collected") or 0)
    if name == "external_purchases":
        summaries = db.purchase_lines_summary([r["id"] for r in rows if r.get("id")])
        for r in rows:
            sm = summaries.get(int(r["id"]), {})
            r["line_count"] = sm.get("line_count") or 0
            r["items_summary"] = sm.get("first_item") or r.get("item_name") or "—"
            if sm.get("line_count", 0) > 1:
                r["items_summary"] = f"{sm.get('first_item') or '—'} (+{sm['line_count'] - 1})"
            r["total"] = sm.get("total")
            if r["total"] is None:
                r["total"] = float(r.get("qty") or 0) * float(r.get("unit_price") or 0)
            r["received"] = bool((r.get("received_voucher_no") or "").strip())
    if name == "contractor_supplies":
        summaries = db.contractor_supply_lines_summary([r["id"] for r in rows if r.get("id")])
        for r in rows:
            sm = summaries.get(int(r["id"]), {})
            r["line_count"] = sm.get("line_count") or 0
            r["qty_total"] = sm.get("qty_total") or 0
            r["items_summary"] = sm.get("first_item") or "—"
            if sm.get("line_count", 0) > 1:
                r["items_summary"] = f"{sm.get('first_item') or '—'} (+{sm['line_count'] - 1})"
            r["total"] = sm.get("total") or 0
            r["received"] = bool((r.get("received_voucher_no") or "").strip())
    if name == "custody":
        summaries = db.custody_lines_summary([r["id"] for r in rows if r.get("id")])
        for r in rows:
            sm = summaries.get(int(r["id"]), {})
            r["line_count"] = sm.get("line_count") or 0
            r["qty_total"] = sm.get("qty_total") or (float(r.get("qty") or 0) if r.get("qty") not in (None, "") else 0)
            r["items_summary"] = sm.get("first_item") or r.get("item_name") or "—"
            if sm.get("line_count", 0) > 1:
                r["items_summary"] = f"{sm.get('first_item') or '—'} (+{sm['line_count'] - 1})"
            r["issued"] = bool((r.get("issued_voucher_no") or "").strip())
            r["returned"] = bool((r.get("return_voucher_no") or "").strip())
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    date_keys = _module_date_keys(name, module)
    if date_keys:
        rows = _filter_rows_by_date_range(rows, date_from, date_to, *date_keys)
    item_filter = (request.args.get("item_no") or "").strip()
    ticket_filter = (request.args.get("ticket_no") or "").strip()
    source_filter = (request.args.get("source") or "").strip().lower()
    if name == "warehouse_tx":
        db.backfill_warehouse_tx_sources()
        db.enrich_warehouse_txs_work_order(rows)
        for r in rows:
            r["unit"] = db.normalize_warehouse_unit(r.get("unit"))
    if name == "warehouse_items":
        for r in rows:
            detail = db.warehouse_balance_detail(r.get("item_no"))
            r["balance"] = detail.get("available_balance")
            r["actual_balance"] = detail.get("balance")
            r["reserved"] = detail.get("reserved")
    if name == "warehouse_tx" and item_filter:
        rows = [r for r in rows if (r.get("item_no") or "").lower() == item_filter.lower()]
    if name == "warehouse_tx" and source_filter in ("ops", "constructions", "projects", "external", "custody", "contractors", "reinforcement"):
        rows = [
            r
            for r in rows
            if (r.get("source_section") or "").strip().lower() == source_filter
        ]
    if ticket_filter and any(f[0] == "ticket_no" for f in module.get("fields", [])):
        rows = [r for r in rows if (r.get("ticket_no") or "") == ticket_filter]
    dept_filter = (request.args.get("department") or "").strip()
    if dept_filter and name == "reinforcement_works":
        rows = [r for r in rows if (r.get("department") or "").strip() == dept_filter]
    excavation_filter = (request.args.get("excavation") or "").strip() in {"1", "yes", "true"}
    if excavation_filter and name in ("coordination", "quality_clearances"):
        excav_tickets = set(db.collect_excavation_ticket_nos())
        rows = [r for r in rows if (r.get("ticket_no") or "") in excav_tickets]
    linked_section_filter = db.normalize_linked_section(request.args.get("linked_section") or "")
    if linked_section_filter and name == "issued_licenses":
        rows = [
            r
            for r in rows
            if db.normalize_linked_section(r.get("linked_section")) == linked_section_filter
        ]
    money_keys = _module_money_keys(name, module)
    missing_amount = _missing_amount_flag()
    missing_count = _count_missing_amount(rows, *money_keys) if money_keys else 0
    if missing_amount and money_keys:
        rows = _filter_missing_amount_rows(rows, *money_keys)
    return {
        "rows": rows,
        "tickets": tickets,
        "money_keys": money_keys,
        "missing_amount": missing_amount,
        "missing_count": missing_count,
        "item_filter": item_filter,
        "ticket_filter": ticket_filter,
        "source_filter": source_filter,
        "dept_filter": dept_filter,
        "date_from": date_from,
        "date_to": date_to,
        "date_keys": date_keys,
        "excavation_filter": excavation_filter,
        "linked_section_filter": linked_section_filter,
    }


@app.route("/module/<name>/export.xlsx")
@login_required
def module_export_excel(name):
    module = MODULES.get(name)
    if not module:
        flash(_t("القسم غير موجود"), "danger")
        return redirect(url_for("ops_home"))
    if name in ("warehouse_items", "primary_team_orders", "warehouse_tx"):
        return redirect(url_for("module_list", name=name))
    money_keys = _module_money_keys(name, module)
    if not money_keys:
        flash(_t("لا يوجد تصدير لهذه القائمة"), "danger")
        return redirect(url_for("module_list", name=name))
    packed = _load_module_list_rows(name, module)
    rows = packed["rows"]
    list_cols = list(module.get("list_cols") or [])
    for mk in money_keys:
        if mk not in list_cols:
            list_cols.append(mk)
    label_map = {f[0]: f[1] for f in module.get("fields") or []}
    headers = [_t(label_map.get(k, k)) for k in list_cols]
    stamp = datetime.now().strftime("%Y%m%d")
    suffix = "-بدون-مبلغ" if packed["missing_amount"] else ""
    title = _t(module.get("title") or name)
    return _simple_xlsx_export(
        title,
        headers,
        rows,
        list_cols,
        f"{name}{suffix}-{stamp}.xlsx",
    )


@app.route("/module/<name>/export.pdf")
@login_required
def module_export_pdf(name):
    module = MODULES.get(name)
    if not module:
        flash(_t("القسم غير موجود"), "danger")
        return redirect(url_for("ops_home"))
    if name in ("warehouse_items", "primary_team_orders"):
        return redirect(url_for("module_list", name=name))
    packed = _load_module_list_rows(name, module)
    rows = packed["rows"]
    list_cols = list(module.get("list_cols") or [f[0] for f in module.get("fields") or []])
    label_map = {f[0]: f[1] for f in module.get("fields") or []}
    headers = [_t(label_map.get(k, k)) for k in list_cols]
    filters = []
    if packed["ticket_filter"]:
        filters.append(f"{_t('رقم العطل')}: {packed['ticket_filter']}")
    if packed["item_filter"]:
        filters.append(f"{_t('المادة')}: {packed['item_filter']}")
    if packed["source_filter"]:
        filters.append(f"{_t('التخصص')}: {packed['source_filter']}")
    if packed["dept_filter"]:
        filters.append(f"{_t('القسم')}: {packed['dept_filter']}")
    if packed["date_from"] or packed["date_to"]:
        filters.append(f"{_t('من')}: {packed['date_from'] or '—'} | {_t('إلى')}: {packed['date_to'] or '—'}")
    if packed["excavation_filter"]:
        filters.append(_t("حفر فقط"))
    if packed["linked_section_filter"]:
        filters.append(f"{_t('مرتبط بتبويب')}: {_linked_section_label(packed['linked_section_filter'])}")
    if packed["missing_amount"]:
        filters.append(_t("بدون مبلغ"))
    stamp = datetime.now().strftime("%Y%m%d")
    suffix = "-مفلتر" if filters else ""
    money_keys = _module_money_keys(name, module)
    total_amount = _sum_money_field(rows, *money_keys) if money_keys else 0
    amount_cards = []
    if money_keys:
        amount_cards.append(
            {
                "title": _t("إجمالي المبالغ"),
                "value": total_amount,
                "money": True,
                "subtitle": _t("حسب الفلترة الحالية"),
            }
        )
        if module.get("section") in ("ops", "constructions", "projects", "maintenance"):
            amount_cards.extend(helpers.work_ratio_cards(base_amount=total_amount))
    data = reports_svc.build_table_pdf(
        title_text=_t(module.get("title") or name),
        headers=headers,
        rows=rows,
        field_keys=list_cols,
        filters=filters,
        amount_cards=amount_cards,
    )
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=f"{name}{suffix}-{stamp}.pdf",
        mimetype="application/pdf",
    )


@app.route("/module/<name>/new", methods=["GET", "POST"])
@login_required
def module_new(name):
    module = MODULES.get(name)
    if not module:
        return redirect(url_for("ops_home"))
    if name == "primary_team_orders":
        # الإضافة من العمليات والصيانة ← الفرق الأولية فقط (وليس المستودع)
        return redirect(url_for("ops_primary_teams"))
    if name == "warehouse_tx":
        # الإدخال المتعدد هو الافتراضي للوارد/الصرف/الإرجاع
        target = url_for("warehouse_tx_multi")
        qs = request.query_string.decode("utf-8", errors="ignore") if request.query_string else ""
        if qs:
            target = f"{target}?{qs}"
        return redirect(target)
    conn = db.connect()
    ticket_options = db.list_ticket_options(conn)
    tickets = [t["value"] for t in ticket_options]
    prefill = {f[0]: "" for f in module["fields"]}
    boq_approved_total = None
    if request.args.get("ticket_no") and "ticket_no" in prefill:
        prefill["ticket_no"] = request.args.get("ticket_no")
        ticket = db.resolve_ticket_ref(prefill["ticket_no"], conn)
        if ticket:
            if "rekaz_code" in prefill and not (prefill.get("rekaz_code") or "").strip():
                prefill["rekaz_code"] = ticket.get("rekaz_code") or ""
            if "work_order" in prefill and not (prefill.get("work_order") or "").strip():
                prefill["work_order"] = (ticket.get("work_order") or "").strip()
            if request.args.get("work_order") and "work_order" in prefill:
                prefill["work_order"] = (request.args.get("work_order") or "").strip()
        if name == "metering":
            boq_approved_total = _metering_boq_approved_total(prefill["ticket_no"], conn)
            if boq_approved_total is not None and prefill.get("approved_value") in ("", None):
                prefill["approved_value"] = boq_approved_total
    if name == "quality_clearances" and request.args.get("clearance_stage") and "clearance_stage" in prefill:
        prefill["clearance_stage"] = (request.args.get("clearance_stage") or "").strip()
    if name == "new_coordinations" and "coord_kind" in prefill:
        kind_arg = (request.args.get("coord_kind") or "").strip()
        if kind_arg:
            prefill["coord_kind"] = kind_arg
        elif not (prefill.get("coord_kind") or "").strip():
            prefill["coord_kind"] = "تنسيق جديد"
    # ربط مسار الجودة من العمليات / الإنشاءات / المشاريع
    for key in ("linked_section", "project_code", "construction_work_no", "district", "location", "work_desc", "authority", "work_order"):
        if key in prefill and request.args.get(key):
            prefill[key] = (request.args.get(key) or "").strip()
    if "linked_section" in prefill and request.args.get("linked_section"):
        sec = db.normalize_linked_section(request.args.get("linked_section"))
        if sec:
            prefill["linked_section"] = db.linked_section_label(sec)
    if name == "issued_licenses" and request.args.get("workflow_status") and "workflow_status" in prefill:
        prefill["workflow_status"] = (request.args.get("workflow_status") or "").strip()
    if name == "warehouse_tx" and request.args.get("ticket_no"):
        prefill["tx_type"] = prefill.get("tx_type") or "منصرف للمعاملة"
        prefill["tx_date"] = prefill.get("tx_date") or datetime.now().strftime("%Y-%m-%d")
    if name == "warehouse_tx":
        form_ctx = _warehouse_form_ctx()
        if form_ctx not in _warehouse_create_contexts():
            conn.close()
            flash(
                _t(
                    "إدخال معاملات المستودع يتم من صفحات المستودعات فقط، والصفحات الرئيسية للعرض فقط."
                ),
                "danger",
            )
            return redirect(url_for("warehouses_home"))
        source = _warehouse_source_from_ctx(form_ctx)
        prefill["source_section"] = source
        source_ref = (request.args.get("source_ref") or "").strip()
        if source_ref:
            prefill["source_ref"] = source_ref
            # source_ref قد يكون رقم عطل للربط — لا تضعه في أمر العمل
            if not (prefill.get("work_order") or "").strip():
                tno = (prefill.get("ticket_no") or "").strip()
                if source_ref != tno and not db.resolve_ticket_ref(source_ref, conn):
                    prefill["work_order"] = source_ref
        elif source == "ops" and prefill.get("ticket_no"):
            prefill["source_ref"] = prefill["ticket_no"]
        if prefill.get("ticket_no"):
            ticket = db.resolve_ticket_ref(prefill["ticket_no"], conn)
            if ticket:
                if "rekaz_code" in prefill and not (prefill.get("rekaz_code") or "").strip():
                    prefill["rekaz_code"] = ticket.get("rekaz_code") or ""
                if (ticket.get("work_order") or "").strip():
                    prefill["work_order"] = ticket.get("work_order")
        prefill = db.apply_warehouse_tx_work_order(prefill, conn)
        if source in ("constructions", "projects", "ops", "reinforcement"):
            requested_type = (request.args.get("tx_type") or "").strip()
            prefill["tx_type"] = requested_type or prefill.get("tx_type") or "منصرف للمعاملة"
            prefill["tx_date"] = prefill.get("tx_date") or datetime.now().strftime("%Y-%m-%d")
        reuse = str(request.args.get("reuse_voucher") or "").strip() in {"1", "on", "yes", "true"}
        existing_voucher = (request.args.get("voucher_no") or "").strip()
        if reuse and existing_voucher:
            prefill["voucher_no"] = existing_voucher
            # انسخ بيانات السند القائم للمادة الجديدة
            prev = conn.execute(
                "SELECT * FROM warehouse_tx WHERE voucher_no=? ORDER BY id LIMIT 1",
                (existing_voucher,),
            ).fetchone()
            if prev:
                prev = dict(prev)
                for k in (
                    "tx_date",
                    "ticket_no",
                    "rekaz_code",
                    "source_section",
                    "source_ref",
                    "work_order",
                    "region",
                    "recipient",
                    "sender",
                ):
                    if not (prefill.get(k) or "").strip() and prev.get(k) not in (None, ""):
                        prefill[k] = prev.get(k)
            prefill = db.apply_warehouse_tx_work_order(prefill, conn)
        elif not (prefill.get("voucher_no") or "").strip():
            prefill["voucher_no"] = db.next_warehouse_voucher_no(conn)
    if request.method == "POST":
        data = _module_form_data(module)
        form_ctx = _warehouse_form_ctx()
        if name == "photos":
            try:
                _apply_photos_from_request(data)
            except ValueError as exc:
                conn.close()
                flash(str(exc), "danger")
                section = module.get("section")
                return render_template(
                    "module_form.html",
                    name=name,
                    module=_mod(module),
                    row=data,
                    tickets=tickets,
                    ticket_options=ticket_options,
                    warehouse_items=[],
                    boq_items=[],
                    mode="new",
                    section=section,
                    section_meta=_smeta(SECTION_META.get(section)),
                    section_modules=modules_for_section(section) if section else [],
                    photo_storage=media_svc.storage_backend(),
                    photo_ephemeral=backup_svc.is_trial_free(),
                )
        try:
            _apply_attachments_from_request(name, data)
        except ValueError as exc:
            conn.close()
            flash(str(exc), "danger")
            return redirect(request.url)
        if name == "warehouse_tx":
            voucher = (data.get("voucher_no") or "").strip()
            reuse = str(request.values.get("reuse_voucher") or "").strip() in {"1", "on", "yes", "true"}
            if reuse and voucher:
                # إضافة مادة لسند قائم — لا تصدر رقماً جديداً
                data["voucher_no"] = voucher
            elif not voucher or conn.execute(
                "SELECT 1 FROM warehouse_tx WHERE voucher_no=? LIMIT 1", (voucher,)
            ).fetchone():
                data["voucher_no"] = db.next_warehouse_voucher_no(conn)
            prepared, err = _prepare_warehouse_tx_create(data, form_ctx, conn)
            if err:
                flash(err, "danger")
                return _render_warehouse_tx_form(
                    module, name, data, tickets, ticket_options, "new", form_ctx
                )
            data = prepared
        if name == "quantities":
            item_no = (data.get("item_no") or "").strip()
            if item_no and not db.get_contract_boq_item(item_no, conn):
                flash(_t("رقم البند «{item_no}» غير موجود في دليل العقد النشط", item_no=item_no), "danger")
                section = module.get("section")
                return render_template(
                    "module_form.html",
                    name=name,
                    module=_mod(module),
                    row=data,
                    tickets=tickets,
                    ticket_options=ticket_options,
                    warehouse_items=[],
                    boq_items=[],
                    mode="new",
                    section=section,
                    section_meta=_smeta(SECTION_META.get(section)),
                    section_modules=modules_for_section(section) if section else [],
                )
            data = db.enrich_quantity_from_boq(data, conn)
        if name == "metering":
            _apply_metering_approved_from_boq(data, conn)
        if name == "quality_clearances" and not (data.get("rekaz_code") or "").strip():
            data["rekaz_code"] = db.next_series_code("rr", conn)
            if not (data.get("clearance_no") or "").strip():
                data["clearance_no"] = data["rekaz_code"]
        if name == "projects" and not (data.get("project_code") or "").strip():
            data["project_code"] = db.next_series_code("pr", conn)
        if name == "contractor_supplies":
            if not (data.get("supply_no") or "").strip():
                data["supply_no"] = db.next_series_code("cs", conn)
            if not (data.get("supply_date") or "").strip():
                data["supply_date"] = datetime.now().strftime("%Y-%m-%d")
            if not (data.get("status") or "").strip():
                data["status"] = "جديد"
        if name == "custody":
            if not (data.get("custody_no") or "").strip():
                data["custody_no"] = db.next_series_code("cu", conn)
            if not (data.get("custody_date") or "").strip():
                data["custody_date"] = datetime.now().strftime("%Y-%m-%d")
            if not (data.get("status") or "").strip():
                data["status"] = "مسلمة"
            item_no = (data.get("item_no") or "").strip()
            if item_no:
                item = conn.execute(
                    "SELECT * FROM warehouse_items WHERE lower(item_no)=lower(?)",
                    (item_no,),
                ).fetchone()
                if item:
                    data["item_no"] = item["item_no"]
                    data["item_name"] = item["item_name"] or data.get("item_name") or ""
                    data["unit"] = db.normalize_warehouse_unit(item["unit"] or data.get("unit") or "")
        if name == "reinforcement_works":
            if not (data.get("work_no") or "").strip():
                data["work_no"] = db.next_series_code("rf", conn)
            if not (data.get("work_date") or "").strip():
                data["work_date"] = datetime.now().strftime("%Y-%m-%d")
            if not (data.get("status") or "").strip():
                data["status"] = "جديد"
        if name == "new_coordinations":
            if not (data.get("coord_no") or "").strip():
                data["coord_no"] = db.next_series_code("nc", conn)
            if not (data.get("request_date") or "").strip():
                data["request_date"] = datetime.now().strftime("%Y-%m-%d")
            if not (data.get("status") or "").strip():
                data["status"] = "مسودة"
            if not (data.get("coord_kind") or "").strip():
                data["coord_kind"] = "تنسيق جديد"
            if not db.normalize_linked_section(data.get("linked_section")):
                data["linked_section"] = "الإنشاءات"
        if name == "issued_licenses":
            if not (data.get("license_no") or "").strip():
                data["license_no"] = db.next_series_code("rl", conn)
            if not (data.get("issue_date") or "").strip():
                data["issue_date"] = datetime.now().strftime("%Y-%m-%d")
            if not (data.get("status") or "").strip():
                data["status"] = "سارية"
            if not db.normalize_linked_section(data.get("linked_section")):
                data["linked_section"] = "الإنشاءات"
        if name == "primary_team_orders" and not (data.get("work_order") or "").strip():
            flash(_t("أمر العمل مطلوب"), "danger")
            section = module.get("section")
            return render_template(
                "module_form.html",
                name=name,
                module=_mod(module),
                row=data,
                tickets=tickets,
                ticket_options=ticket_options,
                warehouse_items=[],
                boq_items=[],
                mode="new",
                section=section,
                section_meta=_smeta(SECTION_META.get(section)),
                section_modules=modules_for_section(section) if section else [],
                form_ctx=None,
            )
        keys = [f[0] for f in module["fields"]]
        # transferred_license_id ليس في الحقول المعروضة — لا يُدرج من النموذج
        cur = conn.execute(
            f"INSERT INTO {module['table']}({', '.join(keys)}) VALUES ({', '.join(['?']*len(keys))})",
            [data.get(k) for k in keys],
        )
        mirrored = 0
        if name == "warehouse_tx":
            mirrored = _insert_warehouse_mirror_zeros(conn, [data], form_ctx)
        link_res = None
        if name in ("contractor_works", "construction_works") and db.is_excavation_work_type(
            data.get("work_type")
        ):
            tno = (data.get("ticket_no") or "").strip()
            if tno:
                link_res = db.ensure_excavation_coordination(
                    tno,
                    reason=f"ربط تلقائي — معاملة حفر {data.get('work_no') or ''}".strip(),
                    conn=conn,
                    create_clearance=True,
                )
            else:
                flash(_t("معاملة حفر: اربط رقم العطل لبدء إجراءات الإخلاء من التنسيقات"), "danger")
        safety_created = db.ensure_excavation_safety_permits(conn)
        transfer_res = None
        if name == "new_coordinations" and (data.get("status") or "").strip() == "تم الإصدار":
            transfer_res = db.transfer_new_coordination_to_license(cur.lastrowid, conn=conn)
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
        db.log_audit(
            current_user_name(),
            "إضافة",
            module["title"],
            new_id,
            (str(data)[:200] + (f" / {_t('{n} نسخ صفري', n=mirrored)}" if mirrored else ""))[:240],
        )
        if name == "warehouse_tx" and mirrored:
            flash(
                _t(
                    "تم الحفظ مع {m} سطراً صفرياً في الاتجاه المقابل",
                    m=mirrored,
                ),
                "ok",
            )
        elif name == "external_purchases":
            flash(_t("تم حفظ الطلب — أضف الأصناف من المستودع ثم رحّلها."), "ok")
        elif name == "contractor_supplies":
            flash(_t("تم حفظ التوريد — أضف المواد من المستودع ثم رحّلها."), "ok")
        else:
            flash(_t("تمت الإضافة"), "ok")
        _flash_excavation_link(link_res)
        if safety_created:
            flash(_t("تم تحديث تصاريح السلامة لمعاملات الحفر"), "ok")
        if transfer_res and transfer_res.get("created"):
            flash(
                _t(
                    "تم نقل الرخصة المصدرة {no} إلى قسم {sec}",
                    no=transfer_res.get("license_no"),
                    sec=_linked_section_label(transfer_res.get("linked_section")),
                ),
                "ok",
            )
        _after_data_change()
        if name == "external_purchases":
            return redirect(url_for("module_edit", name=name, row_id=new_id))
        if name == "custody":
            return redirect(url_for("module_edit", name=name, row_id=new_id))
        if name == "contractor_supplies":
            if not (data.get("supply_no") or "").strip():
                # تأكد من رقم توريد بعد الإدراج إن كان فارغاً
                pass
            return redirect(url_for("module_edit", name=name, row_id=new_id))
        if name == "reinforcement_works":
            return redirect(url_for("reinforcement_work_view", row_id=new_id))
        if name == "issued_licenses":
            journey = _redirect_license_evacuations_journey(data)
            if journey:
                return journey
        return _redirect_after_module(name, data, form_ctx=_warehouse_form_ctx() if name == "warehouse_tx" else None)
    warehouse_items = db.list_warehouse_items() if name in ("warehouse_tx", "custody") else []
    reinforcement_departments = (
        db.list_reinforcement_departments(active_only=True)
        if name == "reinforcement_works"
        else []
    )
    boq_items = []
    if request.args.get("item_no") and "item_no" in prefill:
        prefill["item_no"] = request.args.get("item_no")
        if name == "warehouse_tx":
            prefill = db.enrich_warehouse_tx_from_item(prefill)
        if name == "quantities":
            prefill = db.enrich_quantity_from_boq(prefill, conn)
    if name == "reinforcement_works" and request.args.get("department") and "department" in prefill:
        prefill["department"] = request.args.get("department")
    if name == "primary_team_orders":
        # الإضافة من صفحة العمليات ← الفرق الأولية فقط
        conn.close()
        return redirect(url_for("ops_primary_teams"))
    conn.close()
    section = module.get("section")
    return render_template(
        "module_form.html",
        name=name,
        module=_mod(module),
        row=prefill,
        tickets=tickets,
        ticket_options=ticket_options,
        warehouse_items=warehouse_items,
        reinforcement_departments=reinforcement_departments,
        boq_items=boq_items,
        mode="new",
        section=section,
        section_meta=_smeta(SECTION_META.get(section)),
        section_modules=modules_for_section(section) if section else [],
        photo_storage=media_svc.storage_backend() if name == "photos" else None,
        photo_ephemeral=backup_svc.is_trial_free() if name == "photos" else False,
        boq_approved_total=boq_approved_total,
        form_ctx=_warehouse_form_ctx() if name == "warehouse_tx" else None,
        reuse_voucher=str(request.args.get("reuse_voucher") or "").strip() in {"1", "on", "yes", "true"}
        if name == "warehouse_tx"
        else False,
    )


@app.route("/module/<name>/<int:row_id>/edit", methods=["GET", "POST"])
@login_required
def module_edit(name, row_id):
    module = MODULES.get(name)
    if not module:
        return redirect(url_for("ops_home"))
    conn = db.connect()
    row = conn.execute(f"SELECT * FROM {module['table']} WHERE id=?", (row_id,)).fetchone()
    ticket_options = db.list_ticket_options(conn)
    tickets = [t["value"] for t in ticket_options]
    if not row:
        conn.close()
        flash(_t("السجل غير موجود"), "danger")
        return redirect(url_for("module_list", name=name))
    if request.method == "POST":
        data = _module_form_data(module)
        if name == "photos":
            try:
                _apply_photos_from_request(data)
            except ValueError as exc:
                conn.close()
                flash(str(exc), "danger")
                section = module.get("section")
                return render_template(
                    "module_form.html",
                    name=name,
                    module=_mod(module),
                    row={**dict(row), **data},
                    tickets=tickets,
                    ticket_options=ticket_options,
                    warehouse_items=[],
                    boq_items=[],
                    mode="edit",
                    section=section,
                    section_meta=_smeta(SECTION_META.get(section)),
                    section_modules=modules_for_section(section) if section else [],
                    photo_storage=media_svc.storage_backend(),
                    photo_ephemeral=backup_svc.is_trial_free(),
                )
        try:
            _apply_attachments_from_request(name, data)
        except ValueError as exc:
            conn.close()
            flash(str(exc), "danger")
            return redirect(request.url)
        if name == "warehouse_tx":
            data = db.enrich_warehouse_tx_from_item(data)
            data = db.enrich_warehouse_tx_codes(data, conn)
            # حافظ على المرجع الداخلي إن لم يُرسل (حقل مخفي)
            if not (data.get("source_ref") or "").strip():
                data["source_ref"] = (dict(row).get("source_ref") or "").strip()
            if not (data.get("source_section") or "").strip():
                data["source_section"] = (dict(row).get("source_section") or "").strip()
            data = db.apply_warehouse_tx_work_order(data, conn)
            linked = (
                (data.get("ticket_no") or "").strip()
                or (data.get("rekaz_code") or "").strip()
                or (data.get("source_ref") or "").strip()
                or (data.get("work_order") or "").strip()
            )
            if db.is_outbound_warehouse_tx(data.get("tx_type") or "") and not linked:
                flash(
                    _t("الصرف يتطلب ربطاً بمعاملة من الصفحات الرئيسية (عطل / إنشاءات / مشروع)."),
                    "danger",
                )
                return _render_warehouse_tx_form(
                    module,
                    name,
                    data,
                    tickets,
                    ticket_options,
                    "edit",
                    _warehouse_form_ctx(),
                )
        if name == "quantities":
            item_no = (data.get("item_no") or "").strip()
            if item_no and not db.get_contract_boq_item(item_no, conn):
                flash(_t("رقم البند «{item_no}» غير موجود في دليل العقد النشط", item_no=item_no), "danger")
                section = module.get("section")
                return render_template(
                    "module_form.html",
                    name=name,
                    module=_mod(module),
                    row=data,
                    tickets=tickets,
                    ticket_options=ticket_options,
                    warehouse_items=[],
                    boq_items=[],
                    mode="edit",
                    section=section,
                    section_meta=_smeta(SECTION_META.get(section)),
                    section_modules=modules_for_section(section) if section else [],
                )
            data = db.enrich_quantity_from_boq(data, conn)
        if name == "metering":
            _apply_metering_approved_from_boq(data, conn)
        if name == "quality_clearances" and not (data.get("rekaz_code") or "").strip():
            data["rekaz_code"] = db.next_series_code("rr", conn)
        if name == "projects" and not (data.get("project_code") or "").strip():
            data["project_code"] = db.next_series_code("pr", conn)
        if name == "contractor_supplies":
            if not (data.get("supply_no") or "").strip():
                data["supply_no"] = dict(row).get("supply_no") or db.next_series_code("cs", conn)
            if not (data.get("status") or "").strip():
                data["status"] = dict(row).get("status") or "جديد"
        if name == "custody":
            old = dict(row)
            if not (data.get("custody_no") or "").strip():
                data["custody_no"] = old.get("custody_no") or db.next_series_code("cu", conn)
            if not (data.get("status") or "").strip():
                data["status"] = old.get("status") or "مسلمة"
            if (old.get("issued_voucher_no") or "").strip():
                for k in ("item_no", "item_name", "unit", "qty", "issued_voucher_no", "return_voucher_no"):
                    if k in data:
                        data[k] = old.get(k)
            else:
                item_no = (data.get("item_no") or "").strip()
                if item_no:
                    item = conn.execute(
                        "SELECT * FROM warehouse_items WHERE lower(item_no)=lower(?)",
                        (item_no,),
                    ).fetchone()
                    if item:
                        data["item_no"] = item["item_no"]
                        data["item_name"] = item["item_name"] or data.get("item_name") or ""
                        data["unit"] = db.normalize_warehouse_unit(item["unit"] or data.get("unit") or "")
        if name == "reinforcement_works":
            if not (data.get("work_no") or "").strip():
                data["work_no"] = dict(row).get("work_no") or db.next_series_code("rf", conn)
            if not (data.get("status") or "").strip():
                data["status"] = dict(row).get("status") or "جديد"
        if name == "new_coordinations":
            if not (data.get("coord_no") or "").strip():
                data["coord_no"] = dict(row).get("coord_no") or db.next_series_code("nc", conn)
            if not (data.get("coord_kind") or "").strip():
                data["coord_kind"] = dict(row).get("coord_kind") or "تنسيق جديد"
            if not db.normalize_linked_section(data.get("linked_section")):
                data["linked_section"] = dict(row).get("linked_section") or "الإنشاءات"
        if name == "issued_licenses":
            if not (data.get("license_no") or "").strip():
                data["license_no"] = dict(row).get("license_no") or db.next_series_code("rl", conn)
            if not db.normalize_linked_section(data.get("linked_section")):
                data["linked_section"] = dict(row).get("linked_section") or "الإنشاءات"
        if name == "primary_team_orders" and not (data.get("work_order") or "").strip():
            flash(_t("أمر العمل مطلوب"), "danger")
            section = module.get("section")
            return render_template(
                "module_form.html",
                name=name,
                module=_mod(module),
                row={**dict(row), **data},
                tickets=tickets,
                ticket_options=ticket_options,
                warehouse_items=[],
                boq_items=[],
                mode="edit",
                section=section,
                section_meta=_smeta(SECTION_META.get(section)),
                section_modules=modules_for_section(section) if section else [],
                form_ctx=None,
            )
        keys = [f[0] for f in module["fields"]]
        sets = ", ".join([f"{k}=?" for k in keys])
        conn.execute(
            f"UPDATE {module['table']} SET {sets} WHERE id=?",
            [data.get(k) for k in keys] + [row_id],
        )
        link_res = None
        if name in ("contractor_works", "construction_works") and db.is_excavation_work_type(
            data.get("work_type")
        ):
            tno = (data.get("ticket_no") or "").strip()
            if tno:
                link_res = db.ensure_excavation_coordination(
                    tno,
                    reason=f"ربط تلقائي — معاملة حفر {data.get('work_no') or ''}".strip(),
                    conn=conn,
                    create_clearance=True,
                )
            else:
                flash(_t("معاملة حفر: اربط رقم العطل لبدء إجراءات الإخلاء من التنسيقات"), "danger")
        safety_created = db.ensure_excavation_safety_permits(conn)
        transfer_res = None
        if name == "new_coordinations" and (data.get("status") or "").strip() == "تم الإصدار":
            transfer_res = db.transfer_new_coordination_to_license(row_id, conn=conn)
        conn.commit()
        conn.close()
        db.log_audit(current_user_name(), "تعديل", module["title"], row_id, str(data)[:240])
        flash(_t("تم الحفظ"), "ok")
        _flash_excavation_link(link_res)
        if safety_created:
            flash(_t("تم تحديث تصاريح السلامة لمعاملات الحفر"), "ok")
        if transfer_res and transfer_res.get("created"):
            flash(
                _t(
                    "تم نقل الرخصة المصدرة {no} إلى قسم {sec}",
                    no=transfer_res.get("license_no"),
                    sec=_linked_section_label(transfer_res.get("linked_section")),
                ),
                "ok",
            )
        _after_data_change()
        # التعديل من المستودع يُبقي المستخدم داخل المستودع افتراضياً
        edit_ctx = _warehouse_form_ctx() if name == "warehouse_tx" else None
        if name == "warehouse_tx" and edit_ctx not in _warehouse_create_contexts():
            edit_ctx = "warehouses"
        if name == "issued_licenses":
            journey = _redirect_license_evacuations_journey(data)
            if journey:
                return journey
        if name == "reinforcement_works":
            return redirect(url_for("reinforcement_work_view", row_id=row_id))
        return _redirect_after_module(name, data, form_ctx=edit_ctx)
    data = dict(row)
    warehouse_items = (
        db.list_warehouse_items()
        if name in ("warehouse_tx", "external_purchases", "contractor_supplies", "custody")
        else []
    )
    boq_items = []
    boq_approved_total = None
    if name == "metering":
        boq_approved_total = _metering_boq_approved_total(data.get("ticket_no"), conn)
        if boq_approved_total is not None and data.get("approved_value") in (None, ""):
            data["approved_value"] = boq_approved_total
    quality_workflow = None
    purchase_lines = []
    custody_lines = []
    supply_lines = []
    if name == "construction_works":
        quality_workflow = db.quality_workflow_for_ref(
            ticket_no=data.get("ticket_no"),
            construction_work_no=data.get("work_no"),
            linked_section="constructions",
            conn=conn,
        )
    elif name == "projects":
        quality_workflow = db.quality_workflow_for_ref(
            ticket_no=data.get("ticket_no"),
            project_code=data.get("project_code"),
            linked_section="projects",
            conn=conn,
        )
    elif name == "external_purchases":
        purchase_lines = db.list_purchase_lines(row_id, conn=conn)
    elif name == "custody":
        custody_lines = db.list_custody_lines(row_id, conn=conn)
    elif name == "contractor_supplies":
        supply_lines = db.list_contractor_supply_lines(row_id, conn=conn)
    reinforcement_departments = []
    if name == "reinforcement_works":
        reinforcement_departments = db.list_reinforcement_departments(active_only=False, conn=conn)
    conn.close()
    section = module.get("section")
    return render_template(
        "module_form.html",
        name=name,
        module=_mod(module),
        row=data,
        tickets=tickets,
        ticket_options=ticket_options,
        warehouse_items=warehouse_items,
        boq_items=boq_items,
        mode="edit",
        section=section,
        section_meta=_smeta(SECTION_META.get(section)),
        section_modules=modules_for_section(section) if section else [],
        photo_storage=media_svc.storage_backend() if name == "photos" else None,
        photo_ephemeral=backup_svc.is_trial_free() if name == "photos" else False,
        boq_approved_total=boq_approved_total,
        quality_workflow=quality_workflow,
        purchase_lines=purchase_lines,
        custody_lines=custody_lines,
        supply_lines=supply_lines,
        reinforcement_departments=reinforcement_departments,
        form_ctx=(
            _warehouse_form_ctx() or "warehouses"
            if name == "warehouse_tx"
            else None
        ),
    )


@app.route("/module/<name>/<int:row_id>/delete", methods=["POST"])
@login_required
def module_delete(name, row_id):
    module = MODULES.get(name)
    if not module:
        return redirect(url_for("ops_home"))
    # كل عمليات الحذف تتطلب كود تأكيد يُرسل للمستخدم
    if not _delete_password_ok():
        fallback = (
            url_for("warehouse_balances", view="items")
            if name == "warehouse_items"
            else url_for("ops_primary_teams")
            if name == "primary_team_orders"
            else url_for("module_list", name=name)
        )
        return _reject_bad_delete_password(fallback)
    conn = db.connect()
    conn.execute(f"DELETE FROM {module['table']} WHERE id=?", (row_id,))
    conn.commit()
    conn.close()
    db.log_audit(current_user_name(), "حذف", module["title"], row_id)
    flash(_t("تم الحذف"), "ok")
    _after_data_change()
    if name == "warehouse_items":
        return redirect(url_for("warehouse_balances", view="items"))
    if name == "primary_team_orders":
        return redirect(url_for("ops_primary_teams"))
    return redirect(url_for("module_list", name=name))


@app.route("/module/new_coordinations/<int:row_id>/transfer", methods=["POST"])
@login_required
def new_coordination_transfer(row_id):
    """نقل تنسيق جديد إلى الرخص المصدرة وربطه بالقسم المستهدف."""
    if not permissions.can("section.quality") or not permissions.can("modules.write"):
        return permissions.deny_redirect()
    conn = db.connect()
    row = conn.execute("SELECT * FROM new_coordinations WHERE id=?", (row_id,)).fetchone()
    if not row:
        conn.close()
        flash(_t("التنسيق غير موجود"), "danger")
        return redirect(url_for("module_list", name="new_coordinations"))
    try:
        result = db.transfer_new_coordination_to_license(
            row_id,
            license_no=(request.form.get("license_no") or "").strip() or None,
            issue_date=(request.form.get("issue_date") or "").strip() or None,
            expiry_date=(request.form.get("expiry_date") or "").strip() or None,
            linked_section=(request.form.get("linked_section") or "").strip() or None,
            conn=conn,
        )
        conn.commit()
    except Exception as exc:
        conn.close()
        flash(_t("تعذر نقل الرخصة: {exc}", exc=exc), "danger")
        return redirect(url_for("module_edit", name="new_coordinations", row_id=row_id))
    conn.close()
    if result.get("created"):
        flash(
            _t(
                "تم نقل الرخصة المصدرة {no} إلى قسم {sec}",
                no=result.get("license_no"),
                sec=_linked_section_label(result.get("linked_section")),
            ),
            "ok",
        )
        db.log_audit(
            current_user_name(),
            "نقل رخصة",
            "التنسيقات الجديدة",
            row_id,
            result.get("license_no") or "",
        )
        _after_data_change()
        return redirect(url_for("quality_home", tab="permits", sub="active"))
    flash(_t("الرخصة منقولة مسبقاً"), "ok")
    return redirect(url_for("quality_home", tab="permits", sub="active"))


# ---------- Cashflow (مخفي من الواجهة — يوجّه للمتابعات المالية) ----------
@app.route("/cashflow", methods=["GET", "POST"])
@login_required
def cashflow():
    flash(_t("صفحة التدفق النقدي غير مفعّلة في الواجهة. راجع المستخلصات من المتابعات المالية."), "ok")
    return redirect(url_for("financial_home"))


@app.route("/ops/primary-teams", methods=["GET", "POST"])
@login_required
def ops_primary_teams():
    """الفرق الأولية (أوامر عمل الكهرباء) — الإضافة من العمليات والصيانة وليس من المستودع."""
    conn = db.connect()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            if not permissions.can("modules.write") or not permissions.can("button.module.primary_team_orders.add"):
                conn.close()
                flash(_t("لا تملك صلاحية الإضافة."), "danger")
                return redirect(url_for("ops_primary_teams"))
            work_order = (request.form.get("work_order") or "").strip()
            if not work_order:
                flash(_t("أمر العمل مطلوب"), "danger")
            else:
                amount_raw = (request.form.get("amount") or "").strip()
                amount = None
                if amount_raw:
                    try:
                        amount = float(amount_raw)
                    except ValueError:
                        amount = None
                order_date = (request.form.get("order_date") or "").strip() or datetime.now().strftime(
                    "%Y-%m-%d"
                )
                cur = conn.execute(
                    """
                    INSERT INTO primary_team_orders(work_order, extract_no, amount, order_date, notes)
                    VALUES (?,?,?,?,?)
                    """,
                    (
                        work_order,
                        (request.form.get("extract_no") or "").strip(),
                        amount,
                        order_date,
                        (request.form.get("notes") or "").strip(),
                    ),
                )
                conn.commit()
                db.log_audit(
                    current_user_name(),
                    "إضافة",
                    "الفرق الأولية",
                    cur.lastrowid,
                    work_order,
                )
                flash(_t("تمت إضافة أمر العمل"), "ok")
                _after_data_change()
        elif action == "delete":
            if not permissions.can("modules.write") or not permissions.can("button.module.primary_team_orders.delete"):
                conn.close()
                flash(_t("لا تملك صلاحية الحذف."), "danger")
                return redirect(url_for("ops_primary_teams"))
            if not _delete_password_ok():
                conn.close()
                return _reject_bad_delete_password(url_for("ops_primary_teams"))
            row_id = request.form.get("id")
            conn.execute("DELETE FROM primary_team_orders WHERE id=?", (row_id,))
            conn.commit()
            db.log_audit(current_user_name(), "حذف", "الفرق الأولية", row_id)
            flash(_t("تم الحذف"), "ok")
            _after_data_change()
    q = (request.args.get("q") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    missing_amount = _missing_amount_flag()
    rows = db.rows_to_dicts(
        conn.execute("SELECT * FROM primary_team_orders ORDER BY id DESC").fetchall()
    )
    conn.close()
    if q:
        ql = q.lower()
        rows = [
            r
            for r in rows
            if ql in (r.get("work_order") or "").lower()
            or ql in (r.get("extract_no") or "").lower()
            or ql in (str(r.get("amount") or "")).lower()
            or ql in (r.get("notes") or "").lower()
        ]
    rows = _filter_rows_by_date_range(rows, date_from, date_to, "order_date")
    missing_count = _count_missing_amount(rows, "amount")
    if missing_amount:
        rows = _filter_missing_amount_rows(rows, "amount")
    summary_cards = build_list_summary_cards(
        rows,
        count_label=_t("عدد الأوامر"),
        money_keys=("amount",),
        date_keys=("order_date",),
        detail_key="work_order",
        missing_amount_count=missing_count,
        missing_amount_active=missing_amount,
        missing_amount_endpoint="ops_primary_teams",
    )
    summary_cards.extend(helpers.work_ratio_cards(base_amount=_sum_money_field(rows, "amount")))
    return render_template(
        "primary_teams.html",
        rows=rows,
        q=q,
        date_from=date_from,
        date_to=date_to,
        today=datetime.now().strftime("%Y-%m-%d"),
        missing_amount=missing_amount,
        export_href=_url_with_filters("export_primary_teams_excel"),
        export_pdf_href=_url_with_filters("export_primary_teams_pdf"),
        summary_cards=summary_cards,
    )


@app.route("/ops/primary-teams/export.xlsx")
@login_required
def export_primary_teams_excel():
    q = (request.args.get("q") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    missing_amount = _missing_amount_flag()
    conn = db.connect()
    rows = db.rows_to_dicts(
        conn.execute("SELECT * FROM primary_team_orders ORDER BY id DESC").fetchall()
    )
    conn.close()
    if q:
        ql = q.lower()
        rows = [
            r
            for r in rows
            if ql in (r.get("work_order") or "").lower()
            or ql in (r.get("extract_no") or "").lower()
            or ql in (str(r.get("amount") or "")).lower()
            or ql in (r.get("notes") or "").lower()
        ]
    rows = _filter_rows_by_date_range(rows, date_from, date_to, "order_date")
    if missing_amount:
        rows = _filter_missing_amount_rows(rows, "amount")
    headers = [
        _t("أمر العمل"),
        _t("رقم المستخلص"),
        _t("المبلغ"),
        _t("التاريخ"),
        _t("ملاحظات"),
    ]
    fields = ["work_order", "extract_no", "amount", "order_date", "notes"]
    stamp = datetime.now().strftime("%Y%m%d")
    suffix = "-بدون-مبلغ" if missing_amount else ""
    return _simple_xlsx_export(
        _t("الفرق الأولية"),
        headers,
        rows,
        fields,
        f"الفرق-الأولية{suffix}-{stamp}.xlsx",
    )


@app.route("/ops/primary-teams/export.pdf")
@login_required
def export_primary_teams_pdf():
    q = (request.args.get("q") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    missing_amount = _missing_amount_flag()
    conn = db.connect()
    rows = db.rows_to_dicts(
        conn.execute("SELECT * FROM primary_team_orders ORDER BY id DESC").fetchall()
    )
    conn.close()
    if q:
        ql = q.lower()
        rows = [
            r
            for r in rows
            if ql in (r.get("work_order") or "").lower()
            or ql in (r.get("extract_no") or "").lower()
            or ql in (str(r.get("amount") or "")).lower()
            or ql in (r.get("notes") or "").lower()
        ]
    rows = _filter_rows_by_date_range(rows, date_from, date_to, "order_date")
    if missing_amount:
        rows = _filter_missing_amount_rows(rows, "amount")
    headers = [
        _t("أمر العمل"),
        _t("رقم المستخلص"),
        _t("المبلغ"),
        _t("التاريخ"),
        _t("ملاحظات"),
    ]
    fields = ["work_order", "extract_no", "amount", "order_date", "notes"]
    filters = []
    if q:
        filters.append(f"{_t('بحث')}: {q}")
    if date_from or date_to:
        filters.append(f"{_t('من')}: {date_from or '—'} | {_t('إلى')}: {date_to or '—'}")
    if missing_amount:
        filters.append(_t("بدون مبلغ"))
    data = reports_svc.build_table_pdf(
        title_text=_t("الفرق الأولية"),
        headers=headers,
        rows=rows,
        field_keys=fields,
        filters=filters,
        amount_cards=[
            {
                "title": _t("إجمالي المبالغ"),
                "value": _sum_money_field(rows, "amount"),
                "money": True,
                "subtitle": _t("حسب الفلترة الحالية"),
            },
            *helpers.work_ratio_cards(base_amount=_sum_money_field(rows, "amount")),
        ],
    )
    stamp = datetime.now().strftime("%Y%m%d")
    suffix = "-مفلتر" if filters else ""
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=f"الفرق-الأولية{suffix}-{stamp}.pdf",
        mimetype="application/pdf",
    )


@app.route("/teams", methods=["GET", "POST"])
@login_required
def teams_page():
    conn = db.connect()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            conn.execute(
                "INSERT INTO teams(name,leader,technicians,driver,vehicle,area,status,notes) VALUES (?,?,?,?,?,?,?,?)",
                (
                    request.form.get("name"),
                    request.form.get("leader"),
                    request.form.get("technicians") or 0,
                    request.form.get("driver"),
                    request.form.get("vehicle"),
                    request.form.get("area"),
                    request.form.get("status") or "نشطة",
                    request.form.get("notes"),
                ),
            )
            conn.commit()
            flash(_t("تمت إضافة الفرقة"), "ok")
        elif action == "delete":
            if not _delete_password_ok():
                conn.close()
                return _reject_bad_delete_password(url_for("teams_page"))
            conn.execute("DELETE FROM teams WHERE id=?", (request.form.get("id"),))
            conn.commit()
            flash(_t("تم الحذف"), "ok")
    rows = db.rows_to_dicts(conn.execute("SELECT * FROM teams ORDER BY id").fetchall())
    conn.close()
    active_n = sum(1 for r in rows if (r.get("status") or "") == "نشطة")
    tech_n = sum(int(r.get("technicians") or 0) for r in rows)
    summary_cards = [
        _summary_card(_t("عدد الفرق"), len(rows), _t("كل الفرق المسجّلة")),
        _summary_card(_t("فرق نشطة"), active_n, _t("جاهزة للمهام")),
        _summary_card(_t("إجمالي الفنيين"), tech_n, _t("مجموع عدد الفنيين")),
        _summary_card(
            _t("فرق متوقفة / صيانة"),
            len(rows) - active_n,
            _t("غير جاهزة حالياً"),
        ),
    ]
    return render_template("teams.html", rows=rows, summary_cards=summary_cards)


@app.route("/admin/backups", defaults={"subpath": ""})
@app.route("/admin/backups/<path:subpath>")
def backups_ui_removed(subpath=""):
    """صفحة الحفظ أُزيلت من واجهة المنتج — الحفظ يعمل تلقائياً في الخلفية."""
    abort(404)


@app.route("/api/backups/latest")
def api_backups_latest():
    """سحب أحدث حفظة للجهاز الرئيسي (يتطلب رمز المزامنة) — للاستخدام التشغيلي فقط."""
    token = request.args.get("token") or request.headers.get("X-Backup-Token")
    if not backup_svc.token_matches(token):
        return {"ok": False, "error": "رمز غير صالح"}, 401
    try:
        backup_svc.create_auto_backup(force=False)
    except Exception:
        pass
    latest = backup_svc.latest_backup(purpose="auto") or backup_svc.latest_backup()
    if not latest:
        return {"ok": False, "error": "لا توجد حفظات بعد"}, 404
    zip_path = backup_svc.build_backup_zip(latest["_rel"])
    stamp = (latest.get("created_at") or "").replace(":", "").replace("T", "-")
    return send_file(
        zip_path,
        as_attachment=True,
        download_name=f"rekaz-auto-{stamp or 'latest'}.zip",
        mimetype="application/zip",
    )


@app.route("/api/backups/auto-run")
def api_backups_auto_run():
    """تشغيل دورة الحفظ التلقائي (من keep-alive أو الجهاز الرئيسي)."""
    token = request.args.get("token") or request.headers.get("X-Backup-Token")
    if not backup_svc.token_matches(token):
        return {"ok": False, "error": "رمز غير صالح"}, 401
    force = (request.args.get("force") or "").strip() in {"1", "true", "yes"}
    result = backup_svc.create_auto_backup(force=force)
    return {
        "ok": True,
        **{k: v for k, v in result.items() if k != "backup"},
        "backup_id": (result.get("backup") or {}).get("id"),
    }, 200


@app.route("/api/backups/sync-status")
def api_backups_sync_status():
    token = request.args.get("token") or request.headers.get("X-Backup-Token")
    if not backup_svc.token_matches(token):
        return {"ok": False, "error": "رمز غير صالح"}, 401
    return {"ok": True, **backup_svc.auto_status()}, 200


@app.route("/warehouses/balances")
@login_required
def warehouse_balances():
    view = (request.args.get("view") or "balances").strip().lower()
    if view not in ("balances", "items"):
        view = "balances"
    q = (request.args.get("q") or "").strip().lower()
    conn = db.connect()
    items = db.rows_to_dicts(conn.execute("SELECT * FROM warehouse_items ORDER BY item_no").fetchall())
    conn.close()
    for item in items:
        item["unit"] = db.normalize_warehouse_unit(item.get("unit"))
        detail = db.warehouse_balance_detail(item.get("item_no"))
        item.update(detail)
    if q:
        items = [
            r
            for r in items
            if q in (r.get("item_no") or "").lower()
            or q in (r.get("item_name") or "").lower()
            or q in (r.get("category") or "").lower()
        ]
    hint = _t("حسب البحث الحالي") if q else _t("حسب الفلتر الحالي")
    summary_cards = [
        _summary_card(_t("عدد الأصناف"), len(items), hint),
        _summary_card(
            _t("إجمالي الوارد"),
            f"{sum(float(r.get('inbound') or 0) for r in items):.2f}",
            hint,
        ),
        _summary_card(
            _t("إجمالي المنصرف"),
            f"{sum(float(r.get('outbound') or 0) for r in items):.2f}",
            hint,
        ),
        _summary_card(
            _t("محجوز للعهد"),
            f"{sum(float(r.get('reserved') or 0) for r in items):.2f}",
            _t("بنود عهد لم تُصرف من المستودع بعد"),
        ),
        _summary_card(
            _t("رصيد ركاز المتاح"),
            f"{sum(float(r.get('available_balance') if r.get('available_balance') is not None else r.get('balance') or 0) for r in items):.2f}",
            _t("الرصيد − حجوزات العهد"),
        ),
    ]
    return render_template(
        "warehouse_balances.html",
        rows=items,
        q=q,
        view=view,
        warehouse_active="balances",
        summary_cards=summary_cards,
    )


@app.route("/warehouses/items/template.xlsx")
@login_required
def warehouse_items_template():
    data = warehouse_excel.build_items_template()
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name="قالب_أصناف_المستودع.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/warehouses/balances/template.xlsx")
@login_required
def warehouse_items_template_legacy():
    """توافق مع الروابط القديمة — يوجّه لقالب الأصناف."""
    return redirect(url_for("warehouse_items_template"))


@app.route("/warehouses/tx/template.xlsx")
@login_required
def warehouse_tx_template():
    data = warehouse_excel.build_tx_template()
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name="قالب_حركات_المستودع.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/warehouses/items/import", methods=["POST"])
@login_required
def warehouse_items_import():
    if not permissions.can("modules.write"):
        return permissions.deny_redirect()
    f = request.files.get("file")
    if not f or not f.filename:
        flash(_t("اختر ملف Excel للمواد"), "danger")
        return redirect(url_for("warehouse_balances", view="items"))
    try:
        result = warehouse_excel.import_items_from_excel(f)
        flash(
            f"استيراد الأصناف: جديد {result['ok']} | محدّث {result['updated']} | أرصدة افتتاحية {result['opening']}",
            "ok",
        )
        if result.get("errors"):
            flash(" / ".join(result["errors"][:5]), "danger")
        db.log_audit(current_user_name(), "استيراد Excel", "أصناف المستودع", details=str(result)[:240])
    except Exception as exc:
        flash(_t("تعذر الاستيراد: {exc}", exc=exc), "danger")
    return redirect(url_for("warehouse_balances", view="items"))


@app.route("/warehouses/balances/import", methods=["POST"])
@login_required
def warehouse_items_import_legacy():
    """توافق قديم — الاستيراد أصبح من أصناف المستودع."""
    return warehouse_items_import()


@app.route("/warehouses/balances/clear", methods=["POST"])
@login_required
def warehouse_balances_clear():
    """مسح كل حركات المستودع → أرصدة صفرية مع الإبقاء على أصناف المواد."""
    if not permissions.can("modules.write"):
        return permissions.deny_redirect()
    if not _delete_password_ok():
        return _reject_bad_delete_password(url_for("warehouse_balances"))
    confirm = (request.form.get("confirm") or "").strip()
    if confirm != "مسح":
        flash(_t('للتأكيد اكتب كلمة «مسح» في خانة التأكيد ثم أعد المحاولة.'), "danger")
        return redirect(url_for("warehouse_balances"))
    try:
        deleted = db.clear_warehouse_balances()
        flash(_t("تم مسح الأرصدة: حُذفت {deleted} حركة مستودع. الأصناف بقيت كما هي.", deleted=deleted), "ok")
        db.log_audit(current_user_name(), "مسح أرصدة", "معاملات المستودع", details=f"deleted={deleted}")
    except Exception as exc:
        flash(_t("تعذر مسح الأرصدة: {exc}", exc=exc), "danger")
    return redirect(url_for("warehouse_balances"))


@app.route("/warehouses/tx/import", methods=["POST"])
@login_required
def warehouse_tx_import():
    if not permissions.can("modules.write"):
        return permissions.deny_redirect()
    flash(
        _t(
            "إدخال معاملات المستودع يتم من صفحات المستودعات فقط، والصفحات الرئيسية للعرض فقط."
        ),
        "danger",
    )
    return redirect(url_for("module_list", name="warehouse_tx"))


@app.route("/users/list", methods=["GET", "POST"])
@login_required
def users_list():
    conn = db.connect()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            try:
                username = (request.form.get("username") or "").strip()
                if db.is_hidden_username(username):
                    flash(_t("تعذر الإضافة"), "danger")
                else:
                    role = permissions.normalize_role(request.form.get("role") or "مدخل بيانات")
                    conn.execute(
                        "INSERT INTO users(username, full_name, email, mobile, role, active, password, notes, is_hidden) VALUES (?,?,?,?,?,?,?,?,0)",
                        (
                            username,
                            request.form.get("full_name"),
                            (request.form.get("email") or "").strip(),
                            (request.form.get("mobile") or "").strip(),
                            role,
                            1 if request.form.get("active") == "1" else 0,
                            request.form.get("password") or "1234",
                            request.form.get("notes"),
                        ),
                    )
                    conn.commit()
                    db.log_audit(current_user_name(), "إضافة", "مستخدم", details=username)
                    flash(_t("تم إضافة المستخدم"), "ok")
            except Exception as exc:
                flash(_t("تعذر الإضافة: {exc}", exc=exc), "danger")
        elif action == "update":
            uid = request.form.get("id")
            target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if db.user_is_hidden(target):
                flash(_t("تعذر التحديث"), "danger")
            else:
                role = permissions.normalize_role(request.form.get("role") or "مدخل بيانات")
                password = (request.form.get("password") or "").strip()
                if password:
                    conn.execute(
                        "UPDATE users SET full_name=?, email=?, mobile=?, role=?, active=?, password=?, notes=? WHERE id=?",
                        (
                            request.form.get("full_name"),
                            (request.form.get("email") or "").strip(),
                            (request.form.get("mobile") or "").strip(),
                            role,
                            1 if request.form.get("active") == "1" else 0,
                            password,
                            request.form.get("notes"),
                            uid,
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE users SET full_name=?, email=?, mobile=?, role=?, active=?, notes=? WHERE id=?",
                        (
                            request.form.get("full_name"),
                            (request.form.get("email") or "").strip(),
                            (request.form.get("mobile") or "").strip(),
                            role,
                            1 if request.form.get("active") == "1" else 0,
                            request.form.get("notes"),
                            uid,
                        ),
                    )
                conn.commit()
                if str(session.get("user_id")) == str(uid):
                    session["full_name"] = request.form.get("full_name")
                    session["role"] = role
                db.log_audit(current_user_name(), "تعديل", "مستخدم", uid)
                flash(_t("تم تحديث المستخدم"), "ok")
        elif action == "user_permissions":
            uid = request.form.get("id")
            target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if db.user_is_hidden(target):
                flash(_t("تعذر تحديث صلاحيات هذا المستخدم"), "danger")
            else:
                db.ensure_user_permission_overrides_table(conn)
                conn.execute("DELETE FROM user_permission_overrides WHERE user_id=?", (uid,))
                for perm in permissions.PERM_LABELS:
                    effect = (request.form.get("perm__" + perm) or "").strip()
                    if effect in {"allow", "deny"}:
                        conn.execute(
                            "INSERT INTO user_permission_overrides(user_id, perm, effect) VALUES (?,?,?)",
                            (uid, perm, effect),
                        )
                conn.commit()
                db.log_audit(current_user_name(), "تحديث صلاحيات فردية", "مستخدم", uid)
                flash(_t("تم تحديث صلاحيات المستخدم الخاصة"), "ok")
        elif action == "regen_api_key":
            uid = request.form.get("id")
            target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if db.user_is_hidden(target):
                flash(_t("تعذر التحديث"), "danger")
            elif not permissions.has_perm("api.access", role=target["role"]):
                flash(_t("دور هذا المستخدم لا يملك صلاحية الوصول عبر API"), "danger")
            else:
                db.regenerate_api_key(uid, conn)
                flash(_t("تم إنشاء مفتاح API جديد للمستخدم. المفتاح القديم لم يعد صالحاً."), "ok")
        elif action == "send_test_email":
            uid = request.form.get("id")
            target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if db.user_is_hidden(target):
                flash(_t("تعذّر إرسال البريد"), "danger")
            elif not target:
                flash(_t("المستخدم غير موجود"), "danger")
            else:
                email = (target["email"] or "").strip()
                if not email:
                    flash(_t("لا يوجد بريد إلكتروني لهذا المستخدم"), "danger")
                else:
                    subject = _t("رسالة تجريبية من نظام ركاز")
                    body = _t(
                        "هذه رسالة مؤقتة لتأكيد ربط البريد في نظام ركاز.\n\nالمستخدم: {name}\n\nسيتم لاحقاً ترتيب رسائل الإشعارات الرسمية.",
                        name=target["full_name"] or target["username"],
                    )
                    ok, err = mailer.send_email(to_addrs=[email], subject=subject, body=body)
                    if ok:
                        db.log_audit(current_user_name(), "إرسال بريد تجريبي", "مستخدم", uid, email)
                        flash(_t("تم إرسال البريد التجريبي إلى {email}", email=email), "ok")
                    else:
                        flash(_t("تعذّر إرسال البريد: {err}", err=err), "danger")
        elif action == "delete":
            if not _delete_password_ok():
                conn.close()
                return _reject_bad_delete_password(url_for("users_list"))
            uid = request.form.get("id")
            target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if db.user_is_hidden(target):
                flash(_t("تعذر الحذف"), "danger")
            elif target and str(session.get("user_id")) == str(uid):
                flash(_t("لا يمكن حذف حسابك الحالي"), "danger")
            else:
                admins = conn.execute(
                    "SELECT COUNT(*) FROM users WHERE lower(role)='admin' AND active=1 AND coalesce(is_hidden,0)=0"
                ).fetchone()[0]
                if target and permissions.normalize_role(target["role"]) == "admin" and admins <= 1:
                    flash(_t("لا يمكن حذف آخر مدير نظام نشط"), "danger")
                else:
                    conn.execute("DELETE FROM users WHERE id=?", (uid,))
                    conn.commit()
                    db.log_audit(current_user_name(), "حذف", "مستخدم", uid)
                    flash(_t("تم الحذف"), "ok")
        elif action == "toggle":
            uid = request.form.get("id")
            target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if db.user_is_hidden(target):
                flash(_t("تعذر التحديث"), "danger")
            elif target and str(session.get("user_id")) == str(uid):
                flash(_t("لا يمكن إيقاف حسابك الحالي"), "danger")
            else:
                conn.execute(
                    "UPDATE users SET active = CASE WHEN active=1 THEN 0 ELSE 1 END WHERE id=?",
                    (uid,),
                )
                conn.commit()
                flash(_t("تم تحديث الحالة"), "ok")
    rows = db.list_visible_users(conn)
    db.ensure_user_permission_overrides_table(conn)
    for row in rows:
        row["role"] = permissions.normalize_role(row.get("role"))
        row["perm_overrides"] = db.user_permission_overrides(row["id"], conn)
        row["role_perm_set"] = permissions.perms_for_role(row["role"])
        if row["role"] == "admin":
            row["role_perm_set"] = set(permissions.ALL_PERMS)
        row["perm_count"] = len(permissions.effective_perms_for_user(row))
        row["perm_allow_count"] = sum(1 for effect in row["perm_overrides"].values() if effect == "allow")
        row["perm_deny_count"] = sum(1 for effect in row["perm_overrides"].values() if effect == "deny")
    conn.close()
    return render_template(
        "users.html",
        rows=rows,
        perm_labels=permissions.PERM_LABELS,
    )


@app.route("/admin/audit-log/view")
@login_required
def audit_log_page():
    q = (request.args.get("q") or "").strip()
    conn = db.connect()
    if q:
        like = f"%{q}%"
        rows = db.rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM audit_log
                WHERE user_name LIKE ? OR action LIKE ? OR entity LIKE ? OR details LIKE ?
                ORDER BY id DESC LIMIT 300
                """,
                (like, like, like, like),
            ).fetchall()
        )
    else:
        rows = db.rows_to_dicts(conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 300").fetchall())
    conn.close()
    return render_template("audit_log.html", rows=rows, q=q)


@app.route("/search")
@login_required
def global_search():
    q = (request.args.get("q") or "").strip()
    results = []
    if q:
        like = f"%{q}%"
        conn = db.connect()
        for t in conn.execute(
            """
            SELECT id, ticket_no, district, fault_type, status FROM tickets
            WHERE ticket_no LIKE ? OR district LIKE ? OR fault_type LIKE ? OR station_no LIKE ?
            LIMIT 40
            """,
            (like, like, like, like),
        ).fetchall():
            results.append(
                {
                    "scope": "العمليات",
                    "tab": "الأعطال",
                    "label": t["ticket_no"],
                    "detail": f"{t['district'] or ''} — {t['fault_type'] or ''} — {t['status'] or ''}",
                    "url": url_for("ticket_view", ticket_id=t["id"]),
                    "trace_url": url_for("transaction_trace", q=t["ticket_no"]),
                }
            )
        for name, mod in MODULES.items():
            cols = [f[0] for f in mod["fields"] if f[2] in ("text", "ticket")][:3]
            if not cols:
                continue
            where = " OR ".join([f"CAST({c} AS TEXT) LIKE ?" for c in cols])
            try:
                rows = conn.execute(
                    f"SELECT id, {', '.join(cols)} FROM {mod['table']} WHERE {where} LIMIT 20",
                    tuple([like] * len(cols)),
                ).fetchall()
            except Exception:
                continue
            for r in rows:
                detail = " | ".join(str(r[c] or "") for c in cols)
                results.append(
                    {
                        "scope": SECTION_META.get(mod.get("section"), {}).get("title", ""),
                        "tab": mod["title"],
                        "label": detail[:80],
                        "detail": detail,
                        "url": url_for("module_edit", name=name, row_id=r["id"]),
                        "trace_url": url_for("transaction_trace", q=str(r[cols[0]] or q)),
                    }
                )
        conn.close()
    return render_template("search.html", q=q, results=results)


def _trace_cols(conn, table):
    try:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _trace_rows(conn, table, match_cols, q, order_col="id", limit=80):
    cols = _trace_cols(conn, table)
    usable = [c for c in match_cols if c in cols]
    if not usable:
        return []
    where = " OR ".join([f"trim(coalesce({c},''))=?" for c in usable])
    order = order_col if order_col in cols else "id"
    try:
        return db.rows_to_dicts(
            conn.execute(
                f"SELECT * FROM {table} WHERE {where} ORDER BY {order} DESC LIMIT ?",
                tuple([q] * len(usable)) + (limit,),
            ).fetchall()
        )
    except Exception:
        return []


def _trace_add(items, *, step, title, section, status="", date="", detail="", url="", severity="ok"):
    items.append(
        {
            "step": step,
            "title": title,
            "section": section,
            "status": status or "—",
            "date": date or "",
            "detail": detail or "",
            "url": url or "",
            "severity": severity,
        }
    )


def _transaction_trace_payload(q):
    q = (q or "").strip()
    payload = {"q": q, "items": [], "issues": [], "summary": {}, "current": None}
    if not q:
        return payload
    conn = db.connect()
    items = payload["items"]

    tickets = _trace_rows(conn, "tickets", ("ticket_no", "rekaz_code", "work_order"), q, "id")
    for r in tickets:
        _trace_add(
            items,
            step="1",
            title="تم إدخال العطل",
            section="العمليات والصيانة",
            status=r.get("status"),
            date=r.get("receive_date"),
            detail=f"{r.get('ticket_no') or ''} / {r.get('fault_type') or ''} / {r.get('district') or ''}",
            url=url_for("ticket_view", ticket_id=r["id"]),
        )

    primary = _trace_rows(conn, "primary_team_orders", ("work_order", "extract_no"), q, "id")
    for r in primary:
        _trace_add(
            items,
            step="1",
            title="أمر عمل فرق أولية",
            section="العمليات والصيانة",
            status=r.get("extract_no") or "مدخل",
            date=r.get("order_date") or r.get("created_at"),
            detail=f"{r.get('work_order') or ''} / {r.get('amount') or 0} ر.س",
            url=url_for("ops_primary_teams", q=r.get("work_order") or q),
        )

    reinforcement = _trace_rows(conn, "reinforcement_works", ("work_no", "ticket_no", "station_no"), q, "id")
    for r in reinforcement:
        _trace_add(
            items,
            step="1",
            title="معاملة تعزيز / اسكيمات",
            section=r.get("department") or "التعزيز",
            status=r.get("status"),
            date=r.get("work_date"),
            detail=f"{r.get('work_no') or ''} / {r.get('work_type') or ''} / {r.get('value') or 0} ر.س",
            url=url_for("reinforcement_work_view", row_id=r["id"]),
        )

    for table, title, section, url_builder in (
        ("construction_works", "معاملة إنشاءات", "الإنشاءات", lambda r: url_for("module_edit", name="construction_works", row_id=r["id"])),
        ("projects", "مشروع", "المشاريع", lambda r: url_for("module_edit", name="projects", row_id=r["id"])),
    ):
        rows = _trace_rows(conn, table, ("work_no", "project_code", "ticket_no"), q, "id")
        for r in rows:
            code = r.get("work_no") or r.get("project_code") or r.get("ticket_no") or q
            _trace_add(
                items,
                step="1",
                title=title,
                section=section,
                status=r.get("status"),
                date=r.get("work_date") or r.get("start_date"),
                detail=f"{code} / {r.get('work_type') or r.get('project_name') or ''} / {r.get('value') or 0} ر.س",
                url=url_builder(r),
            )

    linked_sets = (
        ("quantities", "بنود وكميات", "الكميات", ("ticket_no",), "id"),
        ("photos", "صور ومرفقات", "الصور", ("ticket_no",), "id"),
        ("metering", "اعتماد/تمتير", "التمتير", ("ticket_no",), "id"),
        ("invoices", "مستخلصات وفوترة", "المتابعات المالية", ("ticket_no", "work_order", "invoice_id", "invoice_no"), "id"),
        ("warehouse_tx", "حركة مستودع", "المستودعات", ("ticket_no", "rekaz_code", "work_order", "source_ref", "voucher_no"), "id"),
        ("new_coordinations", "تنسيق جديد", "التنسيقات", ("ticket_no", "project_code", "construction_work_no", "coord_no", "license_no"), "id"),
        ("issued_licenses", "متابعة تصريح", "التصاريح", ("ticket_no", "project_code", "construction_work_no", "work_order", "license_no", "rtc_no"), "id"),
        ("quality_clearances", "إخلاء", "الجودة", ("ticket_no", "rekaz_code", "clearance_no"), "id"),
        ("safety_permits", "تصريح سلامة", "السلامة", ("ticket_no", "work_order", "permit_no"), "id"),
        ("external_purchases", "مشتريات خارجية", "المشتريات", ("purchase_no", "ticket_no", "received_voucher_no"), "id"),
        ("custody", "عهدة", "العهد", ("custody_no", "issued_voucher_no", "return_voucher_no"), "id"),
    )
    for table, title, section, cols, order in linked_sets:
        for r in _trace_rows(conn, table, cols, q, order):
            date = (
                r.get("tx_date")
                or r.get("request_date")
                or r.get("issue_date")
                or r.get("permit_date")
                or r.get("purchase_date")
                or r.get("custody_date")
                or r.get("submit_date")
                or r.get("invoice_date")
            )
            status = r.get("status") or r.get("tx_type") or r.get("sap_status") or r.get("workflow_status") or "مرتبط"
            if table == "warehouse_tx":
                if not (r.get("source_ref") or r.get("ticket_no") or r.get("work_order")):
                    payload["issues"].append(f"سند مستودع {r.get('voucher_no') or r.get('id')} بدون مرجع واضح للمعاملة.")
                url = url_for("warehouse_voucher_detail", voucher_no=r["voucher_no"]) if r.get("voucher_no") else url_for("module_edit", name=table, row_id=r["id"])
                detail = f"{r.get('voucher_no') or ''} / {r.get('item_no') or ''} {r.get('item_name') or ''} / {r.get('qty') or 0}"
            else:
                url = url_for("module_edit", name=table, row_id=r["id"])
                detail = " / ".join(str(x or "") for x in (r.get("ticket_no"), r.get("work_order"), r.get("license_no"), r.get("notes")) if x)[:180]
            _trace_add(items, step="2", title=title, section=section, status=status, date=date, detail=detail, url=url)

    audits = []
    try:
        audits = db.rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM audit_log
                WHERE details LIKE ? OR entity_id LIKE ?
                ORDER BY id DESC LIMIT 20
                """,
                (f"%{q}%", f"%{q}%"),
            ).fetchall()
        )
    except Exception:
        audits = []
    for r in audits:
        _trace_add(
            items,
            step="3",
            title=f"{r.get('action') or 'نشاط'} - {r.get('entity') or ''}",
            section="سجل النشاط",
            status=r.get("user_name") or "مسجل",
            date=r.get("created_at"),
            detail=r.get("details") or "",
            url=url_for("audit_log_page", q=q),
            severity="audit",
        )
    conn.close()

    if not any(i["step"] == "1" for i in items):
        payload["issues"].append("لم يتم العثور على سجل إدخال رئيسي لهذا الرقم؛ قد يكون الرقم تابعاً لسند أو مرحلة لاحقة فقط.")
    if items and not any(i["section"] in ("المستودعات", "التنسيقات", "التصاريح", "الجودة", "السلامة", "المتابعات المالية") for i in items):
        payload["issues"].append("المعاملة موجودة في البداية فقط ولم تظهر لها مراحل لاحقة بعد.")
    payload["summary"] = {
        "count": len(items),
        "issues": len(payload["issues"]),
        "sections": len({i["section"] for i in items}),
    }
    practical_items = [i for i in items if i.get("section") != "سجل النشاط"]
    payload["current"] = (practical_items[-1] if practical_items else (items[-1] if items else None))
    return payload


@app.route("/trace")
@login_required
def transaction_trace():
    q = (request.args.get("q") or "").strip()
    trace = _transaction_trace_payload(q)
    return render_template("transaction_trace.html", q=q, trace=trace)


# ---------- Jump ----------
@app.route("/api/jump-destinations")
@login_required
def api_jump_destinations():
    from flask import jsonify

    items = list(review_engine.jump_destinations())
    # إدارة التبويبات العامة + التبويبات المخصصة لكل الأقسام
    if permissions.can("app.tabs.manage") or permissions.can("ops.tabs.manage"):
        items.append(
            {
                "title": "إدارة التبويبات",
                "path": "/contracts-admin/tabs",
                "group": "عقود",
                "keywords": "تبويب tabs manage contracts",
            }
        )
    for tab in db.list_app_custom_tabs(visible_only=True):
        section = (tab.get("section") or "").strip()
        need_section = _SECTION_PERM.get(section)
        if need_section and not permissions.can(need_section):
            continue
        need = (tab.get("required_perm") or "").strip()
        if need and not permissions.has_perm(need):
            continue
        sec_title = (SECTION_META.get(section) or {}).get("title") or section
        items.append(
            {
                "title": tab.get("title_ar") or tab.get("slug") or "تبويب",
                "path": db.app_custom_tab_href(tab),
                "group": sec_title,
                "keywords": f"{tab.get('slug') or ''} {tab.get('title_en') or ''} {section} تبويب",
            }
        )
    items = permissions.filter_jump_items(items)
    return jsonify(localize_jump(items, _lang()))


def main():
    create_app()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5070"))
    use_waitress = os.environ.get("USE_WAITRESS", "").lower() in {"1", "true", "yes"}
    # على Render وأمثاله نستخدم Waitress تلقائياً
    if use_waitress or os.environ.get("RENDER"):
        from waitress import serve

        print(f"تشغيل الإنتاج (Waitress) على http://{host}:{port}")
        serve(app, host=host, port=port, threads=8)
    else:
        print(f"تشغيل نظام متابعة الأعمال العام — مكتب خدمات خريص على http://{host}:{port}")
        app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
