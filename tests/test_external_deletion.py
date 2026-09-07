import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sqlite3
import unittest
from webapp.db import delete_external_record


class ExternalDeletionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE external_purchases(id INTEGER PRIMARY KEY, purchase_no TEXT, received_voucher_no TEXT);
            CREATE TABLE external_purchase_lines(id INTEGER PRIMARY KEY, purchase_id INTEGER, warehouse_tx_id INTEGER);
            CREATE TABLE custody(id INTEGER PRIMARY KEY, custody_no TEXT, issued_voucher_no TEXT, return_voucher_no TEXT, warehouse_tx_id INTEGER, return_warehouse_tx_id INTEGER);
            CREATE TABLE custody_lines(id INTEGER PRIMARY KEY, custody_id INTEGER, warehouse_tx_id INTEGER, return_warehouse_tx_id INTEGER);
            INSERT INTO external_purchases VALUES(1, 'P1', NULL), (2, 'P2', NULL);
            INSERT INTO external_purchase_lines VALUES(1, 1, NULL), (2, 2, NULL);
            INSERT INTO custody VALUES(1, 'C1', NULL, NULL, NULL, NULL);
            INSERT INTO custody_lines VALUES(1, 1, NULL, NULL);
        """)

    def tearDown(self):
        self.conn.close()

    def test_purchase_removes_own_lines_only(self):
        self.assertEqual(delete_external_record('external_purchases', 1, self.conn), 'P1')
        self.conn.commit()
        self.assertEqual(self.conn.execute('SELECT purchase_id FROM external_purchase_lines').fetchall()[0][0], 2)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM external_purchases').fetchone()[0], 1)

    def test_custody_removes_lines(self):
        delete_external_record('custody', 1, self.conn)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM custody_lines').fetchone()[0], 0)

    def test_posted_parent_or_line_blocks_deletion(self):
        for table, column in [('external_purchases', 'received_voucher_no'), ('external_purchase_lines', 'warehouse_tx_id'), ('custody', 'issued_voucher_no'), ('custody', 'return_warehouse_tx_id'), ('custody_lines', 'warehouse_tx_id'), ('custody_lines', 'return_warehouse_tx_id')]:
            with self.subTest(table=table, column=column):
                self.conn.execute(f'UPDATE {table} SET {column}=? WHERE id=1', ('123',))
                name = 'custody' if table.startswith('custody') else 'external_purchases'
                with self.assertRaises(ValueError):
                    delete_external_record(name, 1, self.conn)
                self.assertEqual(self.conn.execute(f'SELECT count(*) FROM {name} WHERE id=1').fetchone()[0], 1)
                self.conn.rollback()

    def test_missing_record_rejected(self):
        with self.assertRaises(ValueError):
            delete_external_record('custody', 999, self.conn)

    def test_rollback_restores_parent_and_lines(self):
        delete_external_record('custody', 1, self.conn)
        self.conn.rollback()
        for table in ['custody', 'custody_lines']:
            self.assertEqual(self.conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 1)


class DeleteRoutePermissionTests(unittest.TestCase):
    def test_line_delete_requires_each_delete_permission(self):
        tree = ast.parse(Path('webapp/app.py').read_text(encoding='utf-8'))
        for endpoint, module in [('purchase_line_delete', 'external_purchases'), ('custody_line_delete', 'custody')]:
            function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == endpoint)
            function.decorator_list = []
            for denied in ['section.external', 'modules.delete', f'button.module.{module}.delete']:
                with self.subTest(endpoint=endpoint, denied=denied):
                    database = Mock()
                    namespace = {'permissions': SimpleNamespace(can=lambda key: key != denied, deny_redirect=lambda: 'denied'), 'db': database}
                    exec(compile(ast.Module(body=[function], type_ignores=[]), '<route>', 'exec'), namespace)
                    self.assertEqual(namespace[endpoint](1), 'denied')
                    database.connect.assert_not_called()

    def test_changed_templates_parse(self):
        from jinja2 import Environment
        env = Environment()
        for name in ['base.html', 'module_form.html', 'module_list.html', '_purchase_lines.html', '_custody_warehouse.html']:
            env.parse((Path('webapp/templates') / name).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
