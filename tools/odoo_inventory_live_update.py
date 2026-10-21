"""Refresh material indicators from native movements; never alter stock moves."""
import odoo_project_inventory as inventory


def install(api):
    product_names = ['x_rekaz_in_qty', 'x_rekaz_out_qty',
                     'x_rekaz_return_qty', 'x_rekaz_damage_qty']
    balance_names = ['x_incoming_qty', 'x_outgoing_qty', 'x_returned_qty',
                     'x_scrapped_qty', 'x_balance_qty', 'x_reserved_qty',
                     'x_after_reservation_qty', 'x_global_balance_qty']
    moves = api.call('stock.move', 'fields_get', [], attributes=['type', 'relation'])
    qty = next((name for name in ('quantity', 'quantity_done') if name in moves), None)
    unit = next((name for name in ('product_uom', 'product_uom_id', 'uom_id')
                 if name in moves), None)
    if not qty or not unit:
        raise ValueError('Unsupported stock move quantity/unit schema; no settings changed')
    warehouses = api.call('stock.warehouse', 'search_read', [[
        ('id', '=', inventory.REKAZ_WAREHOUSE_ID), ('company_id', '=', api.company_id),
    ]], fields=['view_location_id'], limit=2)
    if len(warehouses) != 1 or not warehouses[0]['view_location_id']:
        raise ValueError('Configured Rekaz warehouse is absent in this company; no settings changed')
    location_id = warehouses[0]['view_location_id'][0]
    balance_compute = inventory.summary_compute_code(qty, unit, location_id)
    compile(balance_compute, '<material-summary>', 'exec')
    balance_depends = inventory.summary_dependencies(qty, unit)
    prepared = []
    for model, names in [('product.template', product_names),
                         (inventory.BALANCE_MODEL, balance_names)]:
        rows = api.call('ir.model.fields', 'search_read', [[
            ('model', '=', model), ('name', 'in', names),
        ]], fields=['id', 'name', 'compute', 'depends', 'store'], limit=False)
        if len(rows) != len(names) or {r['name'] for r in rows} != set(names):
            raise ValueError('Existing material indicators are missing or duplicated: ' + model)
        if model == 'product.template' and any(not r['compute'] for r in rows):
            raise ValueError('Product computation is missing; no settings changed: ' + model)
        if model == inventory.BALANCE_MODEL:
            missing = [r['name'] for r in rows if not r['compute']]
            if missing:
                print('Repairing missing summary computations: ' + ', '.join(missing))
        prepared.append((model, rows))
    for model, rows in prepared:
        # Nonstored values always read the latest native move/quant state,
        # including cancellations, corrections and changed transaction links.
        # Preserve the existing computation, company filters and UOM conversions.
        ids = [r['id'] for r in rows]
        expected = {}
        for row in rows:
            values = {'store': False, 'readonly': True}
            if model == inventory.BALANCE_MODEL:
                values.update(compute=inventory.summary_compute_code(qty, unit, location_id, target=row['name']), depends=balance_depends)
            expected[row['id']] = values.get('compute', row['compute'])
            api.call('ir.model.fields', 'write', [[row['id']], values])
        check = api.call('ir.model.fields', 'read', [ids],
                         fields=['id', 'compute', 'store', 'readonly'])
        before = expected
        if len(check) != len(ids) or any(
                r['store'] or not r['readonly'] or r['compute'] != before[r['id']]
                for r in check):
            raise ValueError('Live indicator configuration could not be verified: ' + model)
        print('Live material indicators verified: ' + model)
    # Install missing readonly summary rows using the existing supported path.
    inventory.ensure_summary_rows(api, api.company_id)
    print('Live indicators enabled. Approved movements update actual quantities; '
          'drafts remain pending. Reopen/refresh an already-open screen to see another user\'s changes. '
          'No transfers validated, and no physical stock quantities changed by setup.')
    return 0
