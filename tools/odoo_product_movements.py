"""Read-only native product movement totals and transaction drill-downs."""


def setup_code():
    compute = """totals = {}
products = self.mapped('product_variant_ids')
for product in products:
    totals[product.id] = {'incoming':0.0,'outgoing':0.0,'returned':0.0,'scrapped':0.0}
for move in self.env['stock.move'].search([('product_id','in',products.ids),('state','=','done'),('picking_id.x_rekaz_task','!=',False),('company_id','in',self.env.companies.ids)]):
    flow = move.picking_id.x_rekaz_movement
    if flow in totals[move.product_id.id]:
        totals[move.product_id.id][flow] += move.UNIT._compute_quantity(move.QTY,move.product_id.uom_id)
for rec in self:
    for flow, field in [('incoming','x_rekaz_in_qty'),('outgoing','x_rekaz_out_qty'),('returned','x_rekaz_return_qty'),('scrapped','x_rekaz_damage_qty')]:
        rec[field] = sum(totals[p.id][flow] for p in rec.product_variant_ids)
"""
    return r"""if not env.user.has_group('base.group_system'):
    raise UserError('الإعداد متاح لمسؤول النظام فقط.')
unit = False
qty = False
for f in ['product_uom','product_uom_id','uom_id']:
    if not unit and f in env['stock.move']._fields:
        unit = f
for f in ['quantity','quantity_done']:
    if not qty and f in env['stock.move']._fields:
        qty = f
if not unit or not qty:
    raise UserError('تعذر تحديد حقول الكميات والوحدات.')
compute = COMPUTE.replace('move.UNIT','move.'+unit).replace('move.QTY','move.'+qty)
mid = env['ir.model']._get('product.template')
specs = [('incoming','x_rekaz_in_qty','الوارد'),('outgoing','x_rekaz_out_qty','المنصرف'),('returned','x_rekaz_return_qty','المسترجع'),('scrapped','x_rekaz_damage_qty','التالف')]
for flow, name, label in specs:
    field = env['ir.model.fields'].search([('model_id','=',mid.id),('name','=',name)],limit=1)
    if not field:
        env['ir.model.fields'].create({'model_id':mid.id,'name':name,'field_description':label+' — معاملات معتمدة','ttype':'float','state':'manual','readonly':True})
for flow, name, label in specs:
    env['ir.model.fields'].search([('model_id','=',mid.id),('name','=',name)],limit=1).write({'compute':compute,'store':False,'depends':'product_variant_ids','readonly':True})
move_arch = '<list create="0" edit="0" delete="0"><field name="date"/><field name="product_id"/><field name="picking_id"/><field name="x_rekaz_task"/><field name="location_id"/><field name="location_dest_id"/><field name="'+qty+'" string="الكمية المنفذة"/><field name="'+unit+'"/><field name="state"/></list>'
move_view = env['ir.ui.view'].search([('name','=','Rekaz product transaction movements'),('model','=','stock.move')],limit=1)
vals = {'name':'Rekaz product transaction movements','model':'stock.move','type':'list','arch_db':move_arch}
if move_view:
    move_view.write(vals)
else:
    move_view = env['ir.ui.view'].create(vals)
buttons = ''
for flow, name, label in specs:
    code = "action = {'type':'ir.actions.act_window','name':TITLE,'res_model':'stock.move','view_mode':'list,form','views':[(VIEW,'list'),(False,'form')],'domain':[('product_id','in',record.product_variant_ids.ids),('state','=','done'),('picking_id.x_rekaz_task','!=',False),('picking_id.x_rekaz_movement','=',FLOW),('company_id','in',env.companies.ids)],'context':{'create':False,'edit':False,'delete':False,'group_by':'x_rekaz_task'}}"
    code = code.replace('TITLE',repr(label+' — معاملات المادة')).replace('VIEW',str(move_view.id)).replace('FLOW',repr(flow))
    act = env['ir.actions.server'].search([('name','=','Rekaz product '+flow+' transactions'),('model_id','=',mid.id)],limit=1)
    vals = {'name':'Rekaz product '+flow+' transactions','model_id':mid.id,'state':'code','code':code}
    if act:
        act.write(vals)
    else:
        act = env['ir.actions.server'].create(vals)
    buttons += '<button name="'+str(act.id)+'" type="action" class="btn btn-light border rounded px-4 py-3 text-center"><div class="d-flex flex-column gap-2"><span class="fw-bold">'+label+'</span><field name="'+name+'" readonly="1" nolabel="1" class="fw-bold"/></div></button>'
form_arch = '<data><xpath expr="//sheet/notebook" position="before"><div class="mb-3"><h4>حركة مواد المعاملات</h4><div class="d-flex flex-wrap gap-3">'+buttons+'</div><div class="text-muted small mt-2">الحركات المعتمدة فقط — اضغط نوع الحركة لفتح المعاملات المرتبطة بالمادة.</div></div></xpath><xpath expr="//sheet" position="inside"><div class="alert alert-info" role="status">الوارد والمنصرف والتالف والمسترجع تخص الحركات المعتمدة المرتبطة بالمعاملات في الشركات المختارة، بوحدة المادة الأساسية. اضغط على نوع الحركة لعرض المعاملات والسندات المرتبطة بها. المسودات لا تدخل في هذه الأرقام.</div></xpath></data>'
list_parent = env.ref('product.product_template_tree_view',raise_if_not_found=False)
form_parent = env.ref('product.product_template_only_form_view')
configs = [('Rekaz product transaction buttons','form',form_parent.id,form_arch)]
if list_parent:
    list_arch = '<data><xpath expr="//list" position="inside">'+''.join('<field name="'+name+'" string="'+label+'" optional="show" readonly="1"/>' for flow,name,label in specs)+'</xpath></data>'
    configs.append(('Rekaz product transaction columns','list',list_parent.id,list_arch))
for name, kind, parent, arch in configs:
    view = env['ir.ui.view'].search([('name','=',name),('model','=','product.template')],limit=1)
    vals = {'name':name,'model':'product.template','type':kind,'inherit_id':parent,'arch_db':arch}
    if view:
        view.write(vals)
    else:
        env['ir.ui.view'].create(vals)
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'تم ترتيب عرض المنتجات','message':'أضيفت أعمدة وأزرار الوارد والمنصرف والتالف والمسترجع مع فتح معاملات المادة. لم تتغير أي كمية مخزون.','sticky':True}}
""".replace('COMPUTE', repr(compute)) + owner_policy_code()


def owner_policy_code():
    return r'''
owner = env.ref('stock.stock_location_suppliers')
links = env['ir.actions.server'].search([('name','=','Rekaz task stock transfer link '+str(env.company.id))])
if len(links) != 1:
    raise UserError('تعذر تحديد قاعدة ربط مواد المعاملات؛ لم يتغير مسار الوارد.')
link = links[0]
if '# Rekaz owner-direct receipts' not in link.code:
    patched = []
    incoming = False
    changed = False
    for line in link.code.splitlines():
        if line.strip() == "if flow == 'incoming':":
            incoming = True
        elif line.strip().startswith('elif flow'):
            incoming = False
        patched.append(line)
        if incoming and line.strip().startswith('expected_source ='):
            indent = line[:len(line)-len(line.lstrip())]
            patched.append(indent+'# Rekaz owner-direct receipts')
            patched.append(indent+"if dashboard_flow == 'incoming' and (rec.state != 'done' or rec.location_id.id == "+str(owner.id)+"):")
            patched.append(indent+'    expected_source = '+str(owner.id))
            changed = True
    if not changed:
        raise UserError('صيغة قاعدة الوارد مختلفة؛ راجعها قبل تغيير المسار.')
    link.write({'code':'\n'.join(patched)})
incoming_type = env['stock.picking.type'].search([('x_rekaz_origin','=','rekaz-main:inventory:dashboard:incoming'),('company_id','=',env.company.id)])
if len(incoming_type) != 1:
    raise UserError('تعذر تحديد نوع الوارد المباشر للمعاملة.')
incoming_type.write({'default_location_src_id':owner.id})
form_parent = env.ref('product.product_template_only_form_view')
list_parent = env.ref('product.product_template_tree_view',raise_if_not_found=False)
arch = '<data>'
for name in ['standard_price','list_price','taxes_id','supplier_taxes_id']:
    arch += '<xpath expr="//field[@name=\''+name+'\']" position="attributes"><attribute name="invisible">True</attribute></xpath>'
for name in ['standard_price','list_price']:
    arch += '<xpath expr="//label[@for=\''+name+'\']" position="attributes"><attribute name="invisible">True</attribute></xpath>'
    arch += '<xpath expr="//field[@name=\''+name+'\']/ancestor::div[1]" position="attributes"><attribute name="invisible">True</attribute></xpath>'
if form_parent._get_combined_arch().xpath("//field[@name='tax_string']"):
    arch += '<xpath expr="//field[@name=\'tax_string\']" position="attributes"><attribute name="invisible">True</attribute></xpath>'
arch += '<xpath expr="//sheet/notebook" position="before"><div class="alert alert-success">مواد مقدمة من المالك — الشركة السعودية للطاقة. الوارد مباشرة للمعاملة، دون إدخال تكلفة شراء أو رصيد افتتاحي. يتم تسجيل الكميات الفعلية من خلال سندات المواد المرتبطة بالمعاملة.</div></xpath></data>'
configs = [('Rekaz owner material cost-free form','form',form_parent.id,arch)]
if list_parent:
    arch = '<data>'
    for name in ['standard_price','list_price']:
        arch += '<xpath expr="//field[@name=\''+name+'\']" position="attributes"><attribute name="column_invisible">True</attribute></xpath>'
    arch += '</data>'
    configs.append(('Rekaz owner material cost-free list','list',list_parent.id,arch))
for name, kind, parent, arch in configs:
    view = env['ir.ui.view'].search([('name','=',name),('model','=','product.template')],limit=1)
    vals = {'name':name,'model':'product.template','type':kind,'inherit_id':parent,'arch_db':arch}
    if view:
        view.write(vals)
    else:
        env['ir.ui.view'].create(vals)
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'مواد المالك جاهزة','message':'أخفيت حقول التكلفة والأسعار، وأصبح الوارد الجديد من المالك مباشرة للمعاملة. لم تُعدّل الحركات المعتمدة أو الأرصدة السابقة.','sticky':True}}
'''


def install(api):
    models = api.find('ir.model', [('model', '=', 'stock.picking')], ['id'])
    if len(models) != 1:
        raise ValueError('Stock transfers model missing')
    action_id = api.ensure('ir.actions.server',
                           [('name', '=', 'Rekaz product movements installer'), ('model_id', '=', models[0]['id'])],
                           {'name': 'Rekaz product movements installer', 'model_id': models[0]['id'],
                            'state': 'code', 'code': setup_code()})
    api.call('ir.actions.server', 'write', [[action_id], {'code': setup_code()}])
    if getattr(api, 'major_version', 18) >= 20:
        api.call('ir.actions.server', 'run', [], ids=[action_id])
    else:
        api.call('ir.actions.server', 'run', [[action_id]])
