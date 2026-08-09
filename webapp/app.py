from __future__ import annotations

import io
import json
import os
from datetime import datetime, timedelta
from functools import wraps

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

from webapp import db
from webapp.i18n import tr as i18n_tr, _ as i18n_phrase, tv as i18n_tv, localize_module, localize_section_meta, localize_jump
from webapp.modules_config import MODULES, SECTION_META, modules_for_section
from webapp import review_engine
from webapp import permissions
from webapp import warehouse_excel
from webapp import tickets_excel
from webapp import backup as backup_svc
from webapp import media as media_svc

app = Flask(__name__, instance_relative_config=True)
# يجب أن يبقى SECRET_KEY ثابتاً بين إعادة التشغيل — تغييره يُبطل جلسات الجميع
app.secret_key = os.environ.get("SECRET_KEY", "rakaz-khurais-emergency-2026")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.permanent_session_lifetime = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
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
# صور سجل الصور: عدة ملفات حتى ~6MB لكل منها
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
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
    except Exception:
        pass
    return app


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
    # حماية الصفحات — يتطلب تسجيل دخول
    if request.endpoint and request.endpoint not in PUBLIC_ENDPOINTS and not session.get("user_id"):
        if request.endpoint != "static":
            return redirect(url_for("login", next=request.path))
        return None
    # نظام الصلاحيات لكل التطبيق
    if session.get("user_id") and request.endpoint not in PUBLIC_ENDPOINTS:
        session["role"] = permissions.normalize_role(session.get("role"))
        missing = permissions.required_perm_for_request()
        if missing:
            label = permissions.PERM_LABELS.get(missing, missing)
            return permissions.deny_redirect(_t("ليس لديك صلاحية: {label}", label=_t(label)))


def current_user_name():
    return session.get("full_name") or session.get("username") or _t("مستخدم")



def _lang():
    return session.get("lang") or "ar"


def _t(text, **kwargs):
    return i18n_phrase(_lang(), text, **kwargs)


def _tv(value):
    return i18n_tv(_lang(), value)


def _mod(module):
    return localize_module(module, _lang())


def _smeta(meta):
    return localize_section_meta(meta, _lang())


def _after_data_change():
    """مزامنة صامتة بعد أي تعديل — بدون أزرار أو رسائل للمستخدم."""
    try:
        backup_svc.silent_backup_after_change()
    except Exception:
        pass


def _link_excavation_if_needed(ticket_no: str, reason: str = "", conn=None) -> dict | None:
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


def _flash_excavation_link(result: dict | None):
    if not result:
        return
    parts = []
    if result.get("created_coord"):
        parts.append(_t("تم ربط المعاملة بالتنسيقات"))
    if result.get("created_clearance"):
        parts.append(_t("تم فتح إجراء إخلاء الأسفلت"))
    if parts:
        flash(" — ".join(parts), "ok")


def _linked_section_label(section: str | None) -> str:
    return {
        "ops": _t("العمليات والصيانة"),
        "projects": _t("المشاريع"),
        "constructions": _t("الإنشاءات"),
    }.get(db.normalize_linked_section(section), section or "—")


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapper


@app.context_processor
def inject_globals():
    lang = session.get("lang") or "ar"

    def tr(key, **kwargs):
        return i18n_tr(lang, key, **kwargs)

    def _(text, **kwargs):
        return i18n_phrase(lang, text, **kwargs)

    def tv(value):
        return i18n_tv(lang, value)

    def can(*perms):
        return permissions.can(*perms)

    return {
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
        "nav_sections": permissions.nav_sections_for_role() if session.get("user_id") else [],
        "is_login_page": (request.endpoint or "") in {"login", "forgot_password"},
        "hosting": backup_svc.hosting_info(),
    }


def money(n):
    try:
        return f"{float(n or 0):,.2f} {_t('ر.س')}"
    except Exception:
        return f"0.00 {_t('ر.س')}"


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
    by_status = {s: 0 for s in g.lists.get("execution_status", [])}
    delayed = 0
    tickets_value = 0.0
    for t in tickets:
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
    return redirect(url_for("login"))


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
        session["role"] = permissions.normalize_role(user["role"])
        session["lang"] = saved_lang
        db.log_audit(user["full_name"], "دخول", "نظام", user["id"], user["username"])
        nxt = request.args.get("next") or url_for("ops_home")
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
        db.log_audit(current_user_name(), "خروج", "نظام", session.get("user_id"))
    lang = session.get("lang")
    session.clear()
    if lang:
        session["lang"] = lang
    return redirect(url_for("login"))


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
        links.append(
            {
                "label": mod["title"],
                "href": url_for("module_list", name=key),
                "count": _count(mod["table"]),
                "key": key,
            }
        )
    return links


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
    tools = [
        {"label": _t("الفرق الأولية"), "href": url_for("ops_primary_teams")},
        {"label": _t("فرق المهام العاجلة"), "href": url_for("teams_page")},
    ]

    conn = db.connect()
    recent = db.rows_to_dicts(
        conn.execute("SELECT * FROM tickets ORDER BY id DESC LIMIT 12").fetchall()
    )
    for r in recent:
        r["response_min"] = response_minutes(r.get("dispatch_time"), r.get("arrival_time"))
    _attach_ticket_final_values(recent, conn)
    conn.close()

    return render_template(
        "ops_home.html",
        stats=dashboard_stats(),
        tools=tools,
        recent_tickets=recent,
        total_count=_count("tickets"),
    )


@app.route("/constructions")
@login_required
def constructions_home():
    links = section_links("constructions")
    return render_template(
        "section_hub.html",
        title=_t("الإنشاءات - التنفيذ"),
        subtitle=_t("متابعة معاملات الإنشاءات والتنفيذ وربطها بأعطال المكتب."),
        links=links,
        section="constructions",
        section_modules=modules_for_section("constructions"),
        section_meta=_smeta(SECTION_META["constructions"]),
        total_count=sum(i.get("count") or 0 for i in links),
    )


@app.route("/new-coordinations")
@login_required
def new_coords_home():
    """تحويل قديم: التنسيقات الجديدة داخل قسم التنسيقات والجودة فقط."""
    return redirect(url_for("quality_home", tab="new_coords", sub="coords"))


@app.route("/projects")
@login_required
def projects_home():
    links = section_links("projects")
    return render_template(
        "section_hub.html",
        title=_t("المشاريع"),
        subtitle=_t("مشاريع خاصة ومشاريع الكهرباء — أكواد PR وترقيم مستقل عن أعطال الطوارئ."),
        links=links,
        section="projects",
        section_modules=modules_for_section("projects"),
        section_meta=_smeta(SECTION_META["projects"]),
        total_count=sum(i.get("count") or 0 for i in links),
    )


@app.route("/contractors")
@login_required
def contractors_home():
    links = section_links("contractors")
    return render_template(
        "section_hub.html",
        title=_t("المقاولين"),
        subtitle=_t("متابعة أعمال المقاولين وربطها بأعطال المكتب."),
        links=links,
        section="contractors",
        section_modules=modules_for_section("contractors"),
        section_meta=_smeta(SECTION_META["contractors"]),
        total_count=sum(i.get("count") or 0 for i in links),
    )


@app.route("/quality")
@login_required
def quality_home():
    """مركز التنسيقات والجودة — واجهة بأسلوب رسملة (تبويبات / فلاتر / تقارير)."""
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
        "واجهة متابعة التنسيقات والرخص والإخلاءات — بنفس هيكل رسملة: تبويبات وتصنيفات وتقرير موحد."
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
    )


@app.route("/safety")
@login_required
def safety_home():
    links = section_links("safety")
    return render_template(
        "section_hub.html",
        title=_t("السلامة"),
        subtitle=_t("تصاريح العمل وبلاغات السلامة المرتبطة بالمواقع."),
        links=links,
        section="safety",
        section_modules=modules_for_section("safety"),
        section_meta=_smeta(SECTION_META["safety"]),
        total_count=sum(i.get("count") or 0 for i in links),
    )


@app.route("/warehouses")
@login_required
def warehouses_home():
    db.backfill_warehouse_tx_sources()
    conn = db.connect()
    recent_tx = db.rows_to_dicts(
        conn.execute("SELECT * FROM warehouse_tx ORDER BY id DESC LIMIT 12").fetchall()
    )
    db.enrich_warehouse_txs_work_order(recent_tx, conn)
    conn.close()
    counts = {
        "constructions_works": _count("construction_works"),
        "constructions_tx": db.count_warehouse_tx_by_source("constructions"),
        "ops_tickets": _count("tickets"),
        "ops_tx": db.count_warehouse_tx_by_source("ops"),
        "projects": _count("projects"),
        "projects_tx": db.count_warehouse_tx_by_source("projects"),
        "items": _count("warehouse_items"),
    }
    return render_template(
        "warehouses_home.html",
        counts=counts,
        recent_tx=recent_tx,
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
    if source == "ops" and view not in ("tickets", "teams", "movements"):
        view = "tickets"
    if source == "constructions" and view not in ("works", "movements"):
        view = "works"
    if source == "projects" and view not in ("projects", "movements"):
        view = "projects"

    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    conn = db.connect()
    tx_count = db.count_warehouse_tx_by_source(source, conn)
    tx_rows = []
    rows = []

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
            sql += " AND (ticket_no LIKE ? OR rekaz_code LIKE ? OR district LIKE ? OR fault_type LIKE ? OR team LIKE ?)"
            like = f"%{q}%"
            params.extend([like, like, like, like, like])
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id DESC"
        rows = db.rows_to_dicts(conn.execute(sql, params).fetchall())
        cmap = _warehouse_tx_count_map("ops", conn)
        for r in rows:
            r["wh_count"] = cmap.get(str(r.get("ticket_no") or ""), 0)
    elif view == "works":
        rows = db.rows_to_dicts(
            conn.execute("SELECT * FROM construction_works ORDER BY id DESC").fetchall()
        )
        if q:
            ql = q.lower()
            rows = [
                r
                for r in rows
                if ql in (r.get("work_no") or "").lower()
                or ql in (r.get("site") or "").lower()
                or ql in (r.get("work_type") or "").lower()
            ]
        cmap = _warehouse_tx_count_map("constructions", conn)
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

    return render_template(
        "warehouse_specialty.html",
        active=active,
        source=source,
        title=title,
        subtitle=subtitle,
        view=view,
        q=q,
        status=status,
        rows=rows,
        tx_rows=tx_rows,
        tx_count=tx_count,
        list_endpoint=list_endpoint,
        wh_from={
            "ops": "wh_ops",
            "constructions": "wh_constructions",
            "projects": "wh_projects",
        }.get(source, "warehouses"),
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


def _warehouse_tx_option_urls(form_from: str, *, ticket_no: str = "", source_ref: str = "", source: str = "ops") -> dict:
    """روابط خيارات وارد / صرف / إرجاع لسياق المستودع."""
    base = {"from": form_from}
    if ticket_no:
        base["ticket_no"] = ticket_no
    if source_ref:
        base["source_ref"] = source_ref
    src = (source or "ops").strip().lower()
    if src == "ops":
        in_type, out_type, ret_type = "وارد من الكهرباء", "منصرف للمقاول", "إرجاع للكهرباء"
    else:
        in_type, out_type, ret_type = "وارد من موقع العمل", "منصرف للمقاول", "إرجاع للمجمعة"
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
        return redirect(url_for("warehouses_home"))

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
    }.get(section, "home")

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

    back_url = url_for("warehouses_home")
    if section == "ops":
        back_url = url_for("warehouse_ops", view="movements")
    elif section == "constructions":
        back_url = url_for("warehouse_constructions", view="movements")
    elif section == "projects":
        back_url = url_for("warehouse_projects", view="movements")

    form_from = {
        "ops": "wh_ops",
        "constructions": "wh_constructions",
        "projects": "wh_projects",
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
        out_url=url_for("module_new", name="warehouse_tx", tx_type="منصرف للمقاول", **base_args),
        return_url=url_for("module_new", name="warehouse_tx", tx_type="إرجاع للكهرباء", **base_args),
        parent_url=_warehouse_parent_url(parent),
    )


def _warehouse_mirror_tx_type(tx_type: str, source: str = "") -> str | None:
    """نوع الحركة المقابل للنسخ التلقائي (وارد↔منصرف) — بدون الإرجاع."""
    t = (tx_type or "").strip()
    if not t or "إرجاع" in t:
        return None
    if "وارد" in t or "افتتاح" in t:
        return "منصرف للمقاول"
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
                "إدخال معاملات المستودع يتم من الإنشاءات أو العمليات والصيانة أو المشاريع (أو تبويباتها داخل المستودع)."
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
    }.get(form_ctx, url_for("warehouses_home"))

    conn = db.connect()
    ticket_options = db.list_ticket_options(conn)
    warehouse_items = db.list_warehouse_items()

    header = {
        "voucher_no": "",
        "tx_date": datetime.now().strftime("%Y-%m-%d"),
        "tx_type": (request.values.get("tx_type") or "").strip() or "منصرف للمقاول",
        "recipient": "",
        "sender": "",
        "ticket_no": (request.values.get("ticket_no") or "").strip(),
        "rekaz_code": "",
        "source_section": source,
        "source_ref": (request.values.get("source_ref") or "").strip(),
        "work_order": (request.values.get("work_order") or "").strip(),
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

    reuse = str(request.values.get("reuse_voucher") or "").strip() in {"1", "on", "yes", "true"}
    existing_voucher = (request.values.get("voucher_no") or "").strip()
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
                if request.method == "GET" or not (request.form.get(k) or "").strip():
                    if prev.get(k) not in (None, "") and not (header.get(k) or "").strip():
                        header[k] = prev.get(k)
            if request.values.get("tx_type"):
                header["tx_type"] = request.values.get("tx_type")
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
            header["voucher_no"] = existing_voucher
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
        "منصرف للمقاول": _t("صرف متعدد"),
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


# كلمة سر تأكيد الحذف لكل التطبيق (مستودع / أعطال / وحدات / مستخدمين / فرق…)
DELETE_PASSWORD = "112233"
WAREHOUSE_DELETE_PASSWORD = DELETE_PASSWORD  # توافق خلفي


def _delete_password_ok() -> bool:
    """يتحقق من كلمة سر الحذف (112233) لأي عملية حذف في التطبيق."""
    return (request.form.get("delete_password") or "").strip() == DELETE_PASSWORD


def _reject_bad_delete_password(fallback_url: str):
    flash(_t("كلمة سر الحذف غير صحيحة — أعد المحاولة من مربع التأكيد"), "danger")
    nxt = (request.form.get("next") or "").strip()
    return redirect(nxt or fallback_url)


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
    links = section_links("external")
    return render_template(
        "section_hub.html",
        title=_t("المشتريات الخارجية والعهد"),
        subtitle=_t("طلبات الشراء الخارجي ومتابعة العهد المسلمة للموظفين."),
        links=links,
        section="external",
        section_modules=modules_for_section("external"),
        section_meta=_smeta(SECTION_META["external"]),
        total_count=sum(i.get("count") or 0 for i in links),
    )


@app.route("/financial")
@login_required
def financial_home():
    links = section_links("financial")
    stats = dashboard_stats()
    finance_stats = {
        "sap_raised": stats["sap_raised"],
        "invoices_total": stats["invoices_total"],
        "collected": stats["collected"],
        "remaining": stats["remaining"],
        "liquidity": stats["liquidity"],
    }
    return render_template(
        "section_hub.html",
        title=_t("المتابعات المالية"),
        subtitle=_t("المستخلصات و SAP ودليل البنود للتمتير."),
        links=links,
        section="financial",
        section_modules=modules_for_section("financial"),
        section_meta=_smeta(SECTION_META["financial"]),
        total_count=_count("invoices"),
        finance_stats=finance_stats,
    )


@app.route("/maintenance")
@login_required
def maintenance_home():
    links = section_links("maintenance")
    return render_template(
        "section_hub.html",
        title=_t("الورشة (سيارات - معدات)"),
        subtitle=_t("متابعة سيارات ومعدات الورش وربطها بالفرق الميدانية."),
        links=links,
        section="maintenance",
        section_modules=modules_for_section("maintenance"),
        section_meta=_smeta(SECTION_META["maintenance"]),
        total_count=sum(i.get("count") or 0 for i in links),
    )


@app.route("/hr")
@login_required
def hr_home():
    links = section_links("hr")
    return render_template(
        "section_hub.html",
        title=_t("الموارد البشرية"),
        subtitle=_t("سجل الموظفين والأقسام وحالات الالتحاق."),
        links=links,
        section="hr",
        section_modules=modules_for_section("hr"),
        section_meta=_smeta(SECTION_META["hr"]),
        total_count=sum(i.get("count") or 0 for i in links),
    )


@app.route("/contracts-admin")
@login_required
def contracts_admin_home():
    links = section_links("contracts")
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


@app.route("/admin/audit-log")
@login_required
def audit_log_home():
    return redirect(url_for("audit_log_page"))


# ---------- Tickets (الأعطال) ----------
TICKET_FIELDS = [
    "ticket_no",
    "rekaz_code",
    "receive_date",
    "district",
    "receive_time",
    "agent",
    "station_no",
    "feeder_no",
    "location",
    "fault_type",
    "classification",
    "team",
    "dispatch_time",
    "arrival_time",
    "status",
    "execution_date",
    "photographed",
    "quantities_done",
    "asphalt_clearance",
    "metering_status",
    "consultant_approval",
    "invoice_status",
    "work_order",
    "invoice_no",
    "sap_status",
    "items_value",
    "notes",
]


def ticket_from_form():
    data = {f: (request.form.get(f) or "").strip() for f in TICKET_FIELDS}
    iv = data.get("items_value")
    data["items_value"] = float(iv) if iv not in ("", None) else None
    return data


@app.route("/tickets")
@login_required
def tickets_list():
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    conn = db.connect()
    sql = "SELECT * FROM tickets WHERE 1=1"
    params = []
    if q:
        sql += " AND (ticket_no LIKE ? OR rekaz_code LIKE ? OR district LIKE ? OR fault_type LIKE ? OR team LIKE ? OR agent LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like, like, like, like])
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id DESC"
    rows = db.rows_to_dicts(conn.execute(sql, params).fetchall())
    for r in rows:
        r["response_min"] = response_minutes(r.get("dispatch_time"), r.get("arrival_time"))
    _attach_ticket_final_values(rows, conn)
    conn.close()
    return render_template("tickets_list.html", rows=rows, q=q, status=status)


@app.route("/tickets/template.xlsx")
@login_required
def tickets_template():
    data = tickets_excel.build_tickets_template()
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name="قالب_الأعطال.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/tickets/import", methods=["POST"])
@login_required
def tickets_import():
    if not permissions.can("tickets.write"):
        return permissions.deny_ticket_mutate()
    f = request.files.get("file")
    if not f or not f.filename:
        flash(_t("اختر ملف Excel للأعطال"), "danger")
        return redirect(url_for("tickets_list"))
    try:
        result = tickets_excel.import_tickets_from_excel(f)
        flash(_t("استيراد الأعطال: جديد {ok} | محدّث {updated}", ok=result["ok"], updated=result["updated"]), "ok")
        if result.get("errors"):
            flash(" / ".join(result["errors"][:5]), "danger")
        db.log_audit(current_user_name(), "استيراد Excel", "أعطال", details=str(result)[:240])
        if result["ok"] or result["updated"]:
            _after_data_change()
    except Exception as exc:
        flash(_t("تعذر الاستيراد: {exc}", exc=exc), "danger")
    return redirect(url_for("tickets_list"))


@app.route("/tickets/new", methods=["GET", "POST"])
@login_required
def ticket_new():
    if not permissions.can("tickets.write"):
        return permissions.deny_ticket_mutate()
    if request.method == "POST":
        data = ticket_from_form()
        if not data["ticket_no"]:
            flash(_t("رقم العطل مطلوب"), "danger")
            return render_template("ticket_form.html", row=data, mode="new")
        conn = db.connect()
        try:
            if not (data.get("rekaz_code") or "").strip():
                data["rekaz_code"] = db.next_series_code("er", conn)
            cols = ", ".join(TICKET_FIELDS)
            placeholders = ", ".join(["?"] * len(TICKET_FIELDS))
            cur = conn.execute(
                f"INSERT INTO tickets({cols}) VALUES ({placeholders})",
                [data[f] for f in TICKET_FIELDS],
            )
            conn.commit()
            db.log_audit(
                current_user_name(),
                "إضافة",
                "عطل",
                cur.lastrowid,
                f"{data.get('ticket_no')} / {data.get('rekaz_code')}",
            )
            new_id = cur.lastrowid
            flash(_t("تم إنشاء العطل بنجاح — كود ركاز {code} — الخطوة التالية: إضافة الكمية", code=data.get("rekaz_code")), "ok")
            _after_data_change()
            return _ticket_edit_redirect(new_id, "boq")
        except Exception as exc:
            flash(_t("تعذر الحفظ: {exc}", exc=exc), "danger")
        finally:
            conn.close()
    blank = {f: "" for f in TICKET_FIELDS}
    blank["receive_date"] = datetime.now().strftime("%Y-%m-%d")
    blank["status"] = "جديد"
    blank["rekaz_code"] = ""  # يُولَّد تلقائياً عند الحفظ
    return render_template("ticket_form.html", row=blank, mode="new")


def _ticket_wizard_steps():
    """خطوات تعديل العطل بالترتيب (عربي)."""
    steps = [
        ("data", _t("بيانات المعاملة")),
        ("boq", _t("إضافة الكمية")),
        ("photos", _t("الصور")),
        ("metering", _t("التمتير")),
    ]
    if permissions.can("section.warehouses"):
        steps.append(("warehouse", _t("المستودع")))
    steps.append(("done", _t("الاكتمال")))
    return steps


def _ticket_next_step(current):
    keys = [s[0] for s in _ticket_wizard_steps()]
    if not keys:
        return "data"
    if current not in keys:
        return keys[0]
    idx = keys.index(current)
    return keys[idx + 1] if idx + 1 < len(keys) else keys[-1]


def _ticket_edit_redirect(ticket_id, step):
    """الانتقال لصفحة العطل في وضع التعديل مع التركيز على الخطوة."""
    step = step or "data"
    return redirect(url_for("ticket_view", ticket_id=ticket_id, edit=1, step=step) + f"#step-{step}")


@app.route("/tickets/<int:ticket_id>")
@login_required
def ticket_view(ticket_id):
    conn = db.connect()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close()
        flash(_t("العطل غير موجود"), "danger")
        return redirect(url_for("tickets_list"))
    ticket = dict(row)
    tno = ticket["ticket_no"]
    related = {
        "quantities": db.rows_to_dicts(conn.execute("SELECT * FROM quantities WHERE ticket_no=?", (tno,)).fetchall()),
        "photos": db.rows_to_dicts(conn.execute("SELECT * FROM photos WHERE ticket_no=?", (tno,)).fetchall()),
        "coordination": db.rows_to_dicts(conn.execute("SELECT * FROM coordination WHERE ticket_no=?", (tno,)).fetchall()),
        "metering": db.rows_to_dicts(conn.execute("SELECT * FROM metering WHERE ticket_no=?", (tno,)).fetchall()),
        "warehouse_tx": db.rows_to_dicts(
            conn.execute(
                "SELECT * FROM warehouse_tx WHERE ticket_no=? OR (rekaz_code<>'' AND lower(rekaz_code)=lower(?)) ORDER BY id DESC",
                (tno, ticket.get("rekaz_code") or ""),
            ).fetchall()
        ),
        "boq_lines": db.list_ticket_boq_lines(ticket_id=ticket_id, conn=conn),
    }
    boq_file = db.active_contract_boq_file(conn)
    has_boq = db.has_boq_catalog(conn)
    has_excavation = db.ticket_has_excavation(tno, conn)
    excavation_link = None
    if has_excavation:
        excavation_link = db.ensure_excavation_coordination(
            tno,
            reason="ربط تلقائي من عرض العطل — حفر",
            conn=conn,
            create_clearance=True,
        )
        # أعد تحميل التنسيقات/الإخلاء بعد الربط
        related["coordination"] = db.rows_to_dicts(
            conn.execute("SELECT * FROM coordination WHERE ticket_no=?", (tno,)).fetchall()
        )
        related["clearances"] = db.rows_to_dicts(
            conn.execute("SELECT * FROM quality_clearances WHERE ticket_no=?", (tno,)).fetchall()
        )
    else:
        related["clearances"] = db.rows_to_dicts(
            conn.execute("SELECT * FROM quality_clearances WHERE ticket_no=?", (tno,)).fetchall()
        )
    conn.commit()
    conn.close()
    for q in related["quantities"]:
        q["total"] = float(q.get("qty") or 0) * float(q.get("unit_price") or 0)
    for p in related["photos"]:
        p["complete"] = _t("مكتمل") if media_svc.photos_complete(p) else _t("ناقص")
    boq_base = sum(float(x.get("line_total") or 0) for x in related["boq_lines"])
    settings_ratio = float((g.settings or {}).get("emergency_ratio") or 0)
    emergency_applied = db.ticket_emergency_ratio(related["boq_lines"], settings_ratio)
    ticket["response_min"] = response_minutes(ticket.get("dispatch_time"), ticket.get("arrival_time"))
    if related["boq_lines"]:
        # إجمالي البنود دائماً من الأسطر (كمية×سعر) وليس من قيمة قديمة مضاعَفة
        ticket["items_value"] = boq_base
        base_for_final = boq_base
    else:
        base_for_final = ticket.get("items_value")
    ticket["boq_base_total"] = boq_base if related["boq_lines"] else None
    ticket["emergency_ratio_applied"] = emergency_applied
    ticket["final_value"] = final_value(base_for_final, ratio=emergency_applied)
    ticket["boq_final_total"] = ticket["final_value"]
    ticket["has_excavation"] = has_excavation
    # تعديل العطل/البنود يتطلب tickets.write فقط (ليس modules.write)
    can_mutate = permissions.can("tickets.write")
    wants_edit = request.args.get("edit") == "1"
    if wants_edit and not can_mutate:
        flash(_t("ليس لديك صلاحية لتعديل العطل أو بنوده. العرض متاح للقراءة فقط."), "danger")
        return redirect(url_for("ticket_view", ticket_id=ticket_id))
    edit_mode = wants_edit and can_mutate
    wizard_steps = _ticket_wizard_steps() if edit_mode else []
    step_keys = [s[0] for s in wizard_steps]
    raw_step = (request.args.get("step") or "data").strip()
    edit_step = raw_step if raw_step in step_keys else (step_keys[0] if step_keys else "data")
    next_step = _ticket_next_step(edit_step) if edit_mode else None
    return render_template(
        "ticket_view.html",
        ticket=ticket,
        related=related,
        has_boq_catalog=has_boq,
        boq_file=boq_file,
        emergency_ratio=float((g.settings or {}).get("emergency_ratio") or 0),
        edit_mode=edit_mode,
        can_mutate=can_mutate,
        wizard_steps=wizard_steps,
        edit_step=edit_step,
        next_step=next_step,
        step_labels=dict(wizard_steps),
    )


@app.route("/tickets/<int:ticket_id>/edit", methods=["GET", "POST"])
@login_required
def ticket_edit(ticket_id):
    if not permissions.can("tickets.write"):
        return permissions.deny_ticket_mutate()
    conn = db.connect()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close()
        flash(_t("العطل غير موجود"), "danger")
        return redirect(url_for("tickets_list"))
    if request.method == "POST":
        data = ticket_from_form()
        if not (data.get("rekaz_code") or "").strip():
            existing_code = (dict(row).get("rekaz_code") or "").strip()
            data["rekaz_code"] = existing_code or db.next_series_code("er", conn)
        sets = ", ".join([f"{f}=?" for f in TICKET_FIELDS])
        conn.execute(
            f"UPDATE tickets SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            [data[f] for f in TICKET_FIELDS] + [ticket_id],
        )
        db.sync_warehouse_tx_work_order_for_ticket(
            data.get("ticket_no") or dict(row).get("ticket_no") or "",
            data.get("work_order") or "",
            data.get("rekaz_code") or "",
            conn,
        )
        conn.commit()
        link_res = _link_excavation_if_needed(
            data.get("ticket_no") or dict(row).get("ticket_no") or "",
            reason="ربط تلقائي بعد حفظ العطل — حفر/إخلاء أسفلت",
            conn=conn,
        )
        if link_res and (link_res.get("created_coord") or link_res.get("created_clearance")):
            conn.commit()
        conn.close()
        db.log_audit(current_user_name(), "تعديل", "عطل", ticket_id, data.get("ticket_no"))
        flash(_t("تم حفظ المعاملة — انتقل لإضافة الكمية"), "ok")
        _flash_excavation_link(link_res)
        _after_data_change()
        return _ticket_edit_redirect(ticket_id, "boq")
    conn.close()
    # التعديل يتم على صفحة العرض الكاملة (صور / بنود / …) بعد طلب التعديل
    return _ticket_edit_redirect(ticket_id, request.args.get("step") or "data")


@app.route("/tickets/<int:ticket_id>/delete", methods=["POST"])
@login_required
def ticket_delete(ticket_id):
    if not permissions.can("tickets.delete"):
        return permissions.deny_redirect(_t("ليس لديك صلاحية لحذف الأعطال."))
    if not _delete_password_ok():
        return _reject_bad_delete_password(url_for("ticket_view", ticket_id=ticket_id, edit=1))
    conn = db.connect()
    row = conn.execute("SELECT ticket_no FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    conn.execute("DELETE FROM tickets WHERE id=?", (ticket_id,))
    conn.commit()
    conn.close()
    db.log_audit(current_user_name(), "حذف", "عطل", ticket_id, row["ticket_no"] if row else "")
    flash(_t("تم حذف العطل"), "ok")
    _after_data_change()
    return redirect(url_for("tickets_list"))


@app.route("/tickets/<int:ticket_id>/boq/add", methods=["POST"])
@login_required
def ticket_boq_add(ticket_id):
    if not permissions.can("tickets.write"):
        return permissions.deny_ticket_mutate(_t("ليس لديك صلاحية لإضافة بنود العقد على العطل."))
    conn = db.connect()
    ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not ticket:
        conn.close()
        flash(_t("العطل غير موجود"), "danger")
        return redirect(url_for("tickets_list"))
    item_no = (request.form.get("item_no") or "").strip()
    qty_raw = (request.form.get("qty") or "").strip()
    work_class = (request.form.get("work_class") or "اعتيادي").strip()
    ratio_raw = (request.form.get("increase_ratio") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    if not item_no:
        conn.close()
        flash(_t("أدخل رقم البند من دليل العقد"), "danger")
        return _ticket_edit_redirect(ticket_id, "boq")
    try:
        qty = float(qty_raw) if qty_raw != "" else 0.0
    except ValueError:
        conn.close()
        flash(_t("الكمية غير صالحة"), "danger")
        return _ticket_edit_redirect(ticket_id, "boq")
    try:
        ratio = float(ratio_raw) if ratio_raw != "" else None
    except ValueError:
        ratio = None
    catalog = db.get_contract_boq_item(item_no, conn)
    if not catalog:
        conn.close()
        flash(_t("رقم البند «{item_no}» غير موجود في دليل العقد النشط — تحقق من الرقم أو ارفع الدليل من إدارة العقود", item_no=item_no), "danger")
        return _ticket_edit_redirect(ticket_id, "boq")
    active = db.active_contract_boq_file(conn)
    unit_price = catalog.get("unit_price")
    totals = db.calc_boq_line_totals(qty, unit_price, work_class, ratio)
    desc = (
        (catalog.get("short_desc") or "").strip()
        or (catalog.get("description") or "").strip()
    )
    conn.execute(
        """
        INSERT INTO ticket_boq_lines(
          ticket_id, ticket_no, file_id, item_no, description, unit, qty, unit_price,
          line_total, work_class, increase_ratio, final_total, notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ticket_id,
            ticket["ticket_no"],
            (active or {}).get("id") if active else catalog.get("file_id"),
            catalog.get("item_no"),
            desc,
            catalog.get("unit"),
            qty,
            unit_price,
            totals["line_total"],
            totals["work_class"],
            totals["increase_ratio"],
            totals["final_total"],
            notes,
        ),
    )
    # مزامنة صف كميات للتوافق مع الطباعة والتقارير القديمة
    conn.execute(
        """
        INSERT INTO quantities(ticket_no, item_no, description, unit, qty, unit_price, notes)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            ticket["ticket_no"],
            catalog.get("item_no"),
            desc,
            catalog.get("unit"),
            qty,
            unit_price,
            notes,
        ),
    )
    db.sync_ticket_items_value(ticket_id, conn)
    link_res = None
    if db.is_excavation_text(desc, notes, item_no):
        link_res = db.ensure_excavation_coordination(
            ticket["ticket_no"],
            reason=f"ربط تلقائي — بند حفر {item_no}",
            conn=conn,
            create_clearance=True,
        )
    conn.commit()
    conn.close()
    db.log_audit(current_user_name(), "إضافة بند عقد", "عطل", ticket_id, f"{item_no} × {qty}")
    flash(_t("تمت إضافة البند وحساب التكلفة — أضف بنداً آخر أو انتقل للخطوة التالية"), "ok")
    _flash_excavation_link(link_res)
    _after_data_change()
    return _ticket_edit_redirect(ticket_id, "boq")


@app.route("/tickets/<int:ticket_id>/boq/<int:line_id>/delete", methods=["POST"])
@login_required
def ticket_boq_delete(ticket_id, line_id):
    if not permissions.can("tickets.write"):
        return permissions.deny_ticket_mutate(_t("ليس لديك صلاحية لحذف بنود العقد من العطل."))
    if not _delete_password_ok():
        return _reject_bad_delete_password(url_for("ticket_view", ticket_id=ticket_id, edit=1, step="boq"))
    conn = db.connect()
    line = conn.execute(
        "SELECT * FROM ticket_boq_lines WHERE id=? AND ticket_id=?",
        (line_id, ticket_id),
    ).fetchone()
    if line:
        # حذف صف كميات مطابق (أحدث صف بنفس رقم البند والكمية)
        qty_row = conn.execute(
            """
            SELECT id FROM quantities
            WHERE ticket_no=? AND lower(item_no)=lower(?)
              AND ABS(COALESCE(qty,0) - ?) < 0.0001
            ORDER BY id DESC LIMIT 1
            """,
            (line["ticket_no"], line["item_no"] or "", float(line["qty"] or 0)),
        ).fetchone()
        if qty_row:
            conn.execute("DELETE FROM quantities WHERE id=?", (qty_row["id"],))
    conn.execute("DELETE FROM ticket_boq_lines WHERE id=? AND ticket_id=?", (line_id, ticket_id))
    db.sync_ticket_items_value(ticket_id, conn)
    conn.commit()
    conn.close()
    flash(_t("تم حذف البند"), "ok")
    _after_data_change()
    return _ticket_edit_redirect(ticket_id, "boq")


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


@app.route("/tickets/<int:ticket_id>/print")
@login_required
def ticket_print(ticket_id):
    conn = db.connect()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close()
        flash(_t("العطل غير موجود"), "danger")
        return redirect(url_for("tickets_list"))
    ticket = dict(row)
    tno = ticket["ticket_no"]
    boq_lines = db.list_ticket_boq_lines(ticket_id=ticket_id, conn=conn)
    legacy_qty = db.rows_to_dicts(conn.execute("SELECT * FROM quantities WHERE ticket_no=?", (tno,)).fetchall())
    photos = db.rows_to_dicts(conn.execute("SELECT * FROM photos WHERE ticket_no=?", (tno,)).fetchall())
    coordination = db.rows_to_dicts(conn.execute("SELECT * FROM coordination WHERE ticket_no=?", (tno,)).fetchall())
    metering = db.rows_to_dicts(conn.execute("SELECT * FROM metering WHERE ticket_no=?", (tno,)).fetchall())
    conn.close()
    ticket["response_min"] = response_minutes(ticket.get("dispatch_time"), ticket.get("arrival_time"))
    settings_ratio = float((g.settings or {}).get("emergency_ratio") or 0)
    emergency_applied = db.ticket_emergency_ratio(boq_lines, settings_ratio)
    boq_base = sum(float(x.get("line_total") or 0) for x in boq_lines) if boq_lines else None
    if boq_base is not None:
        ticket["items_value"] = boq_base
    ticket["boq_base_total"] = boq_base
    ticket["emergency_ratio_applied"] = emergency_applied
    ticket["final_value"] = final_value(
        ticket.get("items_value"),
        ratio=emergency_applied if boq_lines else settings_ratio,
    )
    if boq_lines:
        quantities = [
            {
                "item_no": x.get("item_no"),
                "description": x.get("description"),
                "unit": x.get("unit"),
                "qty": x.get("qty"),
                "unit_price": x.get("unit_price"),
                "total": x.get("line_total"),
                "work_class": x.get("work_class"),
                "increase_ratio": x.get("increase_ratio"),
            }
            for x in boq_lines
        ]
    else:
        quantities = legacy_qty
        for q in quantities:
            q["total"] = float(q.get("qty") or 0) * float(q.get("unit_price") or 0)
    return render_template(
        "ticket_print.html",
        ticket=ticket,
        quantities=quantities,
        photos=photos,
        coordination=coordination,
        metering=metering,
        printed_at=datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
        emergency_ratio_applied=emergency_applied if boq_lines else settings_ratio,
        boq_base_total=boq_base,
    )


# ---------- Generic CRUD helpers ----------
# MODULES imported from webapp.modules_config


def _module_form_data(module):
    data = {}
    for key, _label, ftype in module["fields"]:
        val = (request.form.get(key) or "").strip()
        if ftype == "number":
            data[key] = float(val) if val != "" else None
        elif ftype == "image":
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
    if ctx in ("constructions", "projects", "warehouses"):
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
    }.get((form_ctx or "").strip().lower(), "")


def _warehouse_create_contexts():
    """السياقات المسموح منها إنشاء حركة (رئيسية أو داخل المستودع)."""
    return (
        "ops",
        "constructions",
        "projects",
        "wh_ops",
        "wh_constructions",
        "wh_projects",
    )


def _warehouse_main_sections():
    return ("ops", "constructions", "projects")


def _warehouse_source_label(section: str) -> str:
    return {
        "ops": _t("العمليات والصيانة"),
        "constructions": _t("الإنشاءات"),
        "projects": _t("المشاريع"),
        "warehouses": _t("المستودعات"),
    }.get(section or "", section or "")


def _redirect_after_module(name, data, form_ctx=None):
    """بعد حفظ سجل مرتبط بعطل: العودة لصفحة العطل والخطوة التالية في المعالج."""
    if name == "warehouse_items":
        return redirect(url_for("warehouse_balances", view="items"))
    if name == "primary_team_orders":
        return redirect(url_for("ops_primary_teams"))
    tno = str((data or {}).get("ticket_no") or "").strip()
    form_ctx = form_ctx or _warehouse_form_ctx()

    if name == "warehouse_tx":
        # من داخل المستودع: ابقَ في المستودع دائماً (بدون تحويل للصفحات الرئيسية)
        voucher = (data.get("voucher_no") or "").strip()
        if voucher and form_ctx in ("wh_ops", "wh_constructions", "wh_projects", "warehouses", "ops", "constructions", "projects"):
            # بعد الحفظ افتح عرض المعاملة (سند)
            if form_ctx in ("wh_ops", "wh_constructions", "wh_projects", "warehouses") or not (
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
        if form_ctx == "warehouses":
            source = (request.values.get("source") or data.get("source_section") or "").strip().lower()
            if source == "ops":
                return redirect(url_for("warehouse_ops", view="movements"))
            if source == "constructions":
                return redirect(url_for("warehouse_constructions", view="movements"))
            if source == "projects":
                return redirect(url_for("warehouse_projects", view="movements"))
            return redirect(url_for("warehouses_home"))
        # من الصفحة الرئيسية (معالج العطل)
        if (
            form_ctx == "ops"
            and tno
            and permissions.can("tickets.read")
            and permissions.can("section.ops")
        ):
            conn = db.connect()
            row = conn.execute("SELECT id FROM tickets WHERE ticket_no=?", (tno,)).fetchone()
            conn.close()
            if row:
                nxt = "done"
                allowed = {s[0] for s in _ticket_wizard_steps()}
                if nxt not in allowed:
                    nxt = "done"
                label = dict(_ticket_wizard_steps()).get(nxt, nxt)
                flash(_t("تم الحفظ — الخطوة التالية: {label}", label=label), "ok")
                return _ticket_edit_redirect(row["id"], nxt)
        if form_ctx == "constructions":
            return redirect(url_for("module_list", name="construction_works"))
        if form_ctx == "projects":
            return redirect(url_for("module_list", name="projects"))
        if form_ctx == "ops":
            return redirect(url_for("warehouse_ops"))
        return redirect(url_for("warehouses_home"))

    next_after = {
        "quantities": "photos",
        "photos": "metering",
        "metering": "warehouse" if permissions.can("section.warehouses") else "done",
    }
    if tno and name in next_after:
        conn = db.connect()
        row = conn.execute("SELECT id FROM tickets WHERE ticket_no=?", (tno,)).fetchone()
        conn.close()
        if row:
            nxt = next_after[name]
            allowed = {s[0] for s in _ticket_wizard_steps()}
            if nxt not in allowed:
                nxt = "done"
            label = dict(_ticket_wizard_steps()).get(nxt, nxt)
            flash(_t("تم الحفظ — الخطوة التالية: {label}", label=label), "ok")
            return _ticket_edit_redirect(row["id"], nxt)
    if tno:
        return redirect(url_for("module_list", name=name, ticket_no=tno))
    return redirect(url_for("module_list", name=name))


def _prepare_warehouse_tx_create(data: dict, form_ctx: str, conn) -> tuple:
    """يملأ مصدر الحركة — مسموح من الصفحات الرئيسية أو تبويبات المستودع المستقلة."""
    if form_ctx not in _warehouse_create_contexts():
        return None, _t(
            "إدخال معاملات المستودع يتم من الإنشاءات أو العمليات والصيانة أو المشاريع (أو تبويباتها داخل المستودع)."
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
        if not (request.args.get("ticket_no") or request.args.get("item_no")):
            return redirect(url_for("warehouses_home"))
    conn = db.connect()
    rows = db.rows_to_dicts(conn.execute(f"SELECT * FROM {module['table']} ORDER BY id DESC").fetchall())
    tickets = [r["ticket_no"] for r in conn.execute("SELECT ticket_no FROM tickets ORDER BY id DESC").fetchall()]
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
        for r in rows:
            r["total"] = (float(r.get("qty") or 0) * float(r.get("unit_price") or 0))
    item_filter = (request.args.get("item_no") or "").strip()
    ticket_filter = (request.args.get("ticket_no") or "").strip()
    source_filter = (request.args.get("source") or "").strip().lower()
    if name == "warehouse_tx":
        db.backfill_warehouse_tx_sources()
        db.enrich_warehouse_txs_work_order(rows)
    if name == "warehouse_items":
        for r in rows:
            r["balance"] = db.warehouse_balance(r.get("item_no"))
    if name == "warehouse_tx" and item_filter:
        rows = [r for r in rows if (r.get("item_no") or "").lower() == item_filter.lower()]
    if name == "warehouse_tx" and source_filter in ("ops", "constructions", "projects"):
        rows = [
            r
            for r in rows
            if (r.get("source_section") or "").strip().lower() == source_filter
        ]
    if ticket_filter and any(f[0] == "ticket_no" for f in module.get("fields", [])):
        rows = [r for r in rows if (r.get("ticket_no") or "") == ticket_filter]
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
    section = module.get("section")
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
        section=section,
        section_meta=_smeta(SECTION_META.get(section)),
        section_modules=modules_for_section(section) if section else [],
        warehouse_source=source_filter if name == "warehouse_tx" else None,
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
        if ticket and "rekaz_code" in prefill:
            prefill["rekaz_code"] = ticket.get("rekaz_code") or ""
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
    if name == "issued_licenses" and request.args.get("workflow_status") and "workflow_status" in prefill:
        prefill["workflow_status"] = (request.args.get("workflow_status") or "").strip()
    if name == "warehouse_tx" and request.args.get("ticket_no"):
        prefill["tx_type"] = prefill.get("tx_type") or "منصرف للمقاول"
        prefill["tx_date"] = prefill.get("tx_date") or datetime.now().strftime("%Y-%m-%d")
    if name == "warehouse_tx":
        form_ctx = _warehouse_form_ctx()
        if form_ctx not in _warehouse_create_contexts():
            conn.close()
            flash(
                _t(
                    "إدخال معاملات المستودع يتم من الإنشاءات أو العمليات والصيانة أو المشاريع (أو تبويباتها داخل المستودع)."
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
            if ticket and (ticket.get("work_order") or "").strip():
                prefill["work_order"] = ticket.get("work_order")
            elif ticket and "rekaz_code" in prefill and not (prefill.get("rekaz_code") or "").strip():
                prefill["rekaz_code"] = ticket.get("rekaz_code") or ""
        prefill = db.apply_warehouse_tx_work_order(prefill, conn)
        if source in ("constructions", "projects", "ops"):
            requested_type = (request.args.get("tx_type") or "").strip()
            prefill["tx_type"] = requested_type or prefill.get("tx_type") or "منصرف للمقاول"
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
        else:
            flash(_t("تمت الإضافة"), "ok")
        _flash_excavation_link(link_res)
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
        return _redirect_after_module(name, data, form_ctx=_warehouse_form_ctx() if name == "warehouse_tx" else None)
    warehouse_items = db.list_warehouse_items() if name == "warehouse_tx" else []
    boq_items = []
    if request.args.get("item_no") and "item_no" in prefill:
        prefill["item_no"] = request.args.get("item_no")
        if name == "warehouse_tx":
            prefill = db.enrich_warehouse_tx_from_item(prefill)
        if name == "quantities":
            prefill = db.enrich_quantity_from_boq(prefill, conn)
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
        transfer_res = None
        if name == "new_coordinations" and (data.get("status") or "").strip() == "تم الإصدار":
            transfer_res = db.transfer_new_coordination_to_license(row_id, conn=conn)
        conn.commit()
        conn.close()
        db.log_audit(current_user_name(), "تعديل", module["title"], row_id, str(data)[:240])
        flash(_t("تم الحفظ"), "ok")
        _flash_excavation_link(link_res)
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
        return _redirect_after_module(name, data, form_ctx=edit_ctx)
    data = dict(row)
    warehouse_items = db.list_warehouse_items() if name == "warehouse_tx" else []
    boq_items = []
    boq_approved_total = None
    if name == "metering":
        boq_approved_total = _metering_boq_approved_total(data.get("ticket_no"), conn)
        if boq_approved_total is not None and data.get("approved_value") in (None, ""):
            data["approved_value"] = boq_approved_total
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
    # كل عمليات الحذف في التطبيق تتطلب كلمة سر التأكيد
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
        if not permissions.can("modules.write"):
            conn.close()
            flash(_t("لا تملك صلاحية الإضافة."), "danger")
            return redirect(url_for("ops_primary_teams"))
        action = request.form.get("action")
        if action == "add":
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
    return render_template(
        "primary_teams.html",
        rows=rows,
        q=q,
        today=datetime.now().strftime("%Y-%m-%d"),
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
    return render_template("teams.html", rows=rows)


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
    return render_template(
        "warehouse_balances.html",
        rows=items,
        q=q,
        view=view,
        warehouse_active="balances",
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
            "إدخال معاملات المستودع يتم فقط من الصفحات الرئيسية: الإنشاءات، العمليات والصيانة، والمشاريع."
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
                role = permissions.normalize_role(request.form.get("role") or "مدخل بيانات")
                conn.execute(
                    "INSERT INTO users(username, full_name, role, active, password, notes) VALUES (?,?,?,?,?,?)",
                    (
                        request.form.get("username"),
                        request.form.get("full_name"),
                        role,
                        1 if request.form.get("active") == "1" else 0,
                        request.form.get("password") or "1234",
                        request.form.get("notes"),
                    ),
                )
                conn.commit()
                db.log_audit(current_user_name(), "إضافة", "مستخدم", details=request.form.get("username"))
                flash(_t("تم إضافة المستخدم"), "ok")
            except Exception as exc:
                flash(_t("تعذر الإضافة: {exc}", exc=exc), "danger")
        elif action == "update":
            uid = request.form.get("id")
            role = permissions.normalize_role(request.form.get("role") or "مدخل بيانات")
            password = (request.form.get("password") or "").strip()
            if password:
                conn.execute(
                    "UPDATE users SET full_name=?, role=?, active=?, password=?, notes=? WHERE id=?",
                    (
                        request.form.get("full_name"),
                        role,
                        1 if request.form.get("active") == "1" else 0,
                        password,
                        request.form.get("notes"),
                        uid,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE users SET full_name=?, role=?, active=?, notes=? WHERE id=?",
                    (
                        request.form.get("full_name"),
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
        elif action == "delete":
            if not _delete_password_ok():
                conn.close()
                return _reject_bad_delete_password(url_for("users_list"))
            uid = request.form.get("id")
            target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if target and str(session.get("user_id")) == str(uid):
                flash(_t("لا يمكن حذف حسابك الحالي"), "danger")
            else:
                admins = conn.execute(
                    "SELECT COUNT(*) FROM users WHERE lower(role)='admin' AND active=1"
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
            if target and str(session.get("user_id")) == str(uid):
                flash(_t("لا يمكن إيقاف حسابك الحالي"), "danger")
            else:
                conn.execute(
                    "UPDATE users SET active = CASE WHEN active=1 THEN 0 ELSE 1 END WHERE id=?",
                    (uid,),
                )
                conn.commit()
                flash(_t("تم تحديث الحالة"), "ok")
    rows = db.rows_to_dicts(conn.execute("SELECT * FROM users ORDER BY id").fetchall())
    conn.close()
    for row in rows:
        row["role"] = permissions.normalize_role(row.get("role"))
        row["perm_count"] = len(permissions.perms_for_role(row["role"]))
    return render_template(
        "users.html",
        rows=rows,
        role_matrix=permissions.role_matrix(),
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
                    }
                )
        conn.close()
    return render_template("search.html", q=q, results=results)


# ---------- Jump ----------
@app.route("/api/jump-destinations")
@login_required
def api_jump_destinations():
    from flask import jsonify

    items = permissions.filter_jump_items(review_engine.jump_destinations())
    return jsonify(localize_jump(items, _lang()))


@app.route("/export/tickets.xlsx")
@login_required
def export_tickets_excel():
    data = tickets_excel.export_tickets()
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=f"الأعطال-{datetime.now().strftime('%Y%m%d')}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


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
