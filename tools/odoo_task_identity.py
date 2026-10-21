"""Global task identity normalization and Odoo deployment helpers."""

NORMALIZE_CODE = '''def canonical(value):
    value = (value or '').upper()
    for source, target in zip('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789'):
        value = value.replace(source, target)
    value = ''.join(value.split())
    for separator in ['-', '/', '.', '\\u200b', '\\u200e', '\\u200f', '\\ufeff']:
        value = value.replace(separator, '')
    if value.isdigit():
        value = value.lstrip('0') or '0'
    return value or False
'''


def audit_code():
    return """if not env.user.has_group('base.group_system'):
    raise UserError('الفحص متاح لمسؤول النظام فقط.')
""" + NORMALIZE_CODE + """
rows = env['project.task'].sudo().with_context(active_test=False).search([])
duplicates = []
for field, label in [('x_rekaz_work_order','أمر العمل'),('x_rekaz_fault_no','رقم العطل')]:
    groups = {}
    for row in rows:
        key = canonical(row[field])
        if key:
            if key not in groups:
                groups[key] = []
            groups[key].append(row.id)
    for key, ids in groups.items():
        if len(ids)>1:
            duplicates.append(label+' '+key+' : '+str(ids))
env.cr.execute('SELECT 1')
sql_ready = env.cr.fetchone()[0] == 1
message = 'المعاملات: '+str(len(rows))+'؛ مجموعات التكرار: '+str(len(duplicates))+'؛ SQL: '+str(sql_ready)+'؛ '+ ' | '.join(duplicates)
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'فحص أرقام المعاملات','message':message,'sticky':True}}
"""


def guard_code():
    return NORMALIZE_CODE + """
all_rows = env['project.task'].sudo().with_context(active_test=False).search([])
for rec in records:
    values = {}
    for source, target, label in [('x_rekaz_work_order','x_rekaz_order_key','أمر العمل'),('x_rekaz_fault_no','x_rekaz_fault_key','رقم العطل')]:
        key = canonical(rec.sudo()[source])
        if key:
            for other in all_rows:
                if other.id != rec.id and canonical(other[source]) == key:
                    raise UserError('لا يمكن الحفظ: '+label+' مكرر ومستخدم في معاملة أخرى. راجع الرقم قبل المتابعة.')
        if rec.sudo()[target] != key:
            values[target] = key
    if values:
        rec.sudo().write(values)
"""


def setup_code():
    return """if not env.user.has_group('base.group_system'):
    raise UserError('الإعداد متاح لمسؤول النظام فقط.')
""" + NORMALIZE_CODE + """
model = env['ir.model']._get('project.task')
for name, label in [('x_rekaz_order_key','مفتاح أمر العمل الفريد'),('x_rekaz_fault_key','مفتاح العطل الفريد')]:
    field = env['ir.model.fields'].search([('model_id','=',model.id),('name','=',name)],limit=1)
    if not field:
        env['ir.model.fields'].create({'model_id':model.id,'name':name,'ttype':'char','field_description':label,'state':'manual','readonly':True,'copied':False,'groups':[(6,0,[env.ref('base.group_system').id])]})
env.flush_all()
rows = env['project.task'].sudo().with_context(active_test=False).search([],order='id')
duplicates = []
for source, target, label in [('x_rekaz_work_order','x_rekaz_order_key','أمر العمل'),('x_rekaz_fault_no','x_rekaz_fault_key','رقم العطل')]:
    seen = {}
    for row in rows:
        key = canonical(row[source])
        stored = key
        if key and key in seen:
            duplicates.append(label+' '+key+' : '+str(seen[key])+','+str(row.id))
            stored = False
        elif key:
            seen[key] = row.id
        if target == 'x_rekaz_order_key':
            env.cr.execute('UPDATE project_task SET x_rekaz_order_key = %s WHERE id = %s', (stored or None,row.id))
        else:
            env.cr.execute('UPDATE project_task SET x_rekaz_fault_key = %s WHERE id = %s', (stored or None,row.id))
env.invalidate_all()
env.cr.execute("CREATE UNIQUE INDEX IF NOT EXISTS rekaz_task_unique_order_v1 ON project_task (x_rekaz_order_key) WHERE x_rekaz_order_key IS NOT NULL AND x_rekaz_order_key <> ''")
env.cr.execute("CREATE UNIQUE INDEX IF NOT EXISTS rekaz_task_unique_fault_v1 ON project_task (x_rekaz_fault_key) WHERE x_rekaz_fault_key IS NOT NULL AND x_rekaz_fault_key <> ''")
guard = GUARD
name = 'Rekaz global task identity guard'
automation = env['base.automation'].search([('name','=',name),('model_id','=',model.id)],limit=1)
field_ids = env['ir.model.fields'].search([('model_id','=',model.id),('name','in',['x_rekaz_work_order','x_rekaz_fault_no','x_rekaz_order_key','x_rekaz_fault_key'])]).ids
vals = {'name':name,'model_id':model.id,'trigger':'on_create_or_write','trigger_field_ids':[(6,0,field_ids)],'active':True,'filter_domain':'[]'}
if automation:
    automation.write(vals)
else:
    automation = env['base.automation'].create(vals)
server = env['ir.actions.server'].search([('name','=',name),('model_id','=',model.id)],limit=1)
vals = {'name':name,'model_id':model.id,'state':'code','code':guard}
if server:
    server.write(vals)
else:
    server = env['ir.actions.server'].create(vals)
automation.write({'action_server_ids':[(6,0,[server.id])]})
test_count = 0
for column in ['x_rekaz_order_key','x_rekaz_fault_key']:
    usable = rows.filtered(lambda r: r[column])[:2]
    if len(usable) == 2:
        if column == 'x_rekaz_order_key':
            query = "UPDATE project_task SET x_rekaz_order_key = (SELECT x_rekaz_order_key FROM project_task WHERE id = "+str(usable[0].id)+") WHERE id = "+str(usable[1].id)
        else:
            query = "UPDATE project_task SET x_rekaz_fault_key = (SELECT x_rekaz_fault_key FROM project_task WHERE id = "+str(usable[0].id)+") WHERE id = "+str(usable[1].id)
        env.cr.execute("DO $rekaz$ BEGIN BEGIN "+query+"; RAISE EXCEPTION 'UNIQUE GUARD DID NOT BLOCK'; EXCEPTION WHEN unique_violation THEN NULL; END; END $rekaz$")
        test_count += 1
review_ids = []
for field in ['x_rekaz_work_order','x_rekaz_fault_no']:
    seen = {}
    for row in rows:
        key = canonical(row[field])
        if key:
            if key in seen:
                review_ids += [seen[key],row.id]
            else:
                seen[key] = row.id
review = env['ir.actions.act_window'].search([('name','=','مراجعة الأرقام المكررة القديمة'),('res_model','=','project.task')],limit=1)
vals = {'name':'مراجعة الأرقام المكررة القديمة','res_model':'project.task','view_mode':'list,form','domain':repr([('id','in',list(set(review_ids)))]),'context':"{'active_test':False,'create':False}"}
if review:
    review.write(vals)
else:
    review = env['ir.actions.act_window'].create(vals)
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'تم تفعيل منع التكرار','message':'اختبار فهارس التفرّد: '+str(test_count)+' ناجح. الحالات القديمة للمراجعة: '+str(len(duplicates))+'؛ '+ ' | '.join(duplicates),'sticky':True}}
""".replace('guard = GUARD', 'guard = ' + repr(guard_code()))
