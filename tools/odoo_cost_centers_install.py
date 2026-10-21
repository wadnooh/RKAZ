if not env.user.has_group('base.group_system'):
    raise UserError('الإعداد لمسؤول النظام فقط.')
Plans = env['account.analytic.plan']
plan = Plans.search([('name','=','مراكز تكلفة معاملات ركاز')], limit=1)
if not plan:
    plan = Plans.create({'name':'مراكز تكلفة معاملات ركاز','default_applicability':'optional'})
Fields = env['ir.model.fields']
def add_field(model, name, label, kind, extra):
    field = Fields.search([('model','=',model),('name','=',name)],limit=1)
    if not field:
        vals = {'model_id':env['ir.model']._get_id(model),'name':name,'field_description':label,'ttype':kind,'state':'manual','copied':False}
        vals.update(extra)
        field = Fields.create(vals)
    return field
add_field('project.task','x_rekaz_cost_center','مركز تكلفة المعاملة','many2one',{'relation':'account.analytic.account','on_delete':'restrict','readonly':True})
add_field('account.analytic.account','x_rekaz_task','معاملة ركاز','many2one',{'relation':'project.task','on_delete':'restrict','readonly':True})
add_field('project.task','x_rekaz_cost_budget','ميزانية التكلفة المعتمدة','float',{})
add_field('project.task','x_rekaz_cost_readiness','اكتمال بيانات التكلفة','selection',{'selection_ids':[(0,0,{'value':'pending','name':'بيانات التكلفة قيد الاستكمال'}),(0,0,{'value':'review','name':'جاهزة للمراجعة'})]})
Tasks = env['project.task'].with_context(active_test=False)
created = 0
for task in Tasks.search([]):
    if not task.company_id:
        continue
    code = 'REKAZ-TASK-' + str(task.id)
    center = task.x_rekaz_cost_center
    if not center:
        center = env['account.analytic.account'].with_context(active_test=False).search([('code','=',code),('plan_id','=',plan.id),('company_id','=',task.company_id.id)],limit=1)
    if not center:
        center = env['account.analytic.account'].create({'name':task.name,'code':code,'plan_id':plan.id,'company_id':task.company_id.id,'partner_id':task.partner_id.id,'x_rekaz_task':task.id})
        created += 1
    if task.x_rekaz_cost_center != center:
        task.write({'x_rekaz_cost_center':center.id,'x_rekaz_cost_readiness':'pending'})
    if center.x_rekaz_task != task:
        center.write({'x_rekaz_task':task.id})
modelid = env['ir.model']._get_id('project.task')
guard = """for task in records:
    if task.company_id:
        plan = env['account.analytic.plan'].sudo().search([('name','=','مراكز تكلفة معاملات ركاز')],limit=1)
        code = 'REKAZ-TASK-' + str(task.id)
        center = task.x_rekaz_cost_center
        if not center:
            center = env['account.analytic.account'].sudo().with_context(active_test=False).search([('code','=',code),('plan_id','=',plan.id),('company_id','=',task.company_id.id)],limit=1)
        if not center:
            center = env['account.analytic.account'].sudo().create({'name':task.name,'code':code,'plan_id':plan.id,'company_id':task.company_id.id,'partner_id':task.partner_id.id,'x_rekaz_task':task.id})
        if task.x_rekaz_cost_center != center:
            task.sudo().write({'x_rekaz_cost_center':center.id,'x_rekaz_cost_readiness':'pending'})
        if center.name != task.name or center.partner_id != task.partner_id:
            center.sudo().write({'name':task.name,'partner_id':task.partner_id.id})
"""
automation = env['base.automation'].search([('name','=','Rekaz transaction analytic center')],limit=1)
if not automation:
    automation = env['base.automation'].create({'name':'Rekaz transaction analytic center','model_id':modelid,'trigger':'on_create_or_write'})
triggerfields = Fields.search([('model','=','project.task'),('name','in',['name','partner_id','company_id'])])
automation.write({'trigger_field_ids':[(6,0,triggerfields.ids)],'active':True})
server = env['ir.actions.server'].search([('base_automation_id','=',automation.id),('state','=','code')],limit=1)
if not server:
    server = env['ir.actions.server'].create({'name':'مركز تكلفة المعاملة تلقائياً','model_id':modelid,'state':'code','base_automation_id':automation.id,'code':guard})
else:
    server.write({'code':guard})
view = env['ir.ui.view'].search([('key','=','rekaz.cost_center.task')],limit=1)
arch = '<data><xpath expr="//sheet/notebook" position="inside"><page string="مركز التكلفة" name="rekaz_cost_center"><group><field name="x_rekaz_cost_center" readonly="1"/><field name="x_rekaz_cost_readiness"/><field name="x_rekaz_cost_budget" groups="base.group_system"/></group><div class="alert alert-info">مركز مستقل للمعاملة. مواد المالك لا تحمل تكلفة شراء. الربح لا يعتمد قبل استكمال واعتماد تكاليف العمالة والمعدات والمصروفات. قيمة الأعمال والفواتير والتحصيل مؤشرات منفصلة.</div></page></xpath></data>'
vals = {'name':'Rekaz transaction cost center','key':'rekaz.cost_center.task','model':'project.task','inherit_id':env.ref('project.view_task_form2').id,'arch_db':arch,'active':True}
if view:
    view.write(vals)
else:
    env['ir.ui.view'].create(vals)
act = env['ir.actions.act_window'].search([('name','=','مراكز تكلفة المعاملات — ركاز'),('res_model','=','account.analytic.account')],limit=1)
vals = {'name':'مراكز تكلفة المعاملات — ركاز','res_model':'account.analytic.account','view_mode':'list,form','domain':repr([('plan_id','=',plan.id)]),'context':repr({'default_plan_id':plan.id})}
if act:
    act.write(vals)
else:
    act = env['ir.actions.act_window'].create(vals)
parent = env.ref('project.menu_main_pm')
menu = env['ir.ui.menu'].search([('name','=','مراكز تكلفة المعاملات'),('parent_id','=',parent.id)],limit=1)
if not menu:
    env['ir.ui.menu'].create({'name':'مراكز تكلفة المعاملات','parent_id':parent.id,'action':'ir.actions.act_window,'+str(act.id),'sequence':65})
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'مراكز تكلفة المعاملات','message':'تم ربط '+str(Tasks.search_count([('x_rekaz_cost_center','!=',False)]))+' معاملة. مراكز جديدة: '+str(created)+'. لم تنشأ قيود أو تكاليف تقديرية. قائمة المراكز: '+str(act.id),'sticky':True}}
