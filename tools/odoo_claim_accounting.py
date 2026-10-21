"""Install explicit draft journal entries for approved Rekaz claims."""
import odoo_native_claims as claims
import odoo_project_inventory as inventory


def entry_code(company_id, account_company_field):
    check = "company.id not in account.company_ids.ids" if account_company_field == 'company_ids' else 'account.company_id != company'
    return f'''if not env.context.get('rekaz_entry_link'):
    for rec in records:
        if rec.x_company_id.id == {company_id} and rec.x_entry_request:
            company = rec.x_company_id
            amount = company.currency_id.round(rec.x_amount or 0.0)
            if not (rec.x_name or '').strip() or not (amount > 0) or amount == float('inf') or not rec.x_task_id:
                raise UserError('أكمل رقم المستخلص والمبلغ والمعاملة قبل إنشاء القيد.')
            journal = rec.x_entry_journal
            debit = rec.x_entry_debit
            credit = rec.x_entry_credit
            if not journal or journal.company_id != company or journal.type != 'general':
                raise UserError('اختر يومية قيود عامة تابعة للشركة.')
            if not debit or not credit or debit == credit:
                raise UserError('اختر حساباً مديناً وحساباً دائناً مختلفين.')
            for account in [debit, credit]:
                if {check}:
                    raise UserError('الحساب المحاسبي لا يتبع شركة المستخلص.')
                if account.account_type in ['asset_receivable', 'liability_payable'] and not rec.x_entry_partner:
                    raise UserError('العميل أو المورد إلزامي عند استخدام حساب ذمم.')
            key = 'rekaz-claim:' + str(company.id) + ':' + str(rec.id)
            moves = env['account.move'].search([('company_id', '=', company.id), ('x_rekaz_claim_key', '=', key)], limit=2)
            if len(moves) > 1:
                raise UserError('يوجد قيد مكرر لهذا المستخلص؛ راجع المحاسب.')
            if rec.x_entry_move:
                if not moves or rec.x_entry_move != moves:
                    raise UserError('رابط القيد لا يطابق المستخلص؛ راجع المحاسب.')
            elif moves:
                raise UserError('يوجد قيد سابق يحتاج مراجعة الربط؛ لن ينشأ قيد جديد.')
            else:
                if rec.x_task_id.company_id != company or (rec.x_task_id.x_rekaz_claim_no or '').strip() != rec.x_name or rec.x_task_id.x_rekaz_claim_amount != rec.x_amount:
                    raise UserError('بيانات المستخلص لا تطابق المعاملة الأصلية.')
                label = 'مستخلص ' + rec.x_name
                partner = rec.x_entry_partner.id or False
                move = env['account.move'].with_company(company).create({{
                    'move_type': 'entry', 'journal_id': journal.id, 'company_id': company.id,
                    'date': datetime.date.today().strftime('%Y-%m-%d'), 'ref': label,
                    'x_rekaz_claim_key': key,
                    'line_ids': [(0, 0, {{'name': label, 'account_id': debit.id, 'partner_id': partner, 'debit': amount, 'credit': 0.0}}),
                                 (0, 0, {{'name': label, 'account_id': credit.id, 'partner_id': partner, 'debit': 0.0, 'credit': amount}})]}})
                rec.with_context(rekaz_entry_link=True).write({{'x_entry_move': move.id}})
'''


def install(api):
    models = api.find('ir.model', [('model', '=', claims.MODEL)], ['id'])
    if len(models) != 1:
        raise ValueError('Run --claims-setup first')
    schema = api.call('account.account', 'fields_get', [], attributes=['type'])
    company_field = 'company_ids' if 'company_ids' in schema else 'company_id'
    for name, kind, label, relation in [
        ('x_entry_journal', 'many2one', 'يومية القيد', 'account.journal'),
        ('x_entry_debit', 'many2one', 'الحساب المدين', 'account.account'),
        ('x_entry_credit', 'many2one', 'الحساب الدائن', 'account.account'),
        ('x_entry_partner', 'many2one', 'العميل / المورد', 'res.partner'),
        ('x_entry_move', 'many2one', 'القيد المحاسبي', 'account.move'),
        ('x_entry_request', 'boolean', 'إنشاء قيد مسودة', None)]:
        inventory.field(api, claims.MODEL, name, kind, label, **({'relation': relation, 'on_delete': 'restrict'} if relation else {}))
    inventory.field(api, 'account.move', 'x_rekaz_claim_key', 'char', 'هوية مستخلص ركاز', index=True)
    name = 'Rekaz claim draft entry company ' + str(api.company_id)
    action = claims.upsert(api, 'ir.actions.server', [('name', '=', name), ('model_id', '=', models[0]['id'])],
                           {'name': name, 'model_id': models[0]['id'], 'state': 'code', 'code': entry_code(api.company_id, company_field)})
    claims.upsert(api, 'base.automation', [('name', '=', name), ('model_id', '=', models[0]['id'])],
                  {'name': name, 'model_id': models[0]['id'], 'trigger': 'on_create_or_write', 'active': True,
                   'filter_domain': repr([('x_company_id', '=', api.company_id)]), 'action_server_ids': [(6, 0, [action])]})
    view = api.find('ir.ui.view', [('name', '=', 'Rekaz claims form'), ('model', '=', claims.MODEL)], ['id'])
    if len(view) != 1:
        raise ValueError('Claims form not found; run --claims-setup')
    domain = "[('" + company_field + "', 'in', [x_company_id])]" if company_field == 'company_ids' else "[('company_id', '=', x_company_id)]"
    arch = '''<xpath expr="//sheet" position="inside"><group string="القيد المحاسبي"><field name="x_entry_journal" domain="[('company_id','=',x_company_id),('type','=','general')]"/><field name="x_entry_debit" domain="''' + domain + '''"/><field name="x_entry_credit" domain="''' + domain + '''"/><field name="x_entry_partner"/><field name="x_entry_request"/><field name="x_entry_move" readonly="1"/></group><div>ينشئ قيداً مسودة بعملة الشركة دون ترحيل نهائي أو احتساب ضريبة تلقائي.</div></xpath>'''
    claims.upsert(api, 'ir.ui.view', [('name', '=', 'Rekaz claim accounting form'), ('model', '=', claims.MODEL)],
                  {'name': 'Rekaz claim accounting form', 'model': claims.MODEL, 'type': 'form', 'inherit_id': view[0]['id'], 'arch_db': arch})
    print('Draft journal entry automation installed; no historical entries created and none posted.', flush=True)
