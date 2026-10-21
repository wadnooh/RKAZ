import datetime
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from odoo_claim_accounting import entry_code

class Env(dict):
    context = {}

class EntryTests(unittest.TestCase):
    def fixture(self):
        company = NS(id=1, currency_id=NS(round=lambda a: round(a, 2)))
        debit = NS(id=4, company_ids=NS(ids=[1]), account_type='income')
        credit = NS(id=5, company_ids=NS(ids=[1]), account_type='income')
        rec = Mock(id=7, x_company_id=company, x_name='CL1', x_amount=100.12,
                   x_entry_request=True, x_entry_move=False, x_entry_partner=NS(id=8),
                   x_entry_journal=NS(id=3,company_id=company,type='general'),
                   x_entry_debit=debit,x_entry_credit=credit,
                   x_task_id=NS(company_id=company,x_rekaz_claim_no='CL1',x_rekaz_claim_amount=100.12))
        moves = Mock(); moves.search.return_value=[]
        moves.with_company.return_value.create.return_value=NS(id=9)
        return rec,moves,Env({'account.move':moves})

    def run_code(self,rec,env):
        exec(entry_code(1,'company_ids'),dict(records=[rec],env=env,UserError=ValueError,datetime=datetime))

    def test_creates_balanced_draft_without_posting(self):
        rec,moves,env=self.fixture(); self.run_code(rec,env)
        values=moves.with_company.return_value.create.call_args.args[0]
        self.assertEqual(values['move_type'],'entry')
        self.assertEqual(sum(line[2]['debit']-line[2]['credit'] for line in values['line_ids']),0)
        self.assertNotIn('state',values)
        self.assertEqual(values['x_rekaz_claim_key'],'rekaz-claim:1:7')
        moves.action_post.assert_not_called()

    def test_cross_company_account_is_refused(self):
        rec,moves,env=self.fixture(); rec.x_entry_debit.company_ids.ids=[2]
        with self.assertRaises(ValueError): self.run_code(rec,env)
        moves.with_company.assert_not_called()

    def test_existing_entry_is_not_duplicated(self):
        rec,moves,env=self.fixture(); moves.search.return_value=[NS(id=9)]
        with self.assertRaises(ValueError): self.run_code(rec,env)
        moves.with_company.assert_not_called()

    def test_zero_amount_is_refused(self):
        rec,moves,env=self.fixture(); rec.x_amount=0
        with self.assertRaises(ValueError): self.run_code(rec,env)
        moves.with_company.assert_not_called()
