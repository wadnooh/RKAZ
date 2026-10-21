"""Odoo Execute Code body, not a standalone command-line program."""
if not env.user.has_group('base.group_system'):
    raise UserError('نقل قوائم مراكز التكلفة متاح لمسؤول النظام فقط.')

accounting = env.ref('account.menu_finance', raise_if_not_found=False)
if not accounting:
    raise UserError('قائمة تطبيق المحاسبة غير متاحة. لم يتم تغيير القوائم.')

Menu = env['ir.ui.menu']
section = Menu.search([
    ('name', '=', 'مراكز تكلفة المعاملات'),
    ('parent_id', '=', accounting.id),
    ('action', '=', False),
], limit=1)
if not section:
    section = Menu.create({
        'name': 'مراكز تكلفة المعاملات',
        'parent_id': accounting.id,
        'sequence': 45,
    })

moved = []
for action_id, label, sequence in [
    (1348, 'مراكز تكلفة المعاملات', 10),
    (1350, 'التكاليف التشغيلية للمعاملات', 20),
    (1351, 'تحليل التكاليف المعتمدة', 30),
]:
    action = env['ir.actions.act_window'].browse(action_id).exists()
    if not action:
        raise UserError('إجراء مراكز التكلفة غير موجود؛ تم إيقاف النقل.')
    menus = Menu.search([('action', '=', 'ir.actions.act_window,' + str(action.id))])
    if not menus:
        raise UserError('قائمة مراكز التكلفة غير موجودة؛ تم إيقاف النقل.')
    menus.write({'parent_id': section.id, 'sequence': sequence})
    moved.append(label)

action = {
    'type': 'ir.actions.client',
    'tag': 'display_notification',
    'params': {
        'title': 'نقل مراكز التكلفة إلى المحاسبة',
        'message': 'تم نقل: ' + '، '.join(moved) + '. صلاحيات القوائم والبيانات محفوظة. لا قيود أو حركات مالية جديدة.',
        'sticky': True,
    },
}
