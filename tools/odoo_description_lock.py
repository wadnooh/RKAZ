"""Freeze the first completed transaction description; editors are explicitly assigned."""
GROUP_XMLID = 'rekaz_description.group_edit_locked_description'


def guard_code(company, projects):
    return f'''for rec in records:
    if rec.company_id.id == {company} and rec.project_id.id in {projects!r}:
        snapshot = rec.sudo().x_rekaz_description_snapshot or ''
        current = str(rec.description or '')
        if not snapshot and not current:
            rec.sudo().write({{'description': templates[rec.project_id.id]}})
            continue
        editor = env.user.has_group('base.group_system') or env.user.has_group('{GROUP_XMLID}')
        if snapshot and current != str(snapshot) and not editor:
            raise UserError('الوصف معتمد ومقفل بعد أول إدخال. يلزم منح صلاحية تعديل الوصف بعد الحفظ من مسؤول النظام.')
        populated = False
        for cell in current.split('<td')[1:]:
            value = cell.split('>', 1)[-1].split('</td>', 1)[0]
            for tag in ['<br>', '<br/>', '<br />', '<p>', '</p>', '<div>', '</div>', '&nbsp;', '&#160;']:
                value = value.replace(tag, '')
            if value.strip():
                populated = True
        updates = {{}}
        if current and populated and current != str(snapshot):
            updates['x_rekaz_description_snapshot'] = current
        locked = bool(snapshot) or bool(updates)
        if rec.x_rekaz_description_locked != locked:
            updates['x_rekaz_description_locked'] = locked
        if updates:
            rec.sudo().write(updates)
'''


def installer_code(company=1, projects=(5, 6)):
    guard = guard_code(company, list(projects))
    return f'''if not env.user.has_group('base.group_system'):
    raise UserError('تثبيت الفورمة متاح لمسؤول النظام فقط.')
Task = env['project.task']
Field = env['ir.model.fields']
task_model = env['ir.model']._get('project.task')
system_group = env.ref('base.group_system')
group = env.ref('{GROUP_XMLID}', raise_if_not_found=False)
if not group:
    group = env['res.groups'].create({{'name': 'تعديل الوصف بعد الحفظ'}})
    env['ir.model.data'].create({{'module':'rekaz_description','name':'group_edit_locked_description','model':'res.groups','res_id':group.id,'noupdate':True}})
for name, kind, label in [('x_rekaz_description_snapshot','html','نسخة الوصف المعتمدة'),('x_rekaz_description_locked','boolean','الوصف معتمد ومقفل')]:
    field = Field.search([('model_id','=',task_model.id),('name','=',name)],limit=1)
    values = {{'model_id':task_model.id,'name':name,'ttype':kind,'field_description':label,'state':'manual','readonly':True}}
    if 'copied' in Field._fields:
        values['copied'] = False
    if kind == 'html':
        values['groups'] = [(6,0,[system_group.id])]
    if not field:
        Field.create(values)
templates = {{}}
for project_id in {list(projects)!r}:
    sample = Task.search([('company_id','=',{company}),('project_id','=',project_id),('description','!=',False)],order='id',limit=1)
    description = str(sample.description or '')
    rows = []
    for row in description.split('<tr>')[1:]:
        if '</th>' in row:
            rows.append('<tr>' + row.split('</th>',1)[0] + '</th><td><br/></td></tr>')
    if not rows:
        raise UserError('لا يوجد نموذج وصف صالح للمشروع؛ لم يتم تثبيت القفل.')
    if project_id == 6 and not any('رقم الإشعار' in row for row in rows):
        rows.append('<tr><th scope="row">رقم الإشعار</th><td><br/></td></tr>')
    heading = 'معاملات التعزيز / اسكيمات' if project_id == 6 else 'المعاملات والأعطال'
    templates[project_id] = '<div dir="rtl" style="text-align:right"><h3>' + heading + '</h3><table class="table table-bordered"><tbody>' + ''.join(rows) + '</tbody></table></div>'
onchange_code = 'templates = ' + repr(templates) + "\\nfor rec in ([record] if record else []):\\n    if rec.company_id.id == {company} and rec.project_id.id in templates and not rec.description:\\n        rec.update({{'description': templates[rec.project_id.id]}})"
guard_code = 'templates = ' + repr(templates) + '\\n' + {guard!r}
validation = "        if not snapshot and current:\\n            for row in templates[rec.project_id.id].split('<tr>')[1:]:\\n                label = row.split('</th>',1)[0].split('>',1)[-1]\\n                if label not in current:\\n                    raise UserError('استخدم فورمة الوصف الثابتة وأكمل القيم دون تغيير عناوينها.')\\n"
guard_code = guard_code.replace('        editor = ', validation + '        editor = ')
for label, code in [('Rekaz locked description guard {company}', guard_code),('Rekaz new description template {company}',onchange_code)]:
    action = env['ir.actions.server'].search([('name','=',label),('model_id','=',task_model.id)],limit=1)
    vals = {{'name':label,'model_id':task_model.id,'state':'code','code':code}}
    if action:
        action.write(vals)
    else:
        action = env['ir.actions.server'].create(vals)
    rule = env['base.automation'].search([('name','=',label),('model_id','=',task_model.id)],limit=1)
    vals = {{'name':label,'model_id':task_model.id,'trigger':'on_change' if 'template' in label else 'on_create_or_write','active':True,'filter_domain':repr([('company_id','=',{company}),('project_id','in',{list(projects)!r})]),'action_server_ids':[(6,0,[action.id])]}}
    if 'template' in label:
        vals['on_change_field_ids'] = [(6,0,Field.search([('model_id','=',task_model.id),('name','=','project_id')]).ids)]
    if rule:
        rule.write(vals)
    else:
        env['base.automation'].create(vals)
parent = env.ref('project.view_task_form2')
arch = '<data><xpath expr="//sheet" position="inside"><field name="x_rekaz_description_locked" invisible="1"/></xpath><xpath expr="//field[@name=\\\'description\\\']" position="replace"><field name="description" widget="html" readonly="x_rekaz_description_locked" groups="!base.group_system,!{GROUP_XMLID}"/><field name="description" widget="html" groups="base.group_system,{GROUP_XMLID}"/><div class="alert alert-info" invisible="not x_rekaz_description_locked">الوصف محفوظ ومعتمد. تعديله متاح فقط لمن مُنح صلاحية تعديل الوصف بعد الحفظ.</div></xpath></data>'
view = env['ir.ui.view'].search([('name','=','Rekaz fixed description form'),('model','=','project.task')],limit=1)
vals = {{'name':'Rekaz fixed description form','model':'project.task','inherit_id':parent.id,'type':'form','arch_db':arch}}
if view:
    view.write(vals)
else:
    env['ir.ui.view'].create(vals)
for rec in Task.search([('company_id','=',{company}),('project_id','in',{list(projects)!r}),('description','!=',False)]):
    if not rec.sudo().x_rekaz_description_snapshot:
        rec.sudo().write({{'x_rekaz_description_snapshot':str(rec.description),'x_rekaz_description_locked':True}})
action = {{'type':'ir.actions.client','tag':'display_notification','params':{{'title':'تم تثبيت فورمة الوصف','message':'تم حفظ الأوصاف الحالية وقفلها، وتجهيز فورمة المعاملات الجديدة. لم يتم منح صلاحية التعديل لأي مستخدم جديد.','sticky':True}}}}
'''
