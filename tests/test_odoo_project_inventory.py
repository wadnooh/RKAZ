import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import odoo_project_inventory as inventory


class InventoryTests(unittest.TestCase):
    def test_stock_migration_creates_draft_when_central_stock_is_empty(self):
        from tempfile import TemporaryDirectory
        import sqlite3

        task = {
            'id': 7,
            'name': 'Transaction 7',
            'x_rekaz_origin': 'reportrek:rekaz-main:tickets:1',
            'x_rekaz_stock_location': [90, 'Transaction stock'],
            'project_id': [30, 'Project'],
            'x_rekaz_fault_no': 'F-7',
            'x_rekaz_notification_no': False,
            'x_rekaz_work_order': 'WO-7',
        }
        unmatched = {
            'unlinked': [], 'invalid': [], 'invalid_items': [],
            'invalid_tasks': [], 'unsupported_types': [],
            'unsupported_tasks': [], 'central_receipts': [],
        }
        balance = {
            'task': task, 'item_no': 'A-1', 'quantity': 5.0,
            'source_ids': [12],
        }
        api = Mock(company_id=7)

        def api_call(model, method, args, **kwargs):
            if method == 'fields_get':
                if model == 'product.product':
                    return {'is_storable': {'type': 'boolean'}}
                return {}
            if model == 'stock.quant' and method == 'search_read':
                return []
            if model == 'stock.picking' and method == 'search_read':
                return []
            if model == 'stock.picking' and method == 'create':
                return 444
            raise AssertionError(f'Unexpected Odoo call: {model}.{method}')

        api.call.side_effect = api_call
        api.find.side_effect = lambda model, domain, fields: (
            [{'id': 101}] if model == 'stock.picking.type' else [])
        saved_reports = []

        with TemporaryDirectory() as directory:
            source = Path(directory) / 'source.db'
            conn = sqlite3.connect(source)
            conn.executescript("""
                CREATE TABLE warehouse_items(
                    id INTEGER PRIMARY KEY, item_no TEXT, item_name TEXT, unit TEXT
                );
                CREATE TABLE warehouse_tx(id INTEGER PRIMARY KEY, item_no TEXT);
                INSERT INTO warehouse_items VALUES (1, 'A-1', 'Cable', 'EA');
            """)
            conn.commit()
            conn.close()

            with (
                patch.object(inventory, 'install'),
                patch.object(inventory, 'warehouse', return_value={
                    'id': 1, 'lot_stock_id': [2, 'WH/Stock'],
                }),
                patch.object(inventory, 'task_index', return_value={
                    ('ticket_no', 'F-7'): {7: task},
                }),
                patch.object(inventory, 'aggregate_task_stock', return_value=(
                    {(7, 'a-1'): balance}, unmatched)),
                patch.object(inventory, 'move_schema', return_value=(
                    'product_uom', 'move_ids_without_package', None)),
                patch.object(inventory, 'source_unit_names', return_value=['EA']),
                patch.object(inventory, 'read_units_by_names', return_value=[{
                    'id': 4, 'name': 'EA', 'rounding': 1.0,
                }]),
                patch.object(inventory, 'source_units'),
                patch.object(inventory, 'create_product', return_value=501),
                patch.object(
                    inventory.sync.m, 'save_report',
                    side_effect=lambda _path, report: saved_reports.append(dict(report))),
            ):
                result = inventory.migrate_current_transaction_stock(api, source)

        self.assertEqual(result, 0)
        created = next(
            call.args[2][0] for call in api.call.call_args_list
            if call.args[:2] == ('stock.picking', 'create'))
        self.assertEqual(created['location_id'], 2)
        self.assertEqual(created['move_ids_without_package'][0][2]['product_uom_qty'], 5.0)
        self.assertEqual(saved_reports[-1]['shortages'], [{
            'item_no': 'A-1', 'required': 5.0, 'available': 0.0,
            'reason': 'central_stock_short; draft transfer created and may result in negative stock',
        }])
        self.assertEqual(api.call.call_args_list[-1].args[:2],
                         ('stock.picking', 'create'))

    def test_open_inventory_scopes_records_and_new_movements_to_selected_task(self):
        from types import SimpleNamespace
        rec = SimpleNamespace(id=186,name='Fault',company_id=SimpleNamespace(id=1),x_rekaz_stock_location=SimpleNamespace(id=20))
        scope = {'record':rec,'records':[rec],'env':SimpleNamespace(context={'allowed_company_ids':[1]}),'UserError':ValueError}
        exec(inventory.task_inventory_open_code(),scope)
        action=scope['action']
        self.assertEqual(action['domain'], [('x_rekaz_task','=',186),('company_id','=',1)])
        self.assertEqual(action['context']['default_x_rekaz_task'],186)
        self.assertNotIn('search_default_available',action['context'])
        button=ET.fromstring(inventory.task_materials_view_arch(1327)).find('.//button')
        self.assertEqual(button.attrib['name'],'1327')
        self.assertEqual(button.attrib['type'],'action')

    def test_task_materials_are_display_only_and_link_to_inventory_entry(self):
        root = ET.fromstring(inventory.task_materials_view_arch())
        self.assertEqual(root.find('.//a').attrib['href'], '/odoo/inventory/11/action-723')
        fields = [f for f in root.findall('.//page//field') if f.find('list') is not None]
        self.assertEqual(len(fields), 5)
        for field in fields:
            self.assertEqual(field.attrib['readonly'], '1')
            self.assertIn("'no_open': True", field.attrib['options'])
            form = field.find('form')
            self.assertEqual([form.attrib[x] for x in ('create','edit','delete')], ['0','0','0'])
            self.assertTrue(all(f.attrib['readonly']=='1' for f in form.findall('.//field')))
        # Stock-entry forms remain editable at the actual source screen.
        self.assertNotIn('edit="0"', inventory.picking_form_arch())

    def test_electricity_receipt_is_central_only_not_double_counted_or_blocking(self):
        lookup = {('ticket_no', 'T-7'): {7: {'id': 7}}}
        rows = [
            {'id': 1, 'ticket_no': 'T-7', 'tx_type': 'وارد من الكهرباء', 'item_no': 'A', 'qty': 100},
            {'id': 2, 'ticket_no': 'T-7', 'tx_type': 'منصرف للمعاملة', 'item_no': 'A', 'qty': 12},
            {'id': 3, 'ticket_no': 'T-7', 'tx_type': 'وارد من موقع العمل', 'item_no': 'A', 'qty': 3},
        ]
        balances, report = inventory.aggregate_task_stock(rows, lookup)
        self.assertEqual(balances[(7, 'a')]['quantity'], 9)
        self.assertEqual(balances[(7, 'a')]['source_ids'], [2, 3])
        self.assertEqual(report['central_receipts'], [{'source_id': 1, 'task_id': 7, 'item_no': 'A'}])
        self.assertEqual(report['unsupported_types'], [])
        self.assertFalse(inventory.transaction_balance_is_uncertain(7, 'A', report))

    def test_receipt_alone_never_creates_a_transaction_balance(self):
        balances, report = inventory.aggregate_task_stock([
            {'id': 1, 'tx_type': 'وارد من الكهرباء', 'item_no': 'A', 'qty': 100},
        ], {})
        self.assertEqual(balances, {})
        self.assertEqual(len(report['central_receipts']), 1)
        self.assertEqual(report['unlinked'], [])

    def test_inventory_migration_limits_unit_lookup_to_relevant_unit_names(self):
        aliases = {
            'EA': ('Units', 'Unit', 'الوحدات', 'وحدة'),
            'KIT': ('Kits',),
        }

        names = inventory.source_unit_names(['EA', 'KIT', 'M', ''], aliases)

        self.assertEqual(set(names), {
            'EA', 'KIT', 'Kits', 'M', 'Units', 'Unit',
            'ركاز / KIT', 'الوحدات', 'وحدة',
        })

    def test_transaction_stock_balance_uses_only_verified_transaction_directions(self):
        task = {'id': 7}
        lookup = {('ticket_no', 'T-7'): {7: task}}
        rows = [
            {'id': 1, 'ticket_no': 'T-7', 'tx_type': 'منصرف للمعاملة',
             'item_no': 'A-1', 'qty': 12},
            {'id': 2, 'ticket_no': 'T-7', 'tx_type': 'وارد من موقع العمل',
             'item_no': 'A-1', 'qty': 3},
            {'id': 3, 'ticket_no': 'T-7', 'tx_type': 'إرجاع للمجمعة',
             'item_no': 'A-1', 'qty': 2},
            {'id': 4, 'ticket_no': 'T-7', 'tx_type': 'إرجاع عهدة',
             'item_no': 'A-1', 'qty': 4},
            {'id': 5, 'ticket_no': 'T-7', 'tx_type': 'منصرف للمعاملة',
             'item_no': 'B-2', 'qty': 5},
        ]

        balances, report = inventory.aggregate_task_stock(rows, lookup)

        self.assertEqual(balances[(7, 'a-1')]['quantity'], 7)
        self.assertEqual(balances[(7, 'a-1')]['source_ids'], [1, 2, 3])
        self.assertEqual(balances[(7, 'b-2')]['quantity'], 5)
        self.assertEqual(report['unsupported_types'], [
            {'task_id': 7, 'source_id': 4, 'tx_type': 'إرجاع عهدة',
             'item_no': 'A-1'},
        ])
        self.assertTrue(inventory.transaction_balance_is_uncertain(7, 'a-1', report))
        self.assertFalse(inventory.transaction_balance_is_uncertain(7, 'B-2', report))

    def test_transaction_stock_balance_reports_unlinked_and_invalid_rows(self):
        lookup = {('ticket_no', 'T-7'): {7: {'id': 7}}}
        rows = [
            {'id': 1, 'ticket_no': 'T-8', 'tx_type': 'منصرف للمعاملة',
             'item_no': 'A-1', 'qty': 2},
            {'id': 2, 'ticket_no': 'T-7', 'tx_type': 'منصرف للمعاملة',
             'item_no': 'A-1', 'qty': float('nan')},
        ]

        balances, report = inventory.aggregate_task_stock(rows, lookup)

        self.assertEqual(balances, {})
        self.assertEqual(report['unlinked'][0]['source_id'], 1)
        self.assertEqual(report['invalid'][0]['reason'], 'invalid_quantity')
        self.assertEqual(report['invalid_items'], [{'task_id': 7, 'item_no': 'A-1'}])
        self.assertTrue(inventory.transaction_balance_is_uncertain(7, 'a-1', report))

    def test_returns_do_not_use_supplier_receipt_route(self):
        self.assertEqual(inventory.operation_key('إرجاع عهدة'), 'return')
        self.assertEqual(inventory.operation_key('وارد من موقع العمل'), 'return')
        self.assertEqual(inventory.operation_key('وارد من الكهرباء'), 'incoming')
        with self.assertRaises(ValueError): inventory.operation_key('إرجاع للكهرباء')

    def test_configured_operations_select_exact_ids_not_other_outgoing_types(self):
        api = Mock(company_id=7)
        api.call.side_effect = [
            [{'in_type_id': [10, 'receive'], 'out_type_id': [20, 'dispatch'],
              'int_type_id': [30, 'internal'], 'lot_stock_id': [2, 'stock']}],
            [{'id': 10, 'company_id': [7, 'Company'], 'warehouse_id': [1, 'Warehouse'],
              'code': 'incoming', 'active': True,
              'default_location_src_id': [1, 'supplier'], 'default_location_dest_id': [2, 'stock']}],
            [{'id': 20, 'company_id': [7, 'Company'], 'warehouse_id': [1, 'Warehouse'],
              'code': 'outgoing', 'active': True,
              'default_location_src_id': [2, 'stock'], 'default_location_dest_id': [3, 'customer'],
              'return_picking_type_id': False}],
            [{'id': 30, 'company_id': [7, 'Company'], 'warehouse_id': [1, 'Warehouse'],
              'code': 'internal', 'active': True,
              'default_location_src_id': [2, 'stock'], 'default_location_dest_id': [4, 'output']}],
            [{'id': 41, 'name': 'Inventory Adjustments', 'default_location_src_id': [2, 'stock'],
              'default_location_dest_id': [5, 'Inventory Loss']},
             {'id': 40, 'name': 'Damaged Materials', 'default_location_src_id': [2, 'stock'],
              'default_location_dest_id': [6, 'Damage Loss']}],
            [{'id': 5, 'usage': 'inventory'}, {'id': 6, 'usage': 'inventory'}],
        ]
        operations = inventory.operation_types(api, 1)
        self.assertEqual(operations['outgoing']['id'], 20)
        self.assertEqual(operations['internal']['id'], 30)
        self.assertEqual(operations['scrap']['id'], 40)
        self.assertEqual(operations['scrap_location_id'], 6)
        self.assertEqual(operations['stock_location_id'], 2)
        domain = api.call.call_args_list[4].args[2][0]
        self.assertIn(('warehouse_id', '=', 1), domain)
        self.assertIn(('code', '=', 'outgoing'), domain)
        location_domain = api.call.call_args_list[5].args[2][0]
        self.assertIn(('usage', '=', 'inventory'), location_domain)
        domain = api.call.call_args_list[3].args[2][0]
        self.assertIn(('id', '=', 30), domain)
        self.assertEqual(api.call.call_args_list[3].kwargs['context'], {'active_test': False})

    def test_archived_configured_warehouse_operations_are_read_without_reactivation(self):
        api = Mock(company_id=7)
        api.call.side_effect = [
            [{'in_type_id': [10, 'receive'], 'out_type_id': [20, 'dispatch'],
              'int_type_id': [30, 'internal'], 'lot_stock_id': [2, 'stock']}],
            [{'id': 10, 'name': 'Receipts', 'company_id': [7, 'Company'],
              'warehouse_id': [1, 'Warehouse'], 'code': 'incoming', 'active': False,
              'default_location_src_id': [1, 'supplier'], 'default_location_dest_id': [2, 'stock']}],
            [{'id': 20, 'name': 'Delivery', 'company_id': [7, 'Company'],
              'warehouse_id': [1, 'Warehouse'], 'code': 'outgoing', 'active': False,
              'default_location_src_id': [2, 'stock'], 'default_location_dest_id': [3, 'customer']}],
            [{'id': 30, 'name': 'Internal', 'company_id': [7, 'Company'],
              'warehouse_id': [1, 'Warehouse'], 'code': 'internal', 'active': False,
              'default_location_src_id': [2, 'stock'], 'default_location_dest_id': [4, 'output']}],
            [{'id': 31, 'name': 'Damaged', 'default_location_src_id': [2, 'stock'],
              'default_location_dest_id': [6, 'Damage Loss']}],
            [{'id': 6, 'usage': 'inventory'}],
        ]
        operations = inventory.operation_types(api, 1)

        self.assertFalse(operations['incoming']['active'])
        self.assertFalse(operations['outgoing']['active'])
        self.assertFalse(operations['internal']['active'])
        self.assertFalse(any(call.args[0] == 'stock.picking.type'
                             and call.args[1] == 'write'
                             for call in api.call.call_args_list))

    def test_incompatible_configured_operation_reports_actual_reference(self):
        api = Mock(company_id=7)
        api.call.side_effect = [
            [{'in_type_id': [10, 'receive'], 'out_type_id': False,
              'int_type_id': [30, 'internal'], 'lot_stock_id': [2, 'stock']}],
            [{'id': 10, 'name': 'Wrong warehouse', 'company_id': [7, 'Company'],
              'warehouse_id': [2, 'Other'], 'code': 'incoming', 'active': False,
              'default_location_src_id': [1, 'supplier'], 'default_location_dest_id': [2, 'stock']}],
        ]
        with self.assertRaisesRegex(
                ValueError, 'incompatible: in_type_id id=10 name=Wrong warehouse.*warehouse_id=2'):
            inventory.operation_types(api, 1)

    def test_incompatible_configured_operation_stops_before_import(self):
        api = Mock(company_id=7)
        api.call.side_effect = [
            [{'in_type_id': [10, 'receive'], 'out_type_id': False,
              'int_type_id': [30, 'internal'], 'lot_stock_id': [2, 'stock']}],
            [],
        ]
        with self.assertRaisesRegex(ValueError, 'in_type_id id=10 record_not_found'):
            inventory.operation_types(api, 1)
        self.assertEqual(
            api.call.call_args_list[1].args[2][0],
            [('id', '=', 10)])
        self.assertEqual(
            api.call.call_args_list[1].kwargs['context'],
            {'active_test': False})

    def test_package_units_do_not_invent_conversion_to_meters_or_pieces(self):
        api = Mock()
        api.call.side_effect = [
            {'relative_uom_id': {}, 'relative_factor': {}},
            [],
            31,
        ]
        mapping = {'m': [1]}
        inventory.source_units(api, mapping, ['KIT', ''])
        self.assertEqual(mapping['kit'], [31])
        values = api.call.call_args.args[2][0]
        self.assertIs(values['relative_uom_id'], False)
        self.assertEqual(values['relative_factor'], 1)
        self.assertNotIn('', mapping)
        self.assertEqual(api.call.call_args_list[1].args[:3],
                         ('uom.uom', 'search', [[('name', '=', 'ركاز / KIT')]]))

    def test_package_unit_resume_reuses_existing_unit(self):
        api = Mock()
        api.call.side_effect = [
            {'relative_uom_id': {}, 'relative_factor': {}},
            [31],
        ]
        mapping = {}
        inventory.source_units(api, mapping, ['ROL'])
        self.assertEqual(mapping['rol'], [31])
        self.assertEqual(api.call.call_count, 2)

    def test_uom_reads_search_ids_then_read_only_matching_records(self):
        api = Mock()
        api.call.side_effect = [
            {'name': {'type': 'char'}, 'rounding': {'type': 'float'}},
            [11, 12],
            [{'id': 11, 'name': 'Units', 'rounding': 1.0}],
            [{'id': 12, 'name': 'Unit', 'rounding': 0.01}],
        ]

        units = inventory.read_units_by_names(api, ['Unit', 'Units', 'Unit'])

        self.assertEqual([row['id'] for row in units], [11, 12])
        self.assertEqual(api.call.call_args_list[1].args[:3], (
            'uom.uom', 'search',
            [[('active', '=', True), ('name', 'in', ['Unit', 'Units'])]],
        ))
        self.assertEqual(api.call.call_args_list[2].args[:3],
                         ('uom.uom', 'read', [[11]]))
        self.assertEqual(api.call.call_args_list[3].args[:3],
                         ('uom.uom', 'read', [[12]]))
        self.assertEqual(api.call.call_args_list[2].kwargs['fields'],
                         ['name', 'rounding'])

    def test_uom_read_skips_empty_read_when_no_matching_names(self):
        api = Mock()
        api.call.side_effect = [
            {'name': {'type': 'char'}},
            [],
        ]

        self.assertEqual(inventory.read_units_by_names(api, ['Unit']), [])

        self.assertEqual(api.call.call_count, 2)
        self.assertEqual(api.call.call_args_list[1].args[:2], ('uom.uom', 'search'))

    def test_uom_read_omits_rounding_field_when_not_in_remote_schema(self):
        api = Mock()
        api.call.side_effect = [
            {'name': {'type': 'char'}},
            [11],
            [{'id': 11, 'name': 'Units'}],
        ]

        units = inventory.read_units_by_names(api, ['Units'])

        self.assertEqual(units, [{'id': 11, 'name': 'Units'}])
        self.assertEqual(api.call.call_args_list[2].kwargs['fields'], ['name'])

    def test_uom_read_falls_back_only_for_invalid_optional_field_and_caches_projection(self):
        api = Mock()
        api.call.side_effect = [
            {'name': {'type': 'char'}, 'rounding': {'type': 'float'}}, [11, 12],
            ValueError('Odoo JSON-2 failed reason=invalid_field error_class=ValueError'),
            [{'id': 11, 'name': 'Units'}], [{'id': 12, 'name': 'Unit'}],
        ]
        self.assertEqual(len(inventory.read_units_by_names(api, ['Unit', 'Units'])), 2)
        self.assertEqual(api.call.call_args_list[3].kwargs['fields'], ['name'])
        self.assertEqual(api.call.call_args_list[4].kwargs['fields'], ['name'])

    def test_uom_read_does_not_hide_access_or_unrelated_server_errors(self):
        api = Mock()
        api.call.side_effect = [
            {'name': {'type': 'char'}, 'rounding': {'type': 'float'}}, [11],
            ValueError('reason=access_denied'),
        ]
        with self.assertRaisesRegex(ValueError, 'access_denied'):
            inventory.read_units_by_names(api, ['Unit'])
        self.assertEqual(api.call.call_count, 3)

    def test_returns_keep_stock_direction_and_separate_label(self):
        self.assertEqual(inventory.direction('إرجاع للكهرباء'), 'outgoing')
        self.assertEqual(inventory.direction('إرجاع عهدة'), 'incoming')
        self.assertEqual(inventory.movement_label('إرجاع للكهرباء'), 'مرتجع')
        self.assertEqual(inventory.movement_label('وارد من موقع العمل'), 'مرتجع')
        self.assertEqual(inventory.movement_label('منصرف للمعاملة'), 'منصرف')

    def test_warehouse_requires_explicit_company_scoped_id(self):
        api = Mock(company_id=1)
        api.call.return_value = [{'id': 3, 'name': 'Warehouse', 'lot_stock_id': [9, 'Stock'],
                                  'view_location_id': [1, 'WH'], 'active': True}]
        with self.assertRaisesRegex(ValueError, 'ID 1'):
            inventory.warehouse(api)
        domain = api.call.call_args.args[2][0]
        self.assertIn(('company_id', '=', 1), domain)
        api.call.return_value = [{'id': inventory.REKAZ_WAREHOUSE_ID, 'name': 'Warehouse',
                                  'lot_stock_id': [2, 'Stock'], 'view_location_id': [1, 'WH'],
                                  'active': True}]
        self.assertEqual(inventory.warehouse(api)['id'], inventory.REKAZ_WAREHOUSE_ID)
        self.assertEqual(api.call.call_args.args[2][0],
                         [('company_id', '=', 1)])

    def test_warehouse_allows_archived_warehouse_but_requires_one_active_central(self):
        api = Mock(company_id=7)
        api.call.return_value = [
            {'id': 1, 'name': 'Central', 'lot_stock_id': [2, 'WH/Stock'],
             'view_location_id': [1, 'WH'], 'active': True},
            {'id': 5, 'name': 'Extra', 'lot_stock_id': [8, 'WH2/Stock'],
             'view_location_id': [7, 'WH2'], 'active': False},
        ]
        self.assertEqual(inventory.warehouse(api)['id'], 1)
        api.call.assert_called_once_with(
            'stock.warehouse', 'search_read', [[('company_id', '=', 7)]],
            fields=['id', 'name', 'code', 'lot_stock_id', 'view_location_id', 'active'],
            limit=False, context={'active_test': False})

    def test_warehouse_rejects_multiple_active_warehouses_without_changing_them(self):
        api = Mock(company_id=7)
        api.call.return_value = [
            {'id': 1, 'name': 'Central', 'lot_stock_id': [2, 'WH/Stock'],
             'view_location_id': [1, 'WH'], 'active': True},
            {'id': 5, 'name': 'Extra', 'lot_stock_id': [8, 'WH2/Stock'],
             'view_location_id': [7, 'WH2'], 'active': True},
        ]
        with self.assertRaisesRegex(
                ValueError, 'exactly one active warehouse.*1:Central \\[active\\], '
                            '5:Extra \\[active\\]'):
            inventory.warehouse(api)

    def test_warehouse_audit_is_read_only_and_reports_stock_and_history(self):
        api = Mock(company_id=7)

        def call(model, method, args=None, **kwargs):
            if model == 'stock.warehouse' and method == 'fields_get':
                return {name: {} for name in (
                    'id', 'name', 'code', 'company_id', 'lot_stock_id',
                    'view_location_id', 'active', 'in_type_id',
                    'out_type_id', 'int_type_id')}
            if model == 'stock.warehouse' and method == 'search_read':
                return [
                    {'id': 1, 'name': 'Central', 'code': 'WH',
                     'lot_stock_id': [2, 'WH/Stock'],
                     'view_location_id': [1, 'WH'], 'active': True,
                     'in_type_id': [10, 'Receipts'], 'out_type_id': False,
                     'int_type_id': False},
                    {'id': 5, 'name': 'Extra', 'code': 'WH2',
                     'lot_stock_id': [8, 'WH2/Stock'],
                     'view_location_id': [7, 'WH2'], 'active': True,
                     'in_type_id': [20, 'Receipts 2'], 'out_type_id': False,
                     'int_type_id': False},
                ]
            if model == 'stock.picking.type' and method == 'fields_get':
                return {name: {} for name in (
                    'id', 'name', 'code', 'company_id', 'warehouse_id', 'active')}
            if model == 'stock.picking.type' and method == 'search_read':
                return [
                    {'id': 10, 'name': 'Receipts', 'code': 'incoming',
                     'company_id': [7, 'Company'],
                     'warehouse_id': [1, 'Central'], 'active': True},
                    {'id': 20, 'name': 'Receipts 2', 'code': 'incoming',
                     'company_id': [7, 'Company'],
                     'warehouse_id': [5, 'Extra'], 'active': True},
                ]
            if model == 'stock.picking' and method == 'search_count':
                return 0 if args[0][1] == ('picking_type_id', 'in', [10]) else 2
            if model == 'stock.quant' and method == 'search_read':
                if ('location_id', 'child_of', 1) in args[0]:
                    return []
                return [{
                    'product_id': [100, 'Cable'],
                    'location_id': [9, 'WH2/Stock'],
                    'quantity': 3.0,
                    'reserved_quantity': 1.0,
                }]
            raise AssertionError(f'Unexpected Odoo call: {model}.{method}')

        api.call.side_effect = call
        report = inventory.audit_warehouses(api)
        self.assertEqual(report['warehouse_count'], 2)
        self.assertFalse(report['archive_safe_by_basic_checks'])
        self.assertEqual(report['warehouses'][0]['picking_count'], 0)
        self.assertEqual(report['warehouses'][1]['picking_count'], 2)
        self.assertEqual(report['warehouses'][1]['nonzero_internal_stock'], [{
            'product_id': 100,
            'product': 'Cable',
            'quantity': 3.0,
            'reserved_quantity': 1.0,
            'location_ids': [9],
        }])
        self.assertEqual(
            report['warehouses'][0]['configured_default_operation_types']['incoming'],
            {'id': 10, 'name': 'Receipts', 'code': 'incoming', 'active': True,
             'warehouse_id': 1, 'company_id': 7})
        self.assertFalse(any(
            call.args[1] in {'create', 'write', 'unlink'}
            for call in api.call.call_args_list))

    def test_missing_warehouse_is_not_created_automatically(self):
        api = Mock(company_id=7)
        api.call.return_value = []
        with self.assertRaises(ValueError): inventory.warehouse(api)
        api.call.assert_called_once()

    def test_stock_schema_supports_modern_and_legacy_fields(self):
        self.assertEqual(inventory.move_schema({'uom_id': {}, 'description_picking': {}}, {'move_ids': {}}),
                         ('uom_id', 'move_ids', 'description_picking'))
        self.assertEqual(inventory.move_schema({'product_uom': {}, 'name': {}}, {'move_ids_without_package': {}}),
                         ('product_uom', 'move_ids_without_package', 'name'))
        with self.assertRaises(ValueError):
            inventory.move_schema({}, {'move_ids': {}})

    def test_rekaz_dashboard_creates_four_dedicated_operation_types(self):
        api = Mock()
        fields = {name: {'type': 'char'} for name in (
            'name', 'code', 'warehouse_id', 'default_location_src_id',
            'default_location_dest_id', 'sequence_code', 'active', 'sequence')}
        api.call.side_effect = [fields, 201, 202, 203, 204]
        api.find.return_value = []
        operations = {
            'internal': {'id': 101, 'default_location_src_id': [2, 'Stock'],
                         'default_location_dest_id': [4, 'Output']},
            'outgoing': {'id': 102, 'default_location_src_id': [2, 'Stock'],
                         'default_location_dest_id': [3, 'Customer']},
            'scrap': {'id': 103, 'default_location_src_id': [2, 'Stock'],
                      'default_location_dest_id': [5, 'Inventory Loss']},
        }
        result = inventory.install_task_operation_types(api, 1, operations)
        self.assertEqual(result, {
            'incoming': 201, 'outgoing': 202, 'scrapped': 203, 'returned': 204})
        created = [call.args[2][0] for call in api.call.call_args_list
                   if call.args[0] == 'stock.picking.type' and call.args[1] == 'create']
        self.assertEqual([values['name'] for values in created],
                         ['الوارد', 'المنصرف', 'التالف', 'المسترجع'])
        self.assertEqual([values['x_rekaz_origin'].rsplit(':', 1)[-1] for values in created],
                         ['incoming', 'outgoing', 'scrapped', 'returned'])
        self.assertEqual([values['code'] for values in created],
                         ['internal', 'outgoing', 'outgoing', 'internal'])
        self.assertEqual(created[-1]['default_location_src_id'], 4)
        self.assertEqual(created[-1]['default_location_dest_id'], 2)
        self.assertEqual([values['sequence'] for values in created], [20, 30, 40, 50])

    def test_rekaz_dashboard_operation_types_are_updated_idempotently(self):
        api = Mock()
        fields = {name: {'type': 'char'} for name in (
            'name', 'code', 'warehouse_id', 'default_location_src_id',
            'default_location_dest_id', 'sequence_code', 'active', 'sequence')}
        api.call.return_value = fields
        api.find.side_effect = [[{'id': operation_id}] for operation_id in (201, 202, 203, 204)]
        operations = {
            'internal': {'id': 101, 'default_location_src_id': [2, 'Stock'],
                         'default_location_dest_id': [4, 'Output']},
            'outgoing': {'id': 102, 'default_location_src_id': [2, 'Stock'],
                         'default_location_dest_id': [3, 'Customer']},
            'scrap': {'id': 103, 'default_location_src_id': [2, 'Stock'],
                      'default_location_dest_id': [5, 'Inventory Loss']},
            'return': {'id': 104, 'default_location_src_id': [1, 'Customer'],
                       'default_location_dest_id': [5, 'Inventory Loss']},
        }
        result = inventory.install_task_operation_types(api, 1, operations)
        self.assertEqual(result, {
            'incoming': 201, 'outgoing': 202, 'scrapped': 203, 'returned': 204})
        self.assertEqual(
            [call.args[2][0][0] for call in api.call.call_args_list
             if call.args[0] == 'stock.picking.type' and call.args[1] == 'write'],
            [201, 202, 203, 204])
        self.assertFalse(any(call.args[1] == 'create'
                             for call in api.call.call_args_list
                             if call.args[0] == 'stock.picking.type'))

    def test_dashboard_unmarks_standard_type_before_creating_owned_card(self):
        api = Mock()
        fields = {name: {'type': 'char'} for name in (
            'name', 'code', 'warehouse_id', 'default_location_src_id',
            'default_location_dest_id', 'sequence_code', 'active', 'sequence')}
        api.call.side_effect = [fields, None, 301, 302, 303, 304]
        api.find.side_effect = [
            [{'id': 101}], [], [], [], [], [], [], [], []
        ]
        operations = {
            'internal': {'id': 101, 'default_location_src_id': [2, 'Stock'],
                         'default_location_dest_id': [4, 'Output']},
            'outgoing': {'id': 102, 'default_location_src_id': [2, 'Stock'],
                         'default_location_dest_id': [3, 'Customer']},
            'scrap': {'id': 103, 'default_location_src_id': [2, 'Stock'],
                      'default_location_dest_id': [5, 'Inventory Loss']},
            'return': {'id': 104, 'default_location_src_id': [1, 'Customer'],
                       'default_location_dest_id': [5, 'Inventory Loss']},
        }
        inventory.install_task_operation_types(api, 1, operations)
        api.call.assert_any_call('stock.picking.type', 'write',
                                 [[101], {'x_rekaz_origin': False}])
        self.assertEqual(
            sum(call.args[1] == 'create' for call in api.call.call_args_list
                if call.args[0] == 'stock.picking.type'), 4)

    def test_old_custom_inventory_menus_are_deactivated(self):
        def find(model, domain, fields):
            if model == 'ir.model.data':
                return [{'res_id': 9}]
            if model == 'ir.ui.menu':
                return [{'id': 21}] if domain[0][2] == 'حركات ركاز حسب النوع' else [{'id': 22}]
            return []

        api = Mock()
        api.find.side_effect = find
        inventory.retire_owned_ui(api)
        api.call.assert_any_call('ir.ui.menu', 'write', [[22, 21], {'active': False}])

    def test_legacy_operations_are_hidden_and_archived_except_required_base_types(self):
        api = Mock(company_id=7)
        api.call.side_effect = [
            [{'id': 20, 'name': 'استلام وتوريد مواد للمستودع'},
             {'id': 21, 'name': 'تحويلات داخلية'},
             {'id': 22, 'name': 'صرف مواد للمشاريع والمعاملات'},
             {'id': 23, 'name': 'مسترجع مواد من المشاريع والمعاملات'},
             {'id': 24, 'name': 'إتلاف وخردة مواد تالفة'}],
            [{'domain': "[('company_id', '=', 7)]"}],
            True,
            True,
        ]
        api.find.return_value = [{'res_id': 90}]
        inventory.hide_legacy_inventory_dashboard_operations(api, 1, {20, 24})
        api.call.assert_any_call(
            'ir.actions.act_window', 'write',
            [[90], {'domain': "['&', ('id', 'not in', [20, 21, 22, 23, 24]), ('company_id', '=', 7)]"}])
        api.call.assert_any_call(
            'stock.picking.type', 'search_read',
            [[('company_id', '=', 7), ('warehouse_id', '=', 1),
              ('name', 'in', [
                  'استلام وتوريد مواد للمستودع', 'تحويلات داخلية',
                  'صرف مواد للمشاريع والمعاملات',
                  'مسترجع مواد من المشاريع والمعاملات',
                  'إتلاف وخردة مواد تالفة',
              ]), ('active', '=', True)]],
            fields=['id', 'name'], limit=False, context={'lang': 'ar_001'})
        api.call.assert_any_call(
            'stock.picking.type', 'write', [[21, 22, 23], {'active': False}])
        self.assertFalse(any(
            call.args[0] == 'stock.picking.type' and call.args[1] == 'write'
            and set(call.args[2][0]) & {20, 24}
            for call in api.call.call_args_list))

    def test_dashboard_card_hiding_is_idempotent(self):
        api = Mock(company_id=7)
        api.call.side_effect = [
            [{'id': 21, 'name': 'تحويلات داخلية'}],
            [{'domain': "[('id', 'not in', [21])]"}],
            True,
        ]
        api.find.return_value = [{'res_id': 90}]
        inventory.hide_legacy_inventory_dashboard_operations(api, 1)
        self.assertEqual(api.call.call_count, 3)
        self.assertFalse(any(call.args[1] == 'write'
                             for call in api.call.call_args_list
                             if call.args[0] == 'ir.actions.act_window'))
        api.call.assert_any_call(
            'stock.picking.type', 'write', [[21], {'active': False}])

    def test_dashboard_card_hiding_merges_existing_exclusions(self):
        api = Mock(company_id=7)
        api.call.side_effect = [
            [{'id': 21, 'name': 'تحويلات داخلية'},
             {'id': 23, 'name': 'صرف مواد للمشاريع والمعاملات'}],
            [{'domain': "['&', ('id', 'not in', [9, 21]), ('company_id', '=', 7)]"}],
            True,
            True,
        ]
        api.find.return_value = [{'res_id': 90}]

        inventory.hide_legacy_inventory_dashboard_operations(api, 1)

        api.call.assert_any_call(
            'ir.actions.act_window', 'write',
            [[90], {'domain': "['&', ('id', 'not in', [9, 21, 23]), ('company_id', '=', 7)]"}])

    def test_damage_flow_requires_inventory_loss_destination(self):
        api = Mock(company_id=7)
        api.call.side_effect = [
            [{'in_type_id': [10, 'receive'], 'out_type_id': [20, 'dispatch'],
              'int_type_id': [30, 'internal'], 'lot_stock_id': [2, 'stock']}],
            [{'id': 10, 'company_id': [7, 'Company'], 'warehouse_id': [1, 'Warehouse'],
              'code': 'incoming', 'active': True,
              'default_location_src_id': [1, 'supplier'], 'default_location_dest_id': [2, 'stock']}],
            [{'id': 20, 'company_id': [7, 'Company'], 'warehouse_id': [1, 'Warehouse'],
              'code': 'outgoing', 'active': True,
              'default_location_src_id': [2, 'stock'], 'default_location_dest_id': [3, 'customer'],
              'return_picking_type_id': False}],
            [{'id': 30, 'company_id': [7, 'Company'], 'warehouse_id': [1, 'Warehouse'],
              'code': 'internal', 'active': True,
              'default_location_src_id': [2, 'stock'], 'default_location_dest_id': [4, 'output']}],
            [{'id': 40, 'default_location_src_id': [2, 'stock'], 'default_location_dest_id': [5, 'not-loss']}],
            [],
        ]
        with self.assertRaisesRegex(ValueError, 'inventory-loss location'):
            inventory.operation_types(api, 1)

    def test_ambiguous_damage_operations_report_names_without_guessing(self):
        api = Mock(company_id=7)
        api.call.side_effect = [
            [{'in_type_id': [10, 'receive'], 'out_type_id': [20, 'dispatch'],
              'int_type_id': [30, 'internal'], 'lot_stock_id': [2, 'stock']}],
            [{'id': 10, 'company_id': [7, 'Company'], 'warehouse_id': [1, 'Warehouse'],
              'code': 'incoming', 'active': True,
              'default_location_src_id': [1, 'supplier'],
              'default_location_dest_id': [2, 'stock']}],
            [{'id': 20, 'company_id': [7, 'Company'], 'warehouse_id': [1, 'Warehouse'],
              'code': 'outgoing', 'active': True,
              'default_location_src_id': [2, 'stock'],
              'default_location_dest_id': [3, 'customer'], 'return_picking_type_id': False}],
            [{'id': 30, 'company_id': [7, 'Company'], 'warehouse_id': [1, 'Warehouse'],
              'code': 'internal', 'active': True,
              'default_location_src_id': [2, 'stock'],
              'default_location_dest_id': [4, 'output']}],
            [{'id': 40, 'name': 'Inventory Adjustment', 'default_location_src_id': [2, 'stock'],
              'default_location_dest_id': [5, 'Inventory Loss A']},
             {'id': 41, 'name': 'Inventory Loss', 'default_location_src_id': [2, 'stock'],
              'default_location_dest_id': [6, 'Inventory Loss B']}],
            [{'id': 5, 'usage': 'inventory'}, {'id': 6, 'usage': 'inventory'}],
        ]
        with self.assertRaisesRegex(
                ValueError, r'40:Inventory Adjustment -> Inventory Loss A.*41:Inventory Loss'):
            inventory.operation_types(api, 1)

    def test_duplicate_damage_operations_with_identical_route_choose_stable_id(self):
        api = Mock(company_id=7)
        api.call.side_effect = [
            [{'in_type_id': [10, 'receive'], 'out_type_id': [20, 'dispatch'],
              'int_type_id': [30, 'internal'], 'lot_stock_id': [2, 'stock']}],
            [{'id': 10, 'company_id': [7, 'Company'], 'warehouse_id': [1, 'Warehouse'],
              'code': 'incoming', 'active': True,
              'default_location_src_id': [1, 'supplier'],
              'default_location_dest_id': [2, 'stock']}],
            [{'id': 20, 'company_id': [7, 'Company'], 'warehouse_id': [1, 'Warehouse'],
              'code': 'outgoing', 'active': True,
              'default_location_src_id': [2, 'stock'],
              'default_location_dest_id': [3, 'customer'], 'return_picking_type_id': False}],
            [{'id': 30, 'company_id': [7, 'Company'], 'warehouse_id': [1, 'Warehouse'],
              'code': 'internal', 'active': True,
              'default_location_src_id': [2, 'stock'],
              'default_location_dest_id': [4, 'output']}],
            [{'id': 13, 'name': 'التالف', 'default_location_src_id': [2, 'stock'],
              'default_location_dest_id': [5, 'المواد التالفة والخردة']},
             {'id': 10, 'name': 'التالف', 'default_location_src_id': [2, 'stock'],
              'default_location_dest_id': [5, 'المواد التالفة والخردة']}],
            [{'id': 5, 'usage': 'inventory'}],
        ]
        operations = inventory.operation_types(api, 1)
        self.assertEqual(operations['scrap']['id'], 10)
        self.assertEqual(operations['scrap_location_id'], 5)

    def test_product_lock_checks_identity_before_retry(self):
        api = Mock(company_id=1)
        api.call.side_effect = [ValueError('error_class=psycopg2.errors.LockNotAvailable'), 9]
        api.find.return_value = []
        with patch.object(inventory.time, 'sleep'):
            self.assertEqual(inventory.create_product(api, {'x_rekaz_origin': 'item:1', 'uom_id': 2}), 9)
        self.assertEqual(api.call.call_count, 2)
        api.find.assert_called_once()

    def test_product_created_by_other_process_is_reused(self):
        api = Mock(company_id=1)
        api.call.side_effect = ValueError('error_class=psycopg2.errors.LockNotAvailable')
        api.find.return_value = [{'id': 9, 'uom_id': [2, 'unit']}]
        with patch.object(inventory.time, 'sleep'):
            self.assertEqual(inventory.create_product(api, {'x_rekaz_origin': 'item:1', 'uom_id': 2}), 9)
        self.assertEqual(api.call.call_count, 1)

    def test_uncertain_transport_failure_is_not_retried(self):
        api = Mock(company_id=1)
        api.call.side_effect = ValueError('Odoo transport failed')
        with self.assertRaises(ValueError):
            inventory.create_product(api, {'x_rekaz_origin': 'item:1', 'uom_id': 2})
        api.find.assert_not_called()

    def test_incoming_and_outgoing_types_are_explicit(self):
        self.assertEqual(inventory.direction('منصرف للمعاملة'), 'outgoing')
        self.assertEqual(inventory.direction('وارد من موقع العمل'), 'incoming')
        self.assertEqual(inventory.direction('إرجاع عهدة'), 'incoming')
        with self.assertRaises(ValueError): inventory.direction('unknown')

    def test_task_matching_intersects_identifiers_and_refuses_ambiguity(self):
        a, b = {'id': 1}, {'id': 2}
        index = {('ticket_no', 'T'): {1: a, 2: b}, ('rekaz_code', 'ER'): {2: b}, ('work_order', 'WO'): {1: a}}
        self.assertIsNone(inventory.match_task({'ticket_no': 'T'}, index))
        self.assertEqual(inventory.match_task({'ticket_no': 'T', 'rekaz_code': 'ER'}, index), b)
        self.assertIsNone(inventory.match_task({'rekaz_code': 'ER', 'work_order': 'WO'}, index))

    def test_ticket_number_does_not_match_work_order(self):
        self.assertIsNone(inventory.match_task({'ticket_no': '123'}, {('work_order', '123'): {1: {'id': 1}}}))

    def test_transaction_materials_view_is_readonly_and_shows_four_flows(self):
        arch = inventory.task_materials_view_arch()
        root = ET.fromstring(arch)
        self.assertEqual(root.tag, 'data')
        extra_info_xpath = next(
            node for node in root
            if node.get('expr') == "//page[@name='extra_info']")
        self.assertEqual(extra_info_xpath.get('position'), 'replace')
        self.assertIn(inventory.TASK_INBOUND_FIELD, arch)
        self.assertIn(inventory.TASK_OUTBOUND_FIELD, arch)
        self.assertIn(inventory.TASK_RETURNS_FIELD, arch)
        self.assertIn(inventory.TASK_SCRAPS_FIELD, arch)
        self.assertIn(inventory.TASK_BALANCES_FIELD, arch)
        self.assertIn('الوارد − المنصرف − المسترجع − التالف', arch)
        self.assertNotIn('x_rekaz_unclassified_pickings', arch)
        self.assertNotIn('<list create="1"', arch)
        self.assertLess(arch.index('ملخص مواد المعاملة'), arch.index('الوارد'))
        self.assertLess(arch.index('الوارد'), arch.index('المنصرف'))
        self.assertLess(arch.index('المنصرف'), arch.index('التالف'))
        self.assertLess(arch.index('التالف'), arch.index('المسترجع'))
        picking_fields = [node for node in root.iter('field')
                          if node.get('name') in {
                              inventory.TASK_INBOUND_FIELD, inventory.TASK_OUTBOUND_FIELD,
                              inventory.TASK_RETURNS_FIELD, inventory.TASK_SCRAPS_FIELD}]
        self.assertEqual(len(picking_fields), 4)
        for field in picking_fields:
            self.assertEqual(field.get('readonly'), '1')
            self.assertEqual(field.find('list').attrib,
                             {'create': '0', 'edit': '0', 'delete': '0'})
            self.assertIsNone(field.get('context'))
        self.assertNotIn('x_rekaz_unclassified_pickings', arch)
        summary = next(field for field in root.iter('field')
                       if field.get('name') == inventory.TASK_BALANCES_FIELD)
        self.assertEqual([field.get('name') for field in summary.find('list')],
                         ['x_product_id', 'x_uom_id', 'x_incoming_qty',
                          'x_outgoing_qty', 'x_returned_qty', 'x_scrapped_qty',
                          'x_balance_qty', 'x_global_balance_qty'])
        self.assertNotIn('x_reserved_qty', arch)
        self.assertNotIn('x_after_reservation_qty', arch)
        picking_arch = ET.fromstring(inventory.picking_form_arch())
        operation_type_xpath = next(
            node for node in picking_arch
            if node.get('expr') == "//field[@name='picking_type_id']")
        operation_type_domain = operation_type_xpath.find('attribute').text
        self.assertIn("context.get('default_x_rekaz_task')", operation_type_domain)
        self.assertIn("('x_rekaz_origin', 'in',", operation_type_domain)
        self.assertNotIn("('x_rekaz_task',", operation_type_domain)
        self.assertEqual(operation_type_domain.count('rekaz-main:inventory:dashboard:'), 4)
        partner_xpath = next(node for node in picking_arch
                             if node.get('expr') == "//field[@name='partner_id']")
        task_field = next(node for node in picking_arch.iter('field')
                          if node.get('name') == 'x_rekaz_task')
        movement_field = next(node for node in picking_arch.iter('field')
                              if node.get('name') == 'x_rekaz_movement')
        self.assertEqual(task_field.get('readonly'), 'x_rekaz_task')
        self.assertIsNone(movement_field.get('readonly'))
        origin_xpath = next(node for node in picking_arch
                            if node.get('expr') == "//field[@name='origin']")
        self.assertEqual(partner_xpath.find('attribute').text, 'استلام من المورد')
        self.assertEqual(origin_xpath.find('attribute').text, '1')

    def test_supplier_defaults_and_summary_quantities(self):
        fields = {'supplier_rank': {}, 'is_company': {}}
        api = Mock()
        api.find.return_value = [{'id': 12, 'supplier_rank': 0}]
        self.assertEqual(inventory.ensure_main_supplier(api, 'الشركة السعودية للطاقة', fields), 12)
        api.call.assert_called_once_with('res.partner', 'write', [[12], {'supplier_rank': 1}])
        api = Mock()
        api.find.return_value = []
        api.call.return_value = 13
        self.assertEqual(inventory.ensure_main_supplier(api, 'الشركة السعودية للطاقة', fields), 13)
        api.call.assert_called_once_with(
            'res.partner', 'create',
            [{'name': 'الشركة السعودية للطاقة', 'supplier_rank': 1, 'is_company': True}])

        supplier_code = inventory.supplier_default_action_code(7, 10, 12)
        compile(supplier_code, '<supplier action>', 'exec')

        class Record:
            def __init__(self, **values):
                self.__dict__.update(values)

            def write(self, values):
                self.__dict__.update(values)

        default = Record(company_id=Record(id=7), picking_type_id=Record(id=10), partner_id=False)
        selected = Record(company_id=Record(id=7), picking_type_id=Record(id=10),
                          partner_id=Record(id=99))
        exec(supplier_code, {'records': [default, selected]})
        self.assertEqual(default.partner_id, 12)
        self.assertEqual(selected.partner_id.id, 99)

        class Uom(Record):
            def _compute_quantity(self, quantity, target):
                return quantity * 2

        unit = Uom(id=3)
        product = Record(id=5, uom_id=unit)

        def move(flow, quantity, state='done', uom=unit):
            return Record(product_id=product, state=state, quantity=quantity, product_uom_qty=quantity,
                          product_uom=uom, picking_id=Record(x_rekaz_movement=flow))

        task = Record(
            x_rekaz_material_moves=[move('incoming', 9), move('incoming', 3, uom=Uom(id=4)),
                                    move('incoming', 100, state='draft'), move('outgoing', 4),
                                    move('returned', 2), move('scrapped', 1)],
            x_rekaz_material_quants=[Record(product_id=product, quantity=2),
                                     Record(product_id=Record(id=6), quantity=50)])
        class Summary(dict):
            def __getattr__(self, name):
                return self[name]

        company = Record(id=7)
        summary = Summary(
            x_task_id=task, x_product_id=product, x_company_id=company)
        quant_rows = [
            Record(product_id=Record(id=5), company_id=Record(id=7), quantity=14),
            Record(product_id=Record(id=5), company_id=Record(id=7), quantity=6),
            Record(product_id=Record(id=6), company_id=Record(id=7), quantity=50),
            Record(product_id=Record(id=5), company_id=Record(id=8), quantity=100),
        ]

        class QuantModel:
            def search(self, domain):
                self.domain = domain
                return quant_rows

        class LocationModel:
            def search(self, domain):
                self.domain = domain
                return [Record(id=90)]

        quant_model = QuantModel()
        location_model = LocationModel()
        compute_code = inventory.summary_compute_code(
            'quantity', 'product_uom', warehouse_view_location_id=2)
        compile(compute_code, '<material summary compute>', 'exec')
        exec(compute_code, {'self': [summary], 'env': {
            'stock.location': location_model, 'stock.quant': quant_model,
        }})
        self.assertEqual(summary, {
            'x_task_id': task, 'x_product_id': product,
            'x_company_id': company,
            'x_incoming_qty': 15.0, 'x_outgoing_qty': 4.0,
            'x_returned_qty': 2.0, 'x_scrapped_qty': 1.0,
            'x_balance_qty': 2.0,
            'x_reserved_qty': -100.0, 'x_after_reservation_qty': -98.0,
            'x_global_balance_qty': 20.0,
        })
        self.assertIn(('location_id', 'child_of', 2), quant_model.domain)
        self.assertIn(('location_id.usage', '=', 'internal'), quant_model.domain)
        self.assertIn(('x_rekaz_task', '=', False), quant_model.domain)
        self.assertIn(('company_id', 'in', [7]), quant_model.domain)
        self.assertIn(
            ('location_id', 'child_of', [90]),
            quant_model.domain[quant_model.domain.index('!') + 1:])
        self.assertIn(('x_rekaz_task', '!=', False), location_model.domain)

    def test_global_balance_field_is_live_and_created_before_summary_compute(self):
        api = Mock()
        api.find.return_value = [{'id': 25}]
        with (
            patch.object(inventory, 'field') as add_field,
            patch.object(inventory, 'install_summary_access'),
        ):
            inventory.install_summary_model(
                api, 'quantity', 'product_uom', warehouse_view_location_id=2)

        calls = [call for call in add_field.call_args_list
                 if call.args[2] in {'x_global_balance_qty', 'x_incoming_qty'}]
        global_calls = [call for call in calls if call.args[2] == 'x_global_balance_qty']
        self.assertEqual(len(global_calls), 2)
        self.assertLess(
            calls.index(global_calls[0]),
            next(index for index, call in enumerate(calls)
                 if call.args[2] == 'x_incoming_qty'))
        configured = global_calls[-1].kwargs
        self.assertFalse(configured['store'])
        self.assertIn("'location_id', 'child_of', 2", configured['compute'])
        self.assertIn("'x_rekaz_task', '=', False", configured['compute'])

    def test_material_summary_backfills_rows_for_moves_and_live_quants(self):
        api = Mock()
        api.call.side_effect = [
            [{'x_rekaz_task': [1, 'task'], 'product_id': [2, 'move product']}],
            [{'x_rekaz_task': [1, 'task'], 'product_id': [3, 'quant-only product']}],
            [],
            [21, 22],
            [{'x_task_id': [1, 'task'], 'x_product_id': [2, 'move product']},
             {'x_task_id': [1, 'task'], 'x_product_id': [3, 'quant-only product']}],
        ]
        inventory.ensure_summary_rows(api, 7)
        self.assertEqual(api.call.call_args_list[3].args,
                         (inventory.BALANCE_MODEL, 'create', [[
                             {'x_task_id': 1, 'x_product_id': 2, 'x_company_id': 7},
                             {'x_task_id': 1, 'x_product_id': 3, 'x_company_id': 7},
                         ]]))

    def test_material_summary_access_uses_odoo20_access_model_without_ir_rule(self):
        api = Mock(major_version=20)
        api.find.side_effect = lambda model, domain, fields: (
            [{'res_id': 8}] if model == 'ir.model.data' else [])
        inventory.install_summary_access(api, 25)
        writes = [call for call in api.call.call_args_list if call.args[0] == 'ir.access']
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[0].args[2][0]['operation'], 'cr')
        self.assertEqual(writes[0].args[2][0]['group_id'], 8)
        self.assertEqual(writes[1].args[2][0]['operation'], 'crud')
        self.assertIs(writes[1].args[2][0]['group_id'], False)
        self.assertIn('company_ids', writes[1].args[2][0]['domain'])
        self.assertFalse(any(call.args[0] == 'ir.rule' for call in api.call.call_args_list))

    def test_material_summary_access_uses_legacy_rule_only_before_odoo20(self):
        api = Mock(major_version=19)
        api.find.return_value = [{'res_id': 8}]
        inventory.install_summary_access(api, 25)
        api.ensure.assert_any_call(
            'ir.model.access', [('name', '=', 'rekaz_material_balance_25'),
                                ('model_id', '=', 25)],
            {'name': 'rekaz_material_balance_25', 'model_id': 25, 'group_id': 8,
             'perm_read': True, 'perm_create': True, 'perm_write': False,
             'perm_unlink': False})
        api.ensure.assert_any_call(
            'ir.rule', [('name', '=', 'rekaz_material_balance_25_company'),
                        ('model_id', '=', 25)],
            {'name': 'rekaz_material_balance_25_company', 'model_id': 25,
             'domain_force': "[('x_company_id','in',company_ids)]"})
        api.call.assert_not_called()

    def test_link_automation_populates_task_metadata_and_guards_native_flows(self):
        operation_types = {
            'internal_id': 30, 'outgoing_id': 20, 'scrap_id': 40,
            'warehouse_stock_id': 2, 'customer_location_id': 3,
            'scrap_location_id': 5,
            'dashboard_flow_ids': {
                'incoming': 101, 'outgoing': 102, 'scrapped': 103, 'returned': 104,
            },
        }
        code = inventory.picking_link_action_code(
            7, [20, 30, 40, 101, 102, 103, 104],
            {'x_rekaz_fault_no', 'x_rekaz_notification_no', 'x_rekaz_work_order'},
            operation_types, 'move_ids_without_package')
        compile(code, '<picking action>', 'exec')

        class Record:
            def __init__(self, **values):
                self.__dict__.update(values)

            def write(self, values):
                self.__dict__.update(values)

        task = Record(
            id=50, company_id=Record(id=7), project_id=Record(id=30),
            x_rekaz_stock_location=Record(id=90),
            x_rekaz_fault_no='F-1', x_rekaz_notification_no='N-1',
            x_rekaz_work_order='WO-1')
        move = Record(
            x_rekaz_task=Record(id=False), x_rekaz_project=Record(id=False),
            x_rekaz_direction='')
        picking = Record(
            x_rekaz_task=task, company_id=Record(id=7),
            x_rekaz_movement='incoming',
            picking_type_id=Record(id=30, code='internal', warehouse_id=Record(id=1)),
            location_id=Record(id=2), location_dest_id=Record(id=90),
            x_rekaz_project=Record(id=False), x_rekaz_ticket_no='',
            x_rekaz_work_order='', move_ids_without_package=[move])
        exec(code, {'records': [picking], 'UserError': ValueError})
        self.assertEqual(picking.x_rekaz_project, 30)
        self.assertEqual(picking.x_rekaz_ticket_no, 'F-1')
        self.assertEqual(picking.x_rekaz_work_order, 'WO-1')
        self.assertEqual(move.x_rekaz_task, 50)
        self.assertEqual(move.x_rekaz_project, 30)
        self.assertEqual(move.x_rekaz_direction, 'وارد للمعاملة')

        picking.x_rekaz_project = Record(id=30)
        move.x_rekaz_task = Record(id=50)
        move.x_rekaz_project = Record(id=30)
        picking.x_rekaz_movement = 'outgoing'
        picking.picking_type_id = Record(id=20, code='outgoing', warehouse_id=Record(id=1))
        picking.location_id = Record(id=90)
        picking.location_dest_id = Record(id=3)
        exec(code, {'records': [picking], 'UserError': ValueError})
        self.assertEqual(move.x_rekaz_direction, 'منصرف من المعاملة')

        picking.x_rekaz_project = Record(id=30)
        move.x_rekaz_task = Record(id=50)
        move.x_rekaz_project = Record(id=30)
        picking.x_rekaz_movement = 'returned'
        picking.picking_type_id = Record(id=30, code='internal', warehouse_id=Record(id=1))
        picking.location_id = Record(id=90)
        picking.location_dest_id = Record(id=2)
        exec(code, {'records': [picking], 'UserError': ValueError})
        self.assertEqual(move.x_rekaz_direction, 'مسترجع')

        picking.x_rekaz_project = Record(id=30)
        move.x_rekaz_task = Record(id=50)
        move.x_rekaz_project = Record(id=30)
        picking.x_rekaz_movement = 'outgoing'
        picking.picking_type_id = Record(id=20, code='outgoing', warehouse_id=Record(id=1))
        picking.location_id = Record(id=90)
        picking.location_dest_id = Record(id=4)
        with self.assertRaisesRegex(ValueError, 'لا يطابق حركة المعاملة'):
            exec(code, {'records': [picking], 'UserError': ValueError})

        picking.x_rekaz_project = Record(id=30)
        move.x_rekaz_task = Record(id=50)
        move.x_rekaz_project = Record(id=30)
        picking.x_rekaz_movement = 'scrapped'
        picking.picking_type_id = Record(id=40, code='outgoing', warehouse_id=Record(id=1))
        picking.location_id = Record(id=90)
        picking.location_dest_id = Record(id=5)
        exec(code, {'records': [picking], 'UserError': ValueError})
        self.assertEqual(move.x_rekaz_direction, 'تالف')
        picking.x_rekaz_movement = False
        with self.assertRaisesRegex(ValueError, 'تصنيف عملية الإتلاف'):
            exec(code, {'records': [picking], 'UserError': ValueError})
        picking.x_rekaz_movement = 'scrapped'
        picking.location_dest_id = Record(id=4)
        with self.assertRaisesRegex(ValueError, 'لا يطابق حركة المعاملة'):
            exec(code, {'records': [picking], 'UserError': ValueError})

    def test_dashboard_flow_sets_category_and_task_locations(self):
        operation_types = {
            'internal_id': 30, 'outgoing_id': 20, 'scrap_id': 40,
            'warehouse_stock_id': 2, 'customer_location_id': 3,
            'scrap_location_id': 5,
            'dashboard_flow_ids': {
                'incoming': 101, 'outgoing': 102, 'scrapped': 103, 'returned': 104,
            },
        }
        code = inventory.picking_link_action_code(
            7, [20, 30, 40, 101, 102, 103, 104],
            {'x_rekaz_fault_no', 'x_rekaz_notification_no', 'x_rekaz_work_order'},
            operation_types, 'move_ids_without_package')
        compile(code, '<dashboard picking action>', 'exec')

        class Record:
            def __init__(self, **values):
                self.__dict__.update(values)

            def write(self, values):
                self.__dict__.update(values)

        task = Record(
            id=50, company_id=Record(id=7), project_id=Record(id=30),
            x_rekaz_stock_location=Record(id=90),
            x_rekaz_fault_no='F-1', x_rekaz_notification_no='N-1',
            x_rekaz_work_order='WO-1')
        move = Record(
            x_rekaz_task=Record(id=False), x_rekaz_project=Record(id=False),
            x_rekaz_direction='')
        picking = Record(
            x_rekaz_task=task, company_id=Record(id=7), x_rekaz_movement=False,
            state='draft',
            picking_type_id=Record(id=101, code='internal'),
            location_id=Record(id=2), location_dest_id=Record(id=4),
            x_rekaz_project=Record(id=False), x_rekaz_ticket_no='',
            x_rekaz_work_order='', move_ids_without_package=[move])
        exec(code, {'records': [picking], 'UserError': ValueError})
        self.assertEqual(picking.x_rekaz_movement, 'incoming')
        self.assertEqual(picking.location_id.id, 2)
        self.assertEqual(picking.location_dest_id, 90)
        self.assertEqual(move.x_rekaz_task, 50)
        self.assertEqual(move.x_rekaz_direction, 'وارد للمعاملة')

        picking.x_rekaz_movement = 'returned'
        with self.assertRaisesRegex(ValueError, 'لا يطابق تصنيف حركة المعاملة'):
            exec(code, {'records': [picking], 'UserError': ValueError})
        unlinked = Record(
            x_rekaz_task=False, picking_type_id=Record(id=101), state='done')
        with self.assertRaisesRegex(ValueError, 'اربط حركة المخزون بمعاملة'):
            exec(code, {'records': [unlinked], 'UserError': ValueError})

    def test_task_location_action_is_idempotent_and_creates_per_task_locations(self):
        task_fields = {
            'name': {}, 'x_rekaz_fault_no': {},
            'x_rekaz_notification_no': {}, 'x_rekaz_work_order': {},
        }
        code = inventory.task_location_action_code(7, 2, [5, 6], task_fields)
        compile(code, '<task location action>', 'exec')
        self.assertIn("task.write({'x_rekaz_stock_location': location.id})", code)
        self.assertIn("'رقم العطل - ' + fault_number", code)
        self.assertIn("'أمر العمل - ' + work_order_number", code)
        api = Mock(company_id=7)
        api.find.return_value = [{'id': 80, 'location_id': [1, 'WH'], 'usage': 'view'}]
        api.call.side_effect = [
            task_fields,
            [
                {'id': 10, 'name': 'Transaction 10', 'x_rekaz_fault_no': 'F-10',
                 'x_rekaz_notification_no': False, 'x_rekaz_work_order': 'WO-10',
                 'project_id': [5, 'Ops'],
                 'x_rekaz_stock_location': False},
                {'id': 11, 'name': 'Transaction 11', 'x_rekaz_fault_no': False,
                 'x_rekaz_notification_no': False, 'x_rekaz_work_order': 'WO-11',
                 'project_id': [5, 'Ops'],
                 'x_rekaz_stock_location': False},
            ],
            [],
            [101, 102],
            [
                {'id': 101, 'name': 'رقم العطل - F-10',
                 'x_rekaz_task': [10, 'Transaction 10'],
                 'location_id': [1, 'WH'], 'company_id': [7, 'Company'], 'usage': 'internal'},
                {'id': 102, 'name': 'أمر العمل - WO-11',
                 'x_rekaz_task': [11, 'Transaction 11'],
                 'location_id': [1, 'WH'], 'company_id': [7, 'Company'], 'usage': 'internal'},
            ],
            True,
            True,
            [{'id': 10, 'x_rekaz_stock_location': [101, 'رقم العطل - F-10']},
             {'id': 11, 'x_rekaz_stock_location': [102, 'أمر العمل - WO-11']}],
        ]
        projects, root_id = inventory.install_task_stock_locations(
            api, {'lot_stock_id': [2, 'Stock'], 'view_location_id': [1, 'WH']})
        self.assertEqual(projects, [5])
        self.assertEqual(root_id, 1)
        create_values = api.call.call_args_list[3].args[2][0]
        self.assertEqual([item['x_rekaz_task'] for item in create_values], [10, 11])
        self.assertEqual([item['name'] for item in create_values],
                         ['رقم العطل - F-10', 'أمر العمل - WO-11'])
        self.assertEqual([item['location_id'] for item in create_values], [1, 1])
        self.assertTrue(all(item['usage'] == 'internal' for item in create_values))
        self.assertEqual(api.call.call_args_list[5].args,
                         ('project.task', 'write', [[10], {'x_rekaz_stock_location': 101}]))
        self.assertEqual(api.call.call_args_list[6].args,
                         ('project.task', 'write', [[11], {'x_rekaz_stock_location': 102}]))

    def test_transaction_location_name_prefers_fault_then_work_order_then_task_name(self):
        self.assertEqual(inventory.transaction_location_name({
            'name': 'Transaction title', 'x_rekaz_fault_no': 'F-1',
            'x_rekaz_work_order': 'WO-1',
        }), 'رقم العطل - F-1')
        self.assertEqual(inventory.transaction_location_name({
            'name': 'Transaction title', 'x_rekaz_fault_no': '',
            'x_rekaz_notification_no': '', 'x_rekaz_work_order': 'WO-1',
        }), 'أمر العمل - WO-1')
        self.assertEqual(inventory.transaction_location_name({
            'name': 'Transaction title', 'x_rekaz_work_order': '',
        }), 'Transaction title')
        with self.assertRaisesRegex(ValueError, 'no fault number'):
            inventory.transaction_location_name({'name': '', 'x_rekaz_work_order': ''})

    def test_setup_renames_existing_transaction_locations_to_business_identifiers(self):
        api = Mock(company_id=7)
        api.find.return_value = [{
            'id': 80, 'location_id': [1, 'WH'], 'usage': 'view',
        }]
        task_fields = {
            'name': {}, 'project_id': {}, 'x_rekaz_stock_location': {},
            'x_rekaz_fault_no': {}, 'x_rekaz_notification_no': {},
            'x_rekaz_work_order': {},
        }
        api.call.side_effect = [
            task_fields,
            [{
                'id': 10, 'name': 'Transaction title',
                'project_id': [5, 'Operations'],
                'x_rekaz_stock_location': [90, 'RKAZ-TASK-10'],
                'x_rekaz_fault_no': False,
                'x_rekaz_notification_no': False,
                'x_rekaz_work_order': 'WO-10',
            }],
            [{
                'id': 90, 'name': 'RKAZ-TASK-10',
                'x_rekaz_task': [10, 'Transaction title'],
                'location_id': [80, 'Transactions'],
                'company_id': [7, 'Company'], 'usage': 'internal',
            }],
            True,
            [{'id': 10, 'x_rekaz_stock_location': [90, 'أمر العمل - WO-10']}],
        ]

        projects, root_id = inventory.install_task_stock_locations(
            api, {'lot_stock_id': [2, 'Stock'], 'view_location_id': [1, 'WH']})

        self.assertEqual((projects, root_id), ([5], 1))
        self.assertIn(
            ('stock.location', 'write',
             [[90], {'location_id': 1, 'name': 'أمر العمل - WO-10'}]),
            [call.args for call in api.call.call_args_list])
        self.assertFalse(any(
            call.args[0] == 'stock.location'
            and call.args[1] == 'search_read'
            and call.args[2][0] == [('location_id', '=', 80)]
            for call in api.call.call_args_list))

    def test_task_location_automation_renames_linked_location(self):
        code = inventory.task_location_action_code(
            7, 80, [5], {
                'name': {}, 'x_rekaz_fault_no': {},
                'x_rekaz_notification_no': {}, 'x_rekaz_work_order': {},
            })

        class Record:
            def __init__(self, **values):
                self.__dict__.update(values)

            def write(self, values):
                self.__dict__.update(values)

        location = Record(
            id=90, name='RKAZ-TASK-10',
            location_id=Record(id=80), usage='internal')
        task = Record(
            id=10, name='Transaction title',
            company_id=Record(id=7), project_id=Record(id=5),
            x_rekaz_fault_no=False, x_rekaz_notification_no=False,
            x_rekaz_work_order='WO-10',
            x_rekaz_stock_location=Record(id=90))

        class Locations:
            def search(self, domain, limit):
                class RecordSet(list):
                    def __getattr__(self, name):
                        return getattr(self[0], name)

                return RecordSet([location])

        exec(code, {
            'records': [task],
            'env': {'stock.location': Locations()},
            'UserError': ValueError,
        })
        self.assertEqual(location.name, 'أمر العمل - WO-10')
