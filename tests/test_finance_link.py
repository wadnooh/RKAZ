import sqlite3
import unittest
from webapp import db


class FinanceLinkTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
            CREATE TABLE tickets(id INTEGER PRIMARY KEY, ticket_no TEXT, invoice_id TEXT,
                invoice_no TEXT, work_order TEXT, items_value REAL, invoice_status TEXT);
            CREATE TABLE invoices(id INTEGER PRIMARY KEY, invoice_id TEXT, invoice_no TEXT,
                ticket_no TEXT, work_order TEXT, value REAL, status TEXT, sap_status TEXT,
                support_date TEXT, invoice_date TEXT, period TEXT, notes TEXT);
            CREATE TABLE ticket_boq_lines(ticket_no TEXT, final_total REAL, line_total REAL);
            INSERT INTO tickets VALUES(1,'T1','EXT-1','','WO1',100,'');
            INSERT INTO tickets VALUES(2,'T2','EXT-1','','WO2',200,'');
        ''')

    def tearDown(self):
        self.conn.close()

    def test_shared_extract_preserves_each_transaction_and_total(self):
        db.sync_ticket_to_invoices('T1', conn=self.conn)
        db.sync_ticket_to_invoices('T2', conn=self.conn)
        db.sync_ticket_to_invoices('T1', conn=self.conn)
        self.assertEqual(self.conn.execute('SELECT COUNT(*),SUM(value) FROM invoices').fetchone()[:], (2, 300))

    def test_primary_work_orders_share_extract_without_replacing_values(self):
        for order, amount in [('P1', 50), ('P2', 70), ('P1', 60)]:
            db.sync_primary_team_order_to_invoices({'work_order': order, 'extract_no': 'EXT', 'amount': amount}, conn=self.conn)
        self.assertEqual(self.conn.execute('SELECT COUNT(*),SUM(value) FROM invoices').fetchone()[:], (2, 130))

    def test_without_extract_not_transferred_and_boq_value_is_used(self):
        self.conn.execute("UPDATE tickets SET invoice_id='' WHERE ticket_no='T1'")
        self.assertFalse(db.sync_ticket_to_invoices('T1', conn=self.conn)['synced'])
        self.conn.execute("INSERT INTO ticket_boq_lines VALUES('T2',450,500)")
        db.sync_ticket_to_invoices('T2', conn=self.conn)
        self.assertEqual(self.conn.execute('SELECT value FROM invoices').fetchone()[0], 450)
