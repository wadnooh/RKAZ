import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import odoo_inventory_live_update as live


class API:
    company_id = 7

    def __init__(self, missing_product=False):
        self.rows = {}
        self.writes = []
        names = ['x_rekaz_in_qty', 'x_rekaz_out_qty', 'x_rekaz_return_qty', 'x_rekaz_damage_qty']
        balances = ['x_incoming_qty', 'x_outgoing_qty', 'x_returned_qty', 'x_scrapped_qty',
                    'x_balance_qty', 'x_reserved_qty', 'x_after_reservation_qty', 'x_global_balance_qty']
        for model, fields in [('product.template', names), (live.inventory.BALANCE_MODEL, balances)]:
            for name in fields:
                key = len(self.rows) + 1
                self.rows[key] = dict(id=key, model=model, name=name, store=True,
                                      compute=('existing-product-compute' if model == 'product.template' else False),
                                      depends='', readonly=False)
        if missing_product:
            self.rows[1]['compute'] = False

    def call(self, model, method, args, **kwargs):
        if model == 'stock.move' and method == 'fields_get':
            return {'quantity': {'type': 'float'}, 'product_uom_id': {'type': 'many2one'}}
        if model == 'stock.warehouse':
            return [{'view_location_id': [20, 'Warehouse']}]
        assert model == 'ir.model.fields', 'Setup must not write stock records'
        if method == 'search_read':
            selected = args[0][0][2]
            return [dict(row) for row in self.rows.values() if row['model'] == selected]
        if method == 'write':
            self.writes.append(args)
            for key in args[0]:
                self.rows[key].update(args[1])
            return True
        if method == 'read':
            return [dict(self.rows[key]) for key in args[0]]
        raise AssertionError(method)


def test_repairs_missing_summary_without_stock_writes():
    api = API()
    with patch.object(live.inventory, 'ensure_summary_rows') as ensure:
        assert live.install(api) == 0
    ensure.assert_called_once_with(api, 7)
    assert len(api.writes) == 12
    for row in api.rows.values():
        assert not row['store']
        assert row['readonly']
        if row['model'] == live.inventory.BALANCE_MODEL:
            assert "= env[" not in row['compute']
            assert "self.env['stock.quant']" in row['compute']
            assert row['compute'].count("    rec['") == 1
            assert "    rec['" + row['name'] + "']" in row['compute']
            assert "move.state != 'done'" in row['compute']
            assert "('location_id', 'child_of', 20)" in row['compute']
        else:
            assert row['compute'] == 'existing-product-compute'


def test_missing_product_formula_stops_before_any_write():
    api = API(missing_product=True)
    with pytest.raises(ValueError, match='Product computation is missing'):
        live.install(api)
    assert not api.writes


def test_summary_executes_with_odoo_self_only_and_counts_approved_moves():
    class Record(dict):
        pass
    class EmptyModel:
        def search(self, domain):
            return []
    class Records(list):
        env = {'stock.location': EmptyModel(), 'stock.quant': EmptyModel()}
    unit = NS(id=1)
    product = NS(id=12, uom_id=unit)
    def move(state, flow, amount):
        return NS(product_id=product, state=state, quantity=amount,
                  product_uom_qty=amount, uom_id=unit,
                  picking_id=NS(x_rekaz_movement=flow))
    r = Record()
    r.x_product_id = product
    r.x_company_id = NS(id=7)
    r.x_task_id = NS(x_rekaz_material_moves=[move('done', 'incoming', 8),
                                           move('done', 'outgoing', 3),
                                           move('cancel', 'outgoing', 10),
                                           move('draft', 'incoming', 2)],
                      x_rekaz_material_quants=[NS(product_id=product, quantity=5)])
    exec(live.inventory.summary_compute_code('quantity', 'uom_id', 20), {'self': Records([r])})
    assert r['x_incoming_qty'] == 8
    assert r['x_outgoing_qty'] == 3
    assert r['x_balance_qty'] == 5
    assert r['x_reserved_qty'] == -2
    assert r['x_after_reservation_qty'] == 3
