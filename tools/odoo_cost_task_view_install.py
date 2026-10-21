if not env.user.has_group('base.group_system'):
    raise UserError('لمسؤول النظام فقط.')
F = env['ir.model.fields']
mid = env['ir.model']._get_id('project.task')
if not F.search_count([('model','=','project.task'),('name','=','x_rekaz_approved_costs')]):
    F.create({'model_id':mid,'name':'x_rekaz_approved_costs','field_description':'التكاليف التشغيلية المسجلة والمعتمدة','ttype':'one2many','relation':'x_rekaz_cost_entry','relation_field':'x_task','state':'manual','readonly':True,'copied':False,'groups':[(6,0,[env.ref('base.group_system').id])]})
if not F.search_count([('model','=','project.task'),('name','=','x_rekaz_approved_cost_total')]):
    F.create({'model_id':mid,'name':'x_rekaz_approved_cost_total','field_description':'إجمالي التكاليف المسجلة المعتمدة','ttype':'float','state':'manual','readonly':True,'copied':False,'store':False,'compute':"for task in self:\n    total = 0.0\n    for line in task.x_rekaz_approved_costs:\n        if line.x_status == 'approved':\n            total += line.x_total\n    task['x_rekaz_approved_cost_total'] = total",'groups':[(6,0,[env.ref('base.group_system').id])]})
budget = F.search([('model','=','project.task'),('name','=','x_rekaz_cost_budget')],limit=1)
budget.write({'field_description':'ميزانية التكلفة المخططة'})
v = env['ir.ui.view'].search([('key','=','rekaz.cost_center.task')],limit=1)
if 'x_rekaz_approved_costs' not in v.arch_db:
    addition = '''<group groups="base.group_system"><field name="x_rekaz_approved_cost_total"/></group><field name="x_rekaz_approved_costs" groups="base.group_system" domain="[('x_status','=','approved')]" readonly="1"><list create="0" edit="0" delete="0"><field name="x_date"/><field name="x_category"/><field name="x_source"/><field name="x_quantity"/><field name="x_rate"/><field name="x_total" sum="المعتمد"/></list></field>'''
    v.write({'arch_db':v.arch_db.replace('</page>',addition+'</page>')})
F.search([('model','=','x_rekaz_cost_entry'),('name','=','x_name')],limit=1).write({'field_description':'وصف التكلفة'})
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'عرض تكاليف المعاملة','message':'تم ربط عرض التكاليف المعتمدة وإجماليها داخل المعاملة، للمسؤول فقط لحماية بيانات العمالة. لا تمثل الأصفار اكتمال التكلفة.','sticky':True}}
