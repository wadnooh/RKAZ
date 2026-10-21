"""Transaction warehouse screens and Arabic vouchers over native Odoo stock records."""


def report_arch(move_field='move_ids'):
    return '''<t t-name="rekaz_warehouse.transaction_voucher"><t t-call="web.html_container"><t t-foreach="docs" t-as="o"><t t-call="web.basic_layout"><div class="page rk-voucher" dir="rtl">
<style>
.rk-voucher{font-family:Arial,Tahoma,sans-serif;color:#203445;font-size:13px;line-height:1.6;text-align:right;padding:8px 0}
.rk-voucher table{width:100%;border-collapse:collapse;margin:14px 0;table-layout:auto}
.rk-voucher td,.rk-voucher th{padding:9px 8px;vertical-align:middle;border:1px solid #dce4e9}
.rk-voucher th{background:#203e50;color:#fff;text-align:center;font-weight:bold;font-size:12px}
.rk-voucher tbody tr:nth-child(even){background:#f4f7f9}
.rk-voucher h2{font-size:25px;margin:0;color:#17384a;font-weight:bold}
.rk-voucher .rk-brand{font-size:17px;font-weight:bold;color:#477478;letter-spacing:.3px;margin:0 0 3px}
.rk-voucher .rk-subtitle{font-size:11px;color:#6c7e89;letter-spacing:2px;margin-top:4px}
.rk-voucher .rk-head{border-bottom:3px solid #c4a568;padding:0 0 15px;margin-bottom:18px;text-align:center}
.rk-voucher .rk-meta td{background:#f4f7f9;border:0;border-bottom:2px solid white;width:50%;padding:7px 12px}
.rk-voucher .rk-main td{text-align:center;font-size:13px}
.rk-voucher .rk-balance td{text-align:center;font-size:12px}
.rk-voucher .rk-code{direction:ltr;white-space:nowrap;font-family:Arial,sans-serif;font-weight:bold}
.rk-voucher .rk-name{direction:ltr;text-align:left!important;min-width:175px}
.rk-voucher .rk-total{background:#e9f1f4;color:#17384a;font-weight:bold}
.rk-voucher .rk-section{font-size:16px;font-weight:bold;margin-top:20px;border-right:4px solid #c4a568;padding-right:9px}
.rk-voucher .rk-note{font-size:11px;line-height:1.8;color:#5f727d;background:#f4f7f9;padding:10px 13px;border-right:3px solid #b5c7cf}
.rk-voucher .rk-sign td{border:0;border-top:1px solid #8296a1;width:50%;padding-top:13px;text-align:center}
.rk-voucher tr{page-break-inside:avoid}.rk-voucher thead{display:table-header-group}
</style><div class="rk-head"><p class="rk-brand"><span t-field="o.company_id.name"/></p>
<h2 style="text-align:center;">عرض المعاملة — سند حركة المستودع</h2>
<div class="rk-subtitle">TRANSACTION MATERIALS · WAREHOUSE VOUCHER</div></div>
<table class="rk-meta"><tr><td>رقم السند: <span t-field="o.name"/></td><td>المعاملة: <span t-field="o.x_rekaz_task.name"/></td></tr>
<tr><td>التاريخ: <span t-if="o.date_done" t-field="o.date_done"/><span t-else="" t-field="o.scheduled_date"/></td><td>أمر العمل: <span t-field="o.x_rekaz_work_order"/></td></tr>
<tr><td>نوع الحركة: <span t-field="o.x_rekaz_movement"/></td><td>المشروع: <span t-field="o.x_rekaz_project.name"/></td></tr>
<tr><td>المالك / مصدر المواد: <span t-field="o.partner_id.name"/></td><td>المستلم: <span t-field="o.x_rekaz_receiver_name"/> — <span t-field="o.x_rekaz_receiver_phone"/></td></tr>
<tr><td>حالة السند: <span t-field="o.state"/></td><td>مدخل السند: <span t-field="o.create_uid.name"/></td></tr></table>
<div t-if="o.state != 'done'" class="alert alert-warning">السند غير معتمد؛ لا تثبت الحركة استلاماً أو صرفاً فعلياً حتى الاعتماد.</div>
<div class="rk-section">تفاصيل مواد السند</div><table class="rk-main"><thead><tr><th>م</th><th>كود المادة</th><th>اسم المادة</th><th>الوحدة</th><th>المستلم</th></tr></thead><tbody><tr t-foreach="o.MOVES" t-as="line"><t t-set="summary" t-value="o.x_rekaz_task.x_rekaz_material_balances.filtered(lambda r: r.x_product_id == line.product_id)[:1]"/>
<td><span t-out="line_index + 1"/></td><td class="rk-code"><span t-field="line.product_id.default_code"/></td><td class="rk-name"><span t-field="line.product_id.name"/></td><td><span t-field="line.product_id.uom_id.name"/></td><td><span t-out="line.x_rekaz_receiver_name or o.x_rekaz_receiver_name or '—'"/> <span t-out="line.x_rekaz_receiver_phone or o.x_rekaz_receiver_phone or ''"/></td></tr></tbody></table><div class="rk-section">ملخص حركة المواد داخل المعاملة</div><table class="rk-balance"><thead><tr><th>كود المادة</th><th>اسم المادة</th><th>الوحدة</th><th>الوارد</th><th>المنصرف</th><th>المسترجع</th><th>التالف</th><th>رصيد المعاملة</th><th>رصيد المخزون العام</th></tr></thead><tbody><tr t-foreach="o.MOVES" t-as="line"><t t-set="summary" t-value="o.x_rekaz_task.x_rekaz_material_balances.filtered(lambda r: r.x_product_id == line.product_id)[:1]"/>
<td class="rk-code"><span t-field="line.product_id.default_code"/></td><td class="rk-name"><span t-field="line.product_id.name"/></td><td><span t-field="line.product_id.uom_id.name"/></td><td><span t-out="summary.x_incoming_qty if summary else 0"/></td><td><span t-out="summary.x_outgoing_qty if summary else 0"/></td><td><span t-out="summary.x_returned_qty if summary else 0"/></td><td><span t-out="summary.x_scrapped_qty if summary else 0"/></td><td class="rk-total"><span t-out="summary.x_balance_qty if summary else 0"/></td><td><span t-out="summary.x_global_balance_qty if summary else 0"/></td></tr></tbody></table><p>عدد البنود: <span t-out="len(o.MOVES)"/>. لا تُجمع كميات بوحدات مختلفة في إجمالي واحد.</p>
<p class="rk-note">الكميات بوحدة الصنف الأساسية. رصيد المعاملة يخص موقع المعاملة، ورصيد المخزون العام يُقرأ من كميات Odoo الفعلية في المستودع المركزي خارج مواقع المعاملات؛ الرصيد المعروض وقت الطباعة وليس رصيداً تاريخياً لتاريخ السند.</p>
<p>ملاحظات: <span t-field="o.x_rekaz_voucher_notes"/></p><table class="rk-sign" style="margin-top:35px"><tr><td>أمين المستودع: __________________</td><td>توقيع مستلم المواد: __________________</td></tr></table>
</div></t></t></t></t>'''.replace('o.MOVES','o.'+move_field)


def setup_code():
    return '''if not env.user.has_group('base.group_system'):
    raise UserError('تثبيت شاشة المستودع متاح لمسؤول النظام فقط.')
Field = env['ir.model.fields']
for model_name in ['stock.picking','stock.move']:
    mid = env['ir.model']._get(model_name)
    specs = [('x_rekaz_receiver_name','char','اسم مستلم المواد'),('x_rekaz_receiver_phone','char','جوال المستلم')]
    if model_name == 'stock.picking':
        specs.append(('x_rekaz_voucher_notes','text','ملاحظات سند المواد'))
    for name, kind, label in specs:
        found = Field.search([('model_id','=',mid.id),('name','=',name)],limit=1)
        if not found:
            Field.create({'name':name,'model_id':mid.id,'ttype':kind,'field_description':label,'state':'manual'})
configs = []
move_fields = env['stock.move']._fields
unit = False
qty = False
moves = False
for candidate in ['product_uom','product_uom_id','uom_id']:
    if not unit and candidate in move_fields:
        unit = candidate
for candidate in ['quantity','quantity_done']:
    if not qty and candidate in move_fields:
        qty = candidate
for candidate in ['move_ids','move_ids_without_package']:
    if not moves and candidate in env['stock.picking']._fields:
        moves = candidate
if not unit or not qty or not moves:
    raise UserError('تعذر تحديد حقول حركة المواد؛ لم يتم تركيب التقرير.')
report_arch = REPORT_ARCH
report_arch = report_arch.replace('o.MOVES','o.'+moves).replace('line.UNIT','line.'+unit).replace('line.QTY','line.'+qty)
template = env['ir.ui.view'].search([('key','=','rekaz_warehouse.transaction_voucher')],limit=1)
vals = {'name':'Rekaz warehouse transaction voucher','type':'qweb','key':'rekaz_warehouse.transaction_voucher','arch_db':report_arch}
if template:
    template.write(vals)
else:
    env['ir.ui.view'].create(vals)
paper = env['report.paperformat'].search([('name','=','Rekaz warehouse landscape')],limit=1)
if not paper:
    paper = env['report.paperformat'].create({'name':'Rekaz warehouse landscape','format':'A4','orientation':'Landscape','margin_top':10,'margin_bottom':10,'margin_left':7,'margin_right':7,'header_spacing':0,'dpi':90})
paper.write({'margin_top':10,'margin_bottom':10,'header_spacing':0})
report = env['ir.actions.report'].search([('report_name','=','rekaz_warehouse.transaction_voucher')],limit=1)
vals = {'name':'سند مواد المعاملة','model':'stock.picking','report_type':'qweb-pdf','report_name':'rekaz_warehouse.transaction_voucher','paperformat_id':paper.id,'binding_model_id':env['ir.model']._get('stock.picking').id,'binding_type':'report'}
if report:
    report.write(vals)
else:
    report = env['ir.actions.report'].create(vals)
parent = env.ref('stock.view_picking_form')
arch = '<data><xpath expr="//sheet" position="inside"><group string="بيانات سند المواد"><field name="x_rekaz_receiver_name"/><field name="x_rekaz_receiver_phone"/><field name="x_rekaz_voucher_notes"/></group></xpath><xpath expr="//header" position="inside"><button name="'+str(report.id)+'" type="action" string="طباعة سند المواد" class="btn-secondary"/></xpath></data>'
configs.append(('Rekaz material voucher fields','stock.picking','form',arch,parent.id))
if ('name="' + moves + '"') in str(parent.arch_db):
    arch = '<data><xpath expr="//field[@name=\\'MOVE\\']//list" position="inside"><field name="x_rekaz_receiver_name" optional="show"/><field name="x_rekaz_receiver_phone" optional="show"/></xpath><xpath expr="//field[@name=\\'MOVE\\']//list/field[@name=\\'product_uom_qty\\']" position="attributes"><attribute name="string">الكمية</attribute><attribute name="optional"/><attribute name="column_invisible">False</attribute></xpath></data>'.replace('MOVE',moves)
    configs.append(('Rekaz material recipient lines','stock.picking','form',arch,parent.id))
configs.append(('Rekaz transaction warehouse list','stock.picking','list','<list create="1" delete="0"><field name="name"/><field name="scheduled_date"/><field name="x_rekaz_task"/><field name="x_rekaz_project"/><field name="x_rekaz_ticket_no"/><field name="x_rekaz_work_order"/><field name="x_rekaz_movement"/><field name="x_rekaz_receiver_name"/><field name="state"/></list>',False))
configs.append(('Rekaz transaction warehouse search','stock.picking','search',SEARCH_ARCH,False))
view_ids = {}
for name, model, kind, arch, parent_id in configs:
    found = env['ir.ui.view'].search([('name','=',name),('model','=',model)],limit=1)
    vals = {'name':name,'model':model,'type':kind,'arch_db':arch,'inherit_id':parent_id}
    if found:
        found.write(vals)
    else:
        found = env['ir.ui.view'].create(vals)
    view_ids[name] = found.id
window = env['ir.actions.act_window'].search([('name','=','مستودعات المعاملات'),('res_model','=','stock.picking')],limit=1)
vals = {'name':'مستودعات المعاملات','res_model':'stock.picking','view_mode':'list,form','view_id':view_ids['Rekaz transaction warehouse list'],'search_view_id':view_ids['Rekaz transaction warehouse search'],'domain':"[('x_rekaz_task','!=',False),('company_id','in',allowed_company_ids)]"}
if window:
    window.write(vals)
else:
    window = env['ir.actions.act_window'].create(vals)
menu = env['ir.ui.menu'].search([('name','=','مستودعات المعاملات'),('parent_id','=',env.ref('stock.menu_stock_root').id)],limit=1)
vals = {'name':'مستودعات المعاملات','parent_id':env.ref('stock.menu_stock_root').id,'action':'ir.actions.act_window,'+str(window.id),'sequence':15}
if menu:
    menu.write(vals)
else:
    env['ir.ui.menu'].create(vals)
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'تم تجهيز مستودعات المعاملات','message':'شاشة الحركات وبيانات المستلم وسند الطباعة جاهزة. لم تُعتمد أو تُغيّر أي حركة مخزون.','sticky':True}}
'''.replace('REPORT_ARCH', repr(report_arch('MOVES'))).replace('SEARCH_ARCH', repr('<search><field name=\'x_rekaz_task\'/><field name=\'x_rekaz_ticket_no\'/><field name=\'x_rekaz_work_order\'/><field name=\'x_rekaz_receiver_name\'/><filter name=\'incoming\' string=\'الوارد\' domain="[(\'x_rekaz_movement\',\'=\',\'incoming\')]"/><filter name=\'outgoing\' string=\'المنصرف\' domain="[(\'x_rekaz_movement\',\'=\',\'outgoing\')]"/><filter name=\'returned\' string=\'المسترجع\' domain="[(\'x_rekaz_movement\',\'=\',\'returned\')]"/><filter name=\'scrapped\' string=\'التالف\' domain="[(\'x_rekaz_movement\',\'=\',\'scrapped\')]"/><group><filter name=\'project\' string=\'المشروع\' context="{\'group_by\':\'x_rekaz_project\'}"/><filter name=\'task\' string=\'المعاملة\' context="{\'group_by\':\'x_rekaz_task\'}"/></group></search>'))


def install(api):
    mid = api.find('ir.model',[('model','=','stock.picking')],['id'])
    if len(mid)!=1:
        raise ValueError('Stock transfers model missing')
    action_id = api.ensure('ir.actions.server',[('name','=','Rekaz warehouse voucher installer'),('model_id','=',mid[0]['id'])],
                           {'name':'Rekaz warehouse voucher installer','model_id':mid[0]['id'],'state':'code','code':setup_code()})
    api.call('ir.actions.server','write',[[action_id],{'code':setup_code()}])
    if getattr(api,'major_version',18)>=20:
        api.call('ir.actions.server','run',[],ids=[action_id])
    else:
        api.call('ir.actions.server','run',[[action_id]])
