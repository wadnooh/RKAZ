import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import odoo_warehouse_voucher as voucher

class VoucherTests(unittest.TestCase):
    def test_report_is_valid_rtl_xml_with_safe_qweb_cells(self):
        root=ET.fromstring(voucher.report_arch('move_ids'))
        self.assertEqual(root.attrib['t-name'],'rekaz_warehouse.transaction_voucher')
        self.assertTrue(any(x.get('dir')=='rtl' for x in root.iter('div')))
        self.assertTrue(all('t-field' not in x.attrib for x in root.iter('td')))
    def test_voucher_details_do_not_repeat_executed_quantity(self):
        arch=voucher.report_arch()
        self.assertNotIn('المنفذ فعلياً',arch)
        self.assertNotIn('executed',arch)
        self.assertIn('اسم المادة',arch)
        self.assertIn('الوحدة',arch)
        self.assertIn('المستلم',arch)
    def test_drafts_are_not_presented_as_executed_stock(self):
        arch=voucher.report_arch()
        self.assertIn("o.state != 'done'",arch)
        self.assertNotIn('executed',arch)
        self.assertNotIn('المنفذ فعلياً',arch)
        self.assertNotIn('x_reserved_qty',arch)
        self.assertNotIn('المطلوب / الحجز',arch)
        self.assertNotIn('product_uom_qty',arch)
        self.assertIn('x_global_balance_qty',arch)
        self.assertIn('رصيد المعاملة',arch)
        self.assertIn('رصيد المخزون العام',arch)
    def test_setup_changes_configuration_only_without_posting_or_access_grants(self):
        code=voucher.setup_code()
        compile(code,'setup','exec')
        for forbidden in ['button_validate(', 'action_confirm(', 'action_assign(', 'stock.quant', 'ir.access', 'implied_ids', 'sudo(']:
            self.assertNotIn(forbidden,code)
        self.assertIn('allowed_company_ids',code)
        self.assertIn('base.group_system',code)
        self.assertIn('x_rekaz_receiver_name',code)
        self.assertIn('x_rekaz_receiver_phone',code)
        self.assertIn('<attribute name="string">الكمية</attribute>',code)
        self.assertNotIn('الكمية المطلوبة / الحجز',code)
        self.assertIn('Landscape',code)
