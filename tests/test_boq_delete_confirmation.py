import unittest
from pathlib import Path


class BoqDeleteConfirmationTests(unittest.TestCase):
    def test_boq_delete_opens_server_confirmation_with_csrf_and_original_target(self):
        text = (Path(__file__).resolve().parents[1] / 'webapp/templates/_transaction_boq_panel.html').read_text(encoding='utf-8')
        self.assertIn("url_for('delete_confirm_page')", text)
        self.assertIn('name="_csrf_token"', text)
        self.assertIn('name="_delete_target"', text)
        self.assertIn("url_for('module_boq_delete', name=name, row_id=row.id, line_id=line.id)", text)
        self.assertNotIn('onsubmit="return confirm(', text)
