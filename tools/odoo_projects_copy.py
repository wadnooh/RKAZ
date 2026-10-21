"""Copy live custom Rekaz projects and transactions into native Odoo Projects."""
import hashlib
import html
import json
from html.parser import HTMLParser
import re
import rekaz_odoo_sync as sync

LABELS = {
    'id': 'رقم السجل', 'project_code': 'رمز المشروع', 'project_name': 'اسم المشروع',
    'project_type': 'نوع المشروع', 'status': 'الحالة', 'start_date': 'تاريخ البدء',
    'end_date': 'تاريخ الانتهاء', 'ticket_no': 'رقم المعاملة / العطل', 'site': 'الموقع',
    'notes': 'الملاحظات', 'attachments': 'المرفقات', 'value': 'القيمة',
    'receive_date': 'تاريخ استلام البلاغ', 'district': 'الحي', 'receive_time': 'وقت الاستلام',
    'agent': 'مندوب الطوارئ', 'station_no': 'رقم المحطة', 'feeder_no': 'رقم المغذي',
    'location': 'الموقع', 'fault_type': 'نوع العطل', 'classification': 'تصنيف البلاغ',
    'team': 'الفريق المسؤول', 'dispatch_time': 'وقت التوجيه', 'arrival_time': 'وقت الوصول',
    'execution_date': 'تاريخ التنفيذ', 'photographed': 'حالة التصوير',
    'quantities_done': 'حالة حصر الكميات', 'asphalt_clearance': 'إخلاء الأسفلت',
    'metering_status': 'حالة التمتير', 'consultant_approval': 'اعتماد الاستشاري',
    'invoice_status': 'حالة المستخلص', 'work_order': 'رقم أمر العمل',
    'invoice_no': 'رقم الفاتورة', 'invoice_id': 'رقم المستخلص', 'sap_status': 'حالة SAP',
    'items_value': 'قيمة بنود المعاملة', 'created_at': 'تاريخ الإنشاء',
    'updated_at': 'آخر تحديث', 'rekaz_code': 'رقم ركاز', 'has_excavation': 'وجود أعمال حفر',
    'work_no': 'رقم المعاملة', 'work_date': 'تاريخ العمل', 'department': 'القسم',
    'work_type': 'نوع العمل', 'sap_reservation_no': 'رقم حجز SAP',
    'notification_no': 'رقم الإشعار', 'supervisor': 'المشرف', 'contractor': 'المقاول',
    'extract_no': 'رقم المستخلص', 'amount': 'المبلغ', 'order_date': 'تاريخ أمر العمل',
}


def arabic_description(table, row):
    titles = {sync.m.field_name(k): v for k, v in LABELS.items()}
    titles.update({sync.m.field_name(k): v for k, v in sync.m.SPEC[table].get('labels', {}).items()})
    cells = []
    for key, value in row.items():
        if not key.startswith('x_c_') or value is False or value is None or value == '':
            continue
        if key not in titles:
            raise ValueError('Missing Arabic field label; no technical field names will be displayed: ' + key)
        text = str(value).replace('\r\n', '\n').replace('\r', '\n')
        rendered = html.escape(text).replace('\n', '<br/>')
        cells.append('<tr><th scope="row">' + html.escape(titles[key]) + '</th><td>' + rendered + '</td></tr>')
    return '<div dir="rtl" style="text-align:right"><h3>' + html.escape(sync.m.SPEC[table]['title']) + '</h3><table class="table table-bordered"><tbody>' + ''.join(cells) + '</tbody></table></div>'


def arabic_name(table, row):
    if table == 'projects':
        return row.get(sync.m.field_name('project_name')) or row['x_name']
    title = sync.m.SPEC[table]['title']
    code = row.get(sync.m.field_name('rekaz_code')) or row.get(sync.m.field_name('ticket_no')) or row.get(sync.m.field_name('work_order')) or row.get(sync.m.field_name('work_no')) or row['x_name']
    return title + ' — ' + str(code)


def task_number_values(row):
    fields = {
        'x_rekaz_fault_no': 'ticket_no',
        'x_rekaz_notification_no': 'notification_no',
        'x_rekaz_work_order': 'work_order',
    }
    result = {}
    for target, source in fields.items():
        value = row.get(target) or row.get(sync.m.field_name(source)) or row.get(source) or ''
        result[target] = str(value).strip()
    return result


def formatted_task_name(values):
    fault = str(values.get('x_rekaz_fault_no') or '').strip()
    notification = str(values.get('x_rekaz_notification_no') or '').strip()
    work_order = str(values.get('x_rekaz_work_order') or '').strip()
    parts = []
    if fault:
        parts.append('رقم العطل: ' + fault)
    elif notification:
        parts.append('رقم الإشعار: ' + notification)
    if work_order:
        parts.append('أمر العمل: ' + work_order)
    return ' — '.join(parts) if parts else None


def task_name(row, fallback):
    return formatted_task_name(task_number_values(row)) or fallback


def task_numbers_from_description(description):
    class TableParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.rows = []
            self.row = None
            self.cell = None

        def handle_starttag(self, tag, attrs):
            if tag == 'tr':
                self.row = []
            elif tag in {'th', 'td'} and self.row is not None:
                self.cell = []

        def handle_data(self, data):
            if self.cell is not None:
                self.cell.append(data)

        def handle_endtag(self, tag):
            if tag in {'th', 'td'} and self.cell is not None:
                self.row.append(''.join(self.cell))
                self.cell = None
            elif tag == 'tr' and self.row is not None:
                self.rows.append(self.row)
                self.row = None

    parser = TableParser()
    parser.feed(description or '')
    parser.close()
    labels = {
        'رقم المعاملة / العطل': 'x_rekaz_fault_no',
        'رقم العطل': 'x_rekaz_fault_no',
        'رقم العطل المرتبط': 'x_rekaz_fault_no',
        'رقم أمر العمل': 'x_rekaz_work_order',
        'أمر العمل': 'x_rekaz_work_order',
        'رقم الإشعار': 'x_rekaz_notification_no',
    }
    result = {}
    for row in parser.rows:
        if len(row) < 2:
            continue
        label = re.sub(r'\s+', ' ', row[0]).strip()
        target = labels.get(label)
        value = re.sub(r'\s+', ' ', row[1]).strip()
        if target and value and target not in result:
            result[target] = value
    return result


def fingerprint(name, description):
    return hashlib.sha256(json.dumps([name, description], ensure_ascii=False).encode()).hexdigest()


def html_signature(value):
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.tokens = []
        def handle_starttag(self, tag, attrs):
            self.tokens.append(('start', tag, tuple(sorted(attrs))))
        def handle_endtag(self, tag):
            self.tokens.append(('end', tag))
        def handle_data(self, data):
            self.tokens.append(('text', data.replace('\r\n', '\n').replace('\r', '\n')))
    parser = Parser()
    parser.feed(value or '')
    parser.close()
    return parser.tokens


def save_current_hash(api, model, rid):
    saved = api.call(model, 'read', [[rid]], fields=['name', 'description'])[0]
    api.call(model, 'write', [[rid], {'x_rekaz_import_hash': fingerprint(saved['name'], saved.get('description') or '')}])


def upsert(api, model, key, values, legacy=None):
    identity_fields = ['id', 'name', 'description', 'x_rekaz_import_hash']
    if model == 'project.task':
        identity_fields.extend(['x_rekaz_fault_no', 'x_rekaz_notification_no', 'x_rekaz_work_order'])
    found = api.find(model, [('x_rekaz_origin', '=', key), ('company_id', '=', api.company_id)],
                     identity_fields)
    if len(found) > 1:
        raise ValueError('Duplicate native project/task source identity')
    digest = fingerprint(values['name'], values['description'])
    if found:
        old = found[0]
        current = fingerprint(old['name'], old.get('description') or '')
        # Preserve manually edited native records instead of overwriting them.
        verified_legacy = bool(legacy and old['name'] == legacy['name']
                               and old.get('x_rekaz_import_hash') == fingerprint(legacy['name'], legacy['description'])
                               and html_signature(old.get('description')) == html_signature(legacy['description']))
        generated_task_name = (model == 'project.task'
                               and formatted_task_name(task_number_values(old)) == old['name'])
        if old.get('x_rekaz_import_hash') not in (current, digest) and not verified_legacy and not generated_task_name:
            sync.m.save_report(sync.Path(__file__).resolve().parents[1] / 'outputs/reportrek_projects_conflict.json',
                               {'model': model, 'source_key': key, 'target': old,
                                'requested': values, 'current_hash': current, 'requested_hash': digest})
            raise ValueError('Native record edited; review before resuming: ' + model + ' id=' + str(old['id']))
        if (current == old.get('x_rekaz_import_hash') or verified_legacy or generated_task_name) and digest != current:
            api.call(model, 'write', [[old['id']], dict(values, x_rekaz_import_hash=digest)])
            save_current_hash(api, model, old['id'])
        return old['id']
    rid = api.call(model, 'create', [dict(values, x_rekaz_origin=key, x_rekaz_import_hash=digest)])
    save_current_hash(api, model, rid)
    return rid


def install_fields(api, model):
    mid = api.find('ir.model', [('model', '=', model)], ['id'])
    if len(mid) != 1:
        raise ValueError('Install Odoo Projects before copying records')
    field_specs = [('x_rekaz_origin', 'هوية المصدر في ركاز'), ('x_rekaz_import_hash', 'بصمة النسخة المنقولة')]
    if model == 'project.task':
        field_specs.extend([
            ('x_rekaz_fault_no', 'رقم العطل'),
            ('x_rekaz_notification_no', 'رقم الإشعار'),
            ('x_rekaz_work_order', 'رقم أمر العمل'),
        ])
    for name, label in field_specs:
        api.ensure('ir.model.fields', [('model_id', '=', mid[0]['id']), ('name', '=', name)],
                   {'model_id': mid[0]['id'], 'name': name, 'field_description': name,
                    'ttype': 'char', 'state': 'manual', 'index': True})
        found = api.find('ir.model.fields', [('model_id', '=', mid[0]['id']), ('name', '=', name)], ['id'])
        api.call('ir.model.fields', 'write', [[found[0]['id']], {'field_description': label}])


def copy(api, sqlite_source=None):
    # Read actual Odoo records, including current field values, not the old SQLite snapshot.
    required = [sync.m.model_name(t) for t in ('projects', 'tickets', 'reinforcement_works', 'construction_works', 'contractor_works', 'primary_team_orders')]
    available = api.call('ir.model', 'search_read', [[('model', 'in', required)]], fields=['model'], limit=False)
    missing = sorted(set(required) - {row['model'] for row in available})
    if missing and sqlite_source is None:
        raise ValueError('Rekaz source models are absent or inaccessible in this database: ' + ', '.join(missing) + '. No project/task was created.')
    sources = {}
    tables = ('projects', 'tickets', 'reinforcement_works', 'construction_works', 'contractor_works', 'primary_team_orders')
    if sqlite_source is not None:
        with sync.m.snapshot(sqlite_source) as conn:
            specs = sync.m.catalog(conn)
            for table in tables:
                sources[table] = []
                if table not in specs:
                    continue
                for row in sync.m.source_rows(conn, table, specs[table]):
                    sources[table].append(dict(
                        id=row['id'], x_name=sync.m.title(row, table),
                        x_source_key='rekaz-main:' + table + ':' + sync.m.row_key(row, specs[table]),
                        **{sync.m.field_name(k): v for k, v in row.items()}))
    for table in (() if sqlite_source is not None else tables):
        model = sync.m.model_name(table)
        fields = api.call(model, 'fields_get', [], attributes=['type'])
        names = ['id', 'x_name', 'x_source_key'] + [f for f in fields if f.startswith('x_c_')]
        sources[table] = api.call(model, 'search_read',
                                 [[('x_company_id', '=', api.company_id), ('x_source_key', '=like', 'rekaz-main:%')]],
                                 fields=names, limit=False)
    if not any(sources.values()):
        raise ValueError('No imported Rekaz records are available for the selected company. No project/task was created.')
    for table, rows in sources.items():
        for row in rows:
            arabic_description(table, row)
    for model in ('project.project', 'project.task'):
        install_fields(api, model)
    projects, default_projects = {}, {}
    total = 0
    for table, rows in sources.items():
        for row in rows:
            key = 'reportrek:' + row['x_source_key']
            description = arabic_description(table, row)
            legacy_description = '<div><p>منقول من ركاز</p>' + ''.join(
                '<p>' + html.escape(k) + ': ' + html.escape(str(v)) + '</p>'
                for k, v in row.items() if k.startswith('x_c_') and v is not False) + '</div>'
            legacy = {'name': (row.get(sync.m.field_name('project_name')) or row['x_name']) if table == 'projects' else row['x_name'],
                      'description': legacy_description}
            if table == 'projects':
                name = arabic_name(table, row)
                rid = upsert(api, 'project.project', key,
                             {'name': name, 'description': description, 'company_id': api.company_id}, legacy=legacy)
                code = row.get(sync.m.field_name('project_code'))
                if code:
                    projects.setdefault(str(code), []).append(rid)
            else:
                code = row.get(sync.m.field_name('project_code'))
                matches = projects.get(str(code), []) if code else []
                if len(matches) == 1:
                    parent = matches[0]
                else:
                    section = 'ops' if table in ('tickets', 'primary_team_orders') else table
                    if section not in default_projects:
                        default_projects[section] = upsert(api, 'project.project', 'reportrek:rekaz-section:' + section,
                            {'name': 'ركاز — ' + sync.m.SPEC[table]['title'],
                             'description': '<p>معاملات ركاز التي لا تحتوي على رابط مشروع فريد.</p>', 'company_id': api.company_id})
                    parent = default_projects[section]
                numbers = task_number_values(row)
                rid = upsert(api, 'project.task', key,
                             dict(numbers, name=task_name(row, arabic_name(table, row)), description=description,
                                  company_id=api.company_id, project_id=parent), legacy=legacy)
            total += 1
            print('Projects copy: ' + table + ' source_id=' + str(row['id']) + ' target_id=' + str(rid), flush=True)
    print('Native Projects copy finished: source_records=' + str(total), flush=True)
