import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import odoo_finance_link as link


class InvoiceSet(list):
    def write(self, values):
        self[0].update(values)


class Invoices:
    def __init__(self): self.rows = []
    def search(self, domain, limit=2):
        return InvoiceSet([r for r in self.rows if all(r[k] == v for k, op, v in domain)][:limit])
    def create(self, values): self.rows.append(dict(values))


class Record(dict):
    x_company_id = SimpleNamespace(id=1)
    id = 7


class AutomationTests(unittest.TestCase):
    def test_repeat_updates_one_financial_record_and_preserves_collection(self):
        f = link.sync.m.field_name
        record = Record({f('extract_no'): 'EXT1', f('amount'): 100, f('order_date'): '2026-10-13', f('work_order'): 'WO1'})
        invoices = Invoices()
        context = {'records': [record], 'env': {'x_rekaz_sync_invoices': invoices}, 'UserError': ValueError}
        code = link.action_code('primary_team_orders', 1)
        exec(code, context)
        invoices.rows[0][f('collected')] = 25
        record[f('amount')] = 150
        exec(code, context)
        self.assertEqual(len(invoices.rows), 1)
        self.assertEqual(invoices.rows[0][f('value')], 150)
        self.assertEqual(invoices.rows[0][f('collected')], 25)
        record.x_company_id = SimpleNamespace(id=2)
        record[f('amount')] = 200
        exec(code, context)
        self.assertEqual(invoices.rows[0][f('value')], 150)
