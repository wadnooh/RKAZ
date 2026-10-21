import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import odoo_projects_copy as p


class CopyTests(unittest.TestCase):
    def test_arabic_description_preserves_zero_and_multiline_and_escapes_html(self):
        f = p.sync.m.field_name
        result = p.arabic_description('tickets', {f('ticket_no'): '00123', f('items_value'): 0,
                                                 f('notes'): '<script>\r\nنص عربي'})
        self.assertIn('dir="rtl"', result)
        self.assertIn('رقم المعاملة / العطل', result)
        self.assertIn('00123', result)
        self.assertIn('<td>0</td>', result)
        self.assertIn('&lt;script&gt;<br/>نص عربي', result)
        self.assertNotIn('x_c_', result)

    def test_untranslated_field_stops_before_displaying_technical_label(self):
        with self.assertRaisesRegex(ValueError, 'Missing Arabic'):
            p.arabic_description('tickets', {'x_c_unknown': 'value'})

    def test_resume_reuses_existing_identity_without_create(self):
        api = Mock(company_id=1)
        values = {'name': 'A', 'description': '<p>A</p>', 'company_id': 1}
        api.find.return_value = [dict(values, id=8, x_rekaz_import_hash=p.fingerprint('A', '<p>A</p>'))]
        self.assertEqual(p.upsert(api, 'project.task', 'origin', values), 8)
        api.call.assert_not_called()

    def test_changed_source_updates_unedited_task(self):
        api = Mock(company_id=1)
        api.find.return_value = [{'id': 8, 'name': 'Old', 'description': '', 'x_rekaz_import_hash': p.fingerprint('Old', '')}]
        api.call.side_effect = [True, [{'name': 'New', 'description': ''}], True]
        p.upsert(api, 'project.task', 'origin', {'name': 'New', 'description': ''})
        self.assertEqual(api.call.call_args.args[1], 'write')

    def test_legacy_entity_normalization_allows_arabic_upgrade(self):
        api = Mock(company_id=1)
        legacy = {'name': 'Project', 'description': '<div><p>&quot;نص&quot;</p></div>'}
        api.find.return_value = [{'id': 4, 'name': 'Project', 'description': '<div><p>"نص"</p></div>',
                                 'x_rekaz_import_hash': p.fingerprint(**legacy)}]
        new = {'name': 'Project', 'description': '<div dir="rtl">نص</div>'}
        api.call.side_effect = [True, [new], True]
        self.assertEqual(p.upsert(api, 'project.project', 'key', new, legacy=legacy), 4)
        self.assertEqual(api.call.call_args_list[0].args[1], 'write')

    def test_html_signature_does_not_equate_encoded_text_with_injected_tag(self):
        self.assertNotEqual(p.html_signature('<p>&lt;script&gt;</p>'), p.html_signature('<p><script></script></p>'))

    def test_task_title_uses_fault_and_work_order(self):
        values = p.task_number_values({
            p.sync.m.field_name('ticket_no'): '422067653',
            p.sync.m.field_name('work_order'): '422067425',
            p.sync.m.field_name('rekaz_code'): 'ER-181',
        })
        self.assertEqual(
            p.task_name(values, 'fallback'),
            'رقم العطل: 422067653 — أمر العمل: 422067425',
        )

    def test_task_title_uses_notification_when_fault_number_is_missing(self):
        values = p.task_number_values({
            p.sync.m.field_name('work_no'): 'RF-2',
            p.sync.m.field_name('work_order'): '422050327',
            p.sync.m.field_name('notification_no'): '422050327',
            p.sync.m.field_name('rekaz_code'): 'TR-02',
        })
        self.assertEqual(
            p.task_name(values, 'fallback'),
            'رقم الإشعار: 422050327 — أمر العمل: 422050327',
        )

    def test_task_numbers_are_recovered_from_imported_description(self):
        description = (
            '<table><tr><th>رقم المعاملة / العطل</th><td>422067653</td></tr>'
            '<tr><th>رقم أمر العمل</th><td>WO-1</td></tr>'
            '<tr><th>رقم الإشعار</th><td>NT-1</td></tr></table>'
        )
        self.assertEqual(p.task_numbers_from_description(description), {
            'x_rekaz_fault_no': '422067653',
            'x_rekaz_work_order': 'WO-1',
            'x_rekaz_notification_no': 'NT-1',
        })

    def test_task_title_without_any_transaction_number_keeps_fallback(self):
        self.assertEqual(p.task_name({}, 'original task title'), 'original task title')

    def test_edited_native_task_blocks_source_overwrite(self):
        api = Mock(company_id=1)
        api.find.return_value = [{'id': 8, 'name': 'Manual', 'description': '', 'x_rekaz_import_hash': p.fingerprint('Old', '')}]
        with patch.object(p.sync.m, 'save_report'), self.assertRaisesRegex(ValueError, 'edited'):
            p.upsert(api, 'project.task', 'origin', {'name': 'New', 'description': ''})
        api.call.assert_not_called()

    def test_generated_task_title_allows_source_refresh(self):
        old = {'id': 8, 'name': 'رقم الإشعار: NT-1 — أمر العمل: WO-1', 'description': '<p>Old</p>',
               'x_rekaz_import_hash': p.fingerprint('Old title', '<p>Old</p>'),
               'x_rekaz_fault_no': '', 'x_rekaz_notification_no': 'NT-1', 'x_rekaz_work_order': 'WO-1'}
        api = Mock(company_id=1)
        api.find.return_value = [old]
        api.call.side_effect = [True, [{'name': 'رقم العطل: F-1 — أمر العمل: WO-1', 'description': '<p>New</p>'}], True]
        p.upsert(api, 'project.task', 'origin', {
            'name': 'رقم العطل: F-1 — أمر العمل: WO-1', 'description': '<p>New</p>',
            'x_rekaz_fault_no': 'F-1', 'x_rekaz_notification_no': '', 'x_rekaz_work_order': 'WO-1',
        })
        self.assertEqual(api.call.call_args_list[0].args[1], 'write')
