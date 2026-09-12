import io
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from flask import Flask, template_rendered
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader
from webapp import payroll


class PayrollTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'payroll.db'
        conn=self.connect();conn.execute('CREATE TABLE hr_employees(id INTEGER PRIMARY KEY,emp_no TEXT,full_name TEXT,job_title TEXT,basic_salary REAL,housing_allowance REAL)')
        conn.execute("INSERT INTO hr_employees VALUES (1,'E1','Example','Engineer',22000,1000)")
        payroll.ensure_schema(conn);conn.commit();conn.close()
        self.app=Flask(__name__);self.app.secret_key='test'
        self.app.jinja_loader=ChoiceLoader([DictLoader({'base.html':'{% block content %}{% endblock %}','_hr_subnav.html':''}),FileSystemLoader('webapp/templates')])
        self.app.jinja_env.globals.update(can=lambda *args:True,csrf_token=lambda:'test');self.app.jinja_env.filters['money']=str
        payroll.register(self.app,lambda f:f)
        self.patches=[patch.object(payroll.db,'connect',self.connect),patch.object(payroll.permissions,'can',return_value=True),patch.object(payroll.db,'log_audit'),patch.object(payroll.helpers,'after_data_change')]
        for p in self.patches:p.start()
        self.client=self.app.test_client()
        self.client.post('/hr/payroll',data=dict(year=2026,month=6,company='Company'))
        self.period=dict(base_days='30',daily_hours='8',overtime_factor='1.5')
        self.data=dict(company='Company',emp_no='E1',name='Example',job='Engineer',payment='داخل البنك',source_net='',employee_id='1',period_version='1',**{k:'0' for k,_ in payroll.INPUTS})
        self.data.update(basic='22000',days='8')

    def connect(self):
        c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def rows(self):
        c=self.connect()
        try:return [dict(r) for r in c.execute('SELECT * FROM payroll_rows')]
        finally:c.close()

    def test_formula_matches_workbook_partial_month(self):
        d=payroll.validate(self.data,self.period);r=payroll.calculate(d,self.period)
        self.assertEqual(r['net'],Decimal('5866.67'));self.assertIsNone(r['difference'])
        d['source_net']='0';self.assertEqual(payroll.calculate(d,self.period)['difference'],Decimal('5866.67'))
        d.update(basic='3000',housing='300',transport='200',phone='100',nature='0',days='30',overtime_hours='8',bonus='100',deduction='20',advance='50',insurance='30',other_deductions='10')
        r=payroll.calculate(d,self.period)
        self.assertEqual(r['fixed'],Decimal('3600'));self.assertEqual(r['overtime'],Decimal('180'));self.assertEqual(r['net'],Decimal('3770'))

    def test_nonfinite_negative_and_excess_days(self):
        for changes in [dict(basic='NaN'),dict(basic='Infinity'),dict(days='31'),dict(advance='-1'),dict(basic=''),dict(payment='bad')]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):payroll.validate(dict(self.data,**changes),self.period)

    def test_create_preview_duplicate_and_immutable_employee(self):
        r=self.client.post('/hr/payroll/1/preview',data=self.data);self.assertEqual(r.json['net'],'5866.67');self.assertEqual(self.rows(),[])
        self.assertEqual(self.client.post('/hr/payroll/1/row/new',data=self.data).status_code,302)
        self.client.post('/hr/payroll/1/row/new',data=self.data);self.assertEqual(len(self.rows()),1)
        c=self.connect();self.assertEqual(c.execute('SELECT basic_salary FROM hr_employees WHERE id=1').fetchone()[0],22000)
        with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM hr_employees WHERE id=1')
        c.rollback();c.close()
        for path in ['/hr/payroll','/hr/payroll/1','/hr/payroll/1/row/1','/hr/payroll/1/row/new?employee_id=1']:
            self.assertEqual(self.client.get(path).status_code,200,path)

    def test_cross_period_and_stale_edit(self):
        self.client.post('/hr/payroll/1/row/new',data=self.data)
        self.client.post('/hr/payroll',data=dict(year=2026,month=7,company='Company'))
        self.assertEqual(self.client.post('/hr/payroll/2/row/1',data=self.data).status_code,404)
        self.client.post('/hr/payroll/1/row/1',data=dict(self.data,version='0',basic='9999'))
        self.assertEqual(json.loads(self.rows()[0]['payload'])['basic'],'22000')
        self.client.post('/hr/payroll/1/settings',data=dict(version='1',base_days='30',daily_hours='8',overtime_factor='2'))
        self.client.post('/hr/payroll/1/row/1',data=dict(self.data,version='1',basic='9999'))
        self.assertEqual(json.loads(self.rows()[0]['payload'])['basic'],'22000')

    def test_settings_validation_and_filter_totals(self):
        self.client.post('/hr/payroll/1/row/new',data=self.data)
        self.client.post('/hr/payroll/1/settings',data=dict(version='1',base_days='7',daily_hours='8',overtime_factor='2'))
        c=self.connect();self.assertEqual(c.execute('SELECT base_days FROM payroll_periods').fetchone()[0],'30');c.close()
        captured=[]
        def capture(sender,template,context,**extra):captured.append(context)
        template_rendered.connect(capture,self.app)
        try:
            self.client.get('/hr/payroll/1?payment=داخل البنك');self.assertEqual(captured[-1]['totals']['net'],Decimal('5866.67'))
            self.client.get('/hr/payroll/1?q=unmatched');self.assertEqual(captured[-1]['totals']['net'],0)
        finally:template_rendered.disconnect(capture,self.app)

    def test_permissions(self):
        with patch.object(payroll.permissions,'can',return_value=False):
            for path in ['/hr/payroll','/hr/payroll/1','/hr/payroll/1/row/new','/hr/payroll/1?export=xlsx']:
                self.assertEqual(self.client.get(path).status_code,403)
            self.assertEqual(self.client.post('/hr/payroll/1/preview',data=self.data).status_code,403)
        self.assertEqual(self.client.get('/hr/payroll/999').status_code,404)

if __name__=='__main__':unittest.main()
