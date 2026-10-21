if not env.user.has_group('base.group_system'):
    raise UserError('لمسؤول النظام فقط.')
fields = env['ir.model.fields'].sudo().search([('model','=','x_rekaz_task_material_balance'),('name','in',['x_incoming_qty','x_outgoing_qty','x_returned_qty','x_scrapped_qty','x_balance_qty','x_reserved_qty','x_after_reservation_qty','x_global_balance_qty'])])
if len(fields) != 8:
    raise UserError('حقول الملخص غير مكتملة؛ لم يطبق الإصلاح.')
count = 0
for field in fields:
    old = field.compute or ''
    fixed = old.replace("= env['stock.location']", "= self.env['stock.location']").replace("= env['stock.quant']", "= self.env['stock.quant']")
    parts = []
    skip = False
    for line in fixed.splitlines():
        if line.startswith("    rec["):
            skip = not line.startswith("    rec['" + field.name + "']")
        if not skip:
            parts.append(line)
    fixed = '\n'.join(parts) + '\n'
    if old != fixed:
        field.write({'compute':fixed})
        count += 1
lines = env['x_rekaz_task_material_balance'].search([('x_task_id','=',186)],limit=10)
verified = lines.read(['x_incoming_qty','x_outgoing_qty','x_returned_qty','x_scrapped_qty','x_balance_qty','x_reserved_qty','x_after_reservation_qty','x_global_balance_qty'])
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'إصلاح معادلات مواد المعاملات','message':'تم إصلاح '+str(count)+' معادلات، والتحقق من قراءة '+str(len(verified))+' صفوف للمعاملة. لم تتغير الحركات أو الكميات.','sticky':True}}
