import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader
from webapp import fixed_assets as assets


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.db'
        self.conn = self.connect()
        self.conn.executescript("CREATE TABLE workshop_cars(id INTEGER PRIMARY KEY,plate_no TEXT); CREATE TABLE workshop_equipment(id INTEGER PRIMARY KEY,equip_no TEXT,equip_name TEXT); INSERT INTO workshop_cars VALUES(1,'CAR-1');")
        assets.ensure_schema(self.conn)
        self.conn.commit()
        self.app = Flask(__name__)
        self.app.secret_key = 'test-only'
        self.app.jinja_loader = ChoiceLoader([DictLoader({'base.html': '{% block content %}{% endblock %}'}), FileSystemLoader('webapp/templates')])
        self.app.jinja_env.filters['money'] = str
        self.app.jinja_env.globals.update(can=lambda *args: True, csrf_token=lambda: 'test')
        self.app.add_url_rule('/module/<name>', 'module_list', lambda name: '')
        self.app.add_url_rule('/module/<name>/<int:row_id>', 'module_edit', lambda name,row_id: '')
        assets.register(self.app, lambda fn: fn)
        self.client = self.app.test_client()
        self.patches = [patch.object(assets.db, 'connect', self.connect), patch.object(assets.permissions, 'can', return_value=True), patch.object(assets.helpers, 'current_user_name', return_value='Reviewer'), patch.object(assets.helpers, 'after_data_change'), patch.object(assets.db, 'log_audit')]
        for item in self.patches: item.start()
        self.data = dict(asset_no='A1',name='Vehicle', acquired_on='2025-01-01',cost='10000',residual='1000',annual_rate='20',next_review='2027-01-01',notes='',link='car:1')

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        return conn

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.conn.close()
        self.temp.cleanup()

    def test_depreciation_and_floor(self):
        value = assets.depreciation(self.data,date(2026,1,1))
        self.assertEqual(value['accumulated'], Decimal('1800.00'))
        self.assertEqual(value['net'], Decimal('8200.00'))
        self.assertEqual(assets.depreciation(self.data,date(2040,1,1))['net'],Decimal('1000.00'))
        self.assertEqual(assets.depreciation(self.data,date(2024,1,1))['accumulated'],Decimal('0.00'))

    def test_invalid_values(self):
        for key,value in [('cost','NaN'),('cost','Infinity'),('annual_rate','101'),('residual','10001'),('cost','-1'),('acquired_on','invalid')]:
            with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                assets.validate_asset(dict(self.data,**{key:value}))

    def test_create_render_review_and_duplicate_link(self):
        self.assertEqual(self.client.post('/fixed-assets/new',data=self.data).status_code,302)
        self.assertEqual(self.client.get('/fixed-assets').status_code,200)
        self.assertEqual(self.client.get('/fixed-assets/1').status_code,200)
        self.client.post('/fixed-assets/new',data=dict(self.data,asset_no='A2'))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fixed_assets').fetchone()[0],1)
        result = self.client.post('/fixed-assets/1/review',data=dict(reviewed_on='2026-01-01',next_review='2027-02-01',condition='جيد',notes='Checked'))
        self.assertEqual(result.status_code,302)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fixed_asset_reviews').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT next_review FROM fixed_assets').fetchone()[0],'2027-02-01')
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute('DELETE FROM workshop_cars WHERE id=1')
        self.conn.rollback()

    def test_permission_and_missing_record(self):
        with patch.object(assets.permissions,'can',return_value=False):
            self.assertEqual(self.client.get('/fixed-assets').status_code,403)
            self.assertEqual(self.client.post('/fixed-assets/new',data=self.data).status_code,403)
            self.assertEqual(self.client.post('/fixed-assets/1/review').status_code,403)
        self.assertEqual(self.client.get('/fixed-assets/999').status_code,404)

    def test_search_filters_and_totals(self):
        self.client.post('/fixed-assets/new', data=self.data)
        self.client.post('/fixed-assets/new', data=dict(self.data, asset_no='B2', name='Independent', link='', cost='200', residual='0', next_review=''))
        from flask import template_rendered
        captured = []
        def capture(sender, template, context, **extra): captured.append(context)
        template_rendered.connect(capture, self.app)
        try:
            for query, expected in [({'q':'CAR-1'}, ['A1']), ({'q':'Independent'}, ['B2']), ({'kind':'independent'}, ['B2']), ({'review':'unscheduled'}, ['B2']), ({'q':'CAR-1','kind':'independent'}, []), ({'q':"% OR 1=1 --"}, [])]:
                response = self.client.get('/fixed-assets', query_string=query)
                self.assertEqual(response.status_code, 200)
                context = captured[-1]
                self.assertEqual([r['asset_no'] for r in context['rows']], expected)
                self.assertEqual(context['totals']['cost'], sum((Decimal(r['cost']) for r in context['rows']), Decimal(0)))
        finally:
            template_rendered.disconnect(capture, self.app)

    def test_missing_link_rejected(self):
        self.client.post('/fixed-assets/new',data=dict(self.data,link='car:999'))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fixed_assets').fetchone()[0],0)


if __name__ == '__main__': unittest.main()
