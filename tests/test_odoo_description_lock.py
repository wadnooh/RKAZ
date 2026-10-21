import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from odoo_description_lock import guard_code,installer_code

class DescriptionTests(unittest.TestCase):
    def rec(self,current,snapshot='',locked=False):
        r=Mock(company_id=SimpleNamespace(id=1),project_id=SimpleNamespace(id=5),
               description=current,x_rekaz_description_locked=locked)
        r.sudo.return_value=r
        r.x_rekaz_description_snapshot=snapshot
        return r
    def run_guard(self,rec,editor=False):
        env=SimpleNamespace(user=SimpleNamespace(has_group=lambda name:editor))
        exec(guard_code(1,[5,6]),{'records':[rec],'env':env,'UserError':ValueError})
    def test_first_populated_form_is_snapshotted_and_locked(self):
        rec=self.rec('<table><tr><th>رقم العطل</th><td>123</td></tr></table>')
        self.run_guard(rec)
        rec.write.assert_called_once_with({'x_rekaz_description_snapshot':rec.description,'x_rekaz_description_locked':True})
    def test_blank_template_is_still_editable(self):
        rec=self.rec('<table><tr><th>رقم العطل</th><td><br/></td></tr></table>')
        self.run_guard(rec)
        rec.write.assert_not_called()
    def test_quick_create_also_receives_the_blank_form_without_locking(self):
        rec=self.rec('')
        env=SimpleNamespace(user=SimpleNamespace(has_group=lambda name:False))
        template='<table><td><br/></td></table>'
        exec(guard_code(1,[5,6]),{'records':[rec],'env':env,'templates':{5:template},'UserError':ValueError})
        rec.write.assert_called_once_with({'description':template})
    def test_unauthorized_description_change_is_rejected(self):
        rec=self.rec('<td>new</td>','<td>old</td>',True)
        with self.assertRaises(ValueError):self.run_guard(rec)
        rec.write.assert_not_called()
    def test_assigned_editor_can_update_the_approved_snapshot(self):
        rec=self.rec('<td>new</td>','<td>old</td>',True)
        self.run_guard(rec,True)
        rec.write.assert_called_once_with({'x_rekaz_description_snapshot':rec.description})
    def test_workflow_changes_do_not_rewrite_description(self):
        rec=self.rec('<td>same</td>','<td>same</td>',True)
        self.run_guard(rec)
        rec.write.assert_not_called()
    def test_forged_public_unlock_is_restored_from_private_snapshot(self):
        rec=self.rec('<td>same</td>','<td>same</td>',False)
        self.run_guard(rec)
        rec.write.assert_called_once_with({'x_rekaz_description_locked':True})
    def test_installer_keeps_snapshot_private_and_assigns_no_users(self):
        code=installer_code()
        compile(code,'installer','exec')
        self.assertIn("values['groups'] = [(6,0,[system_group.id])]",code)
        self.assertNotIn("'user_ids'",code)
        self.assertNotIn('env.context',guard_code(1,[5,6]))
