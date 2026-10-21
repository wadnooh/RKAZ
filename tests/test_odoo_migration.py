import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('migration', Path(__file__).resolve().parents[1] / 'tools/rekaz_to_odoo18.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class FakeRPC:
    company_id = 7
    def __init__(self):
        self.rows = {}

    def find(self, model, domain, fields=None):
        return [dict(row) for row in self.rows.get(model, []) if all(row.get(key) == value for key, op, value in domain)]

    def call(self, model, method, args=None, **kwargs):
        rows = self.rows.setdefault(model, [])
        if method == 'create':
            row = dict(args[0], id=len(rows)+1)
            rows.append(row)
            return row['id']
        if method == 'read':
            return [dict(r) for r in rows if r['id'] in args[0]]
        if method == 'write':
            for r in rows:
                if r['id'] in args[0]:
                    r.update(args[1])
            return True
        raise AssertionError(method)


class OdooMigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
          CREATE TABLE tickets(id INTEGER PRIMARY KEY,ticket_no TEXT);
          CREATE TABLE construction_works(id INTEGER PRIMARY KEY,work_no TEXT);
          CREATE TABLE warehouse_tx(id INTEGER PRIMARY KEY,ticket_no TEXT);
          CREATE TABLE followups(id INTEGER PRIMARY KEY,section TEXT);
          CREATE TABLE users(id INTEGER PRIMARY KEY,password TEXT);
          INSERT INTO tickets VALUES(1,'T1'),(2,'duplicated'),(3,'duplicated');
          INSERT INTO warehouse_tx VALUES(1,'T1'),(2,'duplicated');
          INSERT INTO followups VALUES(1,'hr'),(2,'ops');
        ''')
        self.api = FakeRPC()
        self.args = SimpleNamespace(source_key='test', uploads=None, max_file_mb=25)

    def tearDown(self):
        self.conn.close()

    def report(self):
        return dict(tables={}, unresolved_links=[], missing_attachments=[], attachments_created=0)

    def test_catalog_excludes_users_and_filters_hr_followups(self):
        catalog = m.catalog(self.conn)
        self.assertNotIn('users', catalog)
        rows = list(m.source_rows(self.conn, 'followups', catalog['followups']))
        self.assertEqual([r['id'] for r in rows], [2])

    def test_repeated_import_preserves_edits_and_flags_changed_source(self):
        catalog = m.catalog(self.conn)
        first = self.report()
        m.transfer(self.api, self.conn, catalog, self.args, first)
        self.assertEqual(first['tables']['tickets']['created'], 3)
        self.api.rows[m.model_name('tickets')][0]['x_name'] = 'Odoo edit'
        self.conn.execute("UPDATE tickets SET ticket_no='new source' WHERE id=1")
        second = self.report()
        m.transfer(self.api, self.conn, catalog, self.args, second)
        self.assertEqual(second['tables']['tickets']['created'], 0)
        self.assertEqual(second['tables']['tickets']['source_changed'], 1)
        self.assertEqual(self.api.rows[m.model_name('tickets')][0]['x_name'], 'Odoo edit')
        self.assertEqual(len(self.api.rows[m.model_name('tickets')]), 3)

    def test_duplicate_natural_reference_not_arbitrarily_linked(self):
        report = self.report()
        m.transfer(self.api, self.conn, m.catalog(self.conn), self.args, report)
        rows = self.api.rows[m.model_name('warehouse_tx')]
        self.assertEqual(rows[0]['x_rel_ticket_no'], 1)
        self.assertNotIn('x_rel_ticket_no', rows[1])
        self.assertEqual(report['unresolved_links'][0]['reason'], 'ambiguous')

    def test_upload_paths_and_s3_are_safe(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'photos').mkdir()
            (root/'photos/file.pdf').write_bytes(b'example')
            self.assertEqual(m.local_media(root, '/media/local/photos/file.pdf'), root/'photos/file.pdf')
            self.assertIsNone(m.local_media(root, '/media/s3/bucket/file.pdf'))
            self.assertIsNone(m.local_media(root, 'https://example.com/file.pdf'))
            with self.assertRaises(ValueError):
                m.local_media(root, '/media/local/%2e%2e/secret')
            self.assertEqual(m.media_refs({'before_shot': 'نعم'}), [])

    def test_readonly_snapshot_does_not_change_source(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'source.db'
            c = sqlite3.connect(path)
            c.execute('CREATE TABLE test(id INTEGER)')
            c.commit(); c.close()
            before = path.read_bytes()
            with m.snapshot(path) as copied:
                copied.execute('INSERT INTO test VALUES(1)')
            self.assertEqual(path.read_bytes(), before)

    def test_large_integer_exact_and_no_nan(self):
        c = {'name':'reference','type':'INTEGER'}
        self.assertEqual(m.odoo_value(9999999999999,c),'9999999999999')
        with self.assertRaises(ValueError):
            m.pack(float('inf'))


if __name__ == '__main__':
    unittest.main()
