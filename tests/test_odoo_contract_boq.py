import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import odoo_contract_boq as boq

class Env:
    context={}

class BoqTests(unittest.TestCase):
    def line(self,work_class='اعتيادي',ratio=0.45):
        company=NS(id=1,currency_id=NS(round=lambda x:round(x,2)))
        contract=NS(id=4)
        return Mock(x_origin=False,x_company_id=company,x_contract_id=contract,
                    x_task_id=NS(company_id=company,project_id=NS(id=10),x_rekaz_boq_class=work_class,x_rekaz_claim_no=False),x_qty=10,x_ratio=ratio,
                    x_work_class=work_class,x_kind='تنفيذ',
                    x_item_id=NS(x_company_id=company,x_contract_id=contract,x_billable=True,x_price=100,x_unit='M',x_name='item'))

    def run_code(self,rec,project_ids=None):
        exec(boq.guard_code(1,project_ids or [10]),{'records':[rec],'env':Env(),'UserError':ValueError})

    def test_standard_and_emergency_price_once_from_base(self):
        for classification,total in [('اعتيادي',1000),('طوارئ',1450)]:
            rec=self.line(classification);self.run_code(rec)
            values=rec.with_context.return_value.write.call_args.args[0]
            self.assertEqual(values['x_total'],total)
            self.assertEqual(values['x_price'],100)
            self.assertEqual(values['x_ratio'],0.45 if classification=='طوارئ' else 0.0)

    def test_heading_and_cross_company_and_zero_quantity_rejected(self):
        for invalid in ['heading','company','zero']:
            rec=self.line()
            if invalid=='heading':rec.x_item_id.x_billable=False
            if invalid=='company':rec.x_item_id.x_company_id=NS(id=2)
            if invalid=='zero':rec.x_qty=0
            with self.assertRaises(ValueError):self.run_code(rec)

    def test_import_preserves_historical_amounts(self):
        rec=self.line();rec.x_origin='ticket_boq_lines:1'
        self.run_code(rec)
        rec.with_context.assert_not_called()

    def test_differing_existing_source_is_reviewed_without_overwrite(self):
        api=Mock(company_id=1)
        api.find.return_value=[{'id':5,'x_name':'manual edit','x_company_id':[1,'company'],'x_origin':'source:1'}]
        report={'created':0,'existing':0,'review':[]}
        self.assertIsNone(boq.save(api,boq.ITEM,'source:1',{'x_name':'source'},report))
        api.call.assert_not_called()
        self.assertEqual(len(report['review']),1)

    def test_emergency_ignores_manual_ratio_to_prevent_wrong_uplift(self):
        rec=self.line('طوارئ',ratio=0.99)
        self.run_code(rec)
        self.assertEqual(rec.with_context.return_value.write.call_args.args[0]['x_total'],1450)

    def test_other_projects_are_not_subject_to_boq_validation(self):
        rec=self.line()
        rec.x_task_id.project_id.id=999
        rec.x_item_id.x_billable=False
        self.run_code(rec,project_ids=[10,20])
        rec.with_context.assert_not_called()

    def test_workflow_automation_domain_is_limited_to_selected_projects(self):
        self.assertEqual(
            boq.project_scope_domain(1,[10,20]),
            repr([('company_id','=',1),('project_id','in',[10,20])]),
        )

    def test_report_action_uses_odoo20_fields(self):
        values = boq.report_action_values('rekaz_boq.transaction_archive')
        self.assertEqual(values['type'], 'ir.actions.report')
        self.assertEqual(values['report_type'], 'qweb-pdf')
        self.assertFalse(values['binding_model_id'])
        self.assertEqual(values['binding_type'], 'report')
        self.assertNotIn('report_file', values)

    def test_details_visibility_tracks_preliminary_clearance_and_finance_stages(self):
        code = boq.details_visibility_compute()
        class Record:
            def __init__(self, stage):
                self.stage_id = NS(name=stage)

            def __setitem__(self, key, value):
                setattr(self, key, value)

        visible_stages = [
            'الإخلاء المبدئي',
            'المستخلص',
            'الإخلاء النهائي',
        ]
        for stage, expected in [('استلام', False)] + [(name, True) for name in visible_stages]:
            record = Record(stage)
            exec(code, {'self': [record]})
            self.assertEqual(record.x_rekaz_boq_details_visible, expected)
        self.assertEqual(boq.DETAILS_VISIBLE_STAGES, tuple(visible_stages))

    def test_task_boq_view_arch_is_well_formed_with_multiple_xpaths(self):
        root = ET.fromstring(boq.task_boq_view_arch())
        self.assertEqual(root.tag, 'data')
        self.assertEqual([child.tag for child in root], ['xpath', 'xpath'])
        self.assertIn(boq.DETAILS_VISIBLE_FIELD, boq.task_boq_view_arch())

    def test_transaction_search_arch_searches_name_and_all_transaction_numbers(self):
        root = ET.fromstring(boq.task_transaction_search_arch())
        field = root.find('field')
        self.assertEqual(root.tag, 'xpath')
        self.assertEqual(field.attrib['name'], 'x_rekaz_fault_no')
        self.assertEqual(field.attrib['string'], 'بحث المعاملة')
        domain = field.attrib['filter_domain']
        for name in ('name', 'x_rekaz_fault_no', 'x_rekaz_notification_no', 'x_rekaz_work_order'):
            self.assertIn("('%s','ilike',self)" % name, domain)

    def test_transaction_search_installs_company_scoped_action_and_project_menu(self):
        api = Mock(company_id=1)
        api.find.side_effect = [
            [{'res_id': 11}],
            [{'res_id': 13}],
        ]
        calls = []

        def upsert(_api, model, domain, values):
            calls.append((model, domain, values))
            return {'ir.ui.view': 21, 'ir.actions.act_window': 22, 'ir.ui.menu': 23}[model]

        with unittest.mock.patch.object(boq.claims, 'upsert', side_effect=upsert):
            boq.install_task_transaction_search(api, 30)

        view = calls[0][2]
        action = calls[1][2]
        menu = calls[2][2]
        self.assertEqual(view['inherit_id'], 11)
        self.assertEqual(action['search_view_id'], 21)
        self.assertEqual(action['domain'], "[('company_id','in',allowed_company_ids)]")
        self.assertEqual(menu['parent_id'], 13)
        self.assertNotIn('groups_id', menu)

    def test_task_title_action_prefers_fault_then_notification_and_appends_work_order(self):
        code = boq.task_name_action_code(1)
        self.assertIn("if fault:", code)
        self.assertIn("elif notification:", code)
        self.assertIn("'أمر العمل: ' + work_order", code)
        self.assertIn("rec.write({'name': name})", code)
        class Record:
            company_id = NS(id=1)

            def __init__(self, fault='', notification='', work_order=''):
                self.x_rekaz_fault_no = fault
                self.x_rekaz_notification_no = notification
                self.x_rekaz_work_order = work_order
                self.name = 'old'

            def write(self, values):
                self.name = values['name']

        for rec, expected in [
            (Record('F-1', 'N-1', 'WO-1'), 'رقم العطل: F-1 — أمر العمل: WO-1'),
            (Record('', 'N-1', 'WO-1'), 'رقم الإشعار: N-1 — أمر العمل: WO-1'),
            (Record('', '', 'WO-1'), 'أمر العمل: WO-1'),
        ]:
            exec(code, {'records': [rec]})
            self.assertEqual(rec.name, expected)

    def test_installation_requires_both_named_rekaz_projects(self):
        api=Mock(company_id=1)
        api.call.return_value=[
            {'id':10,'name':boq.TARGET_PROJECTS[0]},
            {'id':20,'name':boq.TARGET_PROJECTS[1]},
        ]
        self.assertEqual(boq.project_scope(api),[10,20])
        api.call.return_value=api.call.return_value[:1]
        with self.assertRaises(ValueError):
            boq.project_scope(api)

    def test_odoo20_group_permissions_use_global_company_restriction(self):
        api=Mock(company_id=1)
        api.find.side_effect=[
            [{'res_id':7}],
            [{'id':1}],
        ]
        calls=[]
        def upsert(_api,model,domain,values):
            calls.append((model,domain,values))
            return len(calls)
        with unittest.mock.patch.object(boq.claims,'upsert',side_effect=upsert):
            boq.access(api,6288,False)
        self.assertEqual(len(calls),2)
        group_rule=calls[0][2]
        company_rule=calls[1][2]
        self.assertEqual(group_rule['operation'],'r')
        self.assertIs(group_rule['domain'],False)
        self.assertTrue(group_rule['active'])
        self.assertFalse(company_rule['group_id'])
        self.assertEqual(company_rule['operation'],'crud')
        self.assertIn('company_ids',company_rule['domain'])
        self.assertTrue(company_rule['active'])

    def test_odoo20_catalogue_permissions_allow_create_without_update(self):
        api=Mock(company_id=1)
        api.find.side_effect=[
            [{'res_id':7}],
            [{'id':1}],
        ]
        calls=[]
        def upsert(_api,model,domain,values):
            calls.append((model,domain,values))
            return len(calls)
        with unittest.mock.patch.object(boq.claims,'upsert',side_effect=upsert):
            boq.access(api,6288,False,creatable=True)
        self.assertEqual(calls[0][2]['operation'],'cr')
        self.assertFalse(calls[0][2]['domain'])
        self.assertEqual(calls[1][2]['operation'],'crud')

    def test_legacy_catalogue_permissions_allow_create_without_update(self):
        api=Mock(company_id=1)
        api.find.side_effect=[
            [{'res_id':7}],
            [],
        ]
        boq.access(api,6288,False,creatable=True)
        access_values=api.ensure.call_args_list[0].args[2]
        self.assertTrue(access_values['perm_create'])
        self.assertFalse(access_values['perm_write'])
        self.assertFalse(access_values['perm_unlink'])
