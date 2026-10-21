"""Install native transaction stock flows and link stock records to Rekaz tasks."""
import ast
import math
import time
from xml.etree import ElementTree as ET
import rekaz_odoo_sync as sync

REKAZ_WAREHOUSE_ID = 1
TASK_PICKINGS_FIELD = 'x_rekaz_material_pickings'
TASK_QUANTS_FIELD = 'x_rekaz_material_quants'
TASK_INBOUND_FIELD = 'x_rekaz_material_inbound'
TASK_OUTBOUND_FIELD = 'x_rekaz_material_outbound'
TASK_RETURNS_FIELD = 'x_rekaz_material_returns'
TASK_SCRAPS_FIELD = 'x_rekaz_material_scraps'
TASK_BALANCES_FIELD = 'x_rekaz_material_balances'
BALANCE_MODEL = 'x_rekaz_task_material_balance'


def move_schema(fields, picking_fields):
    unit = next((name for name in ('product_uom', 'product_uom_id', 'uom_id') if name in fields), None)
    moves = next((name for name in ('move_ids', 'move_ids_without_package') if name in picking_fields), None)
    if not unit or not moves:
        raise ValueError('Unsupported stock unit/move schema; no inventory imported')
    description = next((name for name in ('name', 'description_picking') if name in fields), None)
    return unit, moves, description


def source_units(api, unit_map, source_codes):
    """Preserve source package counts as independent units, without conversions."""
    schema = api.call('uom.uom', 'fields_get', [], attributes=['type'])
    for code in sorted(set(source_codes) & {'KIT', 'ROL', 'DR'}):
        if len(unit_map.get(code.casefold(), [])) == 1:
            continue
        name = 'ركاز / ' + code
        found_ids = api.call(
            'uom.uom', 'search', [[('name', '=', name)]], limit=2)
        if len(found_ids) > 1:
            raise ValueError('Duplicate source measurement unit: ' + code)
        if found_ids:
            uid = found_ids[0]
        else:
            values = {'name': name, 'active': True}
            if 'category_id' in schema:
                category = api.ensure('uom.category', [('name', '=', name)], {'name': name})
                values.update(category_id=category, uom_type='reference', factor=1.0, rounding=0.01)
            elif {'relative_uom_id', 'relative_factor'} <= set(schema):
                values.update(relative_uom_id=False, relative_factor=1.0)
            else:
                raise ValueError('Unsupported unit schema; source quantities unchanged')
            uid = api.call('uom.uom', 'create', [values])
        unit_map[code.casefold()] = [uid]


def read_units_by_names(api, names):
    names = sorted(set(name for name in names if name), key=str.casefold)
    if not names:
        return []
    schema = api.call('uom.uom', 'fields_get', [], attributes=['type'])
    if 'name' not in schema:
        raise ValueError('The Odoo measurement unit model has no readable name field')
    read_fields = ['name']
    if 'rounding' in schema:
        read_fields.append('rounding')
    ids = api.call(
        'uom.uom', 'search',
        [[('active', '=', True), ('name', 'in', names)]],
        limit=False)
    units = []
    for unit_id in ids:
        try:
            rows = api.call('uom.uom', 'read', [[unit_id]], fields=read_fields)
        except (ValueError, sync.ReadTransportFailure) as exc:
            # Some upgraded databases advertise the legacy rounding field but
            # reject its read. Retry only that optional projection, never writes.
            if 'rounding' not in read_fields or 'reason=invalid_field' not in str(exc):
                raise
            read_fields = ['name']
            rows = api.call('uom.uom', 'read', [[unit_id]], fields=read_fields)
        if len(rows) != 1 or rows[0].get('id') != unit_id:
            raise ValueError('Could not verify measurement unit record ' + str(unit_id))
        units.extend(rows)
    return units


def source_unit_names(source_names, aliases):
    names = set()
    for source_name in source_names:
        source_name = str(source_name or '').strip()
        if not source_name:
            continue
        names.add(source_name)
        names.update(name for name in aliases.get(source_name, ()) if name)
        if source_name in {'KIT', 'ROL', 'DR'}:
            names.add('ركاز / ' + source_name)
    return sorted(names, key=str.casefold)


def create_product(api, values):
    domain = [('x_rekaz_origin', '=', values['x_rekaz_origin']),
              ('company_id', '=', api.company_id)]
    for attempt in range(3):
        try:
            return api.call('product.product', 'create', [values])
        except ValueError as exc:
            # Retry only a confirmed database lock fault, never an uncertain
            # network failure or validation/permission error.
            if 'error_class=psycopg2.errors.LockNotAvailable' not in str(exc) or attempt == 2:
                raise
            print('Inventory product lock: waiting before checking source identity', flush=True)
            time.sleep(10 * (attempt + 1))
            found = api.find('product.product', domain, ['id', 'uom_id'])
            if len(found) > 1:
                raise ValueError('Duplicate product source identity')
            if found:
                if found[0]['uom_id'][0] != values['uom_id']:
                    raise ValueError('Product unit mismatch; refusing to change quantities')
                return found[0]['id']


def direction(value):
    value = str(value or '').strip()
    if value in ('منصرف للمعاملة', 'صرف عهدة', 'إرجاع للكهرباء', 'إرجاع للمجمعة'):
        return 'outgoing'
    if value in ('إرجاع عهدة', 'وارد من الكهرباء', 'وارد من مشتريات خارجية', 'وارد من موقع العمل', 'وارد مواد موردة من مقاول', 'رصيد افتتاحي'):
        return 'incoming'
    raise ValueError('Unknown inventory transaction type')


def movement_label(value):
    value = str(value or '').strip()
    direction(value)
    if value.startswith('إرجاع') or value == 'وارد من موقع العمل':
        return 'مرتجع'
    return 'وارد' if direction(value) == 'incoming' else 'منصرف'


def operation_key(value):
    if value in ('إرجاع عهدة', 'وارد من موقع العمل'):
        return 'return'
    if value in ('إرجاع للكهرباء', 'إرجاع للمجمعة'):
        raise ValueError('Supplier returns require an explicit supplier-return operation')
    return direction(value)


def audit_warehouses(api):
    warehouse_fields = api.call(
        'stock.warehouse', 'fields_get', [], attributes=['type'])
    requested_fields = [
        name for name in (
            'id', 'name', 'code', 'company_id', 'lot_stock_id',
            'view_location_id', 'active', 'in_type_id', 'out_type_id',
            'int_type_id')
        if name in warehouse_fields or name == 'id'
    ]
    warehouses = api.call(
        'stock.warehouse', 'search_read',
        [[('company_id', '=', api.company_id)]],
        fields=requested_fields, limit=False,
        context={'active_test': False})
    type_fields = api.call(
        'stock.picking.type', 'fields_get', [], attributes=['type'])
    requested_type_fields = [
        name for name in ('id', 'name', 'code', 'company_id', 'warehouse_id', 'active')
        if name in type_fields or name == 'id'
    ]
    operation_types = api.call(
        'stock.picking.type', 'search_read',
        [[('company_id', '=', api.company_id)]],
        fields=requested_type_fields, limit=False,
        context={'active_test': False})

    audited = []
    for warehouse in warehouses:
        warehouse_id = warehouse['id']
        view_relation = warehouse.get('view_location_id')
        view_location_id = _relation_id(view_relation)
        types = [
            operation for operation in operation_types
            if _relation_id(operation.get('warehouse_id')) == warehouse_id
        ]
        operation_by_id = {operation['id']: operation for operation in operation_types}
        configured_operation_ids = {
            key: _relation_id(warehouse.get(field_name))
            for key, field_name in (
                ('incoming', 'in_type_id'),
                ('outgoing', 'out_type_id'),
                ('internal', 'int_type_id'),
            )
            if field_name in requested_fields
        }
        configured_operation_refs = {}
        for key, operation_id in configured_operation_ids.items():
            operation = operation_by_id.get(operation_id)
            configured_operation_refs[key] = (
                {
                    'id': operation_id,
                    'name': operation.get('name'),
                    'code': operation.get('code'),
                    'active': operation.get('active'),
                    'warehouse_id': _relation_id(operation.get('warehouse_id')),
                    'company_id': _relation_id(operation.get('company_id')),
                }
                if operation else {'id': operation_id, 'found': False}
            )
        type_ids = [operation['id'] for operation in types]
        picking_count = (
            api.call(
                'stock.picking', 'search_count',
                [[('company_id', '=', api.company_id),
                  ('picking_type_id', 'in', type_ids)]],
                context={'active_test': False})
            if type_ids else 0
        )
        stock_lines = []
        if view_location_id:
            quants = api.call(
                'stock.quant', 'search_read',
                [[('company_id', '=', api.company_id),
                  ('location_id', 'child_of', view_location_id),
                  ('location_id.usage', '=', 'internal')]],
                fields=['product_id', 'location_id', 'quantity',
                        'reserved_quantity'], limit=False)
            by_product = {}
            for quant in quants:
                quantity = float(quant.get('quantity') or 0.0)
                reserved = float(quant.get('reserved_quantity') or 0.0)
                if abs(quantity) <= 1e-9 and abs(reserved) <= 1e-9:
                    continue
                product = quant.get('product_id')
                location = quant.get('location_id')
                product_id = _relation_id(product)
                key = product_id or str(product)
                line = by_product.setdefault(key, {
                    'product_id': product_id,
                    'product': product[1] if isinstance(product, (list, tuple)) else str(product or ''),
                    'quantity': 0.0,
                    'reserved_quantity': 0.0,
                    'location_ids': set(),
                })
                line['quantity'] += quantity
                line['reserved_quantity'] += reserved
                location_id = _relation_id(location)
                if location_id:
                    line['location_ids'].add(location_id)
            stock_lines = [
                {**line, 'location_ids': sorted(line['location_ids'])}
                for line in sorted(
                    by_product.values(),
                    key=lambda value: (str(value['product']).casefold(),
                                       str(value['product_id'])))
            ]
        audited.append({
            'id': warehouse_id,
            'name': warehouse.get('name'),
            'code': warehouse.get('code'),
            'active': warehouse.get('active'),
            'stock_location_id': _relation_id(warehouse.get('lot_stock_id')),
            'view_location_id': view_location_id,
            'operation_types': [{
                'id': operation['id'],
                'name': operation.get('name'),
                'code': operation.get('code'),
                'active': operation.get('active'),
            } for operation in types],
            'configured_default_operation_types': configured_operation_refs,
            'picking_count': picking_count,
            'nonzero_internal_stock': stock_lines,
        })
    return {
        'company_id': api.company_id,
        'warehouse_count': len(audited),
        'warehouses': audited,
        'archive_safe_by_basic_checks': all(
            not row['picking_count'] and not row['nonzero_internal_stock']
            for row in audited if row['id'] != REKAZ_WAREHOUSE_ID
        ),
        'note': (
            'Read-only audit only. No warehouse, operation, stock, or picking '
            'was changed. Any picking history, including cancelled records, '
            'blocks automatic archiving.'
        ),
    }


def warehouse(api, warehouse_id=REKAZ_WAREHOUSE_ID):
    found = api.call('stock.warehouse', 'search_read',
                     [[('company_id', '=', api.company_id)]],
                     fields=['id', 'name', 'code', 'lot_stock_id',
                             'view_location_id', 'active'],
                     limit=False, context={'active_test': False})
    active = [row for row in found if row.get('active')]
    if len(active) != 1:
        names = ', '.join(
            str(row.get('id')) + ':' + str(row.get('name') or '')
            + (' [active]' if row.get('active') else ' [archived]')
            for row in active)
        raise ValueError(
            'Expected exactly one active warehouse for the selected company; found '
            + str(len(active)) + (' (' + names + ')' if names else '')
            + '; no warehouse was created or modified')
    if active[0].get('id') != warehouse_id:
        raise ValueError(
            'The single active company warehouse ID is ' + str(active[0].get('id'))
            + ', not configured Rekaz warehouse ID ' + str(warehouse_id)
            + '; no warehouse was created or modified')
    if not active[0].get('lot_stock_id') or not active[0].get('view_location_id'):
        raise ValueError('Configured Rekaz warehouse has no stock or view location')
    return active[0]


def operation_types(api, warehouse_id):
    configured = api.call('stock.warehouse', 'read', [[warehouse_id]],
                          fields=['in_type_id', 'out_type_id', 'int_type_id', 'lot_stock_id'])[0]
    result = {}
    for code, field_name in [('incoming', 'in_type_id'), ('outgoing', 'out_type_id'),
                             ('internal', 'int_type_id')]:
        relation = configured.get(field_name)
        if not relation:
            raise ValueError('Warehouse operation is not configured: ' + field_name)
        operation_id = relation[0] if isinstance(relation, (list, tuple)) else relation
        rows = api.call('stock.picking.type', 'search_read',
                        [[('id', '=', operation_id)]],
                        fields=['id', 'name', 'company_id', 'warehouse_id', 'code', 'active',
                                'default_location_src_id', 'default_location_dest_id',
                                'return_picking_type_id'], limit=False,
                        context={'active_test': False})
        if len(rows) != 1:
            raise ValueError(
                'Configured warehouse operation is inaccessible, inactive or '
                'incompatible: ' + field_name + ' id=' + str(operation_id)
                + ' record_not_found; no inventory changes were made')
        operation = rows[0]
        if (_relation_id(operation.get('company_id')) != api.company_id
                or _relation_id(operation.get('warehouse_id')) != warehouse_id
                or operation.get('code') != code):
            raise ValueError(
                'Configured warehouse operation is incompatible: ' + field_name
                + ' id=' + str(operation_id)
                + ' name=' + str(operation.get('name') or '')
                + ' code=' + str(operation.get('code'))
                + ' active=' + str(operation.get('active'))
                + ' warehouse_id=' + str(_relation_id(operation.get('warehouse_id')))
                + ' company_id=' + str(_relation_id(operation.get('company_id')))
                + '; no inventory changes were made')
        if not rows[0].get('default_location_src_id') or not rows[0].get('default_location_dest_id'):
            raise ValueError('Configured warehouse operation locations are incomplete: ' + field_name)
        result[code] = rows[0]
    stock_location = configured.get('lot_stock_id')
    result['stock_location_id'] = stock_location[0] if isinstance(stock_location, (list, tuple)) else stock_location
    if not result['stock_location_id']:
        raise ValueError('Warehouse stock location is missing')
    scrap_types = api.call(
        'stock.picking.type', 'search_read',
        [[('company_id', '=', api.company_id), ('warehouse_id', '=', warehouse_id),
          ('code', '=', 'outgoing'), ('active', '=', True)]],
        fields=['name', 'default_location_src_id', 'default_location_dest_id'], limit=False)
    scrap_candidates = [row for row in scrap_types
                        if row.get('default_location_src_id')
                        and row['default_location_src_id'][0] == result['stock_location_id']
                        and row.get('default_location_dest_id')]
    destination_ids = list({row['default_location_dest_id'][0] for row in scrap_candidates})
    if destination_ids:
        locations = api.call(
            'stock.location', 'search_read',
            [[('id', 'in', destination_ids), ('usage', '=', 'inventory')]],
            fields=['id', 'usage'], limit=False)
        inventory_location_ids = {row['id'] for row in locations}
        scrap_candidates = [row for row in scrap_candidates
                            if row['default_location_dest_id'][0] in inventory_location_ids]
    if len(scrap_candidates) > 1:
        damage_terms = ('scrap', 'damage', 'damaged', 'تالف', 'خردة', 'إتلاف', 'اتلاف')
        named_candidates = [
            row for row in scrap_candidates
            if any(term in str(row.get('name') or '').casefold()
                   for term in damage_terms)
        ]
        if len(named_candidates) == 1:
            scrap_candidates = named_candidates
        elif len(named_candidates) > 1:
            scrap_candidates = named_candidates
    if len(scrap_candidates) > 1:
        routes = {
            (row['default_location_src_id'][0],
             row['default_location_dest_id'][0])
            for row in scrap_candidates
        }
        if len(routes) == 1:
            scrap_candidates = [min(scrap_candidates, key=lambda row: row['id'])]
    if len(scrap_candidates) != 1:
        candidates = ', '.join(
            str(row.get('id')) + ':' + str(row.get('name') or '')
            + ' -> ' + str(row.get('default_location_dest_id', ['', ''])[1])
            for row in scrap_candidates)
        raise ValueError(
            'Expected exactly one damage-specific warehouse operation routing WH/Stock '
            'to an inventory-loss location; found ' + str(len(scrap_candidates))
            + (' (' + candidates + ')' if candidates else '')
            + '; no damaged-material flow installed')
    result['scrap'] = scrap_candidates[0]
    result['scrap_location_id'] = result['scrap']['default_location_dest_id'][0]
    relation = result['outgoing'].get('return_picking_type_id')
    if relation:
        rid = relation[0] if isinstance(relation, (list, tuple)) else relation
        rows = api.call('stock.picking.type', 'search_read',
                        [[('id', '=', rid), ('company_id', '=', api.company_id), ('code', '=', 'incoming'),
                          ('warehouse_id', 'in', [False, warehouse_id]), ('active', '=', True)]],
                        fields=['default_location_src_id', 'default_location_dest_id'], limit=False)
        if len(rows) == 1 and rows[0].get('default_location_src_id') and rows[0].get('default_location_dest_id'):
            outgoing = result['outgoing']
            if (rows[0]['default_location_src_id'][0] == outgoing['default_location_dest_id'][0]
                    and rows[0]['default_location_dest_id'][0] == outgoing['default_location_src_id'][0]):
                result['return'] = rows[0]
    return result





def field(api, model, name, kind, label, **extra):
    mid = api.find('ir.model', [('model', '=', model)], ['id'])
    if len(mid) != 1:
        raise ValueError('Required model missing: ' + model)
    if extra.get('index') is True:
        metadata = api.call('ir.model.fields', 'fields_get', [['index']], attributes=['type'])
        if metadata.get('index', {}).get('type') == 'selection':
            extra['index'] = 'btree'
    if kind == 'many2one' and extra.get('required'):
        extra.setdefault('on_delete', 'restrict')
    try:
        api.ensure('ir.model.fields', [('model_id', '=', mid[0]['id']), ('name', '=', name)],
                   dict(model_id=mid[0]['id'], name=name, ttype=kind, field_description=label, state='manual', **extra))
    except ValueError as exc:
        raise ValueError(str(exc) + ' configuring=' + model + '.' + name) from None


def upsert(api, model, domain, values):
    rows = api.find(model, domain, ['id'])
    if len(rows) > 1:
        raise ValueError('Duplicate inventory configuration: ' + model + ' ' + str(domain))
    if rows:
        api.call(model, 'write', [[rows[0]['id']], values])
        return rows[0]['id']
    return api.call(model, 'create', [values])


def retire_owned_ui(api):
    views = api.find('ir.ui.view', [('name', '=', 'Rekaz materials project.project'),
                                    ('model', '=', 'project.project')], ['id'])
    if len(views) > 1:
        raise ValueError('Duplicate Rekaz project inventory views')
    if views:
        api.call('ir.ui.view', 'write', [[views[0]['id']], {'active': False}])
    stock_root = api.find('ir.model.data', [('module', '=', 'stock'), ('name', '=', 'menu_stock_root')], ['res_id'])
    if len(stock_root) != 1:
        raise ValueError('Inventory application menu missing')
    legacy_menus = []
    for name in ('حركات مستودع ركاز', 'حركات ركاز حسب النوع'):
        menus = api.find('ir.ui.menu', [('name', '=', name),
                                       ('parent_id', '=', stock_root[0]['res_id'])], ['id'])
        if len(menus) > 1:
            raise ValueError('Duplicate legacy Rekaz inventory menus')
        legacy_menus.extend(row['id'] for row in menus)
    if legacy_menus:
        api.call('ir.ui.menu', 'write', [legacy_menus, {'active': False}])


def install_task_operation_types(api, warehouse_id, operations):
    fields = api.call('stock.picking.type', 'fields_get', [], attributes=['type'])
    required = {'name', 'code', 'warehouse_id', 'default_location_src_id',
                'default_location_dest_id', 'sequence_code', 'sequence', 'active'}
    if not required <= set(fields):
        raise ValueError('Unsupported warehouse operation type schema; no dashboard flows installed')
    reverse_internal = dict(
        operations['internal'],
        default_location_src_id=operations['internal']['default_location_dest_id'],
        default_location_dest_id=operations['internal']['default_location_src_id'])
    specs = (
        ('incoming', 'الوارد', operations['internal'], 'RKIN', 20),
        ('outgoing', 'المنصرف', operations['outgoing'], 'RKOUT', 30),
        ('scrapped', 'التالف', operations['scrap'], 'RKDMG', 40),
        ('returned', 'المسترجع', operations.get('return') or reverse_internal, 'RKRET', 50),
    )
    standard_ids = {
        row['id'] for key, row in operations.items()
        if key in {'incoming', 'outgoing', 'internal', 'scrap', 'return'}
        and isinstance(row, dict) and row.get('id')
    }
    result = {}
    for movement, label, source, sequence_code, sequence in specs:
        origin = 'rekaz-main:inventory:dashboard:' + movement
        matches = api.find(
            'stock.picking.type',
            [('x_rekaz_origin', '=', origin), ('warehouse_id', '=', warehouse_id)],
            ['id'])
        if len(matches) > 1:
            raise ValueError('Duplicate Rekaz dashboard operation type: ' + label)
        if matches:
            operation_id = matches[0]['id']
            if operation_id in standard_ids:
                api.call('stock.picking.type', 'write',
                         [[operation_id], {'x_rekaz_origin': False}])
                matches = []
            else:
                api.call('stock.picking.type', 'write', [[operation_id], {
                    'name': label,
                    'default_location_src_id': source['default_location_src_id'][0],
                    'default_location_dest_id': source['default_location_dest_id'][0],
                    'sequence': sequence,
                    'active': True,
                }])
        if not matches:
            values = {
                'name': label,
                'code': 'outgoing' if movement in {'outgoing', 'scrapped'} else 'internal',
                'warehouse_id': warehouse_id,
                'default_location_src_id': source['default_location_src_id'][0],
                'default_location_dest_id': source['default_location_dest_id'][0],
                'sequence_code': sequence_code,
                'sequence': sequence,
                'active': True,
                'x_rekaz_origin': origin,
            }
            operation_id = api.call('stock.picking.type', 'create', [values])
        result[movement] = operation_id
    return result


def hide_legacy_inventory_dashboard_operations(api, warehouse_id,
                                              required_operation_ids=()):
    legacy_names = [
        'استلام وتوريد مواد للمستودع',
        'تحويلات داخلية',
        'صرف مواد للمشاريع والمعاملات',
        'مسترجع مواد من المشاريع والمعاملات',
        'إتلاف وخردة مواد تالفة',
    ]
    operations = api.call(
        'stock.picking.type', 'search_read',
        [[('company_id', '=', api.company_id), ('warehouse_id', '=', warehouse_id),
          ('name', 'in', legacy_names), ('active', '=', True)]],
        fields=['id', 'name'], limit=False,
        context={'lang': 'ar_001'})
    operation_ids = sorted({row['id'] for row in operations})
    if not operation_ids:
        return
    protected_ids = set(required_operation_ids)
    archive_ids = sorted(set(operation_ids) - protected_ids)
    action_data = api.find(
        'ir.model.data',
        [('module', '=', 'stock'), ('name', '=', 'stock_picking_type_action')],
        ['res_id'])
    if len(action_data) != 1:
        raise ValueError('Inventory overview action missing or ambiguous')
    action_id = action_data[0]['res_id']
    actions = api.call('ir.actions.act_window', 'read', [[action_id]], fields=['domain'])
    if len(actions) != 1:
        raise ValueError('Could not read inventory overview action domain')
    raw_domain = actions[0].get('domain')
    if not raw_domain:
        domain = []
    elif isinstance(raw_domain, str):
        try:
            domain = ast.literal_eval(raw_domain)
        except (SyntaxError, ValueError):
            raise ValueError('Inventory overview action has an unsupported domain') from None
    else:
        domain = raw_domain
    if not isinstance(domain, (list, tuple)):
        raise ValueError('Inventory overview action domain is not a list')
    excluded_ids = set(operation_ids)
    found_exclusion = False

    def merge_exclusion(parts):
        nonlocal found_exclusion
        result = []
        for part in parts:
            if isinstance(part, (list, tuple)) and len(part) == 3:
                if part[0] == 'id' and part[1] == 'not in':
                    found_exclusion = True
                    values = part[2]
                    if not isinstance(values, (list, tuple, set)):
                        raise ValueError('Inventory overview ID exclusion is unsupported')
                    excluded_ids.update(values)
                    result.append(('id', 'not in', sorted(excluded_ids)))
                else:
                    result.append(part)
            elif isinstance(part, list):
                result.append(merge_exclusion(part))
            else:
                result.append(part)
        return result

    updated_domain = merge_exclusion(list(domain))
    if not found_exclusion:
        exclusion = ('id', 'not in', sorted(excluded_ids))
        if updated_domain:
            updated_domain = ['&', exclusion, *updated_domain]
        else:
            updated_domain = [exclusion]
    if updated_domain != list(domain):
        api.call('ir.actions.act_window', 'write',
                 [[action_id], {'domain': repr(updated_domain)}])
    if archive_ids:
        api.call('stock.picking.type', 'write', [archive_ids, {'active': False}])


def task_stock_sign(tx_type):
    value = str(tx_type or '').strip()
    if value == 'منصرف للمعاملة':
        return 1
    if value in ('وارد من موقع العمل', 'إرجاع للمجمعة'):
        return -1
    return 0


def aggregate_task_stock(rows, task_lookup):
    balances = {}
    report = {
        'unlinked': [],
        'invalid': [],
        'invalid_items': [],
        'invalid_tasks': [],
        'unsupported_types': [],
        'unsupported_tasks': [],
        'central_receipts': [],
    }
    for row in rows:
        sign = task_stock_sign(row.get('tx_type'))
        task = match_task(row, task_lookup)
        # Confirmed workflow: electricity receipts enter the central warehouse;
        # a separate issue document is the only transfer into transaction stock.
        if str(row.get('tx_type') or '').strip() == 'وارد من الكهرباء':
            report['central_receipts'].append({
                'source_id': row.get('id'),
                'task_id': task['id'] if task else None,
                'item_no': str(row.get('item_no') or '').strip(),
            })
            continue
        if not sign:
            if task:
                item_no = str(row.get('item_no') or '').strip()
                report['unsupported_types'].append({
                    'task_id': task['id'],
                    'source_id': row.get('id'),
                    'tx_type': row.get('tx_type'),
                    'item_no': item_no or None,
                })
                if not item_no:
                    report['unsupported_tasks'].append(task['id'])
            continue
        if not task:
            report['unlinked'].append({
                'source_id': row.get('id'),
                'tx_type': row.get('tx_type'),
                'ticket_no': row.get('ticket_no'),
                'rekaz_code': row.get('rekaz_code'),
                'work_order': row.get('work_order'),
            })
            continue
        try:
            quantity = float(row.get('qty'))
        except (TypeError, ValueError):
            quantity = 0
        if not math.isfinite(quantity) or quantity <= 0:
            item_no = str(row.get('item_no') or '').strip()
            report['invalid'].append({
                'source_id': row.get('id'),
                'reason': 'invalid_quantity',
                'quantity': row.get('qty'),
            })
            if item_no:
                report['invalid_items'].append({
                    'task_id': task['id'], 'item_no': item_no,
                })
            else:
                report['invalid_tasks'].append(task['id'])
            continue
        item_no = str(row.get('item_no') or '').strip()
        if not item_no:
            report['invalid'].append({
                'source_id': row.get('id'),
                'reason': 'missing_item_number',
            })
            report['invalid_tasks'].append(task['id'])
            continue
        key = (task['id'], item_no.casefold())
        balance = balances.setdefault(key, {
            'task': task,
            'item_no': item_no,
            'quantity': 0.0,
            'source_ids': [],
        })
        balance['quantity'] += sign * quantity
        balance['source_ids'].append(row.get('id'))
    return balances, report


def _relation_id(value):
    return value[0] if isinstance(value, (list, tuple)) else value


def transaction_balance_is_uncertain(task_id, item_no, report):
    item_key = item_no.casefold()
    if task_id in report['unsupported_tasks'] or task_id in report['invalid_tasks']:
        return True
    return any(
        row['task_id'] == task_id and row['item_no'].casefold() == item_key
        for row in report['unsupported_types'] + report['invalid_items']
        if row.get('item_no'))


def migrate_current_transaction_stock(api, source):
    report = {
        'mode': 'transaction_current_stock_draft_transfers',
        'created': [],
        'existing': [],
        'already_correct': [],
        'shortages': [],
        'conflicts': [],
        'skipped': [],
        'unlinked': [],
        'invalid': [],
        'invalid_items': [],
        'invalid_tasks': [],
        'unsupported_types': [],
        'unsupported_tasks': [],
    }
    report_path = sync.Path(__file__).resolve().parents[1] / 'outputs/reportrek_transaction_stock_migration.json'
    with sync.m.snapshot(source) as conn:
        if not {'warehouse_items', 'warehouse_tx'} <= {
                row['name'] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}:
            raise ValueError('Source snapshot does not contain Rekaz warehouse tables')
        install(api)
        selected_warehouse = warehouse(api)
        task_lookup = task_index(conn, api)
        balances, unmatched = aggregate_task_stock(
            [dict(row) for row in conn.execute('SELECT * FROM warehouse_tx ORDER BY id')],
            task_lookup)
        report['unlinked'] = unmatched['unlinked']
        report['invalid'] = unmatched['invalid']
        report['invalid_items'] = unmatched['invalid_items']
        report['invalid_tasks'] = sorted(set(unmatched['invalid_tasks']))
        report['unsupported_types'] = unmatched['unsupported_types']
        report['unsupported_tasks'] = sorted(set(unmatched['unsupported_tasks']))
        report['central_receipts'] = unmatched['central_receipts']
        report['warehouse_id'] = selected_warehouse['id']
        report['source_snapshot'] = str(source)
        if not balances:
            sync.m.save_report(report_path, report)
            print(sync.m.dump(report))
            return 2 if (report['unlinked'] or report['invalid']
                         or report['unsupported_types']) else 0

        move_fields = api.call('stock.move', 'fields_get', [], attributes=['type'])
        picking_fields = api.call('stock.picking', 'fields_get', [], attributes=['type'])
        move_unit_field, move_list, description_field = move_schema(move_fields, picking_fields)
        product_fields = api.call('product.product', 'fields_get', [], attributes=['type'])
        aliases = {
            'EA': ('Units', 'Unit', 'الوحدات', 'وحدة'),
            'PC': ('Units', 'Unit', 'الوحدات', 'وحدة'),
            'عدد': ('Units', 'Unit', 'الوحدات', 'وحدة'),
            'M': ('m', 'Meter', 'Meters', 'متر'),
        }
        item_rows = {}
        for item in conn.execute('SELECT * FROM warehouse_items ORDER BY id'):
            code = str(item['item_no'] or '').strip().casefold()
            if code:
                item_rows.setdefault(code, []).append(dict(item))
        source_item_codes = {
            value['item_no'].casefold() for value in balances.values()
        }
        report['skipped'].extend(
            {'item_no': code, 'reason': 'ambiguous_source_item'}
            for code in sorted(source_item_codes)
            if len(item_rows.get(code, [])) > 1)
        wanted_units = [
            str(item_rows[code][0].get('unit') or '').strip()
            for code in source_item_codes if len(item_rows.get(code, [])) == 1
        ]
        candidate_unit_names = source_unit_names(wanted_units, aliases)
        units = read_units_by_names(api, candidate_unit_names)
        unit_map = {}
        for unit in units:
            unit_map.setdefault(unit['name'].strip().casefold(), []).append(unit['id'])
        source_units(api, unit_map, wanted_units)
        units = read_units_by_names(
            api, source_unit_names(wanted_units, aliases))
        unit_map = {}
        rounding_by_unit = {}
        for unit in units:
            unit_map.setdefault(unit['name'].strip().casefold(), []).append(unit['id'])
            # Missing precision must not permit an invented quantity tolerance.
            rounding_by_unit[unit['id']] = float(unit.get('rounding') or 0.0)
        prepared = {}
        for balance_key, balance in balances.items():
            task = balance['task']
            if transaction_balance_is_uncertain(task['id'], balance['item_no'], report):
                report['skipped'].append({
                    'task_id': task['id'],
                    'item_no': balance['item_no'],
                    'reason': 'unclassified_transaction_affects_balance',
                })
                continue
            quantity = balance['quantity']
            if quantity <= 1e-9:
                if quantity < -1e-9:
                    report['conflicts'].append({
                        'task_id': task['id'],
                        'item_no': balance['item_no'],
                        'reason': 'negative_transaction_net',
                        'quantity': quantity,
                        'source_ids': balance['source_ids'],
                    })
                continue
            code = balance['item_no'].casefold()
            matches = item_rows.get(code, [])
            if len(matches) != 1:
                if not matches:
                    report['skipped'].append({
                        'task_id': task['id'], 'item_no': balance['item_no'],
                        'reason': 'source_item_missing',
                    })
                continue
            item = matches[0]
            unit_name = str(item.get('unit') or '').strip()
            choices = set()
            for unit_name_candidate in (unit_name,) + aliases.get(unit_name, ()):
                choices.update(unit_map.get(unit_name_candidate.casefold(), []))
            if len(choices) != 1:
                report['skipped'].append({
                    'task_id': task['id'], 'item_no': balance['item_no'],
                    'reason': 'unmapped_unit', 'unit': unit_name,
                })
                continue
            unit_id = next(iter(choices))
            origin = 'rekaz-main:warehouse_items:' + str(item['id'])
            product_search_fields = ['id', 'uom_id']
            if 'is_storable' in product_fields:
                product_search_fields.append('is_storable')
            products = api.find(
                'product.product',
                [('x_rekaz_origin', '=', origin), ('company_id', '=', api.company_id)],
                product_search_fields)
            if len(products) > 1:
                raise ValueError('Duplicate product source identity: ' + balance['item_no'])
            if products:
                if _relation_id(products[0].get('uom_id')) != unit_id:
                    report['skipped'].append({
                        'task_id': task['id'], 'item_no': balance['item_no'],
                        'reason': 'product_unit_mismatch',
                    })
                    continue
                if 'is_storable' in product_fields and not products[0].get('is_storable'):
                    report['skipped'].append({
                        'task_id': task['id'], 'item_no': balance['item_no'],
                        'reason': 'product_is_not_storable',
                    })
                    continue
                product_id = products[0]['id']
            else:
                values = {
                    'name': item.get('item_name') or item.get('item_no'),
                    'default_code': item['item_no'],
                    'company_id': api.company_id,
                    'uom_id': unit_id,
                    'x_rekaz_origin': origin,
                }
                if 'is_storable' in product_fields:
                    values['is_storable'] = True
                product_id = create_product(api, values)
            location_id = _relation_id(task.get('x_rekaz_stock_location'))
            if not location_id:
                report['skipped'].append({
                    'task_id': task['id'], 'item_no': balance['item_no'],
                    'reason': 'transaction_stock_location_missing',
                })
                continue
            prepared[balance_key] = {
                **balance,
                'item': item,
                'product_id': product_id,
                'unit_id': unit_id,
                'rounding': rounding_by_unit[unit_id],
                'location_id': location_id,
            }

        if not prepared:
            sync.m.save_report(report_path, report)
            print(sync.m.dump(report))
            return 2 if (report['skipped'] or report['unlinked'] or report['invalid']
                         or report['unsupported_types'] or report['conflicts']) else 0

        product_ids = sorted({item['product_id'] for item in prepared.values()})
        stock_location_id = _relation_id(selected_warehouse['lot_stock_id'])
        location_ids = sorted({
            item['location_id'] for item in prepared.values()
        } | {stock_location_id})
        quants = api.call(
            'stock.quant', 'search_read',
            [[('company_id', '=', api.company_id),
              ('location_id', 'in', location_ids),
              ('product_id', 'in', product_ids)]],
            fields=['product_id', 'location_id', 'quantity', 'reserved_quantity'],
            limit=False)
        quantities = {}
        available = {}
        for quant in quants:
            product_id = _relation_id(quant.get('product_id'))
            location_id = _relation_id(quant.get('location_id'))
            quantity = float(quant.get('quantity') or 0)
            reserved = float(quant.get('reserved_quantity') or 0)
            key = (product_id, location_id)
            quantities[key] = quantities.get(key, 0.0) + quantity
            if location_id == stock_location_id:
                available[product_id] = available.get(product_id, 0.0) + quantity - reserved

        all_tasks = {}
        for items in task_lookup.values():
            all_tasks.update(items)
        existing_origin_prefix = 'rekaz-main:transaction-current:'
        existing = api.call(
            'stock.picking', 'search_read',
            [[('company_id', '=', api.company_id),
              ('x_rekaz_origin', '=like', existing_origin_prefix + '%')]],
            fields=['id', 'state', 'x_rekaz_origin', 'x_rekaz_task', move_list],
            limit=False)
        existing_task_ids = set()
        pending_demand = {}
        task_origin_by_id = {
            task_id: task.get('x_rekaz_origin')
            for task_id, task in all_tasks.items()
        }
        for picking in existing:
            if picking.get('state') in ('draft', 'confirmed', 'waiting', 'partially_available'):
                move_ids = picking.get(move_list) or []
                if move_ids:
                    moves = api.call(
                        'stock.move', 'read', [move_ids],
                        fields=['product_id', 'product_uom_qty', 'location_id'])
                    for move in moves:
                        if _relation_id(move.get('location_id')) != stock_location_id:
                            continue
                        product_id = _relation_id(move.get('product_id'))
                        pending_demand[product_id] = pending_demand.get(product_id, 0.0) + float(
                            move.get('product_uom_qty') or 0)
            task_id = _relation_id(picking.get('x_rekaz_task'))
            if not task_id:
                report['conflicts'].append({
                    'picking_id': picking['id'],
                    'reason': 'existing_migration_without_task',
                })
                continue
            expected_origin = existing_origin_prefix + str(task_origin_by_id.get(task_id) or '')
            if picking.get('x_rekaz_origin') != expected_origin:
                continue
            if task_id in existing_task_ids:
                report['conflicts'].append({
                    'task_id': task_id,
                    'reason': 'duplicate_existing_migration',
                })
                continue
            existing_task_ids.add(task_id)
            report['existing'].append({
                'task_id': task_id, 'picking_id': picking['id'],
                'state': picking.get('state'),
            })

        planned = {}
        for balance_key, item in prepared.items():
            task_id = item['task']['id']
            product_id = item['product_id']
            if task_id in existing_task_ids:
                continue
            key = (product_id, item['location_id'])
            current = quantities.get(key, 0.0)
            delta = item['quantity'] - current
            tolerance = max(item['rounding'] / 2, 1e-9)
            if delta < -tolerance:
                report['conflicts'].append({
                    'task_id': task_id, 'item_no': item['item_no'],
                    'reason': 'task_location_has_more_than_source_balance',
                    'source_balance': item['quantity'], 'current_quantity': current,
                })
            elif delta > tolerance:
                planned[balance_key] = delta
            else:
                report['already_correct'].append({
                    'task_id': task_id, 'item_no': item['item_no'],
                    'quantity': item['quantity'],
                })

        needed_by_product = {}
        for balance_key, quantity in planned.items():
            product_id = prepared[balance_key]['product_id']
            needed_by_product[product_id] = needed_by_product.get(product_id, 0.0) + quantity
        for product_id, required in needed_by_product.items():
            free_quantity = available.get(product_id, 0.0) - pending_demand.get(product_id, 0.0)
            rounding = max(
                prepared[key]['rounding']
                for key in planned
                if prepared[key]['product_id'] == product_id)
            if free_quantity + max(rounding / 2, 1e-9) < required:
                item_no = next(
                    prepared[key]['item_no'] for key in planned
                    if prepared[key]['product_id'] == product_id)
                report['shortages'].append({
                    'item_no': item_no,
                    'required': required,
                    'available': max(0.0, free_quantity),
                    'reason': 'central_stock_short; draft transfer created and may result in negative stock',
                })

        grouped = {}
        for balance_key, quantity in planned.items():
            item = prepared[balance_key]
            task_id = item['task']['id']
            grouped.setdefault(task_id, []).append((item, quantity))
        dashboard_operation = api.find(
            'stock.picking.type',
            [('x_rekaz_origin', '=', 'rekaz-main:inventory:dashboard:incoming'),
             ('company_id', '=', api.company_id)],
            ['id'])
        if len(dashboard_operation) != 1:
            raise ValueError('Rekaz inbound inventory operation is missing or ambiguous')
        for task_id, lines in sorted(grouped.items()):
            task = lines[0][0]['task']
            task_origin = str(task.get('x_rekaz_origin') or '')
            if not task_origin:
                report['skipped'].append({
                    'task_id': task_id, 'reason': 'task_source_identity_missing',
                })
                continue
            picking_origin = existing_origin_prefix + task_origin
            move_values = []
            for item, quantity in sorted(lines, key=lambda pair: pair[0]['item_no'].casefold()):
                move = {
                    'name': item['item'].get('item_name') or item['item_no'],
                    'product_id': item['product_id'],
                    'product_uom_qty': quantity,
                    move_unit_field: item['unit_id'],
                    'location_id': stock_location_id,
                    'location_dest_id': item['location_id'],
                    'company_id': api.company_id,
                    'x_rekaz_task': task_id,
                    'x_rekaz_project': _relation_id(task.get('project_id')) or False,
                    'x_rekaz_direction': 'وارد للمعاملة',
                }
                if description_field:
                    move[description_field] = item['item'].get('item_name') or item['item_no']
                move_values.append((0, 0, move))
            values = {
                'picking_type_id': dashboard_operation[0]['id'],
                'company_id': api.company_id,
                'location_id': stock_location_id,
                'location_dest_id': lines[0][0]['location_id'],
                'x_rekaz_task': task_id,
                'x_rekaz_movement': 'incoming',
                'x_rekaz_project': _relation_id(task.get('project_id')) or False,
                'x_rekaz_ticket_no': (
                    task.get('x_rekaz_fault_no') or task.get('x_rekaz_notification_no') or False),
                'x_rekaz_work_order': task.get('x_rekaz_work_order') or False,
                'x_rekaz_tx_type': 'رصيد مواد المعاملة الحالي',
                'x_rekaz_origin': picking_origin,
                'origin': task.get('name') or task_origin,
                move_list: move_values,
            }
            picking_id = api.call('stock.picking', 'create', [values])
            report['created'].append({
                'task_id': task_id,
                'picking_id': picking_id,
                'items': [{
                    'item_no': item['item_no'], 'quantity': quantity,
                } for item, quantity in lines],
            })
            sync.m.save_report(report_path, report)
            print('Draft inventory transfer created for transaction ' + str(task_id), flush=True)
    sync.m.save_report(report_path, report)
    print(sync.m.dump(report))
    return 2 if (report['skipped'] or report['unlinked'] or report['invalid']
                 or report['unsupported_types'] or report['conflicts']) else 0


def ensure_main_supplier(api, name, partner_fields):
    if 'supplier_rank' not in partner_fields:
        raise ValueError('Supplier status field is unavailable on res.partner')
    partners = api.find('res.partner', [('name', '=', name)], ['id', 'supplier_rank'])
    if len(partners) > 1:
        raise ValueError('Duplicate supplier partner: ' + name)
    if partners:
        partner = partners[0]
        if partner.get('supplier_rank', 0) < 1:
            api.call('res.partner', 'write', [[partner['id']], {'supplier_rank': 1}])
        return partner['id']
    values = {'name': name, 'supplier_rank': 1}
    if 'is_company' in partner_fields:
        values['is_company'] = True
    elif 'company_type' in partner_fields:
        values['company_type'] = 'company'
    return api.call('res.partner', 'create', [values])


def supplier_default_action_code(company_id, incoming_operation_id, supplier_id):
    return f'''for rec in records:
    if (rec.company_id.id == {company_id}
            and rec.picking_type_id.id == {incoming_operation_id}
            and not rec.partner_id):
        rec.write({{'partner_id': {supplier_id}}})
'''


def summary_compute_code(done_quantity_field, move_unit_field, warehouse_view_location_id, target=None):
    code = f'''global_balances = {{}}
product_ids = list(set(
    rec.x_product_id.id for rec in self if rec.x_product_id))
company_ids = list(set(
    rec.x_company_id.id for rec in self if rec.x_company_id))
if product_ids and company_ids:
    task_locations = self.env['stock.location'].search([
        ('company_id', 'in', company_ids),
        ('location_id', 'child_of', {warehouse_view_location_id}),
        ('x_rekaz_task', '!=', False),
    ])
    task_location_ids = [location.id for location in task_locations]
    quant_domain = [
        ('product_id', 'in', product_ids),
        ('company_id', 'in', company_ids),
        ('location_id', 'child_of', {warehouse_view_location_id}),
        ('location_id.usage', '=', 'internal'),
        ('x_rekaz_task', '=', False),
    ]
    if task_location_ids:
        quant_domain.extend(['!', ('location_id', 'child_of', task_location_ids)])
    quants = self.env['stock.quant'].search(quant_domain)
    for quant in quants:
        key = (quant.company_id.id, quant.product_id.id)
        global_balances[key] = global_balances.get(key, 0.0) + quant.quantity
for rec in self:
    incoming = outgoing = returned = scrapped = balance = reserved = 0.0
    task = rec.x_task_id
    product = rec.x_product_id
    if task and product:
        for move in task.x_rekaz_material_moves:
            if move.product_id.id != product.id or move.state == 'cancel':
                continue
            if move.state != 'done':
                if move.picking_id.x_rekaz_movement == 'incoming':
                    demand = move.product_uom_qty or 0.0
                    if move.{move_unit_field} and move.{move_unit_field}.id != product.uom_id.id:
                        demand = move.{move_unit_field}._compute_quantity(demand, product.uom_id)
                    reserved -= demand
                continue
            quantity = move.{done_quantity_field} or 0.0
            if move.{move_unit_field} and move.{move_unit_field}.id != product.uom_id.id:
                quantity = move.{move_unit_field}._compute_quantity(
                    quantity, product.uom_id)
            movement = move.picking_id.x_rekaz_movement
            if movement == 'incoming':
                incoming += quantity
            elif movement == 'outgoing':
                outgoing += quantity
            elif movement == 'returned':
                returned += quantity
            elif movement == 'scrapped':
                scrapped += quantity
        for quant in task.x_rekaz_material_quants:
            if quant.product_id.id == product.id:
                balance += quant.quantity
    rec['x_incoming_qty'] = incoming
    rec['x_outgoing_qty'] = outgoing
    rec['x_returned_qty'] = returned
    rec['x_scrapped_qty'] = scrapped
    rec['x_balance_qty'] = balance
    rec['x_reserved_qty'] = reserved
    rec['x_after_reservation_qty'] = balance + reserved
    rec['x_global_balance_qty'] = global_balances.get(
        (rec.x_company_id.id, product.id), 0.0) if product else 0.0
'''


    if target is None:
        return code
    lines = []
    skip = False
    for line in code.splitlines():
        if line.startswith("    rec["):
            skip = not line.startswith("    rec['" + target + "']")
        if not skip:
            lines.append(line)
    return '\n'.join(lines) + '\n'


def summary_dependencies(done_quantity_field, move_unit_field):
    return ','.join((
        'x_task_id',
        'x_product_id',
        'x_company_id',
        'x_task_id.x_rekaz_material_moves.product_id',
        'x_task_id.x_rekaz_material_moves.state',
        'x_task_id.x_rekaz_material_moves.product_uom_qty',
        'x_task_id.x_rekaz_material_moves.' + done_quantity_field,
        'x_task_id.x_rekaz_material_moves.' + move_unit_field,
        'x_task_id.x_rekaz_material_moves.picking_id.x_rekaz_movement',
        'x_task_id.x_rekaz_material_quants.product_id',
        'x_task_id.x_rekaz_material_quants.quantity',
        'x_product_id.uom_id',
    ))


def summary_line_action_code(company_id):
    return f'''for rec in records:
    task = rec.x_rekaz_task
    product = rec.product_id
    if task and product and rec.company_id.id == {company_id}:
        matches = env['{BALANCE_MODEL}'].search([
            ('x_task_id', '=', task.id),
            ('x_product_id', '=', product.id),
        ], limit=2)
        if len(matches) > 1:
            raise UserError('توجد أسطر رصيد مكررة للصنف في المعاملة.')
        if not matches:
            env['{BALANCE_MODEL}'].create({{
                'x_task_id': task.id,
                'x_product_id': product.id,
                'x_company_id': rec.company_id.id,
            }})
'''


def install_summary_access(api, model_id):
    group = api.find('ir.model.data',
                     [('module', '=', 'base'), ('name', '=', 'group_user')], ['res_id'])
    if len(group) != 1:
        raise ValueError('Internal users group missing for transaction material summary')
    name = 'rekaz_material_balance_' + str(model_id)
    if getattr(api, 'major_version', 18) >= 20:
        upsert(api, 'ir.access', [('name', '=', name)],
               {'name': name, 'model_id': model_id, 'group_id': group[0]['res_id'],
                'operation': 'cr', 'domain': False, 'active': True})
        rule_name = name + '_company'
        upsert(api, 'ir.access', [('name', '=', rule_name)],
               {'name': rule_name, 'model_id': model_id, 'group_id': False,
                'operation': 'crud', 'domain': "[('x_company_id','in',company_ids)]",
                'active': True})
        return

    api.ensure('ir.model.access',
               [('name', '=', name), ('model_id', '=', model_id)],
               {'name': name, 'model_id': model_id, 'group_id': group[0]['res_id'],
                'perm_read': True, 'perm_create': True, 'perm_write': False,
                'perm_unlink': False})
    rule_name = name + '_company'
    api.ensure('ir.rule',
               [('name', '=', rule_name), ('model_id', '=', model_id)],
               {'name': rule_name, 'model_id': model_id,
                'domain_force': "[('x_company_id','in',company_ids)]"})


def install_summary_model(api, done_quantity_field, move_unit_field,
                          warehouse_view_location_id):
    model_rows = api.find('ir.model', [('model', '=', BALANCE_MODEL)], ['id'])
    if len(model_rows) > 1:
        raise ValueError('Duplicate transaction material summary models')
    if model_rows:
        model_id = model_rows[0]['id']
    else:
        model_id = api.call('ir.model', 'create', [{
            'model': BALANCE_MODEL,
            'name': 'ملخص مواد معاملات ركاز',
            'state': 'manual',
        }])
    for name, kind, label, extra in (
            ('x_task_id', 'many2one', 'المعاملة',
             {'relation': 'project.task', 'required': True}),
            ('x_product_id', 'many2one', 'الصنف',
             {'relation': 'product.product', 'required': True}),
            ('x_company_id', 'many2one', 'الشركة',
             {'relation': 'res.company', 'required': True}),
    ):
        field(api, BALANCE_MODEL, name, kind, label, **extra)
    field(api, BALANCE_MODEL, 'x_uom_id', 'many2one', 'الوحدة',
          relation='uom.uom', related='x_product_id.uom_id', readonly=True)
    # Provision new targets before updating existing stored compute expressions.
    for name, label in [('x_reserved_qty', 'حجز بانتظار توريد المالك'),
                        ('x_after_reservation_qty', 'الرصيد بعد الحجز'),
                        ('x_global_balance_qty', 'رصيد المخزون العام')]:
        field(api, BALANCE_MODEL, name, 'float', label, readonly=True)
    compute = summary_compute_code(
        done_quantity_field, move_unit_field, warehouse_view_location_id)
    depends = summary_dependencies(done_quantity_field, move_unit_field)
    for name, label in (
            ('x_incoming_qty', 'الوارد'),
            ('x_outgoing_qty', 'المنصرف'),
            ('x_returned_qty', 'المسترجع'),
            ('x_scrapped_qty', 'التالف'),
            ('x_balance_qty', 'رصيد المعاملة الداخلي'),
            ('x_reserved_qty', 'حجز بانتظار توريد المالك'),
            ('x_after_reservation_qty', 'الرصيد بعد الحجز'),
    ):
        field(api, BALANCE_MODEL, name, 'float', label, compute=summary_compute_code(done_quantity_field, move_unit_field, warehouse_view_location_id, target=name),
              depends=depends, store=False, readonly=True)
    field(api, BALANCE_MODEL, 'x_global_balance_qty', 'float',
          'رصيد المخزون العام', compute=summary_compute_code(done_quantity_field, move_unit_field, warehouse_view_location_id, target='x_global_balance_qty'), depends=depends,
          store=False, readonly=True)

    install_summary_access(api, model_id)
    return model_id


def install_summary_automation(api, source_model, company_id, trigger_names):
    model_rows = api.find('ir.model', [('model', '=', source_model)], ['id'])
    if len(model_rows) != 1:
        raise ValueError('Transaction summary source model missing: ' + source_model)
    fields = api.call(
        'ir.model.fields', 'search_read',
        [[('model', '=', source_model), ('name', 'in', trigger_names)]],
        fields=['id', 'name'], limit=False)
    field_ids = [row['id'] for row in fields]
    if len(field_ids) != len(trigger_names):
        raise ValueError('Could not resolve transaction material summary trigger fields')
    name = 'Rekaz transaction material summary ' + source_model + ' ' + str(company_id)
    action_id = upsert(
        api, 'ir.actions.server',
        [('name', '=', name), ('model_id', '=', model_rows[0]['id'])],
        {'name': name, 'model_id': model_rows[0]['id'], 'state': 'code',
         'code': summary_line_action_code(company_id)})
    upsert(
        api, 'base.automation',
        [('name', '=', name), ('model_id', '=', model_rows[0]['id'])],
        {'name': name, 'model_id': model_rows[0]['id'], 'trigger': 'on_create_or_write',
         'active': True,
         'filter_domain': repr([('company_id', '=', company_id),
                                ('x_rekaz_task', '!=', False),
                                ('product_id', '!=', False)]),
         'trigger_field_ids': [(6, 0, field_ids)],
         'action_server_ids': [(6, 0, [action_id])]})


def ensure_summary_rows(api, company_id):
    moves = api.call(
        'stock.move', 'search_read',
        [[('company_id', '=', company_id), ('x_rekaz_task', '!=', False),
          ('product_id', '!=', False)]],
        fields=['x_rekaz_task', 'product_id'], limit=False)
    quants = api.call(
        'stock.quant', 'search_read',
        [[('company_id', '=', company_id), ('x_rekaz_task', '!=', False),
          ('product_id', '!=', False)]],
        fields=['x_rekaz_task', 'product_id'], limit=False)
    pairs = set()
    for row in moves + quants:
        task, product = row.get('x_rekaz_task'), row.get('product_id')
        task_id = task[0] if isinstance(task, (list, tuple)) else task
        product_id = product[0] if isinstance(product, (list, tuple)) else product
        if task_id and product_id:
            pairs.add((task_id, product_id))
    existing = api.call(
        BALANCE_MODEL, 'search_read',
        [[('x_company_id', '=', company_id)]],
        fields=['x_task_id', 'x_product_id'], limit=False)
    existing_pairs = set()
    for row in existing:
        task, product = row.get('x_task_id'), row.get('x_product_id')
        pair = (task[0] if isinstance(task, (list, tuple)) else task,
                product[0] if isinstance(product, (list, tuple)) else product)
        if pair in existing_pairs:
            raise ValueError('Duplicate transaction material summary rows')
        existing_pairs.add(pair)
    missing = pairs - existing_pairs
    if missing:
        api.call(BALANCE_MODEL, 'create', [[
            {'x_task_id': task_id, 'x_product_id': product_id,
             'x_company_id': company_id}
            for task_id, product_id in sorted(missing)
        ]])
    verified = api.call(
        BALANCE_MODEL, 'search_read',
        [[('x_company_id', '=', company_id)]],
        fields=['x_task_id', 'x_product_id'], limit=False)
    verified_pairs = set()
    for row in verified:
        task, product = row.get('x_task_id'), row.get('x_product_id')
        verified_pairs.add((task[0] if isinstance(task, (list, tuple)) else task,
                            product[0] if isinstance(product, (list, tuple)) else product))
    if not pairs <= verified_pairs:
        raise ValueError('Could not verify all transaction material summary rows')


def install_supplier_default(api, operation_id, supplier_id, company_id):
    model = api.find('ir.model', [('model', '=', 'stock.picking')], ['id'])
    if len(model) != 1:
        raise ValueError('Stock transfer model missing for supplier default')
    trigger_fields = api.call(
        'ir.model.fields', 'search_read',
        [[('model', '=', 'stock.picking'), ('name', '=', 'picking_type_id')]],
        fields=['id'], limit=False)
    if len(trigger_fields) != 1:
        raise ValueError('Could not resolve supplier default trigger field')
    name = 'Rekaz default supplier ' + str(company_id)
    action_id = upsert(
        api, 'ir.actions.server',
        [('name', '=', name), ('model_id', '=', model[0]['id'])],
        {'name': name, 'model_id': model[0]['id'], 'state': 'code',
         'code': supplier_default_action_code(company_id, operation_id, supplier_id)})
    upsert(
        api, 'base.automation',
        [('name', '=', name), ('model_id', '=', model[0]['id'])],
        {'name': name, 'model_id': model[0]['id'], 'trigger': 'on_create_or_write',
         'active': True,
         'filter_domain': repr([('company_id', '=', company_id),
                                ('picking_type_id', '=', operation_id)]),
         'trigger_field_ids': [(6, 0, [trigger_fields[0]['id']])],
         'action_server_ids': [(6, 0, [action_id])]})


def picking_form_arch():
    return ('<data><xpath expr="//sheet" position="inside">'
            '<group string="ربط المعاملة"><field name="x_rekaz_task" readonly="x_rekaz_task"/>'
            '<field name="x_rekaz_movement"/>'
            '<field name="x_rekaz_project" readonly="1"/>'
            '<field name="x_rekaz_ticket_no" readonly="1"/>'
            '<field name="x_rekaz_work_order" readonly="1"/>'
            '</group></xpath>'
            "<xpath expr=\"//field[@name='picking_type_id']\" position=\"attributes\">"
            "<attribute name=\"domain\">context.get('default_x_rekaz_task') and "
            "[('x_rekaz_origin', 'in', ['rekaz-main:inventory:dashboard:incoming', "
            "'rekaz-main:inventory:dashboard:outgoing', 'rekaz-main:inventory:dashboard:scrapped', "
            "'rekaz-main:inventory:dashboard:returned'])] or []</attribute></xpath>"
            '<xpath expr="//field[@name=\'partner_id\']" '
            'position="attributes"><attribute name="string">استلام من المورد'
            '</attribute></xpath><xpath expr="//field[@name=\'origin\']" '
            'position="attributes"><attribute name="invisible">1</attribute>'
            '</xpath></data>')


def transaction_location_name(task):
    fault_number = str(
        task.get('x_rekaz_fault_no') or task.get('x_rekaz_notification_no') or ''
    ).strip()
    if fault_number:
        return 'رقم العطل - ' + fault_number
    work_order = str(task.get('x_rekaz_work_order') or '').strip()
    if work_order:
        return 'أمر العمل - ' + work_order
    name = str(task.get('name') or '').strip()
    if not name:
        raise ValueError(
            'Transaction has no fault number, work order, or name for its stock location')
    return name


def task_location_name_code(task_var='task', task_fields=None):
    task_fields = set(task_fields or ())
    fault_fields = [name for name in ('x_rekaz_fault_no', 'x_rekaz_notification_no')
                    if name in task_fields]
    fault_expression = ' or '.join(
        f'{task_var}.{name}' for name in fault_fields) or "''"
    work_order_expression = (
        f'{task_var}.x_rekaz_work_order'
        if 'x_rekaz_work_order' in task_fields else "''")
    return f"""fault_number = str({fault_expression} or '').strip()
work_order_number = str({work_order_expression} or '').strip()
if fault_number:
    transaction_location_name = 'رقم العطل - ' + fault_number
elif work_order_number:
    transaction_location_name = 'أمر العمل - ' + work_order_number
else:
    transaction_location_name = str({task_var}.name or '').strip()
if not transaction_location_name:
    raise UserError('لا يوجد رقم عطل أو أمر عمل أو اسم للمعاملة لتسمية موقعها.')
"""


def ensure_task_location_code(task_var='task', task_fields=None):
    return f"""task = {task_var}
{task_location_name_code(task_var, task_fields)}\
root = env['stock.location'].search([('x_rekaz_origin','=','rekaz-main:inventory:task-locations'),('company_id','=',task.company_id.id)],limit=2)
if len(root) != 1 or root.usage != 'view' or not root.active:
    raise UserError('راجع موقع مواد المعاملات الرئيسي في المستودع.')
locations = env['stock.location'].with_context(active_test=False).search([('x_rekaz_task','=',task.id),('company_id','=',task.company_id.id)],limit=2)
if len(locations) > 1:
    raise UserError('توجد مواقع متعددة للمعاملة؛ يلزم مراجعتها.')
if locations:
    location = locations
    if task.x_rekaz_stock_location and task.x_rekaz_stock_location.id != location.id:
        raise UserError('رابط موقع مخزون المعاملة غير متطابق.')
    if location.location_id != root or location.usage != 'internal' or not location.active:
        raise UserError('موقع المعاملة الموجود لا يطابق المستودع؛ راجعه قبل الحركة.')
    if location.name != transaction_location_name:
        location.write({{'name': transaction_location_name}})
elif task.x_rekaz_stock_location:
    raise UserError('رابط موقع المعاملة غير موجود في المستودع؛ يلزم مراجعته.')
else:
    location = env['stock.location'].create({{'name':transaction_location_name,'location_id':root.id,'company_id':task.company_id.id,'usage':'internal','x_rekaz_task':task.id}})
if not task.x_rekaz_stock_location:
    task.write({{'x_rekaz_stock_location':location.id}})
"""


def task_inventory_open_code(task_fields=None):
    return """if not record or len(records) != 1:
    raise UserError('افتح معاملة واحدة لعرض مخزونها.')
if not record.x_rekaz_stock_location:
""" + ''.join(
        '    ' + line + '\n'
        for line in ensure_task_location_code('record', task_fields).splitlines()
    ) + """action = {
    'type': 'ir.actions.act_window',
    'name': 'مخزون المعاملة — ' + record.name,
    'res_model': 'stock.picking',
    'view_mode': 'list,form',
    'target': 'current',
    'domain': [('x_rekaz_task', '=', record.id), ('company_id', '=', record.company_id.id)],
    'context': {'allowed_company_ids': env.context.get('allowed_company_ids', [record.company_id.id]),
                'default_x_rekaz_task': record.id, 'default_company_id': record.company_id.id},
}
"""


def task_materials_view_arch(open_action_id=None):
    def picking_list(field_name, label):
        return ('<group string="' + label + '"><field name="' + field_name
                + '" readonly="1"><list create="0" edit="0" delete="0">'
                '<field name="name"/><field name="x_rekaz_ticket_no"/>'
                '<field name="x_rekaz_work_order"/>'
                '<field name="location_dest_id"/><field name="scheduled_date"/>'
                '<field name="state"/></list></field></group>')

    issue = picking_list(TASK_INBOUND_FIELD, 'الوارد')
    consume = picking_list(TASK_OUTBOUND_FIELD, 'المنصرف')
    returned = picking_list(TASK_RETURNS_FIELD, 'المسترجع')
    arch = (
        '<data><xpath expr="//page[@name=\'extra_info\']" position="replace"/>'
        '<xpath expr="//notebook" position="inside">'
        '<page string="مواد المعاملة" name="rekaz_materials">'
        '<field name="company_id" invisible="1"/><field name="project_id" invisible="1"/>'
        + '<group string="ملخص مواد المعاملة"><field name="' + TASK_BALANCES_FIELD
        + '" readonly="1"><list create="0" edit="0" delete="0">'
          '<field name="x_product_id"/><field name="x_uom_id"/>'
          '<field name="x_incoming_qty"/>'
          '<field name="x_outgoing_qty"/><field name="x_returned_qty"/>'
          '<field name="x_scrapped_qty"/><field name="x_balance_qty"/>'
          '<field name="x_global_balance_qty"/>'
          '</list></field></group>'
        + issue + consume
        + picking_list(TASK_SCRAPS_FIELD, 'التالف')
        + returned
        + '<div>الوارد هو ما تستلمه المعاملة من المخزون، والمنصرف هو ما يخرج منها '
          'للاستخدام. المرتجع يعاد إلى موقع المستودع، والتالف يحول بإجراء Odoo '
          'المعتمد إلى موقع خسائر المخزون. رصيد المعاملة الداخلي هو الكمية الفعلية في موقعها '
          '(الوارد − المنصرف − المسترجع − التالف)، ورصيد المخزون العام يُقرأ من كميات Odoo '
          'في المواقع الداخلية العامة لمستودع ركاز مع استبعاد مواقع المعاملات. لا تتغير الكميات '
          'قبل اعتماد الحركة.</div>'
        '</page></xpath></data>')
    root = ET.fromstring(arch)
    page = root.find('.//page')
    notice = ET.Element('div', {'class': 'alert alert-info'})
    notice.text = 'مواد المعاملة للعرض فقط. الإدخال والتعديل واعتماد الحركات من شاشة المخزون. زر المخزون يفتح المعاملة الحالية ويملأ رابطها تلقائياً عند إضافة حركة.'
    page.insert(0, notice)
    link = (ET.Element('button', {'name': str(open_action_id), 'type': 'action', 'class': 'btn btn-primary mb-3',
                                'string': 'فتح مواد هذه المعاملة في المخزون'}) if open_action_id else
            ET.Element('a', {'href': '/odoo/inventory/11/action-723', 'class': 'btn btn-primary mb-3'}))
    if not open_action_id:
        link.text = 'إدخال وتعديل المواد من المخزون'
    page.insert(1, link)
    for field in page.findall('.//field'):
        listing = field.find('list')
        if listing is None:
            continue
        field.set('readonly', '1')
        field.set('options', "{'no_open': True, 'no_create': True, 'no_create_edit': True}")
        form = ET.SubElement(field, 'form', {'string': 'عرض مواد المعاملة', 'create': '0', 'edit': '0', 'delete': '0'})
        group = ET.SubElement(ET.SubElement(form, 'sheet'), 'group')
        for column in listing.findall('field'):
            ET.SubElement(group, 'field', {'name': column.attrib['name'], 'readonly': '1',
                                         'options': "{'no_open': True, 'no_create': True}"})
    return ET.tostring(root, encoding='unicode')


def picking_link_action_code(company_id, allowed_operation_ids, task_fields=None,
                             operation_types=None, move_field='move_ids'):
    task_fields = set(task_fields or ())
    if not task_fields:
        task_fields = {'x_rekaz_fault_no', 'x_rekaz_notification_no', 'x_rekaz_work_order'}
    dashboard_flow_ids = (operation_types or {}).get('dashboard_flow_ids', {})
    dashboard_flow_by_type = {
        operation_id: movement
        for movement, operation_id in dashboard_flow_ids.items()
    }
    allowed_operation_ids = sorted(set(allowed_operation_ids))
    if not allowed_operation_ids:
        raise ValueError('At least one configured inventory operation is required')
    flow_guard = ''
    if operation_types:
        flow_guard = f'''
        dashboard_flow = {dashboard_flow_by_type!r}.get(rec.picking_type_id.id)
        flow = rec.x_rekaz_movement
        if dashboard_flow:
            if flow and flow != dashboard_flow:
                raise UserError('نوع بطاقة المخزون لا يطابق تصنيف حركة المعاملة.')
            flow = dashboard_flow
            if rec.x_rekaz_movement != flow:
                if rec.state == 'done':
                    raise UserError('لا يمكن تغيير تصنيف حركة مخزون معتمدة.')
                values['x_rekaz_movement'] = flow
        if rec.picking_type_id.id == {operation_types['scrap_id']} and flow != 'scrapped':
            raise UserError('يجب تصنيف عملية الإتلاف على أنها حركة تالف للمعاملة.')
        if not task.x_rekaz_stock_location:
{''.join('            ' + line + chr(10) for line in ensure_task_location_code(
    task_fields=task_fields).splitlines())}
        task_location_id = task.x_rekaz_stock_location.id
        if not task_location_id:
            raise UserError('أنشئ موقع مخزون المعاملة أولاً.')
        if flow == 'incoming':
            expected_types = [{operation_types['internal_id']},
                              {dashboard_flow_ids.get('incoming', -1)}]
            expected_source = {operation_types['warehouse_stock_id']}
            expected_destination = task_location_id
        elif flow == 'outgoing':
            expected_types = [{operation_types['outgoing_id']},
                              {dashboard_flow_ids.get('outgoing', -1)}]
            expected_source = task_location_id
            expected_destination = {operation_types['customer_location_id']}
        elif flow == 'returned':
            expected_types = [{operation_types['internal_id']},
                              {dashboard_flow_ids.get('returned', -1)}]
            expected_source = task_location_id
            expected_destination = {operation_types['warehouse_stock_id']}
        elif flow == 'scrapped':
            expected_types = [{operation_types['scrap_id']},
                              {dashboard_flow_ids.get('scrapped', -1)}]
            expected_source = task_location_id
            expected_destination = {operation_types['scrap_location_id']}
        elif flow:
            raise UserError('نوع حركة المعاملة غير معروف.')
        else:
            expected_types = []
        if expected_types and rec.picking_type_id.id not in expected_types:
            raise UserError('نوع التحويل لا يطابق حركة المعاملة المختارة.')
        if dashboard_flow:
            if rec.location_id.id != expected_source:
                if rec.state == 'done':
                    raise UserError('لا يمكن تغيير مواقع حركة مخزون معتمدة.')
                values['location_id'] = expected_source
            if rec.location_dest_id.id != expected_destination:
                if rec.state == 'done':
                    raise UserError('لا يمكن تغيير مواقع حركة مخزون معتمدة.')
                values['location_dest_id'] = expected_destination
        elif expected_types and (
                rec.location_id.id != expected_source
                or rec.location_dest_id.id != expected_destination):
            raise UserError('نوع التحويل أو مواقعه لا يطابق حركة المعاملة المختارة.')
'''
    ticket_expression = ("(task.x_rekaz_fault_no or task.x_rekaz_notification_no or '').strip()"
                         if {'x_rekaz_fault_no', 'x_rekaz_notification_no'} <= task_fields else "''")
    work_order_expression = ("(task.x_rekaz_work_order or '').strip()"
                             if 'x_rekaz_work_order' in task_fields else "''")
    return f'''for rec in records:
    task = rec.x_rekaz_task
    if not task:
        if (rec.picking_type_id.id in {dashboard_flow_by_type!r}
                and rec.state == 'done'):
            raise UserError('اربط حركة المخزون بمعاملة قبل اعتمادها.')
        continue
    if task:
        values = {{}}
        if rec.company_id.id != {company_id} or task.company_id.id != {company_id}:
            raise UserError('يجب أن تكون المعاملة والتحويل في شركة ركاز المحددة.')
        if rec.picking_type_id.id not in {allowed_operation_ids!r}:
            raise UserError('اختر نوع عملية معتمدًا لحركة المعاملة.')
{flow_guard.rstrip()}
        project_id = task.project_id.id
        ticket_no = {ticket_expression}
        work_order = {work_order_expression}
        if rec.x_rekaz_project.id != project_id:
            values['x_rekaz_project'] = project_id
        if rec.x_rekaz_ticket_no != ticket_no:
            values['x_rekaz_ticket_no'] = ticket_no
        if rec.x_rekaz_work_order != work_order:
            values['x_rekaz_work_order'] = work_order
        if values:
            rec.write(values)
        for move in rec.{move_field}:
            move_values = {{}}
            if move.x_rekaz_task.id != task.id:
                move_values['x_rekaz_task'] = task.id
            if move.x_rekaz_project.id != project_id:
                move_values['x_rekaz_project'] = project_id
            direction = {{'incoming': 'وارد للمعاملة',
                          'outgoing': 'منصرف من المعاملة',
                          'returned': 'مسترجع',
                          'scrapped': 'تالف'}}.get(rec.x_rekaz_movement)
            if not direction:
                direction = 'وارد' if rec.picking_type_id.code == 'incoming' else 'منصرف'
            if move.x_rekaz_direction != direction:
                move_values['x_rekaz_direction'] = direction
            if move_values:
                move.write(move_values)
'''


def task_location_action_code(company_id, task_locations_parent_id, project_ids,
                              task_fields=None):
    project_ids = sorted(set(project_ids))
    if not project_ids:
        raise ValueError('No Rekaz projects found for transaction stock locations')
    task_fields = set(task_fields or ())
    return f'''for task in records:
    if task.company_id.id != {company_id} or task.project_id.id not in {project_ids!r}:
        continue
{''.join('    ' + line + chr(10) for line in task_location_name_code(
    task_fields=task_fields).splitlines())}
    locations = env['stock.location'].search([('x_rekaz_task', '=', task.id)], limit=2)
    if len(locations) > 1:
        raise UserError('توجد مواقع مخزون متعددة للمعاملة نفسها.')
    if locations:
        location = locations
        if location.location_id.id != {task_locations_parent_id}:
            raise UserError('موقع مخزون المعاملة لا يطابق المستودع أو المعاملة.')
        if task.x_rekaz_stock_location and task.x_rekaz_stock_location.id != location.id:
            raise UserError('رابط موقع مخزون المعاملة غير متطابق.')
        if location.name != transaction_location_name:
            location.write({{'name': transaction_location_name}})
        if not task.x_rekaz_stock_location:
            task.write({{'x_rekaz_stock_location': location.id}})
        continue
    location = env['stock.location'].create({{
        'name': transaction_location_name,
        'location_id': {task_locations_parent_id},
        'company_id': {company_id},
        'usage': 'internal',
        'x_rekaz_task': task.id,
    }})
    task.write({{'x_rekaz_stock_location': location.id}})
'''


def install_task_stock_locations(api, warehouse_record):
    view_location_id = warehouse_record['view_location_id'][0]
    branch_key = 'rekaz-main:inventory:task-locations'
    branches = api.find('stock.location',
                        [('x_rekaz_origin', '=', branch_key),
                         ('company_id', '=', api.company_id)],
                        ['id', 'location_id', 'usage'])
    if len(branches) > 1:
        raise ValueError('Duplicate Rekaz transaction location roots')
    branch_id = None
    if branches:
        branch_id = branches[0]['id']
        parent = branches[0].get('location_id')
        parent_id = parent[0] if isinstance(parent, (list, tuple)) else parent
        if parent_id != view_location_id or branches[0].get('usage') != 'view':
            raise ValueError('Rekaz transaction location root has an unexpected parent or usage')
    task_fields = api.call(
        'project.task', 'fields_get', [], attributes=['type'])
    task_read_fields = [
        'id', 'name', 'project_id', 'x_rekaz_stock_location',
        *[name for name in (
            'x_rekaz_fault_no', 'x_rekaz_notification_no', 'x_rekaz_work_order')
          if name in task_fields],
    ]
    tasks = api.call(
        'project.task', 'search_read',
        [[('company_id', '=', api.company_id),
          ('x_rekaz_origin', '=like', 'reportrek:rekaz-main:%')]],
        fields=task_read_fields, limit=False)
    if not tasks:
        raise ValueError('No copied Rekaz transactions found; copy Rekaz projects before inventory setup')
    project_ids = sorted({task['project_id'][0] for task in tasks if task.get('project_id')})
    if any(not task.get('project_id') for task in tasks):
        raise ValueError('A copied Rekaz transaction has no project; no stock locations were linked')
    task_location_names = {}
    for task in tasks:
        name_scope = {
            key: task.get(key)
            for key in ('name', 'x_rekaz_fault_no', 'x_rekaz_notification_no',
                        'x_rekaz_work_order')
        }
        task_name = transaction_location_name(name_scope)
        task_location_names[task['id']] = task_name
    task_ids = [task['id'] for task in tasks]
    locations = api.call(
        'stock.location', 'search_read',
        [[('x_rekaz_task', 'in', task_ids)]],
        fields=['id', 'name', 'x_rekaz_task', 'location_id', 'company_id', 'usage'],
        limit=False)
    by_task = {}
    for location in locations:
        task_relation = location.get('x_rekaz_task')
        task_id = task_relation[0] if isinstance(task_relation, (list, tuple)) else task_relation
        by_task.setdefault(task_id, []).append(location)
    missing = []
    for task in tasks:
        relation = task.get('x_rekaz_stock_location')
        linked_id = relation[0] if isinstance(relation, (list, tuple)) else relation
        candidates = by_task.get(task['id'], [])
        if linked_id:
            if len(candidates) != 1 or candidates[0]['id'] != linked_id:
                raise ValueError('Transaction stock location link is inconsistent for task ' + str(task['id']))
        elif len(candidates) == 1:
            pass
        elif len(candidates) > 1:
            raise ValueError('Multiple transaction stock locations found for task ' + str(task['id']))
        else:
            missing.append(task)
            continue
        if candidates:
            location = candidates[0]
            parent = location.get('location_id')
            parent_id = parent[0] if isinstance(parent, (list, tuple)) else parent
            company = location.get('company_id')
            company_id = company[0] if isinstance(company, (list, tuple)) else company
            if (parent_id not in ({view_location_id, branch_id} if branch_id else {view_location_id})
                    or company_id != api.company_id
                    or location.get('usage') != 'internal'):
                raise ValueError('Transaction stock location has an unexpected warehouse configuration')

    for task in tasks:
        candidates = by_task.get(task['id'], [])
        if not candidates:
            continue
        location = candidates[0]
        parent = location.get('location_id')
        parent_id = parent[0] if isinstance(parent, (list, tuple)) else parent
        changes = {}
        if parent_id != view_location_id:
            changes['location_id'] = view_location_id
        desired_name = task_location_names[task['id']]
        if location.get('name') != desired_name:
            changes['name'] = desired_name
        if changes:
            api.call('stock.location', 'write', [[location['id']], changes])
            location.update(changes)

    if missing:
        values = [{
            'name': task_location_names[task['id']],
            'location_id': view_location_id,
            'company_id': api.company_id,
            'usage': 'internal',
            'x_rekaz_task': task['id'],
        } for task in missing]
        created_ids = api.call('stock.location', 'create', [values])
        if isinstance(created_ids, int):
            created_ids = [created_ids]
        if not isinstance(created_ids, (list, tuple)) or len(created_ids) != len(missing):
            raise ValueError('Could not verify transaction stock locations created')
        verified = api.call(
            'stock.location', 'search_read',
            [[('x_rekaz_task', 'in', [task['id'] for task in missing])]],
            fields=['id', 'name', 'x_rekaz_task', 'location_id', 'company_id', 'usage'],
            limit=False)
        if len(verified) != len(missing):
            raise ValueError('Could not verify all transaction stock locations')
        missing_ids = {task['id'] for task in missing}
        seen_task_ids = set()
        for location in verified:
            task_relation = location.get('x_rekaz_task')
            task_id = task_relation[0] if isinstance(task_relation, (list, tuple)) else task_relation
            if task_id not in missing_ids or task_id in seen_task_ids:
                raise ValueError('Created transaction stock location has an invalid task link')
            seen_task_ids.add(task_id)
            parent = location.get('location_id')
            parent_id = parent[0] if isinstance(parent, (list, tuple)) else parent
            company = location.get('company_id')
            company_id = company[0] if isinstance(company, (list, tuple)) else company
            if (parent_id != view_location_id or company_id != api.company_id
                    or location.get('usage') != 'internal'
                    or location.get('name') != task_location_names[task_id]):
                raise ValueError('Created transaction stock location has an invalid setup')
        if seen_task_ids != missing_ids:
            raise ValueError('Created transaction stock locations do not cover all transactions')
        for location in verified:
            task_id = location['x_rekaz_task'][0]
            by_task[task_id] = [location]
    for task in tasks:
        candidates = by_task.get(task['id'], [])
        if len(candidates) != 1:
            raise ValueError('Could not resolve one stock location for task ' + str(task['id']))
        location_id = candidates[0]['id']
        location_name = task_location_names[task['id']]
        if candidates[0].get('name') != location_name:
            api.call('stock.location', 'write',
                     [[location_id], {'name': location_name}])
        relation = task.get('x_rekaz_stock_location')
        linked_id = relation[0] if isinstance(relation, (list, tuple)) else relation
        if linked_id != location_id:
            api.call('project.task', 'write', [[task['id']],
                                               {'x_rekaz_stock_location': location_id}])
    all_task_ids = [task['id'] for task in tasks]
    refreshed = api.call(
        'project.task', 'search_read',
        [[('id', 'in', all_task_ids)]],
        fields=['id', 'x_rekaz_stock_location'], limit=False)
    links = {row['id']: row.get('x_rekaz_stock_location') for row in refreshed}
    for task in tasks:
        relation = links.get(task['id'])
        linked_id = relation[0] if isinstance(relation, (list, tuple)) else relation
        candidates = by_task.get(task['id'], [])
        if len(candidates) != 1 or linked_id != candidates[0]['id']:
            raise ValueError('Could not verify task stock location link for task ' + str(task['id']))
    return project_ids, view_location_id


def install(api, warehouse_id=REKAZ_WAREHOUSE_ID):
    warehouse_record = warehouse(api, warehouse_id)
    operations = operation_types(api, warehouse_record['id'])
    fields = api.call('stock.picking', 'fields_get', [], attributes=['type'])
    move_fields = api.call('stock.move', 'fields_get', [], attributes=['type'])
    quant_fields = api.call('stock.quant', 'fields_get', [], attributes=['type'])
    move_unit_field, move_list, _ = move_schema(move_fields, fields)
    if not {'x_rekaz_task', 'picking_type_id', 'location_id', 'location_dest_id'} <= set(fields):
        raise ValueError('Unsupported stock transfer schema; no inventory workflow installed')
    if 'partner_id' not in fields:
        raise ValueError('Supplier field is unavailable on stock transfers')
    done_quantity_field = next(
        (name for name in ('quantity', 'quantity_done') if name in move_fields), None)
    if not done_quantity_field:
        raise ValueError('Completed stock quantity is unavailable; no summary installed')
    if move_fields[move_unit_field].get('type') != 'many2one':
        raise ValueError('Unsupported stock move unit field; no summary installed')
    if move_fields[done_quantity_field].get('type') not in ('float', 'integer'):
        raise ValueError('Completed stock quantity is not numeric; no summary installed')
    product_fields = api.call('product.product', 'fields_get', [], attributes=['type'])
    if ('uom_id' not in product_fields
            or product_fields['uom_id'].get('type') != 'many2one'):
        raise ValueError('Product unit field is unavailable; no summary installed')
    if move_list not in fields:
        raise ValueError('Unsupported stock move relation; no inventory workflow installed')
    if not {'product_id', 'product_uom_qty', 'date', 'state', 'location_id',
            'location_dest_id', 'company_id'} <= set(move_fields):
        raise ValueError('Unsupported stock move list schema; no movement menus installed')
    if not {'product_id', 'quantity', 'reserved_quantity', 'location_id'} <= set(quant_fields):
        raise ValueError('Unsupported stock quant schema; no transaction balance installed')
    for model in ('stock.picking', 'stock.picking.type', 'product.product', 'stock.location'):
        field(api, model, 'x_rekaz_origin', 'char', 'هوية المصدر ركاز', index=True)
    field(api, 'stock.location', 'x_rekaz_task', 'many2one', 'معاملة ركاز',
          relation='project.task')
    field(api, 'project.task', 'x_rekaz_stock_location', 'many2one', 'موقع مواد المعاملة',
          relation='stock.location', readonly=True)
    field(api, 'stock.quant', 'x_rekaz_task', 'many2one', 'معاملة ركاز',
          relation='project.task', related='location_id.x_rekaz_task', readonly=True)
    field(api, 'stock.move', 'x_rekaz_task', 'many2one', 'معاملة ركاز', relation='project.task')
    field(api, 'stock.move', 'x_rekaz_project', 'many2one', 'مشروع ركاز', relation='project.project')
    field(api, 'stock.move', 'x_rekaz_direction', 'char', 'وارد / منصرف')
    field(api, 'project.task', 'x_rekaz_material_moves', 'one2many', 'مواد المعاملة',
          relation='stock.move', relation_field='x_rekaz_task')
    field(api, 'stock.picking', 'x_rekaz_task', 'many2one', 'معاملة ركاز', relation='project.task')
    field(api, 'stock.picking', 'x_rekaz_project', 'many2one', 'مشروع ركاز', relation='project.project')
    field(api, 'stock.picking', 'x_rekaz_ticket_no', 'char', 'رقم المعاملة')
    field(api, 'stock.picking', 'x_rekaz_work_order', 'char', 'أمر العمل')
    field(api, 'stock.picking', 'x_rekaz_tx_type', 'char', 'نوع حركة ركاز')
    field(api, 'stock.move', 'x_rekaz_ticket_no', 'char', 'رقم المعاملة',
          related='picking_id.x_rekaz_ticket_no', readonly=True)
    field(api, 'stock.move', 'x_rekaz_work_order', 'char', 'أمر العمل',
          related='picking_id.x_rekaz_work_order', readonly=True)
    field(api, 'stock.picking', 'x_rekaz_movement', 'selection', 'حركة مواد المعاملة',
          selection_ids=[(0, 0, {'value': 'incoming', 'name': 'وارد إلى المعاملة'}),
                         (0, 0, {'value': 'outgoing', 'name': 'منصرف من المعاملة'}),
                         (0, 0, {'value': 'returned', 'name': 'مسترجع إلى المستودع'}),
                         (0, 0, {'value': 'scrapped', 'name': 'تالف'})])
    field(api, 'project.task', TASK_PICKINGS_FIELD, 'one2many', 'تحويلات مواد المعاملة',
          relation='stock.picking', relation_field='x_rekaz_task')
    field(api, 'project.task', TASK_INBOUND_FIELD, 'one2many', 'وارد المعاملة',
          relation='stock.picking', relation_field='x_rekaz_task',
          domain="[('x_rekaz_movement', '=', 'incoming')]")
    field(api, 'project.task', TASK_OUTBOUND_FIELD, 'one2many', 'منصرف المعاملة',
          relation='stock.picking', relation_field='x_rekaz_task',
          domain="[('x_rekaz_movement', '=', 'outgoing')]")
    field(api, 'project.task', TASK_RETURNS_FIELD, 'one2many', 'مسترجع المعاملة',
          relation='stock.picking', relation_field='x_rekaz_task',
          domain="[('x_rekaz_movement', '=', 'returned')]")
    field(api, 'project.task', TASK_QUANTS_FIELD, 'one2many', 'الرصيد الداخلي للمعاملة',
          relation='stock.quant', relation_field='x_rekaz_task')
    field(api, 'project.task', TASK_SCRAPS_FIELD, 'one2many', 'مواد تالفة للمعاملة',
          relation='stock.picking', relation_field='x_rekaz_task',
          domain="[('x_rekaz_movement', '=', 'scrapped')]")
    install_summary_model(
        api, done_quantity_field, move_unit_field,
        warehouse_record['view_location_id'][0])
    field(api, 'project.task', TASK_BALANCES_FIELD, 'one2many', 'ملخص مواد المعاملة',
          relation=BALANCE_MODEL, relation_field='x_task_id')
    root = api.find('ir.model.data', [('module', '=', 'stock'), ('name', '=', 'menu_stock_root')],
                    ['res_id'])
    if len(root) != 1:
        raise ValueError('Inventory application menu missing')
    view = api.find('ir.model.data', [('module', '=', 'stock'), ('name', '=', 'view_picking_form')], ['res_id'])
    if len(view) != 1:
        raise ValueError('Inventory form view missing')
    upsert(api, 'ir.ui.view', [('name', '=', 'Rekaz inventory links'), ('model', '=', 'stock.picking')],
           {'name': 'Rekaz inventory links', 'model': 'stock.picking', 'type': 'form',
            'inherit_id': view[0]['res_id'],
            'arch_db': picking_form_arch()})
    partner_fields = api.call('res.partner', 'fields_get', [], attributes=['type'])
    supplier_name = 'الشركة السعودية للطاقة'
    supplier_id = ensure_main_supplier(api, supplier_name, partner_fields)
    install_supplier_default(api, operations['incoming']['id'], supplier_id, api.company_id)
    task_form = api.find('ir.model.data', [('module', '=', 'project'), ('name', '=', 'view_task_form2')], ['res_id'])
    if len(task_form) != 1:
        raise ValueError('Project task form missing')
    material_task_model = api.find('ir.model', [('model', '=', 'project.task')], ['id'])
    if len(material_task_model) != 1:
        raise ValueError('Project task model missing')
    task_fields = api.call('project.task', 'fields_get', [], attributes=['type'])
    material_action = upsert(api, 'ir.actions.server',
        [('name', '=', 'فتح مخزون المعاملة الحالية'), ('model_id', '=', material_task_model[0]['id'])],
        {'name': 'فتح مخزون المعاملة الحالية', 'model_id': material_task_model[0]['id'],
         'state': 'code', 'code': task_inventory_open_code(task_fields)})
    upsert(api, 'ir.ui.view', [('name', '=', 'Rekaz materials project.task'), ('model', '=', 'project.task')],
           {'name': 'Rekaz materials project.task', 'model': 'project.task', 'type': 'form',
            'inherit_id': task_form[0]['res_id'],
           'arch_db': task_materials_view_arch(material_action)})
    project_ids, task_location_root_id = install_task_stock_locations(api, warehouse_record)
    dashboard_flow_ids = install_task_operation_types(
        api, warehouse_record['id'], operations)
    required_operation_ids = {
        operation['id'] for operation in operations.values()
        if isinstance(operation, dict) and operation.get('id')
    }
    hide_legacy_inventory_dashboard_operations(
        api, warehouse_record['id'], required_operation_ids)
    task_model = api.find('ir.model', [('model', '=', 'project.task')], ['id'])
    if len(task_model) != 1:
        raise ValueError('Project task model missing')
    task_trigger_fields = api.call(
        'ir.model.fields', 'search_read',
        [[('model', '=', 'project.task'),
          ('name', 'in', ['company_id', 'project_id', 'name']
           + [name for name in (
               'x_rekaz_fault_no', 'x_rekaz_notification_no', 'x_rekaz_work_order')
              if name in task_fields])]],
        fields=['id', 'name'], limit=False)
    task_trigger_ids = [row['id'] for row in task_trigger_fields]
    expected_task_trigger_count = 3 + sum(
        name in task_fields for name in (
            'x_rekaz_fault_no', 'x_rekaz_notification_no', 'x_rekaz_work_order'))
    if len(task_trigger_ids) != expected_task_trigger_count:
        raise ValueError('Could not resolve transaction location automation fields')
    location_action_name = 'Rekaz task stock location ' + str(api.company_id)
    location_action_id = upsert(
        api, 'ir.actions.server',
        [('name', '=', location_action_name), ('model_id', '=', task_model[0]['id'])],
        {'name': location_action_name, 'model_id': task_model[0]['id'], 'state': 'code',
         'code': task_location_action_code(
             api.company_id, task_location_root_id, project_ids, task_fields)})
    upsert(
        api, 'base.automation',
        [('name', '=', location_action_name), ('model_id', '=', task_model[0]['id'])],
        {'name': location_action_name, 'model_id': task_model[0]['id'],
         'trigger': 'on_create_or_write', 'active': True,
         'filter_domain': repr([('company_id', '=', api.company_id),
                                ('project_id', 'in', project_ids)]),
         'trigger_field_ids': [(6, 0, task_trigger_ids)],
         'action_server_ids': [(6, 0, [location_action_id])]})
    model_row = api.find('ir.model', [('model', '=', 'stock.picking')], ['id'])
    if len(model_row) != 1:
        raise ValueError('Stock transfer model missing')
    install_summary_automation(api, 'stock.move', api.company_id,
                               ['x_rekaz_task', 'product_id'])
    install_summary_automation(api, 'stock.quant', api.company_id,
                               ['location_id', 'product_id'])
    action_name = 'Rekaz task stock transfer link ' + str(api.company_id)
    trigger_fields = api.call(
        'ir.model.fields', 'search_read',
        [[('model', '=', 'stock.picking'),
          ('name', 'in', ['x_rekaz_task', 'x_rekaz_movement', 'picking_type_id',
                          'location_id', 'location_dest_id', move_list, 'state'])]],
        fields=['id', 'name'], limit=False)
    trigger_ids = [row['id'] for row in trigger_fields]
    if len(trigger_ids) != 7:
        raise ValueError('Could not resolve stock transfer link automation fields')
    allowed_operation_ids = [
        operations['internal']['id'], operations['outgoing']['id'],
        operations['scrap']['id'], *dashboard_flow_ids.values()]
    operation_config = {
        'internal_id': operations['internal']['id'],
        'outgoing_id': operations['outgoing']['id'],
        'scrap_id': operations['scrap']['id'],
        'warehouse_stock_id': operations['stock_location_id'],
        'customer_location_id': operations['outgoing']['default_location_dest_id'][0],
        'scrap_location_id': operations['scrap_location_id'],
        'dashboard_flow_ids': dashboard_flow_ids,
    }
    action_id = upsert(api, 'ir.actions.server',
                       [('name', '=', action_name), ('model_id', '=', model_row[0]['id'])],
                       {'name': action_name, 'model_id': model_row[0]['id'], 'state': 'code',
                        'code': picking_link_action_code(api.company_id, allowed_operation_ids,
                                                        task_fields, operation_config, move_list)})
    upsert(api, 'base.automation',
           [('name', '=', action_name), ('model_id', '=', model_row[0]['id'])],
           {'name': action_name, 'model_id': model_row[0]['id'],
            'trigger': 'on_create_or_write', 'active': True,
            'filter_domain': repr([
                ('company_id', '=', api.company_id), '|',
                ('x_rekaz_task', '!=', False),
                ('picking_type_id', 'in', list(dashboard_flow_ids.values()))]),
            'trigger_field_ids': [(6, 0, trigger_ids)],
            'action_server_ids': [(6, 0, [action_id])]})
    ensure_summary_rows(api, api.company_id)
    retire_owned_ui(api)
    from odoo_warehouse_voucher import install as install_warehouse_voucher
    install_warehouse_voucher(api)
    from odoo_product_movements import install as install_product_movements
    install_product_movements(api)
    from odoo_cycle_inventory import install as install_cycle_inventory
    install_cycle_inventory(api)
    print('Supplier receipts, four Rekaz inventory dashboard flows, transaction material summaries, '
          'and native receipt/issue/return/damage workflows installed; existing movements and stock '
          'quantities unchanged.',
          flush=True)


def task_index(conn, api):
    task_fields = api.call('project.task', 'fields_get', [], attributes=['type'])
    requested_fields = ['x_rekaz_origin', 'project_id', 'name']
    requested_fields.extend(
        name for name in ('x_rekaz_stock_location', 'x_rekaz_fault_no',
                          'x_rekaz_notification_no', 'x_rekaz_work_order')
        if name in task_fields)
    tasks = api.call('project.task', 'search_read',
                     [[('company_id', '=', api.company_id), ('x_rekaz_origin', '=like', 'reportrek:rekaz-main:%')]],
                     fields=requested_fields, limit=False)
    target = {}
    for task in tasks:
        origin = task['x_rekaz_origin']
        if origin in target:
            raise ValueError('Duplicate Odoo transaction source identity: ' + origin)
        target[origin] = task
    specs = sync.m.catalog(conn)
    result = {}
    for table in ('tickets', 'reinforcement_works', 'construction_works', 'contractor_works', 'primary_team_orders'):
        if table not in specs:
            continue
        for row in sync.m.source_rows(conn, table, specs[table]):
            task = target.get('reportrek:rekaz-main:' + table + ':' + sync.m.row_key(row, specs[table]))
            if task:
                for column in ('ticket_no', 'rekaz_code', 'work_order'):
                    raw = str(row.get(column) or '').strip()
                    if raw:
                        result.setdefault((column, raw), {})[task['id']] = task
    return result


def match_task(row, index):
    # Source identity fields retain their meaning; never join work order to ticket number.
    candidates = None
    for column in ('rekaz_code', 'ticket_no', 'work_order'):
        value = str(row.get(column) or '').strip()
        matches = index.get((column, value), {}) if value else {}
        if matches:
            candidates = dict(matches) if candidates is None else {k: v for k, v in candidates.items() if k in matches}
    return next(iter(candidates.values())) if candidates and len(candidates) == 1 else None


def copy(api, source):
    report = {'mode': 'inventory_drafts', 'created': 0, 'existing': 0, 'products_ready': 0, 'zero_quantity': [], 'skipped': []}
    with sync.m.snapshot(source) as conn:
        index = task_index(conn, api)
        if not index:
            raise ValueError('Copy Rekaz tasks to Projects first; no linked source tasks found')
        # Inventory model checks precede schema changes.
        move_fields = api.call('stock.move', 'fields_get', [], attributes=['type'])
        product_fields = api.call('product.product', 'fields_get', [], attributes=['type'])
        picking_fields = api.call('stock.picking', 'fields_get', [], attributes=['type'])
        unit_field, move_list, description_field = move_schema(move_fields, picking_fields)
        selected_warehouse = warehouse(api)
        report['warehouse'] = selected_warehouse
        report['unlinked'] = []
        types = operation_types(api, selected_warehouse['id'])
        units = api.call('uom.uom', 'search_read', [[('active', '=', True)]], fields=['name'], limit=False)
        aliases = {'EA': ('Units', 'Unit', 'الوحدات', 'وحدة'), 'PC': ('Units', 'Unit', 'الوحدات', 'وحدة'),
                   'عدد': ('Units', 'Unit', 'الوحدات', 'وحدة'),
                   'M': ('m', 'Meter', 'Meters', 'متر')}
        unit_map = {}
        for unit in units:
            unit_map.setdefault(unit['name'].strip().casefold(), []).append(unit['id'])
        source_units(api, unit_map, [str(r[0] or '').strip() for r in conn.execute('SELECT DISTINCT unit FROM warehouse_items')])
        install(api)
        products = {}
        product_units = {}
        def import_item(item):
            unit = str(item['unit'] or '').strip()
            choices = set()
            for name in (unit,) + aliases.get(unit, ()):
                choices.update(unit_map.get(name.casefold(), []))
            if len(choices) != 1:
                report['skipped'].append({'item_id': item['id'], 'reason': 'unmapped_unit', 'unit': unit})
                return None
            uom = next(iter(choices))
            item_key = 'rekaz-main:warehouse_items:' + str(item['id'])
            if item_key not in products:
                found = api.find('product.product', [('x_rekaz_origin', '=', item_key), ('company_id', '=', api.company_id)], ['id', 'uom_id'])
                if len(found) > 1:
                    raise ValueError('Duplicate product source identity')
                if found:
                    if found[0]['uom_id'][0] != uom:
                        raise ValueError('Product unit mismatch; refusing to change quantities')
                    products[item_key] = found[0]['id']
                else:
                    product_values = {'name': item['item_name'] or item['item_no'], 'default_code': item['item_no'],
                                      'company_id': api.company_id, 'uom_id': uom, 'x_rekaz_origin': item_key}
                    if 'is_storable' in product_fields:
                        product_values['is_storable'] = True
                    products[item_key] = create_product(api, product_values)
            product_units[products[item_key]] = uom
            return products[item_key]
        for item in conn.execute('SELECT * FROM warehouse_items ORDER BY id'):
            if import_item(item):
                report['products_ready'] += 1
        for raw in conn.execute('SELECT * FROM warehouse_tx ORDER BY id'):
            row = dict(raw)
            task = match_task(row, index)
            task_id = task['id'] if task else False
            project_id = task['project_id'][0] if task and task.get('project_id') else False
            try:
                code = direction(row['tx_type'])
                qty = float(row['qty'])
                if qty == 0:
                    report['zero_quantity'].append(row['id'])
                    continue
                if not math.isfinite(qty) or qty <= 0:
                    raise ValueError('Invalid quantity')
            except (ValueError, TypeError):
                report['skipped'].append({'id': row['id'], 'reason': 'invalid_type_or_quantity'})
                continue
            origin = 'rekaz-main:warehouse_tx:' + str(row['id'])
            existing = api.find('stock.picking', [('company_id', '=', api.company_id), ('x_rekaz_origin', '=', origin)], ['id'])
            if len(existing) > 1:
                duplicate = {'id': row['id'], 'reason': 'duplicate_target_documents',
                             'target_ids': sorted(r['id'] for r in existing)}
                report['skipped'].append(duplicate)
                # Preserve every copy for review; do not choose, cancel or
                # delete stock documents merely because source keys match.
                sync.m.save_report(sync.Path(__file__).resolve().parents[1] / 'outputs/reportrek_inventory_report.json', report)
                print('Inventory review: duplicate source_id=' + str(row['id']) +
                      ' target_ids=' + ','.join(map(str, duplicate['target_ids'])), flush=True)
                continue
            if existing:
                # Enrich only importer-owned metadata; leave quantities and
                # validated stock documents untouched.
                pid = existing[0]['id']
                api.call('stock.picking', 'write', [[pid], {'x_rekaz_tx_type': row['tx_type']}])
                lines = api.call('stock.picking', 'read', [[pid]], fields=[move_list])[0][move_list]
                if lines:
                    api.call('stock.move', 'write', [lines, {'x_rekaz_direction': movement_label(row['tx_type'])}])
                report['existing'] += 1
                if not task_id:
                    report['unlinked'].append({'id': row['id'], 'reason': 'no_unique_task'})
                continue
            item = conn.execute('SELECT * FROM warehouse_items WHERE item_no=?', (row['item_no'],)).fetchone()
            if item is None:
                report['skipped'].append({'id': row['id'], 'reason': 'missing_item'})
                continue
            product_id = products.get('rekaz-main:warehouse_items:' + str(item['id']))
            if not product_id:
                report['skipped'].append({'id': row['id'], 'reason': 'item_unit_requires_review'})
                continue
            try:
                route = operation_key(row['tx_type'])
            except ValueError:
                report['skipped'].append({'id': row['id'], 'reason': 'supplier_return_requires_mapping'})
                continue
            if route not in types:
                report['skipped'].append({'id': row['id'], 'reason': 'project_return_operation_not_configured'})
                continue
            operation = types[route]
            src, dst = operation['default_location_src_id'], operation['default_location_dest_id']
            if not src or not dst:
                raise ValueError('Operation default locations must be configured before importing')
            move = {'product_id': product_id,
                    'product_uom_qty': qty, unit_field: product_units[product_id], 'location_id': src[0], 'location_dest_id': dst[0],
                    'company_id': api.company_id, 'x_rekaz_task': task_id, 'x_rekaz_project': project_id,
                    'x_rekaz_direction': movement_label(row['tx_type'])}
            if description_field:
                move[description_field] = row['item_name'] or row['item_no']
            api.call('stock.picking', 'create', [{'picking_type_id': operation['id'], 'company_id': api.company_id,
                     'location_id': src[0], 'location_dest_id': dst[0], 'origin': row['voucher_no'] or origin,
                     'x_rekaz_task': task_id, 'x_rekaz_project': project_id, 'x_rekaz_tx_type': row['tx_type'],
                     'x_rekaz_ticket_no': row['ticket_no'] or False, 'x_rekaz_work_order': row['work_order'] or False,
                     'x_rekaz_origin': origin, move_list: [(0, 0, move)]}])
            report['created'] += 1
            if not task_id:
                report['unlinked'].append({'id': row['id'], 'reason': 'no_unique_task'})
            print('Inventory linked: source_id=' + str(row['id']), flush=True)
    sync.m.save_report(sync.Path(__file__).resolve().parents[1] / 'outputs/reportrek_inventory_report.json', report)
    print(sync.m.dump(report))
    return 2 if report['skipped'] or report['unlinked'] else 0
