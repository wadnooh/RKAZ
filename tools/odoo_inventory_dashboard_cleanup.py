"""Hide duplicate damage cards from the inventory overview only."""
import ast
import odoo_project_inventory as inventory


def cleanup(api):
    rows = api.call('stock.picking.type', 'search_read', [[
        ('company_id', '=', api.company_id),
        ('warehouse_id', '=', inventory.REKAZ_WAREHOUSE_ID),
        ('active', '=', True),
    ]], fields=['id', 'name', 'x_rekaz_origin'], limit=False,
        context={'lang': 'ar_001'})
    canonical = [r for r in rows if r.get('x_rekaz_origin') ==
                 'rekaz-main:inventory:dashboard:scrapped']
    if len(canonical) != 1:
        raise ValueError('Expected one configured damage flow; no dashboard changes made')
    keep = canonical[0]['id']
    hidden = sorted(r['id'] for r in rows if r['id'] != keep
                    and (r['name'] or '').strip() == 'التالف')
    if not hidden:
        print('No duplicate damage cards found. Nothing changed.')
        return 0
    refs = api.call('ir.model.data', 'search_read', [[
        ('module', '=', 'stock'), ('name', '=', 'stock_picking_type_action'),
        ('model', '=', 'ir.actions.act_window'),
    ]], fields=['res_id'], limit=2)
    if len(refs) != 1:
        raise ValueError('Inventory overview action is missing or ambiguous')
    action_id = refs[0]['res_id']
    action = api.call('ir.actions.act_window', 'read', [[action_id]], fields=['domain'])[0]
    raw = action.get('domain') or '[]'
    domain = ast.literal_eval(raw) if isinstance(raw, str) else raw
    if not isinstance(domain, (list, tuple)):
        raise ValueError('Unsupported inventory overview filter; nothing changed')
    domain = list(domain)
    exclusion = ('id', 'not in', hidden)
    if exclusion not in domain:
        domain.append(exclusion)
        api.call('ir.actions.act_window', 'write', [[action_id], {'domain': repr(domain)}])
    check = api.call('ir.actions.act_window', 'read', [[action_id]], fields=['domain'])[0]['domain']
    checked = ast.literal_eval(check) if isinstance(check, str) else check
    if repr(checked) != repr(domain):
        raise ValueError('Dashboard filter verification failed')
    print('Duplicate damage cards hidden from overview: ' + ','.join(map(str, hidden)))
    print('Configured damage flow retained: ' + str(keep) + '. No operation types or stock records deleted or archived.')
    return 0
