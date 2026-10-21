"""Periodic native stock counts with a complete cost-free material movement overview."""


def setup_code():
    return r'''if not env.user.has_group('base.group_system'):
    raise UserError('الإعداد متاح لمسؤول النظام فقط.')
quant_fields = env['stock.quant']._fields
required = ['product_id','location_id','quantity','inventory_quantity','inventory_quantity_set','inventory_diff_quantity','inventory_date','user_id']
if any(name not in quant_fields for name in required):
    raise UserError('حقول الجرد الأصلي غير متوافقة مع هذه النسخة.')
root = env.ref('stock.menu_stock_root')
group = env.ref('stock.group_stock_user')
status_field = env['ir.model.fields'].search([('model','=','stock.quant'),('name','=','x_rekaz_count_status')],limit=1)
status_vals = {'field_description':'حالة العد','ttype':'char','state':'manual','readonly':True,'store':False,'compute':"for rec in self:\n    rec['x_rekaz_count_status'] = 'تم العد' if rec.inventory_quantity_set else 'لم يُعد بعد'",'depends':'inventory_quantity_set'}
if status_field:
    status_field.write(status_vals)
else:
    status_vals.update({'name':'x_rekaz_count_status','model_id':env['ir.model']._get('stock.quant').id})
    env['ir.model.fields'].create(status_vals)
configs = []
overview = '<list create="0" edit="0" delete="0" default_order="name"><field name="default_code" string="كود المادة"/><field name="name" string="اسم المادة"/><field name="categ_id" optional="show"/><field name="uom_id"/><field name="qty_available" string="الرصيد المسجل الحالي"/><field name="x_rekaz_in_qty" string="وارد المعاملات"/><field name="x_rekaz_out_qty" string="منصرف المعاملات"/><field name="x_rekaz_return_qty" string="المسترجع"/><field name="x_rekaz_damage_qty" string="التالف"/></list>'
configs.append(('Rekaz full material stocktake overview','product.template','list',overview))
header = '<header>'
if 'inventory_quantity' in quant_fields:
    header += '<button name="action_apply_inventory" type="object" string="مراجعة واعتماد فروقات الجرد" groups="stock.group_stock_manager" confirm="ستُطبق فروقات العد على المخزون للحركات المحددة. راجع المادة والموقع والعدد قبل المتابعة."/>'
header += '</header>'
count = '<list editable="bottom" create="1" delete="0" default_order="inventory_date,product_id" decoration-danger="inventory_quantity_set and inventory_diff_quantity != 0">'+header+'<field name="product_id" readonly="id"/><field name="location_id" readonly="id"/>'
if 'lot_id' in quant_fields:
    count += '<field name="lot_id" optional="hide" readonly="id"/>'
if 'owner_id' in quant_fields:
    count += '<field name="owner_id" optional="hide" readonly="id"/>'
if 'package_id' in quant_fields:
    count += '<field name="package_id" optional="hide" readonly="id"/>'
if 'x_rekaz_task' in quant_fields:
    count += '<field name="x_rekaz_task" string="المعاملة" readonly="1"/>'
count += '<field name="quantity" string="الرصيد المسجل" readonly="1"/><field name="inventory_quantity_set" column_invisible="True"/><field name="x_rekaz_count_status" string="حالة العد" readonly="1"/><field name="inventory_quantity" string="العدد الفعلي بالجرد"/><field name="inventory_diff_quantity" string="فرق الجرد" readonly="1"/>'
if 'last_count_date' in quant_fields:
    count += '<field name="last_count_date" string="آخر جرد فعلي" readonly="1" optional="show"/>'
count += '<field name="inventory_date" string="موعد الجرد التالي"/><field name="user_id" string="مسؤول الجرد"/></list>'
configs.append(('Rekaz periodic physical counts','stock.quant','list',count))
count_search = '<search><field name="product_id"/><field name="location_id"/><field name="user_id"/>'
if 'x_rekaz_task' in quant_fields:
    count_search += '<field name="x_rekaz_task"/>'
count_search += '<filter name="differences" string="فروقات تحتاج مراجعة" domain="[(\'inventory_quantity_set\',\'=\',True),(\'inventory_diff_quantity\',\'!=\',0)]"/><filter name="pending" string="لم تُعد بعد" domain="[(\'inventory_quantity_set\',\'=\',False)]"/><filter name="due" string="الجرد المستحق" domain="[(\'inventory_date\',\'&lt;=\',context_today().strftime(\'%Y-%m-%d\'))]"/><group><filter name="location" string="الموقع" context="{\'group_by\':\'location_id\'}"/><filter name="product" string="المادة" context="{\'group_by\':\'product_id\'}"/><filter name="counter" string="مسؤول الجرد" context="{\'group_by\':\'user_id\'}"/></group></search>'
configs.append(('Rekaz periodic count search','stock.quant','search',count_search))
if 'cyclic_inventory_frequency' in env['stock.location']._fields:
    arch = '<list create="0" delete="0" editable="bottom"><field name="complete_name" string="الموقع" readonly="1"/><field name="cyclic_inventory_frequency" string="الفاصل بين الجرد — أيام"/>'
    for name in ['last_inventory_date','next_inventory_date']:
        if name in env['stock.location']._fields:
            arch += '<field name="'+name+'" readonly="1"/>'
    arch += '</list>'
    configs.append(('Rekaz inventory location frequency','stock.location','list',arch))
ids = {}
for name, model, kind, arch in configs:
    view = env['ir.ui.view'].search([('name','=',name),('model','=',model)],limit=1)
    vals = {'name':name,'model':model,'type':kind,'arch_db':arch}
    if view:
        view.write(vals)
    else:
        view = env['ir.ui.view'].create(vals)
    ids[name] = view.id
parent = env['ir.ui.menu'].search([('name','=','الجرد الدوري للمواد'),('parent_id','=',root.id)],limit=1)
group_key = 'groups_id' if 'groups_id' in env['ir.ui.menu']._fields else 'group_ids'
vals = {'name':'الجرد الدوري للمواد','parent_id':root.id,'sequence':16,group_key:[(6,0,[group.id])]}
if parent:
    parent.write(vals)
else:
    parent = env['ir.ui.menu'].create(vals)
product_domain = "[('company_id','in',[False]+allowed_company_ids)"
product_domain += ",('type','in',['product','consu'])]"
windows = [
    ('أرصدة وحركات جميع المواد','product.template',ids['Rekaz full material stocktake overview'],False,product_domain,"{}"),
    ('عد المواد ومواعيد الجرد','stock.quant',ids['Rekaz periodic physical counts'],ids['Rekaz periodic count search'],"[('location_id.usage','=','internal'),('company_id','in',allowed_company_ids)]","{'inventory_mode':True,'create':True}"),
]
if 'cyclic_inventory_frequency' in env['stock.location']._fields:
    locations = env['stock.location'].search([('usage','=','internal'),('company_id','in',env.companies.ids),('cyclic_inventory_frequency','=',0)])
    locations.write({'cyclic_inventory_frequency':30})
    windows.append(('دورية الجرد حسب الموقع','stock.location',ids['Rekaz inventory location frequency'],False,"[('usage','=','internal'),('company_id','in',allowed_company_ids)]","{'create':False}"))
    pending = env['stock.quant'].search([('location_id.usage','=','internal'),('company_id','in',env.companies.ids),('inventory_quantity_set','=',False)])
    for quant in pending:
        default_date = (datetime.datetime.now() + datetime.timedelta(days=quant.location_id.cyclic_inventory_frequency or 30)).date()
        if not quant.inventory_date or quant.inventory_date == default_date or (quant.inventory_date.month == 12 and quant.inventory_date.day == 31):
            next_date = quant.location_id.next_inventory_date if 'next_inventory_date' in env['stock.location']._fields else default_date
            next_date = next_date or default_date
            quant.write({'inventory_date':next_date})
if 'is_inventory' in env['stock.move']._fields:
    windows.append(('سجل تسويات الجرد المعتمدة','stock.move',False,False,"[('is_inventory','=',True),('state','=','done'),('company_id','in',allowed_company_ids)]","{'create':False,'edit':False,'delete':False}"))
for name, model, view_id, search_id, domain, context in windows:
    window = env['ir.actions.act_window'].search([('name','=',name),('res_model','=',model)],limit=1)
    vals = {'name':name,'res_model':model,'view_mode':'list,form','view_id':view_id,'search_view_id':search_id,'domain':domain,'context':context}
    if window:
        window.write(vals)
    else:
        window = env['ir.actions.act_window'].create(vals)
    menu = env['ir.ui.menu'].search([('name','=',name),('parent_id','=',parent.id)],limit=1)
    vals = {'name':name,'parent_id':parent.id,'action':'ir.actions.act_window,'+str(window.id),group_key:[(6,0,[group.id])]}
    if menu:
        menu.write(vals)
    else:
        env['ir.ui.menu'].create(vals)
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'الجرد الدوري جاهز','message':'أضيفت متابعة جميع المواد والعد الفعلي والفروقات والمواعيد وسجل التسويات. الدورية الافتراضية 30 يوماً للمواقع التي لم تحدد دوريتها. لم تُسجّل أي كمية عد أو تسوية.','sticky':True}}
'''


def install(api):
    models = api.find('ir.model', [('model', '=', 'stock.picking')], ['id'])
    if len(models) != 1:
        raise ValueError('Stock transfers model missing')
    action_id = api.ensure('ir.actions.server',
                           [('name', '=', 'Rekaz periodic inventory installer'), ('model_id', '=', models[0]['id'])],
                           {'name': 'Rekaz periodic inventory installer', 'model_id': models[0]['id'],
                            'state': 'code', 'code': setup_code()})
    api.call('ir.actions.server', 'write', [[action_id], {'code': setup_code()}])
    if getattr(api, 'major_version', 18) >= 20:
        api.call('ir.actions.server', 'run', [], ids=[action_id])
    else:
        api.call('ir.actions.server', 'run', [[action_id]])
