"""Move existing Rekaz cost menus to Accounting; no financial data writes."""


def move(api):
    roots = api.call('ir.model.data', 'search_read', [[
        ('module', '=', 'account'), ('name', '=', 'menu_finance'),
        ('model', '=', 'ir.ui.menu'),
    ]], fields=['res_id'], limit=2)
    if len(roots) != 1:
        raise ValueError('Accounting application menu is absent or ambiguous; no menus changed')
    root_id = roots[0]['res_id']
    specifications = [
        (1348, 'account.analytic.account', 'مراكز تكلفة المعاملات', 10),
        (1350, 'x_rekaz_cost_entry', 'التكاليف التشغيلية للمعاملات', 20),
        (1351, 'x_rekaz_cost_entry', 'تحليل التكاليف المعتمدة', 30),
    ]
    targets = []
    for action_id, model, label, sequence in specifications:
        actions = api.call('ir.actions.act_window', 'search_read', [[
            ('id', '=', action_id), ('res_model', '=', model),
        ]], fields=['id'], limit=2)
        if len(actions) != 1:
            raise ValueError('Expected cost action is absent or incompatible: ' + str(action_id))
        menus = api.call('ir.ui.menu', 'search_read', [[
            ('action', '=', 'ir.actions.act_window,' + str(action_id)),
        ]], fields=['id', 'parent_id'], limit=False)
        if not menus:
            raise ValueError('Cost menu is absent; no menus changed: ' + str(action_id))
        targets.append((menus, label, sequence))
    sections = api.call('ir.ui.menu', 'search_read', [[
        ('parent_id', '=', root_id), ('name', '=', 'مراكز تكلفة المعاملات'),
        ('action', '=', False),
    ]], fields=['id'], limit=2)
    if len(sections) > 1:
        raise ValueError('Duplicate Accounting cost sections; review before continuing')
    section_id = sections[0]['id'] if sections else api.call('ir.ui.menu', 'create', [{
        'name': 'مراكز تكلفة المعاملات', 'parent_id': root_id, 'sequence': 45,
    }])
    for menus, label, sequence in targets:
        ids = [row['id'] for row in menus]
        api.call('ir.ui.menu', 'write', [ids, {'parent_id': section_id, 'sequence': sequence}])
        verified = api.call('ir.ui.menu', 'read', [ids], fields=['parent_id'])
        if len(verified) != len(ids) or any(
            not row['parent_id'] or row['parent_id'][0] != section_id for row in verified
        ):
            raise ValueError('Menu move could not be verified; rerun safely to resume')
        print('Accounting menu verified: ' + label)
    print('Cost menus moved to Accounting. Existing access rights, project links, stock and journal entries unchanged.')
    return 0
