from __future__ import annotations

import io
from datetime import datetime

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from webapp import db
from webapp import permissions
from webapp import tickets_excel
from webapp import media as media_svc
from webapp import helpers

tickets_bp = Blueprint(
    "tickets", __name__, url_prefix="/tickets", template_folder="templates"
)

TICKET_FIELDS = [
    "ticket_no", "rekaz_code", "receive_date", "district", "receive_time", "agent",
    "station_no", "feeder_no", "location", "fault_type", "classification", "team",
    "dispatch_time", "arrival_time", "status", "execution_date", "photographed",
    "quantities_done", "asphalt_clearance", "metering_status", "consultant_approval",
    "invoice_status", "work_order", "invoice_no", "sap_status", "items_value", "notes",
]

def ticket_from_form():
    data = {f: (request.form.get(f) or "").strip() for f in TICKET_FIELDS}
    data["status"] = db.normalize_ticket_status(data.get("status"))
    iv = data.get("items_value")
    data["items_value"] = float(iv) if iv not in ("", None) else None
    return data

def _load_filtered_tickets(
    *, q="", status="", date_from="", date_to="", missing_amount=False, conn=None, classification="",
):
    """يحمّل الأعطال مع نفس فلاتر القائمة (بما فيها بدون مبلغ)."""
    own_conn = conn is None
    if own_conn:
        conn = db.connect()

    where_clauses = ["1=1"]
    params = []

    if q:
        like_q = f"%{q}%"
        search_columns = [
            "ticket_no", "rekaz_code", "work_order", "district",
            "fault_type", "team", "agent",
        ]
        where_clauses.append(f"({' OR '.join(f'{col} LIKE ?' for col in search_columns)})")
        params.extend([like_q] * len(search_columns))

    status = db.normalize_ticket_status(status)
    if status:
        if status == "تم الإسناد":
            where_clauses.append("status IN (?, ?)")
            params.extend(["تم الإسناد", "جديد"])
        else:
            where_clauses.append("status = ?")
            params.append(status)

    if classification:
        where_clauses.append("classification = ?")
        params.append(classification)

    if date_from:
        # عمود receive_date نصي، لكن هذا الشرط يعمل مع صيغة ISO 8601 (YYYY-MM-DD)
        where_clauses.append("coalesce(receive_date, '') >= ?")
        params.append(date_from)

    if date_to:
        where_clauses.append("coalesce(receive_date, '') <= ?")
        params.append(date_to)

    sql = f"SELECT * FROM tickets WHERE {' AND '.join(where_clauses)} ORDER BY id DESC"
    
    try:
        rows = db.rows_to_dicts(conn.execute(sql, params).fetchall())
        for r in rows:
            r["status"] = db.normalize_ticket_status(r.get("status"))
            r["response_min"] = helpers.response_minutes(r.get("dispatch_time"), r.get("arrival_time"))
        helpers.attach_ticket_final_values(rows, conn)
    finally:
        if own_conn:
            conn.close()

    missing_count = helpers.count_missing_amount(rows, "final_value")
    if missing_amount:
        rows = helpers.filter_missing_amount_rows(rows, "final_value")
    return rows, missing_count

@tickets_bp.route("/")
@permissions.require_perm("tickets.read")
def list_all():
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    status = db.normalize_ticket_status(status)
    classification = (request.args.get("classification") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    missing_amount = helpers.missing_amount_flag()
    rows, missing_count = _load_filtered_tickets(
        q=q, status=status, date_from=date_from, date_to=date_to, missing_amount=missing_amount, classification=classification,
    )
    summary_cards = [
        helpers.summary_card(helpers.t("عدد الأعطال"), len(rows), helpers.t("حسب الفلتر الحالي")),
        helpers.summary_card(
            helpers.t("المبالغ المدخلة"),
            helpers.sum_money_field(rows, "final_value"),
            helpers.t("مجموع القيم النهائية"),
            money=True,
        ),
        helpers.missing_amount_card(
            missing_count, endpoint=".list_all", active=missing_amount,
        ),
    ]
    latest = helpers.latest_row(rows, "receive_date")
    summary_cards.append(
        helpers.summary_card(
            helpers.t("آخر عطل"),
            (latest or {}).get("ticket_no") or "—",
            ((latest or {}).get("fault_type") or helpers.t("تفاصيل أحدث عطل")),
        )
    )
    summary_cards.append(
        helpers.summary_card(
            helpers.t("تاريخ آخر عطل"),
            ((latest or {}).get("receive_date") or "—"),
            helpers.t("أحدث تاريخ استلام"),
        )
    )
    summary_cards.extend(
        helpers.work_ratio_cards(base_amount=helpers.sum_money_field(rows, "final_value"))
    )
    return render_template(
        "tickets_list.html",
        rows=rows, q=q, status=status, date_from=date_from, date_to=date_to, classification=classification,
        missing_amount=missing_amount,
        export_href=helpers.url_with_filters(".export_excel"),
        export_pdf_href=helpers.url_with_filters(".export_pdf"),
        summary_cards=summary_cards,
    )

@tickets_bp.route("/template.xlsx")
@permissions.require_perm("tickets.read")
def template():
    data = tickets_excel.build_tickets_template()
    return send_file(
        io.BytesIO(data), as_attachment=True, download_name="قالب_الأعطال.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

@tickets_bp.route("/import", methods=["POST"])
@permissions.require_perm("tickets.write")
def import_from_excel():
    f = request.files.get("file")
    if not f or not f.filename:
        flash(helpers.t("اختر ملف Excel للأعطال"), "danger")
        return redirect(url_for(".list_all"))
    try:
        result = tickets_excel.import_tickets_from_excel(f)
        flash(helpers.t("استيراد الأعطال: جديد {ok} | محدّث {updated}", ok=result["ok"], updated=result["updated"]), "ok")
        if result.get("errors"):
            flash(" / ".join(result["errors"][:5]), "danger")
        db.log_audit(helpers.current_user_name(), "استيراد Excel", "أعطال", details=str(result)[:240])
        if result["ok"] or result["updated"]:
            helpers.after_data_change()
    except Exception as exc:
        flash(helpers.t("تعذر الاستيراد: {exc}", exc=exc), "danger")
    return redirect(url_for(".list_all"))

@tickets_bp.route("/new", methods=["GET", "POST"])
@permissions.require_perm("tickets.write")
def new():
    if request.method == "POST":
        data = ticket_from_form()
        if not data["ticket_no"]:
            flash(helpers.t("رقم العطل مطلوب"), "danger")
            return render_template("ticket_form.html", row=data, mode="new")
        conn = db.connect()
        try:
            if not (data.get("rekaz_code") or "").strip():
                data["rekaz_code"] = db.next_series_code("er", conn)
            cols = ", ".join(TICKET_FIELDS)
            placeholders = ", ".join(["?"] * len(TICKET_FIELDS))
            cur = conn.execute(
                f"INSERT INTO tickets({cols}) VALUES ({placeholders})", [data[f] for f in TICKET_FIELDS],
            )
            conn.commit()
            db.log_audit(
                helpers.current_user_name(), "إضافة", "عطل", cur.lastrowid,
                f"{data.get('ticket_no')} / {data.get('rekaz_code')}",
            )
            new_id = cur.lastrowid
            flash(helpers.t("تم إنشاء العطل بنجاح — كود ركاز {code}", code=data.get("rekaz_code")), "ok")
            helpers.after_data_change()
            return _edit_redirect(new_id, "data")
        except Exception as exc:
            flash(helpers.t("تعذر الحفظ: {exc}", exc=exc), "danger")
        finally:
            conn.close()
    blank = {f: "" for f in TICKET_FIELDS}
    blank["receive_date"] = datetime.now().strftime("%Y-%m-%d")
    blank["status"] = "تم الإسناد"
    blank["rekaz_code"] = ""
    return render_template("ticket_form.html", row=blank, mode="new")

def _wizard_steps():
    steps = [
        ("data", helpers.t("بيانات المعاملة")), ("boq", helpers.t("إضافة الكمية")), ("photos", helpers.t("الصور")),
        ("metering", helpers.t("التمتير")),
    ]
    if permissions.can("section.warehouses"):
        steps.append(("warehouse", helpers.t("المستودع")))
    steps.append(("done", helpers.t("الاكتمال")))
    return steps

def _next_step(current):
    keys = [s[0] for s in _wizard_steps()]
    if not keys: return "data"
    if current not in keys: return keys[0]
    idx = keys.index(current)
    return keys[idx + 1] if idx + 1 < len(keys) else keys[-1]

def _edit_redirect(ticket_id, step):
    step = step or "data"
    return redirect(url_for(".view", ticket_id=ticket_id, edit=1, step=step) + f"#step-{step}")

@tickets_bp.route("/<int:ticket_id>")
@permissions.require_perm("tickets.read")
def view(ticket_id):
    conn = db.connect()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close()
        flash(helpers.t("العطل غير موجود"), "danger")
        return redirect(url_for(".list_all"))
    ticket = dict(row)
    ticket["status"] = db.normalize_ticket_status(ticket.get("status"))
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
        "new_coordinations": db.rows_to_dicts(
            conn.execute("SELECT * FROM new_coordinations WHERE ticket_no=? ORDER BY id DESC", (tno,)).fetchall()
        ),
        "issued_licenses": db.rows_to_dicts(
            conn.execute("SELECT * FROM issued_licenses WHERE ticket_no=? ORDER BY id DESC", (tno,)).fetchall()
        ),
    }
    quality_workflow = db.quality_workflow_for_ref(ticket_no=tno, linked_section="ops", conn=conn)
    boq_file = db.active_contract_boq_file(conn)
    has_boq = db.has_boq_catalog(conn)
    has_excavation = db.ticket_has_excavation(tno, conn)
    excavation_link = None
    if has_excavation:
        excavation_link = db.ensure_excavation_coordination(
            tno, reason="ربط تلقائي من عرض العطل — حفر", conn=conn, create_clearance=True,
        )
        related["coordination"] = db.rows_to_dicts(conn.execute("SELECT * FROM coordination WHERE ticket_no=?", (tno,)).fetchall())
        related["clearances"] = db.rows_to_dicts(conn.execute("SELECT * FROM quality_clearances WHERE ticket_no=?", (tno,)).fetchall())
    else:
        related["clearances"] = db.rows_to_dicts(conn.execute("SELECT * FROM quality_clearances WHERE ticket_no=?", (tno,)).fetchall())
    conn.commit()
    conn.close()
    for q in related["quantities"]:
        q["total"] = float(q.get("qty") or 0) * float(q.get("unit_price") or 0)
    for p in related["photos"]:
        p["complete"] = helpers.t("مكتمل") if media_svc.photos_complete(p) else helpers.t("ناقص")
    boq_base = sum(float(x.get("line_total") or 0) for x in related["boq_lines"])
    settings = db.get_settings()
    settings_ratio = float(settings.get("emergency_ratio") or 0)
    emergency_applied = db.ticket_emergency_ratio(related["boq_lines"], settings_ratio)
    ticket["response_min"] = helpers.response_minutes(ticket.get("dispatch_time"), ticket.get("arrival_time"))
    if related["boq_lines"]:
        ticket["items_value"] = boq_base
        base_for_final = boq_base
    else:
        base_for_final = ticket.get("items_value")
    ticket["boq_base_total"] = boq_base if related["boq_lines"] else None
    ticket["emergency_ratio_applied"] = emergency_applied
    ticket["final_value"] = helpers.final_value(base_for_final, ratio=emergency_applied)
    ticket["boq_final_total"] = ticket["final_value"]
    ticket["has_excavation"] = has_excavation
    can_mutate = permissions.can("tickets.write")
    wants_edit = request.args.get("edit") == "1"
    if wants_edit and not can_mutate:
        flash(helpers.t("ليس لديك صلاحية لتعديل العطل أو بنوده. العرض متاح للقراءة فقط."), "danger")
        return redirect(url_for(".view", ticket_id=ticket_id))
    edit_mode = wants_edit and can_mutate
    wizard_steps = _wizard_steps() if edit_mode else []
    step_keys = [s[0] for s in wizard_steps]
    raw_step = (request.args.get("step") or "data").strip()
    edit_step = raw_step if raw_step in step_keys else (step_keys[0] if step_keys else "data")
    next_step = _next_step(edit_step) if edit_mode else None
    return render_template(
        "ticket_view.html", ticket=ticket, related=related, quality_workflow=quality_workflow,
        has_boq_catalog=has_boq, boq_file=boq_file,
        emergency_ratio=float(settings.get("emergency_ratio") or 0),
        edit_mode=edit_mode, can_mutate=can_mutate, wizard_steps=wizard_steps,
        edit_step=edit_step, next_step=next_step, step_labels=dict(wizard_steps),
    )

@tickets_bp.route("/<int:ticket_id>/edit", methods=["GET", "POST"])
@permissions.require_perm("tickets.write")
def edit(ticket_id):
    conn = db.connect()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close()
        flash(helpers.t("العطل غير موجود"), "danger")
        return redirect(url_for(".list_all"))
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
        db.sync_ticket_work_order_to_related(
            data.get("ticket_no") or dict(row).get("ticket_no") or "",
            data.get("work_order") or "", data.get("rekaz_code") or "", conn,
        )
        conn.commit()
        link_res = helpers.link_excavation_if_needed(
            data.get("ticket_no") or dict(row).get("ticket_no") or "",
            reason="ربط تلقائي بعد حفظ العطل — حفر/إخلاء أسفلت", conn=conn,
        )
        if link_res and (link_res.get("created_coord") or link_res.get("created_clearance")):
            conn.commit()
        conn.close()
        db.log_audit(helpers.current_user_name(), "تعديل", "عطل", ticket_id, data.get("ticket_no"))
        flash(helpers.t("تم حفظ المعاملة"), "ok")
        helpers.flash_excavation_link(link_res)
        helpers.after_data_change()
        stay = (request.form.get("step") or request.args.get("step") or "data").strip()
        allowed = {s[0] for s in _wizard_steps()}
        if stay not in allowed: stay = "data"
        return _edit_redirect(ticket_id, stay)
    conn.close()
    return _edit_redirect(ticket_id, request.args.get("step") or "data")

@tickets_bp.route("/<int:ticket_id>/delete", methods=["POST"])
@permissions.require_perm("tickets.delete")
def delete(ticket_id):
    if not helpers.delete_password_ok():
        return helpers.reject_bad_delete_password(url_for(".view", ticket_id=ticket_id, edit=1))
    conn = db.connect()
    row = conn.execute("SELECT ticket_no FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    conn.execute("DELETE FROM tickets WHERE id=?", (ticket_id,))
    conn.commit()
    conn.close()
    db.log_audit(helpers.current_user_name(), "حذف", "عطل", ticket_id, row["ticket_no"] if row else "")
    flash(helpers.t("تم حذف العطل"), "ok")
    helpers.after_data_change()
    return redirect(url_for(".list_all"))

@tickets_bp.route("/<int:ticket_id>/boq/add", methods=["POST"])
@permissions.require_perm("tickets.write")
def boq_add(ticket_id):
    conn = db.connect()
    ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not ticket:
        conn.close()
        flash(helpers.t("العطل غير موجود"), "danger")
        return redirect(url_for(".list_all"))
    item_no = (request.form.get("item_no") or "").strip()
    qty_raw = (request.form.get("qty") or "").strip()
    work_class = (request.form.get("work_class") or "اعتيادي").strip()
    ratio_raw = (request.form.get("increase_ratio") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    if not item_no:
        conn.close()
        flash(helpers.t("أدخل رقم البند من دليل العقد"), "danger")
        return _edit_redirect(ticket_id, "boq")
    try:
        qty = float(qty_raw) if qty_raw != "" else 0.0
    except ValueError:
        conn.close()
        flash(helpers.t("الكمية غير صالحة"), "danger")
        return _edit_redirect(ticket_id, "boq")
    try:
        ratio = float(ratio_raw) if ratio_raw != "" else None
    except ValueError:
        ratio = None
    catalog = db.get_contract_boq_item(item_no, conn)
    if not catalog:
        conn.close()
        flash(helpers.t("رقم البند «{item_no}» غير موجود في دليل العقد النشط — تحقق من الرقم أو ارفع الدليل من إدارة العقود", item_no=item_no), "danger")
        return _edit_redirect(ticket_id, "boq")
    active = db.active_contract_boq_file(conn)
    unit_price = catalog.get("unit_price")
    totals = db.calc_boq_line_totals(qty, unit_price, work_class, ratio)
    desc = (catalog.get("short_desc") or "").strip() or (catalog.get("description") or "").strip()
    conn.execute(
        """
        INSERT INTO ticket_boq_lines(
          ticket_id, ticket_no, file_id, item_no, description, unit, qty, unit_price,
          line_total, work_class, increase_ratio, final_total, notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ticket_id, ticket["ticket_no"], (active or {}).get("id") if active else catalog.get("file_id"),
            catalog.get("item_no"), desc, catalog.get("unit"), qty, unit_price,
            totals["line_total"], totals["work_class"], totals["increase_ratio"], totals["final_total"], notes,
        ),
    )
    conn.execute(
        """
        INSERT INTO quantities(ticket_no, item_no, description, unit, qty, unit_price, notes)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            ticket["ticket_no"], catalog.get("item_no"), desc, catalog.get("unit"), qty, unit_price, notes,
        ),
    )
    db.sync_ticket_items_value(ticket_id, conn)
    db.sync_metering_approved_value_for_ticket(ticket["ticket_no"], conn)
    link_res = None
    if db.is_excavation_text(desc, notes, item_no):
        link_res = db.ensure_excavation_coordination(
            ticket["ticket_no"], reason=f"ربط تلقائي — بند حفر {item_no}", conn=conn, create_clearance=True,
        )
    conn.commit()
    conn.close()
    db.log_audit(helpers.current_user_name(), "إضافة بند عقد", "عطل", ticket_id, f"{item_no} × {qty}")
    flash(helpers.t("تمت إضافة البند وحساب التكلفة — أضف بنداً آخر أو انتقل للخطوة التالية"), "ok")
    helpers.flash_excavation_link(link_res)
    helpers.after_data_change()
    return _edit_redirect(ticket_id, "boq")

@tickets_bp.route("/<int:ticket_id>/boq/<int:line_id>/delete", methods=["POST"])
@permissions.require_perm("tickets.write")
def boq_delete(ticket_id, line_id):
    if not helpers.delete_password_ok():
        return helpers.reject_bad_delete_password(url_for(".view", ticket_id=ticket_id, edit=1, step="boq"))
    conn = db.connect()
    line = conn.execute("SELECT * FROM ticket_boq_lines WHERE id=? AND ticket_id=?", (line_id, ticket_id)).fetchone()
    if line:
        qty_row = conn.execute(
            """
            SELECT id FROM quantities
            WHERE ticket_no=? AND lower(item_no)=lower(?) AND ABS(COALESCE(qty,0) - ?) < 0.0001
            ORDER BY id DESC LIMIT 1
            """,
            (line["ticket_no"], line["item_no"] or "", float(line["qty"] or 0)),
        ).fetchone()
        if qty_row:
            conn.execute("DELETE FROM quantities WHERE id=?", (qty_row["id"],))
    conn.execute("DELETE FROM ticket_boq_lines WHERE id=? AND ticket_id=?", (line_id, ticket_id))
    db.sync_ticket_items_value(ticket_id, conn)
    if line:
        db.sync_metering_approved_value_for_ticket(line["ticket_no"], conn)
    conn.commit()
    conn.close()
    flash(helpers.t("تم حذف البند"), "ok")
    helpers.after_data_change()
    return _edit_redirect(ticket_id, "boq")

@tickets_bp.route("/<int:ticket_id>/print")
@permissions.require_perm("tickets.read")
def print_view(ticket_id):
    conn = db.connect()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close()
        flash(helpers.t("العطل غير موجود"), "danger")
        return redirect(url_for(".list_all"))
    ticket = dict(row)
    ticket["status"] = db.normalize_ticket_status(ticket.get("status"))
    tno = ticket["ticket_no"]
    boq_lines = db.list_ticket_boq_lines(ticket_id=ticket_id, conn=conn)
    legacy_qty = db.rows_to_dicts(conn.execute("SELECT * FROM quantities WHERE ticket_no=?", (tno,)).fetchall())
    photos = db.rows_to_dicts(conn.execute("SELECT * FROM photos WHERE ticket_no=?", (tno,)).fetchall())
    coordination = db.rows_to_dicts(conn.execute("SELECT * FROM coordination WHERE ticket_no=?", (tno,)).fetchall())
    metering = db.rows_to_dicts(conn.execute("SELECT * FROM metering WHERE ticket_no=?", (tno,)).fetchall())
    conn.close()
    ticket["response_min"] = helpers.response_minutes(ticket.get("dispatch_time"), ticket.get("arrival_time"))
    settings = db.get_settings()
    settings_ratio = float(settings.get("emergency_ratio") or 0)
    emergency_applied = db.ticket_emergency_ratio(boq_lines, settings_ratio)
    boq_base = sum(float(x.get("line_total") or 0) for x in boq_lines) if boq_lines else None
    if boq_base is not None:
        ticket["items_value"] = boq_base
    ticket["boq_base_total"] = boq_base
    ticket["emergency_ratio_applied"] = emergency_applied
    ticket["final_value"] = helpers.final_value(
        ticket.get("items_value"), ratio=emergency_applied if boq_lines else settings_ratio,
    )
    if boq_lines:
        quantities = [
            {
                "item_no": x.get("item_no"), "description": x.get("description"), "unit": x.get("unit"),
                "qty": x.get("qty"), "unit_price": x.get("unit_price"), "total": x.get("line_total"),
                "work_class": x.get("work_class"), "increase_ratio": x.get("increase_ratio"),
            } for x in boq_lines
        ]
    else:
        quantities = legacy_qty
        for q in quantities:
            q["total"] = float(q.get("qty") or 0) * float(q.get("unit_price") or 0)
    return render_template(
        "ticket_print.html", ticket=ticket, quantities=quantities, photos=photos,
        coordination=coordination, metering=metering,
        printed_at=datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
        emergency_ratio_applied=emergency_applied if boq_lines else settings_ratio,
        boq_base_total=boq_base,
    )

@tickets_bp.route("/export.xlsx")
@permissions.require_perm("export")
def export_excel():
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    classification = (request.args.get("classification") or "").strip()
    missing_amount = helpers.missing_amount_flag()
    rows, _missing = _load_filtered_tickets(
        q=q, status=status, date_from=date_from, date_to=date_to, missing_amount=missing_amount, classification=classification,
    )
    data = tickets_excel.export_tickets(rows)
    stamp = datetime.now().strftime("%Y%m%d")
    suffix = "-بدون-مبلغ" if missing_amount else ""
    return send_file(
        io.BytesIO(data), as_attachment=True, download_name=f"الأعطال{suffix}-{stamp}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@tickets_bp.route("/export.pdf")
@permissions.require_perm("export")
def export_pdf():
    from webapp import reports as reports_svc

    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    classification = (request.args.get("classification") or "").strip()
    missing_amount = helpers.missing_amount_flag()
    rows, _missing = _load_filtered_tickets(
        q=q,
        status=status,
        date_from=date_from,
        date_to=date_to,
        missing_amount=missing_amount,
        classification=classification,
    )
    headers = [
        helpers.t("رقم العطل"),
        helpers.t("كود ER"),
        helpers.t("أمر العمل"),
        helpers.t("التاريخ"),
        helpers.t("المحطة"),
        helpers.t("الفرقة"),
        helpers.t("الحالة"),
        helpers.t("القيمة النهائية"),
    ]
    fields = ["ticket_no", "rekaz_code", "work_order", "receive_date", "station_no", "team", "status", "final_value"]
    filters = []
    if q:
        filters.append(f"{helpers.t('بحث')}: {q}")
    if status:
        filters.append(f"{helpers.t('الحالة')}: {status}")
    if classification:
        filters.append(f"{helpers.t('التصنيف')}: {classification}")
    if date_from or date_to:
        filters.append(f"{helpers.t('من')}: {date_from or '—'} | {helpers.t('إلى')}: {date_to or '—'}")
    if missing_amount:
        filters.append(helpers.t("بدون مبلغ"))
    data = reports_svc.build_table_pdf(
        title_text=helpers.t("الأعطال"),
        headers=headers,
        rows=rows,
        field_keys=fields,
        filters=filters,
        amount_cards=[
            {
                "title": helpers.t("إجمالي المبالغ"),
                "value": helpers.sum_money_field(rows, "final_value"),
                "money": True,
                "subtitle": helpers.t("حسب الفلترة الحالية"),
            },
            *helpers.work_ratio_cards(base_amount=helpers.sum_money_field(rows, "final_value")),
        ],
    )
    stamp = datetime.now().strftime("%Y%m%d")
    suffix = "-مفلتر" if filters else ""
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=f"الأعطال{suffix}-{stamp}.pdf",
        mimetype="application/pdf",
    )
