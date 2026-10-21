import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from odoo_task_identity import NORMALIZE_CODE, guard_code, setup_code


class Row(dict):
    def __init__(self, rid, order=False, fault=False, **kwargs):
        super().__init__(x_rekaz_work_order=order, x_rekaz_fault_no=fault,
                         x_rekaz_order_key=False, x_rekaz_fault_key=False, **kwargs)
        self.id = rid
    def sudo(self): return self
    def write(self, values): self.update(values)


class Model:
    def __init__(self, rows): self.rows = rows
    def sudo(self): return self
    def with_context(self, **values):
        assert values == {'active_test': False}
        return self
    def search(self, domain): return self.rows


class IdentityTests(unittest.TestCase):
    def test_numeric_formats_and_invisible_characters_match(self):
        scope = {}
        exec(NORMALIZE_CODE, scope)
        clean = scope['canonical']
        self.assertEqual(clean(' ٠٠٤٢٢ ٠٤٩/۱۶۳\u200f'), '422049163')
        self.assertEqual(clean(' - / . '), False)
        self.assertEqual(clean('ab - 12'), 'AB12')

    def run_guard(self, rows, edited):
        exec(guard_code(), {'env': {'project.task': Model(rows)},
                            'records': edited, 'UserError': ValueError})

    def test_rejects_another_projects_order_including_archived(self):
        first = Row(1, order='٤٢٢', active=False, project_id=5)
        second = Row(2, order='00422', project_id=9)
        with self.assertRaisesRegex(ValueError, 'أمر العمل.*مكرر'):
            self.run_guard([first, second], [second])

    def test_rejects_fault_between_projects(self):
        first, second = Row(1, fault='123'), Row(2, fault='١٢٣')
        with self.assertRaisesRegex(ValueError, 'رقم العطل.*مكرر'):
            self.run_guard([first, second], [second])

    def test_self_update_and_forged_key_are_normalized(self):
        row = Row(1, order=' ۰۰۷ ')
        row['x_rekaz_order_key'] = 'forged'
        self.run_guard([row], [row])
        self.assertEqual(row['x_rekaz_order_key'], '7')
        row['x_rekaz_work_order'] = False
        self.run_guard([row], [row])
        self.assertFalse(row['x_rekaz_order_key'])

    def test_setup_has_atomic_database_guards(self):
        code = setup_code()
        compile(code, 'setup', 'exec')
        self.assertIn('CREATE UNIQUE INDEX IF NOT EXISTS rekaz_task_unique_order_v1', code)
        self.assertIn('CREATE UNIQUE INDEX IF NOT EXISTS rekaz_task_unique_fault_v1', code)
        self.assertIn('EXCEPTION WHEN unique_violation THEN NULL', code)


if __name__ == '__main__': unittest.main()
