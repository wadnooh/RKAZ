if not env.user.has_group('base.group_system'):
    raise UserError('لمسؤول النظام فقط.')
projects = env['project.project'].search([('name','=','الإنشاءات — المعاملات الواردة'),('company_id','=',env.company.id)])
if len(projects) > 1:
    raise UserError('يوجد أكثر من مشروع استقبال؛ راجع التكرار.')
project = projects
if not project:
    project = env['project.project'].create({'name':'الإنشاءات — المعاملات الواردة','company_id':env.company.id,'privacy_visibility':'followers','alias_name':False})
F = env['ir.model.fields']
mid = env['ir.model']._get_id('project.task')
specs = [('x_rekaz_mail_subject','عنوان الرسالة الأصلي','char',{'readonly':True}),('x_rekaz_intake_key','مرجع الاستقبال الفريد','char',{'readonly':True}),('x_rekaz_application_no','رقم طلب الخدمة','char',{}),('x_rekaz_file_ref','مرجع ملف المعاملة','char',{}),('x_rekaz_intake_site','الموقع المستخرج','char',{}),('x_rekaz_intake_analysis','ملخص التحليل والملاحظات','text',{}),('x_rekaz_mail_review','مراجعة بيانات الوارد','selection',{'selection_ids':[(0,0,{'value':'pending','name':'بانتظار مراجعة البيانات'}),(0,0,{'value':'reviewed','name':'تمت مراجعة البيانات'})]})]
for name,label,kind,extra in specs:
    if not F.search_count([('model','=','project.task'),('name','=',name)]):
        vals = {'model_id':mid,'name':name,'field_description':label,'ttype':kind,'state':'manual','copied':False}
        vals.update(extra)
        F.create(vals)
env.cr.execute("CREATE UNIQUE INDEX IF NOT EXISTS rekaz_mail_intake_key_unique ON project_task (company_id,x_rekaz_intake_key) WHERE x_rekaz_intake_key IS NOT NULL AND x_rekaz_intake_key <> ''")
stage = env['project.task.type'].search([('name','=','وارد جديد — مراجعة البيانات'),('project_ids','in',project.ids)],limit=1)
if not stage:
    stage = env['project.task.type'].create({'name':'وارد جديد — مراجعة البيانات','project_ids':[(6,0,project.ids)],'sequence':0})
view = env['ir.ui.view'].search([('key','=','rekaz.construction.intake')],limit=1)
arch = '<data><xpath expr="//notebook" position="inside"><page name="rekaz_construction_intake" string="بيانات المعاملة الواردة" invisible="project_id != PROJECT"><div class="alert alert-info">استقبال الإنشاءات جاهز للتجهيز. البريد غير مربوط حتى تحديده. بيانات الرسالة والمرفقات تحتاج مراجعة؛ لا اعتماد مالي أو صرف مواد تلقائي. ملفات PDF الممسوحة تحتاج قراءة مستقلة ولا يكفي عنوان البريد لاستخراجها.</div><group><field name="x_rekaz_mail_subject"/><field name="x_rekaz_file_ref"/><field name="x_rekaz_application_no"/><field name="x_rekaz_intake_site"/><field name="x_rekaz_mail_review"/><field name="x_rekaz_intake_key" groups="base.group_system"/></group><field name="x_rekaz_intake_analysis"/></page></xpath></data>'.replace('PROJECT',str(project.id))
vals = {'name':'Rekaz construction incoming transaction','key':'rekaz.construction.intake','model':'project.task','inherit_id':env.ref('project.view_task_form2').id,'arch_db':arch}
if view:
    view.write(vals)
else:
    env['ir.ui.view'].create(vals)
key = 'file:A26-118:261153514'
task = env['project.task'].with_context(active_test=False).search([('x_rekaz_intake_key','=',key),('company_id','=',env.company.id)],limit=1)
if not task:
    task = env['project.task'].create({'name':'A26-118 — طلب 261153514 — النرجس','project_id':project.id,'company_id':env.company.id,'stage_id':stage.id,'user_ids':[(6,0,[env.user.id])],'x_rekaz_mail_subject':'A26-118','x_rekaz_intake_key':key,'x_rekaz_file_ref':'A26-118','x_rekaz_application_no':'261153514','x_rekaz_intake_site':'الرياض — حي النرجس','x_rekaz_mail_review':'pending','x_rekaz_intake_analysis':'تحليل أولي للملف المرفق أثناء التجهيز، وليس نتيجة جلب من بريد. الملف يضم طلب خدمة ودراسة نطاق عمل ومخطط شبكة ومستندات ملكية ورخصة بناء. رقم الطلب والموقع مأخوذان من نموذج الطلب والدراسة. أمر العمل غير مثبت؛ يترك فارغاً. الإحداثيات والأرقام المالية والبنود تحتاج مراجعة مصدرها ولا ترحل تلقائياً. لا تستخدم أرقام الهوية أو الملكية كرقم للمعاملة.','description':'<p>معاملة إنشاءات واردة من الملف A26-118.pdf. رقم طلب الخدمة: 261153514. الموقع: الرياض — النرجس.</p><p>مصدر الإدخال: تحليل الملف المرفق أثناء الإعداد، وليس بريدًا مربوطًا. تحتاج البيانات والمخططات ونطاق العمل للمراجعة قبل التنفيذ أو الترحيل المالي.</p>'})
act = env['ir.actions.act_window'].search([('name','=','معاملات الإنشاءات الواردة'),('res_model','=','project.task')],limit=1)
vals = {'name':'معاملات الإنشاءات الواردة','res_model':'project.task','view_mode':'kanban,list,form','domain':repr([('project_id','=',project.id)]),'context':repr({'default_project_id':project.id,'default_stage_id':stage.id,'default_x_rekaz_mail_review':'pending'})}
if act:
    act.write(vals)
else:
    act = env['ir.actions.act_window'].create(vals)
parent = env.ref('project.menu_main_pm')
if not env['ir.ui.menu'].search_count([('name','=','معاملات الإنشاءات الواردة'),('parent_id','=',parent.id)]):
    env['ir.ui.menu'].create({'name':'معاملات الإنشاءات الواردة','parent_id':parent.id,'action':'ir.actions.act_window,'+str(act.id),'sequence':62})
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'تجهيز معاملات الإنشاءات الواردة','message':'تم تجهيز المشروع الخاص وبطاقة الملف للمراجعة. لم يربط بريد ولم تُرسل رسائل. القائمة '+str(act.id)+'؛ المعاملة '+str(task.id)+'؛ المشروع '+str(project.id),'sticky':True}}
