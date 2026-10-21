"""Install company-scoped custom Rekaz finance automation (not account.move posting)."""
import rekaz_odoo_sync as sync


def action_code(table, company_id):
    f = sync.m.field_name
    number = f('invoice_id') if table == 'tickets' else f('extract_no')
    fallback = f('invoice_no') if table == 'tickets' else number
    amount = f('items_value') if table == 'tickets' else f('amount')
    date = f('execution_date') if table == 'tickets' else f('order_date')
    ticket = f('ticket_no')
    return f'''for rec in records:
    if rec.x_company_id.id == {company_id}:
        number = str(rec[{number!r}] or rec[{fallback!r}] or '').strip()
        if number:
            key = {table!r} + ':' + str(rec.id)
            invoices = env['x_rekaz_sync_invoices']
            existing = invoices.search([('x_company_id', '=', {company_id}), ('x_finance_source', '=', key)], limit=2)
            if len(existing) > 1:
                raise UserError('Duplicate finance source identity')
            amount = rec[{amount!r}] or 0.0
            if {table!r} == 'tickets':
                lines = env['x_rekaz_sync_ticket_boq_lines'].search([('x_company_id', '=', {company_id}), ({ticket!r}, '=', rec[{ticket!r}])])
                if lines:
                    amount = 0.0
                    for line in lines:
                        amount = amount + (line[{f('final_total')!r}] if line[{f('final_total')!r}] is not False else (line[{f('line_total')!r}] or 0.0))
            values = {{'x_name': number, 'x_company_id': {company_id}, 'x_finance_source': key,
                       {f('invoice_id')!r}: number, {f('invoice_no')!r}: str(rec[{fallback!r}] or number),
                       {f('work_order')!r}: rec[{f('work_order')!r}], {f('value')!r}: amount,
                       {f('invoice_date')!r}: rec[{date!r}] or False}}
            if {table!r} == 'tickets':
                values[{ticket!r}] = rec[{ticket!r}]
            if existing:
                existing.write(values)
            else:
                values['x_source_key'] = 'rekaz-main:finance-auto:' + key
                values[{f('status')!r}] = 'فاتورة صادرة'
                invoices.create(values)
'''


def install(api):
    # Probe capabilities BEFORE creating any metadata.
    automation_fields = api.call('base.automation', 'fields_get', [], attributes=['type', 'selection'])
    triggers = dict(automation_fields.get('trigger', {}).get('selection', []))
    if 'on_create_or_write' not in triggers or 'action_server_ids' not in automation_fields:
        raise ValueError('This Odoo database does not support the required automation API')
    models = {}
    for table in ('tickets', 'primary_team_orders', 'invoices'):
        found = api.find('ir.model', [('model', '=', sync.m.model_name(table))], ['id'])
        if len(found) != 1:
            raise ValueError('Missing or duplicate Rekaz model: ' + table)
        models[table] = found[0]['id']
    api.ensure('ir.model.fields', [('model_id', '=', models['invoices']), ('name', '=', 'x_finance_source')],
               {'model_id': models['invoices'], 'name': 'x_finance_source', 'field_description': 'هوية المعاملة المالية',
                'ttype': 'char', 'state': 'manual', 'index': True})
    for table in ('tickets', 'primary_team_orders'):
        name = 'Rekaz finance link ' + table + ' company ' + str(api.company_id)
        fields = api.call(sync.m.model_name(table), 'fields_get', [], attributes=['type'])
        required = ['work_order', 'invoice_id', 'invoice_no', 'items_value', 'execution_date', 'ticket_no'] if table == 'tickets' else ['work_order', 'extract_no', 'amount', 'order_date']
        if any(sync.m.field_name(field) not in fields for field in required):
            raise ValueError('Required financial source field missing: ' + table)
        action = api.ensure('ir.actions.server', [('name', '=', name), ('model_id', '=', models[table])],
                            {'name': name, 'model_id': models[table], 'state': 'code', 'code': action_code(table, api.company_id)})
        api.ensure('base.automation', [('name', '=', name), ('model_id', '=', models[table])],
                   {'name': name, 'model_id': models[table], 'trigger': 'on_create_or_write',
                    'active': True, 'filter_domain': repr([('x_company_id', '=', api.company_id)]),
                    'action_server_ids': [(6, 0, [action])]})
    print('Finance automation installed. Existing records require a reviewed backfill; no accounting journal entries posted.', flush=True)
