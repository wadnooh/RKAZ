"""محرك المتابعة والمراجعة الشاملة — تنبيهات تلقائية + اكتمال رحلة البلاغ."""

from __future__ import annotations

from webapp import db


def response_minutes(dispatch, arrival):
    if not dispatch or not arrival:
        return None
    try:
        h1, m1 = map(int, str(dispatch).split(":")[:2])
        h2, m2 = map(int, str(arrival).split(":")[:2])
        diff = h2 * 60 + m2 - (h1 * 60 + m1)
        if diff < 0:
            diff += 24 * 60
        return diff
    except Exception:
        return None


def ticket_journey(ticket_no, conn=None):
    own = conn is None
    conn = conn or db.connect()
    ticket = conn.execute("SELECT * FROM tickets WHERE ticket_no=?", (ticket_no,)).fetchone()
    if not ticket:
        if own:
            conn.close()
        return None
    ticket = dict(ticket)

    def count(table):
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE ticket_no=?", (ticket_no,)).fetchone()[0]

    photos = db.rows_to_dicts(conn.execute("SELECT * FROM photos WHERE ticket_no=?", (ticket_no,)).fetchall())
    photo_ok = False
    if photos:
        keys = ["before_shot", "during_shot", "after_shot", "quantities_shot", "location_shot"]
        photo_ok = any(all(p.get(k) == "نعم" for k in keys) for p in photos)

    checks = [
        {"key": "registered", "label": "تسجيل البلاغ", "ok": bool(ticket.get("ticket_no")), "required": True},
        {"key": "dispatched", "label": "توجيه الفرقة", "ok": bool(ticket.get("dispatch_time") and ticket.get("team")), "required": True},
        {"key": "arrived", "label": "وصول الفرقة", "ok": bool(ticket.get("arrival_time")), "required": True},
        {"key": "executed", "label": "تنفيذ / إغلاق", "ok": ticket.get("status") in ("منفذ", "مغلق"), "required": True},
        {"key": "photos", "label": "الصور مكتملة", "ok": photo_ok, "required": True},
        {"key": "quantities", "label": "الكميات", "ok": count("quantities") > 0, "required": True},
        {"key": "coordination", "label": "التنسيقات", "ok": count("coordination") > 0, "required": False},
        {"key": "metering", "label": "التمتير", "ok": count("metering") > 0, "required": True},
        {"key": "invoice", "label": "المستخلص", "ok": count("invoices") > 0, "required": False},
        {"key": "safety", "label": "تصريح سلامة", "ok": count("safety_permits") > 0, "required": False},
    ]
    required = [c for c in checks if c["required"]]
    done_req = sum(1 for c in required if c["ok"])
    score = int(round((done_req / len(required)) * 100)) if required else 0
    if own:
        conn.close()
    return {
        "ticket": ticket,
        "checks": checks,
        "score": score,
        "complete": score >= 100,
        "missing": [c["label"] for c in checks if c["required"] and not c["ok"]],
    }


def build_alerts(settings=None):
    settings = settings or db.get_settings()
    target = float(settings.get("response_target") or 30)
    conn = db.connect()
    tickets = db.rows_to_dicts(conn.execute("SELECT * FROM tickets ORDER BY id DESC").fetchall())
    photos = db.rows_to_dicts(conn.execute("SELECT * FROM photos").fetchall())
    metering = db.rows_to_dicts(conn.execute("SELECT * FROM metering").fetchall())
    invoices = db.rows_to_dicts(conn.execute("SELECT * FROM invoices").fetchall())
    items = db.rows_to_dicts(conn.execute("SELECT * FROM warehouse_items").fetchall())
    open_followups = conn.execute(
        "SELECT COUNT(*) FROM followups WHERE status IN ('مفتوح','قيد المتابعة')"
    ).fetchone()[0]
    overdue = conn.execute(
        """
        SELECT COUNT(*) FROM followups
        WHERE status IN ('مفتوح','قيد المتابعة')
          AND due_date IS NOT NULL AND due_date != ''
          AND due_date < date('now')
        """
    ).fetchone()[0]
    conn.close()

    alerts = []
    photo_map = {}
    for p in photos:
        photo_map.setdefault(p.get("ticket_no"), []).append(p)
    metering_set = {m.get("ticket_no") for m in metering}
    keys = ["before_shot", "during_shot", "after_shot", "quantities_shot", "location_shot"]

    for t in tickets:
        tno = t.get("ticket_no")
        mins = response_minutes(t.get("dispatch_time"), t.get("arrival_time"))
        if mins is not None and mins > target:
            alerts.append(
                {
                    "level": "danger",
                    "category": "استجابة متأخرة",
                    "title": f"بلاغ {tno} — استجابة {mins} دقيقة",
                    "ticket_no": tno,
                    "href_name": "tickets_list",
                    "href_q": tno,
                }
            )
        plist = photo_map.get(tno) or []
        photo_ok = any(all(p.get(k) == "نعم" for k in keys) for p in plist) if plist else False
        if t.get("status") in ("منفذ", "مغلق", "قيد التنفيذ") and not photo_ok:
            alerts.append(
                {
                    "level": "warn",
                    "category": "صور ناقصة",
                    "title": f"بلاغ {tno} — الصور غير مكتملة",
                    "ticket_no": tno,
                    "href_name": "module_list",
                    "href_args": {"name": "photos"},
                }
            )
        if t.get("status") in ("منفذ", "مغلق") and tno not in metering_set:
            alerts.append(
                {
                    "level": "warn",
                    "category": "تمتير ناقص",
                    "title": f"بلاغ {tno} — لا يوجد تمتير",
                    "ticket_no": tno,
                    "href_name": "module_list",
                    "href_args": {"name": "metering"},
                }
            )
        if t.get("status") not in ("مغلق", "مرفوض/إعادة عمل") and t.get("status"):
            journey = ticket_journey(tno)
            if journey and journey["score"] < 70 and t.get("status") in ("منفذ", "قيد التنفيذ"):
                alerts.append(
                    {
                        "level": "info",
                        "category": "استكمال رحلة",
                        "title": f"بلاغ {tno} — اكتمال {journey['score']}% — ناقص: {', '.join(journey['missing'][:3])}",
                        "ticket_no": tno,
                        "href_name": "ticket_review",
                        "href_args": {"ticket_no": tno},
                    }
                )

    for inv in invoices:
        rem = float(inv.get("value") or 0) - float(inv.get("collected") or 0)
        if rem > 0 and (inv.get("sap_status") or "") not in ("مقبول",):
            alerts.append(
                {
                    "level": "info",
                    "category": "مستحقات",
                    "title": f"مستخلص {inv.get('invoice_id') or inv.get('id')} — متبقي {rem:,.0f}",
                    "ticket_no": inv.get("ticket_no"),
                    "href_name": "module_list",
                    "href_args": {"name": "invoices"},
                }
            )

    for item in items:
        bal = db.warehouse_balance(item.get("item_no"))
        min_q = float(item.get("min_qty") or 0)
        if bal <= min_q:
            alerts.append(
                {
                    "level": "danger",
                    "category": "رصيد مستودع",
                    "title": f"مادة {item.get('item_no')} — رصيد {bal} (الحد {min_q})",
                    "ticket_no": None,
                    "href_name": "warehouse_balances",
                    "href_args": {},
                }
            )

    if overdue:
        alerts.insert(
            0,
            {
                "level": "danger",
                "category": "متابعات متأخرة",
                "title": f"يوجد {overdue} متابعة متأخرة عن موعدها",
                "ticket_no": None,
                "href_name": "review_home",
                "href_args": {},
            },
        )

    summary = {
        "total": len(alerts),
        "danger": sum(1 for a in alerts if a["level"] == "danger"),
        "warn": sum(1 for a in alerts if a["level"] == "warn"),
        "info": sum(1 for a in alerts if a["level"] == "info"),
        "open_followups": open_followups,
        "overdue_followups": overdue,
    }
    return alerts, summary


def jump_destinations():
    """قائمة وجهات نافذة القفز السريع."""
    return [
        {"title": "لوحة العمليات", "path": "/ops", "group": "عمليات", "keywords": "لوحة dashboard ops"},
        {"title": "بلاغات الأعمال", "path": "/tickets", "group": "عمليات", "keywords": "بلاغ ticket طوارئ"},
        {"title": "بلاغ جديد", "path": "/tickets/new", "group": "عمليات", "keywords": "إضافة بلاغ جديد"},
        {"title": "الكميات", "path": "/module/quantities", "group": "عمليات", "keywords": "كميات boq"},
        {"title": "قائمة الصور", "path": "/module/photos", "group": "عمليات", "keywords": "صور"},
        {"title": "التمتير", "path": "/module/metering", "group": "عمليات", "keywords": "تمتير مستخلص"},
        {"title": "المستخلصات و SAP", "path": "/module/invoices", "group": "عمليات", "keywords": "فاتورة sap"},
        {"title": "التدفق النقدي", "path": "/cashflow", "group": "عمليات", "keywords": "سيولة نقدي"},
        {"title": "فرق المهام", "path": "/teams", "group": "عمليات", "keywords": "فرقة فريق"},
        {"title": "إجراءات العمل SOP", "path": "/sop", "group": "عمليات", "keywords": "sop إجراء"},
        {"title": "القوائم المرجعية", "path": "/lists", "group": "عمليات", "keywords": "قوائم"},
        {"title": "المتابعة والمراجعة", "path": "/review", "group": "متابعة", "keywords": "مراجعة تنبيه متابعة review"},
        {"title": "الإنشاءات", "path": "/constructions", "group": "أقسام", "keywords": "إنشاءات"},
        {"title": "التنسيقات والجودة", "path": "/quality", "group": "أقسام", "keywords": "تنسيق جودة"},
        {"title": "السلامة", "path": "/safety", "group": "أقسام", "keywords": "سلامة تصريح"},
        {"title": "المستودعات", "path": "/warehouses", "group": "أقسام", "keywords": "مستودع مواد"},
        {"title": "أرصدة المواد", "path": "/warehouses/balances", "group": "أقسام", "keywords": "رصيد"},
        {"title": "المشتريات والعهد", "path": "/external-purchases", "group": "أقسام", "keywords": "شراء عهدة"},
        {"title": "الورشة", "path": "/maintenance", "group": "أقسام", "keywords": "سيارة معدة ورشة"},
        {"title": "إدارة العقود", "path": "/contracts-admin", "group": "أقسام", "keywords": "عقد"},
        {"title": "المستخدمون", "path": "/users/list", "group": "إدارة", "keywords": "مستخدم"},
        {"title": "الإعدادات", "path": "/settings", "group": "إدارة", "keywords": "إعدادات"},
        {"title": "سجل النشاط", "path": "/admin/audit-log/view", "group": "إدارة", "keywords": "سجل audit"},
        {"title": "البحث العام", "path": "/search", "group": "إدارة", "keywords": "بحث search"},
    ]
