import os
import pytest
import sqlite3
from datetime import datetime

os.environ["SECRET_KEY"] = "test-secret-key-for-warehouse-testing"
from webapp import db
from webapp.app import app as flask_app


def test_is_protected_warehouse_user():
    # Staff users
    assert db.is_protected_warehouse_user("عبدالله", {}) is True
    assert db.is_protected_warehouse_user("عبد الله", {}) is True
    assert db.is_protected_warehouse_user("نصير", {}) is True
    assert db.is_protected_warehouse_user("عارف", {}) is True
    assert db.is_protected_warehouse_user("أحمد عارف", {}) is True
    assert db.is_protected_warehouse_user("mohammed_nasseer", {}) is True
    assert db.is_protected_warehouse_user("abdullah_wh", {}) is True

    # Supplier or external users
    assert db.is_protected_warehouse_user("شركة المقاولات الحديثة", {"source_section": "contractors"}) is False
    assert db.is_protected_warehouse_user("مورد الكابلات", {}) is False
    assert db.is_protected_warehouse_user("", {}) is False
    assert db.is_protected_warehouse_user(None, {}) is False


def test_resolve_and_repair_work_orders_from_root():
    conn = db.connect()
    try:
        db.ensure_schema(conn)

        # 1. إنشاء عطل برقم وتذكرة وأمر عمل
        ticket_no = f"T-TEST-{int(datetime.now().timestamp())}"
        work_order = f"WO-TEST-{int(datetime.now().timestamp())}"
        conn.execute(
            """
            INSERT INTO tickets(ticket_no, work_order, status, created_at)
            VALUES (?, ?, 'تم الإسناد', '2026-10-08')
            """,
            (ticket_no, work_order),
        )

        # 2. إنشاء توريد مقاول بدون أمر عمل
        supply_no = f"SUP-TEST-{int(datetime.now().timestamp())}"
        cur = conn.execute(
            """
            INSERT INTO contractor_supplies(supply_no, supply_date, contractor, ticket_no, work_no, status)
            VALUES (?, '2026-10-08', 'مقاول الاختبار', ?, '', 'جديد')
            """,
            (supply_no, ticket_no),
        )
        supply_id = cur.lastrowid

        # 3. إنشاء حركة مستودع مرتبطة بدون أمر عمل
        vouch_no = f"V-TEST-{int(datetime.now().timestamp())}"
        cur_tx = conn.execute(
            """
            INSERT INTO warehouse_tx(
                voucher_no, tx_date, tx_type, item_no, item_name, unit, qty,
                ticket_no, source_section, source_ref, work_order, created_by
            ) VALUES (?, '2026-10-08', 'وارد مواد موردة من مقاول', 'ITEM-01', 'مادة تجريبية', 'عدد', 10,
                ?, 'contractors', ?, '', 'المورد التجريبي')
            """,
            (vouch_no, ticket_no, supply_no),
        )
        tx_id = cur_tx.lastrowid
        conn.commit()

        # 4. التحقق قبل الإصلاح
        tx_before = conn.execute("SELECT work_order FROM warehouse_tx WHERE id=?", (tx_id,)).fetchone()
        assert not (tx_before["work_order"] or "").strip()
        sup_before = conn.execute("SELECT work_no FROM contractor_supplies WHERE id=?", (supply_id,)).fetchone()
        assert not (sup_before["work_no"] or "").strip()

        # 5. تشغيل الإصلاح الجذري
        res = db.reconcile_and_repair_warehouse_work_orders(conn)
        assert res["total_repaired"] >= 1

        # 6. التحقق بعد الإصلاح من تحديث الجدولين وأعمدة التتبع
        tx_after = conn.execute("SELECT work_order, last_action, updated_at FROM warehouse_tx WHERE id=?", (tx_id,)).fetchone()
        assert tx_after["work_order"] == work_order
        assert tx_after["last_action"] != ""
        assert tx_after["updated_at"] != ""

        sup_after = conn.execute("SELECT work_no, last_action, updated_at FROM contractor_supplies WHERE id=?", (supply_id,)).fetchone()
        assert sup_after["work_no"] == work_order
        assert sup_after["last_action"] != ""
        assert sup_after["updated_at"] != ""
    finally:
        conn.close()


def test_add_missing_disbursed_with_protection():
    conn = db.connect()
    try:
        db.ensure_schema(conn)

        item_no = f"ITEM-{int(datetime.now().timestamp())}"
        wo = f"WO-DISB-{int(datetime.now().timestamp())}"

        # حركة توريد من مقاول (ليست من موظف مستودع)
        vouch_supp = f"V-SUP-{int(datetime.now().timestamp())}"
        conn.execute(
            """
            INSERT INTO warehouse_tx(
                voucher_no, tx_date, tx_type, item_no, item_name, unit, qty,
                source_section, source_ref, work_order, created_by
            ) VALUES (?, '2026-10-08', 'وارد مواد موردة من مقاول', ?, 'كابل', 'متر', 25,
                'contractors', 'REF-001', ?, 'مورد خارجي')
            """,
            (vouch_supp, item_no, wo),
        )

        # حركة وارد أخرى تم إدخالها من موظف المستودع المحمي (عبدالله)
        vouch_staff = f"V-STF-{int(datetime.now().timestamp())}"
        item_staff = f"ITEM-STF-{int(datetime.now().timestamp())}"
        conn.execute(
            """
            INSERT INTO warehouse_tx(
                voucher_no, tx_date, tx_type, item_no, item_name, unit, qty,
                source_section, source_ref, work_order, created_by
            ) VALUES (?, '2026-10-08', 'وارد مواد موردة من مقاول', ?, 'قاطع', 'عدد', 5,
                'contractors', 'REF-002', ?, 'عبدالله')
            """,
            (vouch_staff, item_staff, wo),
        )
        conn.commit()

        # تشغيل الإضافة بدون force_staff (الوضع الطبيعي: حماية موظفي المستودع)
        stats = db.add_missing_disbursed_movements(conn, force_staff=False, current_user="النظام")
        assert stats["added_disbursed"] >= 1
        assert stats["skipped_protected"] >= 1

        # التحقق من أنه تم إنشاء منصرف لمعاملة المورد الخارجي
        out_supp = conn.execute(
            "SELECT * FROM warehouse_tx WHERE item_no=? AND tx_type='منصرف للمعاملة'",
            (item_no,),
        ).fetchone()
        assert out_supp is not None
        assert float(out_supp["qty"]) == 25.0
        assert out_supp["work_order"] == wo

        # والتحقق من أنه لم يتم لمس معاملة موظف المستودع عبدالله
        out_staff = conn.execute(
            "SELECT * FROM warehouse_tx WHERE item_no=? AND tx_type='منصرف للمعاملة'",
            (item_staff,),
        ).fetchone()
        assert out_staff is None

        # الآن بتأكيد الإذن المباشر بخطوة واحدة (force_staff=True)
        stats_force = db.add_missing_disbursed_movements(conn, force_staff=True, current_user="المسؤول المعتمد")
        assert stats_force["added_disbursed"] >= 1

        out_staff_after = conn.execute(
            "SELECT * FROM warehouse_tx WHERE item_no=? AND tx_type='منصرف للمعاملة'",
            (item_staff,),
        ).fetchone()
        assert out_staff_after is not None
        assert float(out_staff_after["qty"]) == 5.0
    finally:
        conn.close()


def test_warehouse_contractors_route(client=None):
    flask_app.config["TESTING"] = True
    conn = db.connect()
    db.ensure_schema(conn)
    conn.execute("INSERT OR REPLACE INTO users(id, username, role, active, active_session_token) VALUES (1, 'admin', 'admin', 1, 'test_token')")
    conn.commit()
    conn.close()

    with flask_app.test_client() as test_client:
        with test_client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "admin"
            sess["role"] = "admin"
            sess["session_token"] = "test_token"
            sess["lang"] = "ar"

        # 1. زيارة صفحة المقاولين داخل المستودع
        resp = test_client.get("/warehouses/contractors")
        assert resp.status_code == 200
        assert "مواد موردة من مقاول".encode("utf-8") in resp.data

        # 2. اختبار فلاتر أمر العمل
        resp_has = test_client.get("/warehouses/contractors?wo_status=has_wo")
        assert resp_has.status_code == 200

        resp_no = test_client.get("/warehouses/contractors?wo_status=no_wo")
        assert resp_no.status_code == 200

        # 3. اختبار مسار حركة أوامر العمل مع الفلتر
        resp_wo_mov = test_client.get("/warehouses/work-orders-movements?wo_status=has_wo")
        assert resp_wo_mov.status_code == 200

        # 4. اختبار زر إصلاح أوامر العمل POST
        resp_repair = test_client.post(
            "/warehouses/repair-work-orders",
            data={"next": "/warehouses/contractors"},
            follow_redirects=True,
        )
        assert resp_repair.status_code == 200
        assert "تم فحص وإصلاح المعاملات من الجذور بنجاح".encode("utf-8") in resp_repair.data

        # 5. اختبار زر إضافة المنصرف POST
        resp_disb = test_client.post(
            "/warehouses/add-disbursed",
            data={"next": "/warehouses/contractors"},
            follow_redirects=True,
        )
        assert resp_disb.status_code == 200

        # 6. اختبار زر الصرف الفوري للمعاملة داخل الصفحة
        resp_single_disb = test_client.post(
            "/warehouses/contractor-supply/1/disburse",
            data={"next": "/warehouses/contractors"},
            follow_redirects=True,
        )
        assert resp_single_disb.status_code == 200

        # 7. اختبار تسجيل حركة سريعة داخل الصفحة عبر Modal
        resp_quick = test_client.post(
            "/warehouses/quick-tx",
            data={
                "tx_type": "وارد مواد موردة من مقاول",
                "work_order": "WO-QUICK-99",
                "item_name": "سلك نحاس 50 ملم",
                "qty": "25",
                "unit": "متر",
                "next": "/warehouses/contractors",
            },
            follow_redirects=True,
        )
        assert resp_quick.status_code == 200
        assert "WO-QUICK-99".encode("utf-8") in resp_quick.data


def test_enrich_contractor_supplies_in_out():
    conn = db.connect()
    conn.execute(
        """
        INSERT INTO contractor_supplies(id, supply_no, supply_date, contractor, ticket_no, work_no, status, received_voucher_no)
        VALUES (10, 'SUP-INOUT-1', '2026-10-08', 'مؤسسة الرياض', 'T-555', 'WO-555', 'تم التوريد', 'RV-100')
        """
    )
    conn.execute(
        """
        INSERT INTO contractor_supply_lines(supply_id, supply_no, item_no, item_name, unit, qty)
        VALUES (10, 'SUP-INOUT-1', 'IT-1', 'كيبل نحاس', 'متر', 100.0)
        """
    )
    supplies = [dict(r) for r in conn.execute("SELECT * FROM contractor_supplies WHERE id=10").fetchall()]
    enriched = db.enrich_contractor_supplies_in_out(supplies, conn)
    assert enriched[0]["in_qty"] == 100.0
    assert enriched[0]["out_qty"] == 0.0
    assert enriched[0]["balance_qty"] == 100.0
    assert enriched[0]["mv_status"] == "pending_out"

    # الآن نقوم بالصرف الفوري
    disb_res = db.disburse_contractor_supply_from_warehouse(10, conn=conn, current_user="مشرف المستودع")
    assert disb_res["created"] == 1
    assert disb_res["voucher_no"]

    # إعادة الفحص
    supplies2 = [dict(r) for r in conn.execute("SELECT * FROM contractor_supplies WHERE id=10").fetchall()]
    enriched2 = db.enrich_contractor_supplies_in_out(supplies2, conn)
    assert enriched2[0]["in_qty"] == 100.0
    assert enriched2[0]["out_qty"] == 100.0
    assert enriched2[0]["balance_qty"] == 0.0
    assert enriched2[0]["mv_status"] == "completed"

    conn.close()

