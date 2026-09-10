import io
import os
import sys
import unittest
from datetime import date, timedelta

os.environ['SECRET_KEY'] = 'test-secret-key-for-unit-tests-123456789'
from webapp import app as flask_app
from webapp import db, reports, permissions

class HRSystemTestCase(unittest.TestCase):
    def setUp(self):
        flask_app.app.config['TESTING'] = True
        flask_app.app.config['SECRET_KEY'] = 'test_secret'
        self.client = flask_app.app.test_client()

        # Login session as admin
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'admin'
            sess['username'] = 'admin'
            sess['session_token'] = 'test_token'
            sess['lang'] = 'ar'

        conn = db.connect()
        # Ensure schema
        db.ensure_schema(conn)
        # Ensure user 1 exists with active session token for auth guard
        conn.execute("INSERT OR REPLACE INTO users(id, username, role, active, active_session_token) VALUES (1, 'admin', 'admin', 1, 'test_token')")
        conn.commit()
        conn.close()

    def test_01_db_schema_and_columns(self):
        conn = db.connect()
        emp_cols = [r[1] for r in conn.execute("PRAGMA table_info(hr_employees)").fetchall()]
        leave_cols = [r[1] for r in conn.execute("PRAGMA table_info(hr_leaves)").fetchall()]
        conn.close()

        for expected in ['id_number', 'id_expiry_date', 'nationality', 'profession',
                         'driving_license_no', 'license_expiry_date', 'insurance_expiry_date',
                         'contract_end_date', 'basic_salary', 'housing_allowance',
                         'other_allowances', 'bank_name', 'iban', 'emergency_contact_name']:
            self.assertIn(expected, emp_cols, f"Column {expected} should be in hr_employees")

        for expected in ['employee_id', 'employee_name', 'leave_type', 'start_date', 'end_date', 'days_count', 'status']:
            self.assertIn(expected, leave_cols, f"Column {expected} should be in hr_leaves")

    def test_02_insert_employee_and_stats(self):
        conn = db.connect()
        today = date.today()
        exp_soon = (today + timedelta(days=15)).isoformat()
        exp_far = (today + timedelta(days=200)).isoformat()

        # Insert test employees
        conn.execute("DELETE FROM hr_employees WHERE emp_no IN ('TEST-01', 'TEST-02')")
        conn.execute("""
            INSERT INTO hr_employees (
                emp_no, full_name, job_title, department, status, phone,
                id_number, id_expiry_date, driving_license_no, license_expiry_date,
                basic_salary, housing_allowance, bank_name, iban
            ) VALUES (
                'TEST-01', 'سعد فهد القحطاني', 'مشرف ميداني', 'العمليات', 'على رأس العمل', '0501112233',
                '1011122233', ?, 'DL-1122', ?,
                8000, 2000, 'مصرف الراجحي', 'SA0000000000000000000001'
            )
        """, (exp_soon, exp_far))

        conn.execute("""
            INSERT INTO hr_employees (
                emp_no, full_name, job_title, department, status, phone,
                id_number, id_expiry_date, basic_salary
            ) VALUES (
                'TEST-02', 'أحمد كمال الدين', 'فني كهرباء', 'العمليات', 'على رأس العمل', '0504445566',
                '2011122233', ?, 4500
            )
        """, (exp_far,))
        conn.commit()

        stats = db.get_hr_dashboard_stats(conn)
        self.assertGreaterEqual(stats['total_employees'], 2)
        self.assertGreaterEqual(stats['total_critical'], 1)

        expiring = db.list_expiring_documents(conn, days_threshold=60)
        found = any(d['emp_no'] == 'TEST-01' and d['urgency'] == 'critical' for d in expiring)
        self.assertTrue(found, "TEST-01 should appear as critical expiring document")

        # Clean up
        conn.close()

    def test_03_hr_routes_and_dossier(self):
        # 1. GET /hr
        resp = self.client.get('/hr')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('لوحة الموارد البشرية'.encode('utf-8'), resp.data)
        self.assertIn('رادار الوثائق والتنبيهات'.encode('utf-8'), resp.data)

        # Get employee ID
        conn = db.connect()
        row = conn.execute("SELECT id FROM hr_employees WHERE emp_no='TEST-01'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        emp_id = row['id']

        # 2. GET dossier
        resp_dossier = self.client.get(f'/hr/employee/{emp_id}/dossier')
        self.assertEqual(resp_dossier.status_code, 200)
        self.assertIn('سعد فهد القحطاني'.encode('utf-8'), resp_dossier.data)
        self.assertIn('البيانات المالية والبنكية'.encode('utf-8'), resp_dossier.data)

        # 3. GET PDF
        resp_pdf = self.client.get(f'/hr/employee/{emp_id}/pdf')
        self.assertEqual(resp_pdf.status_code, 200)
        self.assertEqual(resp_pdf.content_type, 'application/pdf')
        self.assertTrue(len(resp_pdf.data) > 1000)

    def test_04_leave_status_synchronization(self):
        conn = db.connect()
        today = date.today().isoformat()
        end_d = (date.today() + timedelta(days=10)).isoformat()

        # Insert active leave for TEST-01 via client post
        resp = self.client.post('/module/hr_leaves/new', data={
            'employee_name': 'سعد فهد القحطاني',
            'leave_type': 'سنوية',
            'start_date': today,
            'end_date': end_d,
            'days_count': '10',
            'status': 'جارية',
            'notes': 'إجازة سنوية مصرحة',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        # Verify employee status updated to 'إجازة'
        row = conn.execute("SELECT status FROM hr_employees WHERE emp_no='TEST-01'").fetchone()
        self.assertEqual(row['status'], 'إجازة')

        # Now test return
        leave_row = conn.execute("SELECT id FROM hr_leaves WHERE employee_name='سعد فهد القحطاني' ORDER BY id DESC").fetchone()
        leave_id = leave_row['id']

        resp_return = self.client.post(f'/module/hr_leaves/{leave_id}/edit', data={
            'employee_name': 'سعد فهد القحطاني',
            'leave_type': 'سنوية',
            'start_date': today,
            'end_date': end_d,
            'days_count': '10',
            'status': 'منتهية',
            'actual_return_date': today,
            'notes': 'عاد لمباشرة العمل',
        }, follow_redirects=True)
        self.assertEqual(resp_return.status_code, 200)

        # Verify employee status returned to 'على رأس العمل'
        row_after = conn.execute("SELECT status FROM hr_employees WHERE emp_no='TEST-01'").fetchone()
        self.assertEqual(row_after['status'], 'على رأس العمل')

        # Clean up test data
        conn.execute("DELETE FROM hr_leaves WHERE employee_name='سعد فهد القحطاني'")
        conn.execute("DELETE FROM hr_employees WHERE emp_no IN ('TEST-01', 'TEST-02')")
        conn.commit()
        conn.close()

if __name__ == '__main__':
    unittest.main()
