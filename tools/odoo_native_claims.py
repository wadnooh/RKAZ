"""Native project claim validation and finance follow-up; no account posting."""
import odoo_project_inventory as inventory

MODEL = 'x_rekaz_project_claim'


def claim_code(company_id, stage_ids, boq_project_ids=None):
    auto_amount = ''
    if boq_project_ids:
        auto_amount = f'''
        if rec.project_id.id in {boq_project_ids!r}:
            if requested:
                lines = rec.x_rekaz_boq_lines.filtered(lambda line: line.x_kind == 'تنفيذ')
                if not rec.x_rekaz_boq_class or not rec.x_rekaz_boq_contract or not lines:
                    raise UserError('أكمل العقد ونوع العمل وبنود التنفيذ قبل الإرسال للمالية.')
                for line in lines:
                    if not line.x_item_id.x_billable or not (line.x_qty > 0) or line.x_contract_id != rec.x_rekaz_boq_contract:
                        raise UserError('راجع الكمية والعقد والبنود قبل إرسال المستخلص.')
            total = rec.company_id.currency_id.round(rec.x_rekaz_boq_total or 0.0)
            amount = total if number else 0.0
            if existing and amount != existing.x_amount:
                raise UserError('المستخلص مرسل للمالية؛ لا يمكن تغيير إجمالي بنوده بعد الإرسال.')
'''
    return f'''for rec in records:
    if rec.company_id.id == {company_id}:
        claims = env['{MODEL}']
        existing = claims.search([('x_task_id', '=', rec.id), ('x_company_id', '=', {company_id})], limit=2)
        if len(existing) > 1:
            raise UserError('توجد نسخة مالية مكررة؛ راجع المسؤول.')
        number = (rec.x_rekaz_claim_no or '').strip()
        amount = rec.x_rekaz_claim_amount or 0.0
        requested = rec.x_rekaz_claim_submit or rec.stage_id.id in {stage_ids!r}
{auto_amount}
        if existing:
            if not requested or number != existing.x_name or amount != existing.x_amount:
                raise UserError('المستخلص مرسل للمالية؛ لا يمكن تغيير بياناته من المعاملة بعد الإرسال.')
        elif requested:
            if not number or not (amount > 0) or amount == float('inf'):
                raise UserError('يجب إدخال رقم المستخلص ومبلغ أكبر من صفر قبل الترحيل.')
            claims.create({{'x_name': number, 'x_amount': amount, 'x_task_id': rec.id,
                           'x_project_id': rec.project_id.id, 'x_company_id': {company_id},
                           'x_status': 'بانتظار مراجعة المالية'}})
{('        if rec.project_id.id in ' + repr(boq_project_ids) + ' and rec.x_rekaz_claim_amount != amount:\n            rec.write({\'x_rekaz_claim_amount\': amount})' if boq_project_ids else '')}
'''


def claim_page_arch(details_visibility=False, auto_amount=False):
    visibility = ''
    return ('<xpath expr="//notebook" position="inside"><page string="المستخلص والمالية" name="rekaz_claim"'
            + visibility
            + '><group><field name="x_rekaz_claim_no"/>'
            + ('<field name="x_rekaz_boq_class" string="نوع العمل"/><field name="x_rekaz_boq_total" string="إجمالي بنود التنفيذ" readonly="1"/>' if auto_amount else '')
            + ('<field name="x_rekaz_claim_amount" readonly="1"/>' if auto_amount else '<field name="x_rekaz_claim_amount"/>')
            + ('<field name="x_rekaz_claim_submit" readonly="not x_rekaz_claim_no or x_rekaz_claim_amount &lt;= 0"/>' if auto_amount else '<field name="x_rekaz_claim_submit"/>')
            + '</group>'
            + ('<div class="alert alert-info" invisible="x_rekaz_claim_no">بانتظار كتابة رقم المستخلص. يظهر إجمالي البنود للمراجعة ولا يُرسل للمالية تلقائياً.</div>' if auto_amount else '')
            + '<div>'
            + ('يُحسب الإجمالي من بنود التنفيذ حسب نوع العمل. اكتب رقم المستخلص واحفظ لملء المبلغ تلقائياً، ثم فعّل الإرسال للمالية. ' if auto_amount else 'أدخل الرقم والمبلغ ثم فعّل الإرسال واحفظ المعاملة. ')
            + 'ينشأ سجل للمتابعة المالية دون قيد محاسبي.</div></page></xpath>')


def upsert(api, model, domain, values):
    rows = api.find(model, domain, ['id'])
    if len(rows) > 1:
        raise ValueError('Duplicate claims configuration: ' + model)
    if rows:
        try:
            api.call(model, 'write', [[rows[0]['id']], values])
        except ValueError as exc:
            raise ValueError(str(exc) + ' configuration=' + str(values.get('name', model))) from None
        return rows[0]['id']
    try:
        return api.call(model, 'create', [values])
    except ValueError as exc:
        raise ValueError(str(exc) + ' configuration=' + str(values.get('name', model))) from None


def install_access(api, model_id, groups):
    available = api.find('ir.model', [('model', '=', 'ir.access')], ['id'])
    if available:
        for label, group_id in groups.items():
            upsert(api, 'ir.access', [('name', '=', 'rekaz_claim_' + label), ('model_id', '=', model_id)],
                   {'name': 'rekaz_claim_' + label, 'model_id': model_id, 'group_id': group_id,
                    'operation': 'cru' if label == 'finance' else 'cr',
                    'domain': "[('x_company_id','in',company_ids)]", 'active': True})
        upsert(api, 'ir.access', [('name', '=', 'Rekaz claim company isolation'), ('model_id', '=', model_id)],
               {'name': 'Rekaz claim company isolation', 'model_id': model_id, 'group_id': False,
                'operation': 'crud', 'domain': "[('x_company_id','in',company_ids)]", 'active': True})
    else:
        for label, group_id in groups.items():
            api.ensure('ir.model.access', [('name', '=', 'rekaz_claim_' + label), ('model_id', '=', model_id)],
                       {'name': 'rekaz_claim_' + label, 'model_id': model_id, 'group_id': group_id,
                        'perm_read': True, 'perm_create': True, 'perm_write': label == 'finance', 'perm_unlink': False})
        api.ensure('ir.rule', [('name', '=', 'Rekaz claim company isolation'), ('model_id', '=', model_id)],
                   {'name': 'Rekaz claim company isolation', 'model_id': model_id,
                    'domain_force': "[('x_company_id','in',company_ids)]"})


def install(api):
    automation = api.call('base.automation', 'fields_get', [], attributes=['type', 'selection'])
    if 'on_create_or_write' not in dict(automation.get('trigger', {}).get('selection', [])) or 'action_server_ids' not in automation:
        raise ValueError('Required automation API unavailable')
    task_models = api.find('ir.model', [('model', '=', 'project.task')], ['id'])
    root = api.find('ir.model.data', [('module', '=', 'account'), ('name', '=', 'menu_finance')], ['res_id'])
    accounting_roots = api.find('ir.ui.menu', [('parent_id', '=', False), ('name', 'in', ['Accounting', 'المحاسبة'])], ['id'])
    if len(accounting_roots) > 1:
        raise ValueError('Multiple Accounting applications; select the intended menu')
    if accounting_roots:
        root = [{'res_id': accounting_roots[0]['id']}]
    groups = {}
    for label, module, name in [('project', 'project', 'group_project_user'), ('finance', 'account', 'group_account_user')]:
        found = api.find('ir.model.data', [('module', '=', module), ('name', '=', name)], ['res_id'])
        if len(found) != 1:
            raise ValueError('Required application group missing: ' + label)
        groups[label] = found[0]['res_id']
    if len(root) != 1 or len(task_models) != 1:
        raise ValueError('Accounting/Projects application missing')
    model_id = api.ensure('ir.model', [('model', '=', MODEL)],
                          {'name': 'مستخلصات ركاز والمتابعة المالية', 'model': MODEL, 'state': 'manual'})
    for name, kind, label, extra in [
        ('x_amount', 'float', 'مبلغ المستخلص', {}),
        ('x_task_id', 'many2one', 'المعاملة', {'relation': 'project.task', 'index': True, 'required': True, 'on_delete': 'restrict'}),
        ('x_project_id', 'many2one', 'المشروع', {'relation': 'project.project'}),
        ('x_company_id', 'many2one', 'الشركة', {'relation': 'res.company', 'required': True, 'on_delete': 'restrict'}),
        ('x_status', 'char', 'حالة المتابعة المالية', {})]:
        inventory.field(api, MODEL, name, kind, label, **extra)
    for name, kind, label in [('x_rekaz_claim_no', 'char', 'رقم المستخلص'),
                              ('x_rekaz_claim_amount', 'float', 'مبلغ المستخلص'),
                              ('x_rekaz_claim_submit', 'boolean', 'إرسال إلى المستخلصات والمالية')]:
        inventory.field(api, 'project.task', name, kind, label)
    install_access(api, model_id, groups)
    stages = api.call('project.task.type', 'search_read', [[('name', 'in', ['المستخلصات', 'المالية', 'المستخلصات والمالية'])]], fields=['id'], limit=False)
    stage_ids = [r['id'] for r in stages]
    task_fields = api.call('project.task', 'fields_get', [], attributes=['type'])
    boq_projects = None
    if 'x_rekaz_boq_total' in task_fields:
        from odoo_contract_boq import project_scope
        boq_projects = project_scope(api)
    label = 'Rekaz native claim guard company ' + str(api.company_id)
    action = upsert(api, 'ir.actions.server', [('name', '=', label), ('model_id', '=', task_models[0]['id'])],
                    {'name': label, 'model_id': task_models[0]['id'], 'state': 'code', 'code': claim_code(api.company_id, stage_ids, boq_projects)})
    upsert(api, 'base.automation', [('name', '=', label), ('model_id', '=', task_models[0]['id'])],
           {'name': label, 'model_id': task_models[0]['id'], 'trigger': 'on_create_or_write', 'active': True,
            'filter_domain': repr([('company_id', '=', api.company_id)]), 'action_server_ids': [(6, 0, [action])]})
    view = api.find('ir.model.data', [('module', '=', 'project'), ('name', '=', 'view_task_form2')], ['res_id'])
    if len(view) != 1:
        raise ValueError('Project task form missing')
    task_fields = api.call('project.task', 'fields_get', [], attributes=['type'])
    arch = claim_page_arch('x_rekaz_boq_details_visible' in task_fields, auto_amount=bool(boq_projects))
    upsert(api, 'ir.ui.view', [('name', '=', 'Rekaz task claim form'), ('model', '=', 'project.task')],
           {'name': 'Rekaz task claim form', 'model': 'project.task', 'type': 'form', 'inherit_id': view[0]['res_id'], 'arch_db': arch})
    for kind, arch in [('list', '<list create="0" delete="0"><field name="x_name" string="رقم المستخلص"/><field name="x_amount"/><field name="x_task_id"/><field name="x_project_id"/><field name="x_status"/></list>'),
                       ('form', '<form create="0" delete="0"><sheet><group><field name="x_name" string="رقم المستخلص" readonly="1"/><field name="x_amount" readonly="1"/><field name="x_task_id" readonly="1"/><field name="x_project_id" readonly="1"/><field name="x_company_id" readonly="1"/><field name="x_status"/></group></sheet></form>')]:
        upsert(api, 'ir.ui.view', [('name', '=', 'Rekaz claims ' + kind), ('model', '=', MODEL)],
               {'name': 'Rekaz claims ' + kind, 'model': MODEL, 'type': kind, 'arch_db': arch})
    window = upsert(api, 'ir.actions.act_window', [('name', '=', 'مستخلصات ركاز'), ('res_model', '=', MODEL)],
                    {'name': 'مستخلصات ركاز', 'res_model': MODEL, 'view_mode': 'list,form',
                     'domain': "[('x_company_id','in',allowed_company_ids)]"})
    upsert(api, 'ir.ui.menu', [('name', '=', 'مستخلصات ركاز'), ('action', '=', 'ir.actions.act_window,' + str(window))],
               {'name': 'مستخلصات ركاز', 'parent_id': root[0]['res_id'], 'action': 'ir.actions.act_window,' + str(window)})
    print('Native claims guard installed; no historical backfill and no accounting entries posted.', flush=True)
