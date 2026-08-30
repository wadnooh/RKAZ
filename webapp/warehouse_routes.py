from __future__ import annotations

from datetime import datetime
import json

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from webapp import db
from webapp import permissions
from webapp import warehouse_excel
from webapp import media as media_svc
from webapp import helpers
from webapp.modules_config import MODULES, SECTION_META, modules_for_section

warehouse_bp = Blueprint(
    "warehouse", __name__, url_prefix="/warehouses", template_folder="templates"
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
    if ctx in ("constructions", "projects", "warehouses", "contractors"):
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
    }.get((form_ctx or "").strip().lower(), "")


def _warehouse_create_contexts():
    """السياقات المسموح منها إنشاء حركة (رئيسية أو داخل المستودع)."""
    return (
        "ops",
        "constructions",
        "projects",
        "contractors",
        "wh_ops",
        "wh_constructions",
        "wh_projects",
        "wh_contractors",
    )


def _warehouse_source_label(section: str) -> str:
    return {
        "ops": helpers.t("العمليات والصيانة"),
        "constructions": helpers.t("الإنشاءات"),
        "projects": helpers.t("المشاريع"),
        "contractors": helpers.t("مواد موردة من مقاول"),
        "external": helpers.t("المشتريات الخارجية"),
        "warehouses": helpers.t("المستودعات"),
    }.get(section or "", section or "")


def _prepare_warehouse_tx_create(data: dict, form_ctx: str, conn) -> tuple:
    """يملأ مصدر الحركة — مسموح من الصفحات الرئيسية أو تبويبات المستودع المستقلة."""
    if form_ctx not in _warehouse_create_contexts():
        return None, helpers.t(
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
        return None, helpers.t(
            "الصرف يتطلب ربطاً بمعاملة (عطل / إنشاءات / مشروع)."
        )
    return data, None


def _warehouse_parent_url(parent: dict):
    kind = (parent or {}).get("parent_kind")
    pid = (parent or {}).get("parent_id")
    if not kind or not pid:
        return None
    if kind == "ticket":
        return url_for("warehouse.ticket_detail", ticket_id=pid)
    if kind == "primary_team":
        return url_for("warehouse.primary_team_detail", row_id=pid)
    if kind == "construction":
        return url_for("warehouse.construction_detail", row_id=pid)
    if kind == "project":
        return url_for("warehouse.project_detail", row_id=pid)
    return None


@warehouse_bp.route("/")
@permissions.require_perm("section.warehouses")
def home():
    """توجيه للمستودعات → إجمالي الكميات (أول تبويب فرعي حقيقي)."""
    return redirect(url_for(".movements_summary"))


@warehouse_bp.route("/summary")
@permissions.require_perm("section.warehouses")
def movements_summary():
    """صفحة إجمالي كميات الوارد والمنصرف والمتبقي بدون تفصيل الحركات."""
    db.backfill_warehouse_tx_sources()
    source = (request.args.get("source") or "").strip().lower()
    if source not in ("", "ops", "constructions", "projects", "external", "contractors"):
        source = ""
    totals = db.warehouse_movements_totals(source or None)
    by_source = db.warehouse_movements_totals_by_source()
    summary_cards = [
        helpers.summary_card(
            helpers.t("إجمالي الكمية الواردة"),
            f"{float(totals.get('inbound') or 0):.2f}",
            helpers.t("مجموع حركات الوارد"),
        ),
        helpers.summary_card(
            helpers.t("إجمالي الكمية المنصرفة"),
            f"{float(totals.get('outbound') or 0):.2f}",
            helpers.t("مجموع حركات المنصرف / الإرجاع"),
        ),
        helpers.summary_card(
            helpers.t("المتبقي"),
            f"{float(totals.get('balance') or 0):.2f}",
            helpers.t("الوارد − المنصرف"),
        ),
        helpers.summary_card(
            helpers.t("عدد الحركات"),
            totals.get("tx_count") or 0,
            helpers.t("سجل حركة في الفلتر الحالي"),
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
        for r in tx_rows:
            r["unit"] = db.normalize_warehouse_unit(r.get("unit"))
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

    summary_cards = []
    if view == "tickets":
        summary_cards = [
            helpers.summary_card(helpers.t("عدد الأعطال"), len(rows), helpers.t("حسب الفلتر الحالي")),
            helpers.summary_card(
                helpers.t("حركات المواد"),
                sum(int(r.get("wh_count") or 0) for r in rows),
                helpers.t("مرتبطة بالأعطال المعروضة"),
            ),
            helpers.summary_card(
                helpers.t("آخر عطل"),
                ((helpers.latest_row(rows, "receive_date") or {}).get("ticket_no") or "—"),
                helpers.t("تفاصيل أحدث عطل"),
            ),
            helpers.summary_card(
                helpers.t("تاريخ آخر عطل"),
                ((helpers.latest_row(rows, "receive_date") or {}).get("receive_date") or "—"),
                helpers.t("أحدث تاريخ استلام"),
            ),
        ]
    elif view == "teams":
        summary_cards = helpers.build_list_summary_cards(
            rows,
            count_label=helpers.t("عدد الأوامر"),
            money_keys=("amount",),
            date_keys=("order_date",),
            detail_key="work_order",
        )
    elif view == "works":
        summary_cards = helpers.build_list_summary_cards(
            rows,
            count_label=helpers.t("عدد المعاملات"),
            money_keys=("value",),
            date_keys=("work_date",),
            detail_key="work_no",
        )
    elif view == "projects":
        summary_cards = helpers.build_list_summary_cards(
            rows,
            count_label=helpers.t("عدد المشاريع"),
            money_keys=(),
            date_keys=("start_date", "end_date"),
            detail_key="project_code",
        )
    elif view == "movements":
        summary_cards = [
            helpers.summary_card(helpers.t("عدد الحركات"), len(tx_rows), helpers.t("حسب الفلتر الحالي")),
            helpers.summary_card(
                helpers.t("إجمالي الكميات"),
                f"{sum(float(r.get('qty') or 0) for r in tx_rows):.2f}",
                helpers.t("مجموع كميات الحركات المعروضة"),
            ),
            helpers.summary_card(
                helpers.t("آخر سجل"),
                ((helpers.latest_row(tx_rows, "tx_date") or {}).get("voucher_no") or "—"),
                helpers.t("تفاصيل أحدث حركة"),
            ),
            helpers.summary_card(
                helpers.t("تاريخ آخر حركة"),
                ((helpers.latest_row(tx_rows, "tx_date") or {}).get("tx_date") or "—"),
                helpers.t("أحدث تاريخ في القائمة"),
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
        rows=rows,
        tx_rows=tx_rows,
        tx_count=tx_count,
        list_endpoint=list_endpoint,
        summary_cards=summary_cards,
        wh_from={
            "ops": "wh_ops",
            "constructions": "wh_constructions",
            "projects": "wh_projects",
        }.get(source, "warehouses"),
    )


@warehouse_bp.route("/constructions")
@permissions.require_perm("section.warehouses", "section.constructions")
def constructions():
    return _warehouse_specialty_page(
        "constructions",
        "constructions",
        helpers.t("الإنشاءات"),
        helpers.t("عرض معاملات الإنشاءات داخل المستودع — بدون الانتقال للصفحة الرئيسية"),
        ".constructions",
    )


@warehouse_bp.route("/ops")
@permissions.require_perm("section.warehouses", "section.ops")
def ops():
    return _warehouse_specialty_page(
        "ops",
        "ops",
        helpers.t("العمليات والصيانة"),
        helpers.t("عرض الأعطال والفرق الأولية (أوامر عمل الكهرباء) داخل المستودع — بدون الانتقال للصفحة الرئيسية"),
        ".ops",
    )


@warehouse_bp.route("/projects")
@permissions.require_perm("section.warehouses", "section.projects")
def projects():
    return _warehouse_specialty_page(
        "projects",
        "projects",
        helpers.t("المشاريع"),
        helpers.t("عرض المشاريع داخل المستودع — بدون الانتقال للصفحة الرئيسية"),
        ".projects",
    )


@warehouse_bp.route("/balances")
@permissions.require_perm("section.warehouses")
def balances():
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
    hint = helpers.t("حسب البحث الحالي") if q else helpers.t("حسب الفلتر الحالي")
    summary_cards = [
        helpers.summary_card(helpers.t("عدد الأصناف"), len(items), hint),
        helpers.summary_card(
            helpers.t("إجمالي الوارد"),
            f"{sum(float(r.get('inbound') or 0) for r in items):.2f}",
            hint,
        ),
        helpers.summary_card(
            helpers.t("إجمالي المنصرف"),
            f"{sum(float(r.get('outbound') or 0) for r in items):.2f}",
            hint,
        ),
        helpers.summary_card(
            helpers.t("المتبقي"),
            f"{sum(float(r.get('balance') or 0) for r in items):.2f}",
            helpers.t("الوارد − المنصرف"),
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


@warehouse_bp.route("/items/template.xlsx")
@permissions.require_perm("section.warehouses")
def items_template():
    data = warehouse_excel.build_items_template()
    return helpers.simple_xlsx_export(
        helpers.t("قالب أصناف المستودع"),
        [],
        [],
        [],
        "قالب_أصناف_المستودع.xlsx"
    )


@warehouse_bp.route("/balances/template.xlsx")
@permissions.require_perm("section.warehouses")
def items_template_legacy():
    """توافق مع الروابط القديمة — يوجّه لقالب الأصناف."""
    return redirect(url_for(".items_template"))


@warehouse_bp.route("/tx/template.xlsx")
@permissions.require_perm("section.warehouses")
def tx_template():
    data = warehouse_excel.build_tx_template()
    return helpers.simple_xlsx_export(
        helpers.t("قالب حركات المستودع"),
        [],
        [],
        [],
        "قالب_حركات_المستودع.xlsx"
    )


@warehouse_bp.route("/items/import", methods=["POST"])
@permissions.require_perm("section.warehouses", "modules.write")
def items_import():
    f = request.files.get("file")
    if not f or not f.filename:
        flash(helpers.t("اختر ملف Excel للمواد"), "danger")
        return redirect(url_for(".balances", view="items"))
    try:
        result = warehouse_excel.import_items_from_excel(f)
        flash(
            f"استيراد الأصناف: جديد {result['ok']} | محدّث {result['updated']} | أرصدة افتتاحية {result['opening']}",
            "ok",
        )
        if result.get("errors"):
            flash(" / ".join(result["errors"][:5]), "danger")
        db.log_audit(helpers.current_user_name(), "استيراد Excel", "أصناف المستودع", details=str(result)[:240])
    except Exception as exc:
        flash(helpers.t("تعذر الاستيراد: {exc}", exc=exc), "danger")
    return redirect(url_for(".balances", view="items"))


@warehouse_bp.route("/balances/import", methods=["POST"])
@permissions.require_perm("section.warehouses", "modules.write")
def items_import_legacy():
    """توافق قديم — الاستيراد أصبح من أصناف المستودع."""
    return items_import()


@warehouse_bp.route("/balances/clear", methods=["POST"])
@permissions.require_perm("section.warehouses", "modules.write")
def balances_clear():
    """مسح كل حركات المستودع → أرصدة صفرية مع الإبقاء على أصناف المواد."""
    if not helpers.delete_password_ok():
        return helpers.reject_bad_delete_password(url_for(".balances"))
    confirm = (request.form.get("confirm") or "").strip()
    if confirm != "مسح":
        flash(helpers.t('للتأكيد اكتب كلمة «مسح» في خانة التأكيد ثم أعد المحاولة.'), "danger")
        return redirect(url_for(".balances"))
    try:
        deleted = db.clear_warehouse_balances()
        flash(helpers.t("تم مسح الأرصدة: حُذفت {deleted} حركة مستودع. الأصناف بقيت كما هي.", deleted=deleted), "ok")
        db.log_audit(helpers.current_user_name(), "مسح أرصدة", "معاملات المستودع", details=f"deleted={deleted}")
    except Exception as exc:
        flash(helpers.t("تعذر مسح الأرصدة: {exc}", exc=exc), "danger")
    return redirect(url_for(".balances"))


@warehouse_bp.route("/tx/import", methods=["POST"])
@permissions.require_perm("section.warehouses", "modules.write")
def tx_import():
    flash(
        helpers.t(
            "إدخال معاملات المستودع يتم فقط من الصفحات الرئيسية: الإنشاءات، العمليات والصيانة، والمشاريع."
        ),
        "danger",
    )
    return redirect(url_for("module_list", name="warehouse_tx"))
