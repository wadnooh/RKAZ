"""Read-only Rekaz PM snapshot to CSV, with native Odoo project/task views."""
import argparse
import csv
import hashlib
import html
import json
from pathlib import Path
import zipfile

import rekaz_to_odoo18 as migration


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with path.open(encoding='utf-8-sig', newline='') as stream:
        actual = list(csv.DictReader(stream))
    assert len(actual) == len(rows), str(path)
    for original, saved in zip(rows, actual):
        assert all(saved[f] == ('' if original.get(f) is None else str(original.get(f, ''))) for f in fields)


def external_id(table, row, spec):
    return 'rekaz_pm.' + table + '_' + hashlib.sha256(migration.row_key(row, spec).encode()).hexdigest()[:24]


def description(table, row):
    labels = migration.SPEC[table]['labels']
    return '<dl>' + ''.join('<dt>' + html.escape(labels.get(k, k)) + '</dt><dd>' + html.escape(str(v)) + '</dd>' for k, v in row.items() if v is not None) + '</dl>'


def export(source, output):
    output.mkdir(parents=True, exist_ok=False)
    report = {'source': str(source.resolve()), 'tables': {}, 'native_projects': 0, 'native_tasks': 0,
              'notes': ['Local snapshot only; not verified against production.',
                        'Native descriptions preserve source fields; no stock/accounting posting.',
                        'Attachment references only; binary files are not included.']}
    projects, tasks = [], []
    with migration.snapshot(source) as conn:
        specs = migration.catalog(conn)
        data = {t: list(migration.source_rows(conn, t, s)) for t, s in specs.items()}
        for table, spec in specs.items():
            fields = [c['name'] for c in spec['columns']]
            rows = data[table]
            raw = [{k: migration.dump(v) if isinstance(v, dict) else v for k, v in r.items()} for r in rows]
            write_csv(output / 'source_csv' / (table + '.csv'), fields, raw)
            custom = []
            for row, packed in zip(rows, raw):
                custom.append(dict(id=external_id(table, row, spec), x_name=migration.title(row, table),
                                   **{migration.field_name(k): v for k, v in packed.items()}))
            write_csv(output / 'custom_odoo_csv' / (table + '.csv'), ['id', 'x_name'] + [migration.field_name(k) for k in fields], custom)
            report['tables'][table] = {'title': spec['title'], 'rows': len(rows), 'model': migration.model_name(table),
                                       'field_map': {k: migration.field_name(k) for k in fields}}
        for row in data.get('projects', []):
            projects.append({'id': external_id('projects', row, specs['projects']),
                             'name': row.get('project_name') or row.get('project_code') or migration.title(row, 'projects'),
                             'description': description('projects', row)})
        task_tables = ['tickets', 'construction_works', 'reinforcement_works', 'contractor_works', 'primary_team_orders']
        for table in task_tables:
            if not data.get(table):
                continue
            section = specs[table]['section']
            group_id = 'rekaz_pm.section_' + section
            if not any(p['id'] == group_id for p in projects):
                projects.append({'id': group_id, 'name': 'ركاز - ' + migration.GROUPS[section], 'description': 'تجميع سجلات القسم الأصلي من ركاز'})
            for row in data[table]:
                tasks.append({'id': external_id(table, row, specs[table]), 'name': migration.title(row, table),
                              'project_id/id': group_id, 'description': description(table, row)})
        write_csv(output / '01_odoo_projects.csv', ['id', 'name', 'description'], projects)
        write_csv(output / '02_odoo_tasks.csv', ['id', 'name', 'project_id/id', 'description'], tasks)
    report.update(native_projects=len(projects), native_tasks=len(tasks))
    (output / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    guide = '''# نسخة إدارة المشاريع من ركاز إلى أودو

المصدر: قاعدة البيانات المحلية instance/rakaz.db. ليست نسخة مؤكدة من السيرفر.

## استيراد تطبيق المشاريع القياسي
1. افتح المشاريع ثم استيراد، وارفع 01_odoo_projects.csv.
2. طابق id مع External ID، وname مع الاسم، وdescription مع الوصف، واضغط اختبار قبل الاستيراد.
3. افتح المهام وارفع 02_odoo_tasks.csv، وطابق project_id/id مع المشروع / External ID. اختبر ثم استورد.
4. الترميز UTF-8 والفاصل فاصلة. المعرفات ثابتة؛ إعادة الاستيراد قد تحدث السجلات السابقة.

بيانات الحالة والمبالغ والتواريخ والموقع وباقي حقول المصدر محفوظة في الوصف. المهام مجمعة حسب قسمها الأصلي؛ لا يفترض التصدير ارتباطاً بمشروع محدد بدون دليل. هذا لا ينقل سير العمل أو المحاسبة أو المخزون.

## النسخة الشاملة
source_csv يحتوي كل حقول الجداول المشمولة في إدارة المشاريع، بما فيها الجداول الفارغة.
custom_odoo_csv يحتوي أعمدة مهيأة للنماذج المخصصة x_rekaz_pm_* المطابقة لأداة tools/rekaz_to_odoo18.py. يلزم إنشاء هذه النماذج والحقول أولاً؛ هذه الملفات ليست للاستيراد في نماذج أودو القياسية. راجع manifest.json لخريطة الحقول والأعداد. العلاقات الأصلية محفوظة كمراجع؛ إعداد العلاقات الفعلية يحتاج الأداة أو ربطاً لاحقاً.
المرفقات محفوظة كمراجع نصية فقط. لا تشمل النسخة ملفات المرفقات ولا المستخدمين وكلمات المرور ولا الموارد البشرية والرواتب.

المصدر المرجعي لطريقة الاستيراد: https://www.odoo.com/documentation/18.0/applications/essentials/export_import_data.html
'''
    (output / 'README_AR.md').write_text(guide, encoding='utf-8-sig')
    archive = output.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for file in sorted(output.rglob('*')):
            if file.is_file():
                z.write(file, file.relative_to(output))
    print(json.dumps({'archive': str(archive), 'tables': len(specs), 'source_records': sum(len(r) for r in data.values()),
                      'projects': len(projects), 'tasks': len(tasks)}, ensure_ascii=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sqlite', type=Path, default=Path('instance/rakaz.db'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export(args.sqlite, args.output)
