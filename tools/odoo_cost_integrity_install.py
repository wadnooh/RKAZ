if not env.user.has_group('base.group_system'):
    raise UserError('لمسؤول النظام فقط.')
model = env['ir.model'].search([('model','=','x_rekaz_cost_entry')],limit=1)
F = env['ir.model.fields']
if not F.search_count([('model','=',model.model),('name','=','x_reversal_of')]):
    F.create({'model_id':model.id,'name':'x_reversal_of','field_description':'عكس تكلفة معتمدة','ttype':'many2one','relation':model.model,'on_delete':'restrict','state':'manual','copied':False})
a = env['base.automation'].search([('name','=','Rekaz approved cost integrity')],limit=1)
s = env['ir.actions.server'].search([('base_automation_id','=',a.id)],limit=1)
code = s.code
# Rebuild from the base validator so repeated installation is deterministic.
marker = "    if r.x_reversal_of:\n        original = r.x_reversal_of\n"
if marker in code:
    start = code.index(marker)
    end = code.index("    if r.x_status == 'approved':", start)
    code = code[:start] + code[end:]
code = code.replace("if r.x_quantity <= 0 or r.x_rate < 0:","if (r.x_quantity <= 0 and not r.x_reversal_of) or r.x_rate < 0:")
code = code.replace("    if r.x_status == 'approved':", "    if r.x_reversal_of:\n        original = r.x_reversal_of\n        if original.x_status != 'approved' or original.x_reversal_of or r.x_task != original.x_task or r.x_category != original.x_category or r.x_quantity != -original.x_quantity or r.x_rate != original.x_rate:\n            raise UserError('العكس يجب أن يطابق التكلفة الأصلية المعتمدة بكمية سالبة مساوية لها.')\n    if r.x_status == 'approved':")
code = code.replace("if r.x_category in ['labor','equipment']:","if r.x_category in ['labor','equipment'] and not r.x_reversal_of:")
s.write({'code':code})
a.write({'trigger_field_ids':[(6,0,F.search([('model','=',model.model),('name','not in',['x_approved_at','x_approved_by','x_total'])]).ids)]})
env.cr.execute('CREATE UNIQUE INDEX IF NOT EXISTS rekaz_cost_source_unique ON x_rekaz_cost_entry (x_company_id, lower(btrim(x_source)))')
env.cr.execute('CREATE UNIQUE INDEX IF NOT EXISTS rekaz_cost_reversal_unique ON x_rekaz_cost_entry (x_reversal_of) WHERE x_reversal_of IS NOT NULL')
env.cr.execute('CREATE UNIQUE INDEX IF NOT EXISTS rekaz_analytic_task_unique ON account_analytic_account (x_rekaz_task) WHERE x_rekaz_task IS NOT NULL')
env.cr.execute("""CREATE OR REPLACE FUNCTION rekaz_cost_freeze_approved() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.x_approved_at IS NOT NULL THEN RAISE EXCEPTION 'Approved Rekaz cost is immutable; use a documented reversal'; END IF; IF TG_OP = 'DELETE' THEN RETURN OLD; END IF; RETURN NEW; END $$""")
env.cr.execute('DROP TRIGGER IF EXISTS rekaz_cost_freeze ON x_rekaz_cost_entry')
env.cr.execute('CREATE TRIGGER rekaz_cost_freeze BEFORE UPDATE OR DELETE ON x_rekaz_cost_entry FOR EACH ROW EXECUTE FUNCTION rekaz_cost_freeze_approved()')
v = env['ir.ui.view'].search([('key','=','rekaz.cost.entry.form')],limit=1)
if 'x_reversal_of' not in v.arch_db:
    v.write({'arch_db':v.arch_db.replace('<field name="x_basis"/>','<field name="x_basis"/><field name="x_reversal_of"/>')})
report = env['ir.actions.act_window'].search([('name','=','تحليل التكاليف المعتمدة — ركاز')],limit=1)
vals = {'name':'تحليل التكاليف المعتمدة — ركاز','res_model':model.model,'view_mode':'pivot,list,form','domain':"[('x_status','=','approved'),('x_company_id','in',allowed_company_ids)]"}
if report:
    report.write(vals)
else:
    report = env['ir.actions.act_window'].create(vals)
p = env.ref('project.menu_main_pm')
if not env['ir.ui.menu'].search_count([('name','=','تحليل التكاليف المعتمدة'),('parent_id','=',p.id)]):
    env['ir.ui.menu'].create({'name':'تحليل التكاليف المعتمدة','parent_id':p.id,'action':'ir.actions.act_window,'+str(report.id),'group_ids':[(6,0,[env.ref('base.group_system').id])],'sequence':67})
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'سلامة سجل التكاليف','message':'تفعيل قفل التكاليف المعتمدة على مستوى قاعدة البيانات، ومنع تكرار المصدر والعكس. تقرير المعتمد فقط: '+str(report.id),'sticky':True}}
