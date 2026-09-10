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

        for expected in ['administration', 'id_number', 'id_expiry_date', 'nationality', 'profession',
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
        self.assertIn('admin_counts', stats)

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

    def test_06_employee_photo_and_comprehensive_report(self):
        conn = db.connect()
        # 1. Verify photo column
        emp_cols = [r[1] for r in conn.execute("PRAGMA table_info(hr_employees)").fetchall()]
        self.assertIn("photo", emp_cols, "Column photo must exist in hr_employees")

        # 2. Insert test employee with photo ref
        conn.execute("DELETE FROM hr_employees WHERE emp_no='TEST-PHOTO-01'")
        conn.execute("""
            INSERT INTO hr_employees (
                emp_no, full_name, administration, department, job_title, status, phone,
                basic_salary, housing_allowance, photo
            ) VALUES (
                'TEST-PHOTO-01', 'محمد طارق الدوسري', 'الموارد البشرية', 'التوظيف', 'أخصائي توظيف',
                'على رأس العمل', '0555555555', 7500, 1800, '["/media/local/photos/employees/test.jpg"]'
            )
        """)
        conn.commit()
        emp = conn.execute("SELECT * FROM hr_employees WHERE emp_no='TEST-PHOTO-01'").fetchone()
        emp_id = emp['id']
        conn.close()

        # 3. Test comprehensive report view
        resp_report = self.client.get('/hr/report')
        self.assertEqual(resp_report.status_code, 200)
        html = resp_report.get_data(as_text=True)
        self.assertIn('التقرير الشامل للموارد البشرية', html)
        self.assertIn('محمد طارق الدوسري', html)
        self.assertIn('TEST-PHOTO-01', html)

        # 4. Test comprehensive report with filters
        resp_filtered = self.client.get('/hr/report?administration=الموارد+البشرية&status=على+رأس+العمل')
        self.assertEqual(resp_filtered.status_code, 200)
        self.assertIn('محمد طارق الدوسري', resp_filtered.get_data(as_text=True))

        # 5. Test Excel export
        resp_excel = self.client.get('/hr/report/excel?administration=الموارد+البشرية')
        self.assertEqual(resp_excel.status_code, 200)
        self.assertEqual(resp_excel.data[:4], b"PK\x03\x04")

        # 6. Test PDF export
        resp_pdf = self.client.get('/hr/report/pdf?administration=الموارد+البشرية')
        self.assertEqual(resp_pdf.status_code, 200)
        self.assertEqual(resp_pdf.data[:4], b"%PDF")

        # 7. Test employee dossier and dossier PDF with photo
        resp_dossier = self.client.get(f'/hr/employee/{emp_id}/dossier')
        self.assertEqual(resp_dossier.status_code, 200)
        self.assertIn('محمد طارق الدوسري', resp_dossier.get_data(as_text=True))

        resp_emp_pdf = self.client.get(f'/hr/employee/{emp_id}/pdf')
        self.assertEqual(resp_emp_pdf.status_code, 200)
        self.assertEqual(resp_emp_pdf.data[:4], b"%PDF")

        # Cleanup
        conn = db.connect()
        conn.execute("DELETE FROM hr_employees WHERE emp_no='TEST-PHOTO-01'")
        conn.commit()
        conn.close()

    def test_07_employee_photo_upload_and_clear(self):
        # 1-pixel valid JPEG
        tiny_jpeg = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9'

        conn = db.connect()
        conn.execute("DELETE FROM hr_employees WHERE emp_no='TEST-UPL-01'")
        conn.commit()
        conn.close()

        # Post new employee with file_photo upload
        resp = self.client.post('/module/hr_employees/new', data={
            'emp_no': 'TEST-UPL-01',
            'full_name': 'سلطان بن عبدالعزيز',
            'administration': 'الإدارة العامة',
            'department': 'المكتب التنفيذي',
            'status': 'على رأس العمل',
            'phone': '0599999999',
            'basic_salary': '12000',
            'housing_allowance': '3000',
            'file_photo': (io.BytesIO(tiny_jpeg), 'photo.jpg', 'image/jpeg'),
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        conn = db.connect()
        row = conn.execute("SELECT id, photo FROM hr_employees WHERE emp_no='TEST-UPL-01'").fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(bool(row['photo']), "Photo field should be populated with uploaded reference")
        self.assertIn("/media/local/photos/employees/", row['photo'])
        emp_id = row['id']

        # Edit employee and clear photo
        resp_clear = self.client.post(f'/module/hr_employees/{emp_id}/edit', data={
            'emp_no': 'TEST-UPL-01',
            'full_name': 'سلطان بن عبدالعزيز',
            'administration': 'الإدارة العامة',
            'department': 'المكتب التنفيذي',
            'status': 'على رأس العمل',
            'phone': '0599999999',
            'basic_salary': '12000',
            'housing_allowance': '3000',
            'photo': row['photo'],
            'clear_photo': '1',
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp_clear.status_code, 200)

        row_cleared = conn.execute("SELECT photo FROM hr_employees WHERE emp_no='TEST-UPL-01'").fetchone()
        self.assertEqual(row_cleared['photo'], "")

        # Clean up
        conn.execute("DELETE FROM hr_employees WHERE emp_no='TEST-UPL-01'")
        conn.commit()
        conn.close()

if __name__ == '__main__':
    unittest.main()


