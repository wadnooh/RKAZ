import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from odoo_native_claims import claim_code, claim_page_arch, install_access

class ClaimTests(unittest.TestCase):
    def test_boq_amount_is_derived_but_number_alone_never_sends_to_finance(self):
        class Env:
            context = {}
            def __getitem__(self, key): return claim
        claim = Mock()
        claim.search.return_value = []
        rec = Mock(company_id=SimpleNamespace(id=1,currency_id=SimpleNamespace(round=lambda x:round(x,2))),
                   project_id=SimpleNamespace(id=5),stage_id=SimpleNamespace(id=2),
                   x_rekaz_claim_no='CL-9',x_rekaz_claim_amount=999,x_rekaz_boq_total=1450,
                   x_rekaz_claim_submit=False)
        code=claim_code(1,[],[5,6])
        exec(code,{'records':[rec],'env':Env(),'UserError':ValueError})
        rec.write.assert_called_once_with({'x_rekaz_claim_amount':1450})
        claim.create.assert_not_called()
        rec.reset_mock()
        rec.x_rekaz_claim_no=''
        exec(code,{'records':[rec],'env':Env(),'UserError':ValueError})
        rec.write.assert_called_once_with({'x_rekaz_claim_amount':0})
        claim.create.assert_not_called()

    def test_boq_sync_has_no_context_validation_bypass_and_writes_after_creation(self):
        code=claim_code(1,[],[5,6])
        self.assertNotIn('env.context',code)
        self.assertGreater(code.index('rec.write('),code.index('claims.create('))

    def test_boq_submission_creates_one_claim_with_derived_amount_even_on_nested_write(self):
        class Lines(list):
            def filtered(self, fn): return Lines(x for x in self if fn(x))
        class Existing(SimpleNamespace):
            def __len__(self): return 1
        claim=Mock()
        saved=[]
        claim.search.side_effect=lambda *a,**k: saved[0] if saved else []
        def create(values):
            saved.append(Existing(x_name=values['x_name'],x_amount=values['x_amount']))
        claim.create.side_effect=create
        class Env:
            def __getitem__(self,key): return claim
        contract=object()
        rec=SimpleNamespace(id=7,company_id=SimpleNamespace(id=1,currency_id=SimpleNamespace(round=lambda x:round(x,2))),
                            project_id=SimpleNamespace(id=5),stage_id=SimpleNamespace(id=2),
                            x_rekaz_claim_no='CL-9',x_rekaz_claim_amount=999,x_rekaz_boq_total=1450,
                            x_rekaz_claim_submit=True,x_rekaz_boq_class='طوارئ',x_rekaz_boq_contract=contract,
                            x_rekaz_boq_lines=Lines([SimpleNamespace(x_kind='تنفيذ',x_item_id=SimpleNamespace(x_billable=True),x_qty=10,x_contract_id=contract)]))
        code=claim_code(1,[],[5,6])
        scope={'records':[rec],'env':Env(),'UserError':ValueError}
        def write(values):
            for key,value in values.items(): setattr(rec,key,value)
            exec(code,scope)
        rec.write=write
        exec(code,scope)
        self.assertEqual(rec.x_rekaz_claim_amount,1450)
        claim.create.assert_called_once()
        self.assertEqual(saved[0].x_amount,1450)
        rec.x_rekaz_claim_no=''
        with self.assertRaises(ValueError): exec(code,scope)

    def test_claim_page_can_be_conditionally_hidden_until_preliminary_clearance(self):
        visible_arch = claim_page_arch(details_visibility=True)
        self.assertIn('invisible="not x_rekaz_boq_details_visible"', visible_arch)
        self.assertNotIn('invisible=', claim_page_arch())

    def test_modern_access_preserves_company_restriction_and_forbids_deletion(self):
        api = Mock()
        api.find.side_effect = [[{'id': 1}], [], [], []]
        install_access(api, 50, {'project': 7, 'finance': 8})
        values = [c.args[2][0] for c in api.call.call_args_list]
        self.assertEqual([v['operation'] for v in values], ['cr', 'cru', 'crud'])
        self.assertIs(values[-1]['group_id'], False)
        self.assertTrue(all('company_ids' in v['domain'] for v in values))
        self.assertTrue(all(c.args[0] == 'ir.access' for c in api.call.call_args_list))

    def test_legacy_access_uses_legacy_models(self):
        api = Mock()
        api.find.return_value = []
        install_access(api, 50, {'project': 7, 'finance': 8})
        self.assertEqual([c.args[0] for c in api.ensure.call_args_list],
                         ['ir.model.access', 'ir.model.access', 'ir.rule'])

    def run_claim(self, number, amount, submitted=True, existing=None, stage=2):
        claim = Mock()
        claim.search.return_value = existing or []
        rec = SimpleNamespace(company_id=SimpleNamespace(id=1), id=7,
                              x_rekaz_claim_no=number, x_rekaz_claim_amount=amount,
                              x_rekaz_claim_submit=submitted, stage_id=SimpleNamespace(id=stage),
                              project_id=SimpleNamespace(id=3))
        exec(claim_code(1, [9]), {'records': [rec], 'env': {'x_rekaz_project_claim': claim}, 'UserError': ValueError})
        return claim

    def test_number_and_positive_finite_amount_required(self):
        for number, amount in [('',10),('A',0),('A',-1),('A',float('nan')),('A',float('inf'))]:
            with self.subTest(number=number,amount=amount), self.assertRaises(ValueError):
                self.run_claim(number,amount)

    def test_finance_record_links_original_task(self):
        claim=self.run_claim('CL-1',10)
        self.assertEqual(claim.create.call_args.args[0]['x_task_id'],7)
        self.assertEqual(claim.create.call_args.args[0]['x_amount'],10)

    def test_stage_transfer_cannot_bypass_validation(self):
        with self.assertRaises(ValueError): self.run_claim('',0,submitted=False,stage=9)

    def test_draft_without_submission_creates_nothing(self):
        self.run_claim('',0,submitted=False).create.assert_not_called()

    def test_repeated_submission_does_not_duplicate(self):
        class Existing:
            x_name='CL-1'
            x_amount=10
            def __len__(self): return 1
        self.run_claim('CL-1',10,existing=Existing()).create.assert_not_called()
        with self.assertRaises(ValueError): self.run_claim('CL-2',10,existing=Existing())
