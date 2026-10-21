if not env.user.has_group('base.group_system'):
    raise UserError('الإعداد لمسؤول النظام فقط.')
M = env['ir.model'].search([('model','=','x_rekaz_cost_entry')],limit=1)
if not M:
    M = env['ir.model'].create({'name':'تكاليف معاملات ركاز','model':'x_rekaz_cost_entry','state':'manual'})
F = env['ir.model.fields']
specs = [
('x_task','المعاملة','many2one',{'relation':'project.task','on_delete':'restrict','required':True}),
('x_company_id','الشركة','many2one',{'relation':'res.company','on_delete':'restrict','required':True}),
('x_date','تاريخ التكلفة','date',{'required':True}),
('x_category','تخصص التكلفة','selection',{'required':True,'selection_ids':[(0,0,{'value':'labor','name':'عمالة فعلية'}),(0,0,{'value':'equipment','name':'تشغيل معدات'}),(0,0,{'value':'expense','name':'مصروف مباشر'}),(0,0,{'value':'shared','name':'نصيب مصروف مشترك'}),(0,0,{'value':'disposal','name':'نقل أو معالجة مخلفات'})]}),
('x_source','مرجع المصدر الفريد','char',{'required':True}),
('x_quantity','الكمية أو الساعات الفعلية','float',{'required':True}),
('x_rate','تكلفة الوحدة الموثقة','float',{'required':True}),
('x_total','التكلفة الفعلية','float',{'readonly':True,'store':True,'depends':'x_quantity,x_rate','compute':"for r in self:\n    r['x_total'] = r.x_quantity * r.x_rate"}),
('x_status','الاعتماد','selection',{'required':True,'selection_ids':[(0,0,{'value':'draft','name':'مسودة'}),(0,0,{'value':'approved','name':'معتمد'})]}),
('x_employee','الموظف','many2one',{'relation':'hr.employee','on_delete':'restrict'}),
('x_equipment','المعدة','many2one',{'relation':'maintenance.equipment','on_delete':'restrict'}),
('x_from','بداية التشغيل','datetime',{}),('x_to','نهاية التشغيل','datetime',{}),
('x_evidence','المستند المؤيد','many2one',{'relation':'ir.attachment','on_delete':'restrict'}),
('x_account_line','المصدر المحاسبي المرجعي','many2one',{'relation':'account.move.line','on_delete':'restrict'}),
('x_basis','أساس التوزيع أو الحساب','text',{}),
('x_approved_at','وقت الاعتماد','datetime',{'readonly':True}),
('x_approved_by','اعتمد بواسطة','many2one',{'relation':'res.users','readonly':True,'on_delete':'restrict'})]
for name,label,kind,extra in specs:
    if not F.search_count([('model','=','x_rekaz_cost_entry'),('name','=',name)]):
        vals = {'model_id':M.id,'name':name,'field_description':label,'ttype':kind,'state':'manual','copied':False}
        vals.update(extra)
        F.create(vals)
access = env['ir.access'].search([('name','=','Rekaz cost ledger administrators')],limit=1)
if not access:
    env['ir.access'].create({'name':'Rekaz cost ledger administrators','model_id':M.id,'group_id':env.ref('base.group_system').id,'operation':'cru','domain':"[('x_company_id','in',company_ids)]",'active':True})
code = """for r in records:
    if r.x_company_id != r.x_task.company_id:
        raise UserError('شركة التكلفة يجب أن تطابق شركة المعاملة.')
    if r.x_approved_at:
        raise UserError('التكلفة المعتمدة مقفلة. يلزم تصحيح موثق منفصل ولا يسمح بتغيير الأصل.')
    if r.x_source:
        duplicate = env['x_rekaz_cost_entry'].sudo().search_count([('id','!=',r.id),('x_company_id','=',r.x_company_id.id),('x_source','=',r.x_source.strip())])
        if duplicate:
            raise UserError('مرجع التكلفة مستخدم مسبقاً. لا يسمح بتكرار تحميل المصدر.')
    if r.x_quantity <= 0 or r.x_rate < 0:
        raise UserError('أدخل كمية فعلية موجبة وتكلفة غير سالبة.')
    if r.x_status == 'approved':
        if not r.x_evidence or not r.x_basis:
            raise UserError('الاعتماد يتطلب مستنداً مؤيداً وأساس حساب موثقاً.')
        if r.x_category in ['labor','equipment']:
            if not r.x_from or not r.x_to or r.x_to <= r.x_from:
                raise UserError('أدخل فترة العمل الفعلية الصحيحة.')
            field = 'x_employee' if r.x_category == 'labor' else 'x_equipment'
            resource = r[field]
            if not resource:
                raise UserError('حدد الموظف أو المعدة قبل الاعتماد.')
            overlap = env['x_rekaz_cost_entry'].sudo().search_count([('id','!=',r.id),('x_status','=','approved'),(field,'=',resource.id),('x_from','<',r.x_to),('x_to','>',r.x_from)])
            if overlap:
                raise UserError('توجد ساعات معتمدة متداخلة للموظف أو المعدة.')
        r.write({'x_approved_at':datetime.datetime.now(),'x_approved_by':env.user.id})
"""
a = env['base.automation'].search([('name','=','Rekaz approved cost integrity')],limit=1)
if not a:
    a = env['base.automation'].create({'name':'Rekaz approved cost integrity','model_id':M.id,'trigger':'on_create_or_write'})
triggerfields = F.search([('model','=','x_rekaz_cost_entry'),('name','not in',['x_approved_at','x_approved_by','x_total'])])
a.write({'trigger_field_ids':[(6,0,triggerfields.ids)],'active':True})
s = env['ir.actions.server'].search([('base_automation_id','=',a.id)],limit=1)
if s:
    s.write({'state':'code','code':code})
else:
    env['ir.actions.server'].create({'name':'سلامة التكلفة المعتمدة','model_id':M.id,'state':'code','code':code,'base_automation_id':a.id})
for key,kind,arch in [
('rekaz.cost.entry.list','list','<list create="1" delete="0"><field name="x_name"/><field name="x_task"/><field name="x_date"/><field name="x_category"/><field name="x_source"/><field name="x_quantity"/><field name="x_rate"/><field name="x_total" sum="الإجمالي"/><field name="x_status"/></list>'),
('rekaz.cost.entry.form','form','<form delete="0"><sheet><div class="alert alert-info">سجل تكلفة تشغيلية؛ لا ينشئ قيداً محاسبياً. لا تجمعه مع المصدر المحاسبي نفسه. مواد الكهرباء مقدمة من المالك بلا تكلفة شراء. الاعتماد يقفل السجل.</div><group><field name="x_name"/><field name="x_task"/><field name="x_company_id"/><field name="x_date"/><field name="x_category"/><field name="x_source"/><field name="x_quantity"/><field name="x_rate"/><field name="x_total"/><field name="x_status"/></group><group string="تتبع المصدر"><field name="x_employee"/><field name="x_equipment"/><field name="x_from"/><field name="x_to"/><field name="x_evidence"/><field name="x_account_line"/><field name="x_basis"/><field name="x_approved_at"/><field name="x_approved_by"/></group></sheet></form>'),
('rekaz.cost.entry.pivot','pivot','<pivot string="تحليل التكاليف المعتمدة"><field name="x_task" type="row"/><field name="x_category" type="col"/><field name="x_total" type="measure"/></pivot>')]:
    v = env['ir.ui.view'].search([('key','=',key)],limit=1)
    vals = {'name':key,'key':key,'model':M.model,'type':kind,'arch_db':arch}
    if v:
        v.write(vals)
    else:
        env['ir.ui.view'].create(vals)
act = env['ir.actions.act_window'].search([('name','=','التكاليف التشغيلية للمعاملات'),('res_model','=',M.model)],limit=1)
vals = {'name':'التكاليف التشغيلية للمعاملات','res_model':M.model,'view_mode':'list,form,pivot','context':repr({'default_x_company_id':env.company.id,'default_x_status':'draft'}),'domain':"[('x_company_id','in',allowed_company_ids)]"}
if act:
    act.write(vals)
else:
    act = env['ir.actions.act_window'].create(vals)
p = env.ref('project.menu_main_pm')
if not env['ir.ui.menu'].search_count([('name','=','التكاليف التشغيلية للمعاملات'),('parent_id','=',p.id)]):
    env['ir.ui.menu'].create({'name':'التكاليف التشغيلية للمعاملات','parent_id':p.id,'action':'ir.actions.act_window,'+str(act.id),'group_ids':[(6,0,[env.ref('base.group_system').id])],'sequence':66})
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'سجل التكاليف التشغيلية','message':'تم تجهيز سجل منفصل بصلاحية مسؤول النظام، مع مستند الاعتماد ومنع تداخل الموارد وتكرار المصدر. لم تُنشأ تكاليف أو قيود. القائمة: '+str(act.id),'sticky':True}}
