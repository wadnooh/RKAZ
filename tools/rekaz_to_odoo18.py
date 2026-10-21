#!/usr/bin/env python3
"""ركاز -> Odoo 18: standalone, standard-library-only data/workspace migration.

Run --help for examples. No write to Odoo unless --apply is supplied.
This creates a CUSTOM data workspace, not a port of Flask business workflows.
Does not post stock/accounting entries, send messages, or copy user credentials.
Use one process at a time and a staging Odoo database first.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import sqlite3
import sys
import tempfile
from urllib.parse import urlparse, unquote
import xml.etree.ElementTree as ET
import xmlrpc.client

VERSION = '1.0.0'
OWNER = 'rekaz_to_odoo18:v1'
GROUPS = {
    'constructions': 'الإنشاءات', 'projects': 'المشاريع والعقود',
    'ops': 'العمليات والصيانة', 'reinforcement': 'التقوية',
    'contractors': 'المقاولون', 'quality': 'التنسيقات والجودة',
    'safety': 'السلامة', 'warehouses': 'المستودعات',
    'external': 'المشتريات والعهد الخارجية', 'reference': 'المراجع والمتابعة',
}
# Only Rekaz project-management tables. HR, salaries, users, tokens and settings excluded.
SPEC = {'quantities': {'title': 'الكميات / ورقة التمتير', 'section': 'ops', 'labels': {'ticket_no': 'رقم العطل', 'item_no': 'رقم البند', 'description': 'وصف البند', 'unit': 'الوحدة', 'qty': 'الكمية', 'unit_price': 'سعر الوحدة', 'ref': 'صورة/مرجع', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'photos': {'title': 'قائمة الصور', 'section': 'ops', 'labels': {'ticket_no': 'رقم العطل', 'before_shot': 'قبل التنفيذ', 'during_shot': 'أثناء التنفيذ', 'after_shot': 'بعد التنفيذ', 'quantities_shot': 'صورة الكميات', 'location_shot': 'صورة الموقع', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'metering': {'title': 'التمتير', 'section': 'ops', 'labels': {'ticket_no': 'رقم العطل', 'officer': 'مسؤول المستخلصات', 'start_date': 'تاريخ بداية التمتير', 'submit_date': 'تاريخ رفع للاستشاري', 'approve_date': 'تاريخ الاعتماد', 'status': 'الحالة', 'approved_value': 'قيمة معتمدة (مبلغ الكميات)', 'reject_ratio': 'نسبة رفض', 'notes': 'ملاحظات الاستشاري', 'attachments': 'المرفقات'}}, 'contractor_works': {'title': 'أعمال المقاولين', 'section': 'contractors', 'labels': {'work_no': 'رقم أمر العمل', 'work_date': 'التاريخ', 'contractor': 'المقاول', 'ticket_no': 'رقم العطل', 'work_type': 'نوع العمل', 'site': 'الموقع', 'status': 'الحالة', 'value': 'القيمة', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'contractor_supplies': {'title': 'مواد موردة من مقاول', 'section': 'contractors', 'labels': {'supply_no': 'رقم التوريد', 'supply_date': 'التاريخ', 'contractor': 'المقاول', 'ticket_no': 'رقم العطل', 'work_no': 'رقم أمر عمل المقاول', 'status': 'الحالة', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'construction_works': {'title': 'أعمال الإنشاءات', 'section': 'constructions', 'labels': {'work_no': 'رقم أمر العمل', 'work_date': 'التاريخ', 'site': 'الموقع', 'district': 'الحي', 'work_type': 'نوع العمل', 'station_no': 'رقم المحطة', 'ticket_no': 'رقم العطل المرتبط', 'status': 'الحالة', 'supervisor': 'المشرف', 'value': 'القيمة التقديرية', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'primary_team_orders': {'title': 'الفرق الأولية', 'section': 'ops', 'labels': {'work_order': 'أمر العمل', 'extract_no': 'رقم المستخلص', 'amount': 'المبلغ', 'order_date': 'التاريخ', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'projects': {'title': 'المشاريع', 'section': 'projects', 'labels': {'project_code': 'كود المشروع', 'project_name': 'اسم المشروع', 'project_type': 'نوع المشروع', 'status': 'الحالة', 'start_date': 'تاريخ البداية', 'end_date': 'تاريخ النهاية', 'ticket_no': 'رقم العطل المرتبط', 'site': 'الموقع', 'value': 'القيمة الإجمالية', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'coordination': {'title': 'التنسيقات الفنية', 'section': 'quality', 'labels': {'ticket_no': 'رقم العطل', 'needs_asphalt': 'هل يحتاج إخلاء أسفلت؟', 'officer': 'مسؤول التنسيقات', 'request_date': 'تاريخ الطلب', 'lab': 'المختبر', 'report_no': 'رقم التقرير', 'receive_date': 'تاريخ الاستلام', 'status': 'الحالة', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'quality_clearances': {'title': 'إخلاءات الأسفلت (فسوحات)', 'section': 'quality', 'labels': {'ticket_no': 'رقم العطل', 'clearance_no': 'رقم الإخلاء', 'rekaz_code': 'كود ركاز (RR)', 'clearance_stage': 'مرحلة الإخلاء', 'request_date': 'تاريخ الطلب', 'issue_date': 'تاريخ الإصدار', 'contractor': 'الجهة', 'status': 'الحالة', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'quality_inspections': {'title': 'فحوصات الجودة', 'section': 'quality', 'labels': {'ticket_no': 'رقم العطل', 'inspect_date': 'تاريخ الفحص', 'inspector': 'المفتش', 'result': 'النتيجة', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'new_coordinations': {'title': 'التنسيقات الجديدة', 'section': 'quality', 'labels': {'coord_no': 'رقم التنسيق', 'coord_kind': 'نوع التبويب', 'request_date': 'تاريخ الطلب', 'authority': 'الجهة', 'work_desc': 'وصف العمل', 'location': 'الموقع', 'district': 'الحي', 'linked_section': 'القسم المستهدف بعد الإصدار', 'ticket_no': 'رقم العطل (عمليات)', 'project_code': 'كود المشروع', 'construction_work_no': 'رقم أمر عمل الإنشاءات', 'status': 'الحالة', 'license_no': 'رقم الرخصة', 'issue_date': 'تاريخ الإصدار', 'expiry_date': 'تاريخ الانتهاء', 'officer': 'المسؤول', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'issued_licenses': {'title': 'الرخص المصدرة', 'section': 'quality', 'labels': {'license_no': 'رقم الرخصة', 'rtc_no': 'رقم مرجع / RTC', 'work_order': 'أمر العمل', 'issue_date': 'تاريخ الإصدار', 'expiry_date': 'تاريخ الانتهاء', 'authority': 'الجهة', 'license_type': 'نوع الرخصة', 'status': 'الحالة', 'workflow_status': 'متابعة التصريح', 'linked_section': 'القسم المرتبط', 'ticket_no': 'رقم العطل (عمليات)', 'project_code': 'كود المشروع', 'construction_work_no': 'رقم أمر عمل الإنشاءات', 'district': 'الحي', 'location': 'الموقع', 'license_length': 'طول الرخصة', 'work_desc': 'وصف العمل', 'consultant_submitted': 'تقديم الاستشاري', 'consultant_submit_date': 'تاريخ تقديم الاستشاري', 'consultant_result': 'نتيجة الاستشاري', 'consultant_notes': 'ملاحظات الاستشاري', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'safety_permits': {'title': 'تصاريح العمل', 'section': 'safety', 'labels': {'permit_no': 'رقم التصريح', 'ticket_no': 'رقم العطل', 'work_order': 'أمر العمل', 'permit_date': 'التاريخ', 'location': 'الموقع', 'issuer': 'المُصدر', 'receiver': 'المستلم', 'work_desc': 'وصف العمل', 'status': 'الحالة', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'safety_incidents': {'title': 'بلاغات السلامة', 'section': 'safety', 'labels': {'incident_no': 'رقم العطل', 'incident_date': 'التاريخ', 'location': 'الموقع', 'incident_type': 'نوع الحادث', 'severity': 'الخطورة', 'status': 'الحالة', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'warehouse_items': {'title': 'أصناف المستودع', 'section': 'warehouses', 'labels': {'item_no': 'رقم المادة', 'item_name': 'اسم المادة', 'unit': 'الوحدة', 'category': 'التصنيف', 'opening_qty': 'رصيد افتتاحي', 'min_qty': 'حد أدنى', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'warehouse_tx': {'title': 'معاملات المستودع', 'section': 'warehouses', 'labels': {'voucher_no': 'رقم السند', 'tx_date': 'تاريخ الحركة', 'tx_type': 'نوع الحركة', 'item_no': 'رقم المادة', 'item_name': 'اسم المادة', 'unit': 'الوحدة', 'qty': 'الكمية', 'recipient': 'المستلم', 'sender': 'المسلم', 'ticket_no': 'رقم العطل / كود ER', 'rekaz_code': 'كود ركاز (ER)', 'source_section': 'القسم المصدر', 'work_order': 'رقم أمر العمل', 'source_ref': 'مرجع المعاملة', 'region': 'المنطقة', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'external_purchases': {'title': 'المشتريات الخارجية', 'section': 'external', 'labels': {'purchase_no': 'رقم الطلب', 'purchase_date': 'التاريخ', 'supplier': 'المورد', 'status': 'الحالة', 'ticket_no': 'رقم العطل', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'custody': {'title': 'العهد', 'section': 'external', 'labels': {'custody_no': 'رقم العهدة', 'custody_date': 'التاريخ', 'employee': 'الموظف', 'item_no': 'رقم المادة', 'item_name': 'المادة / المعدة', 'unit': 'الوحدة', 'qty': 'الكمية', 'status': 'الحالة', 'return_date': 'تاريخ الإرجاع', 'issued_voucher_no': 'سند صرف العهدة', 'return_voucher_no': 'سند إرجاع العهدة', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'reinforcement_departments': {'title': 'أقسام التعزيز', 'section': 'reinforcement', 'labels': {'dept_name': 'اسم القسم', 'dept_code': 'رمز القسم', 'status': 'الحالة', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}, 'reinforcement_works': {'title': 'معاملات التعزيز / اسكيمات', 'section': 'reinforcement', 'labels': {'rekaz_code': 'رقم ركاز', 'work_order': 'رقم أمر العمل', 'work_no': 'رقم المعاملة / الاسكيمة', 'sap_reservation_no': 'رقم حجز الساب', 'notification_no': 'رقم الإشعار', 'station_no': 'رقم المحطة', 'work_date': 'التاريخ', 'department': 'القسم', 'work_type': 'نوع العمل', 'location': 'الموقع', 'ticket_no': 'رقم العطل', 'status': 'الحالة', 'value': 'القيمة', 'notes': 'ملاحظات', 'attachments': 'المرفقات'}}}

EXTRA = {
    'tickets': ('ops', 'المعاملات والأعطال'), 'teams': ('ops', 'الفرق'),
    'invoices': ('ops', 'متابعة المستخلصات والفواتير'),
    'contracts': ('projects', 'العقود'),
    'boq_items': ('reference', 'بنود الكميات'),
    'contract_boq_files': ('reference', 'ملفات كميات العقود'),
    'contract_boq_items': ('reference', 'بنود كميات العقود'),
    'ticket_boq_lines': ('ops', 'بنود كميات المعاملات'),
    'transaction_boq_lines': ('reference', 'بنود كميات الأقسام'),
    'external_purchase_lines': ('external', 'بنود المشتريات'),
    'custody_lines': ('external', 'بنود العهد'),
    'contractor_supply_lines': ('contractors', 'بنود توريدات المقاولين'),
    'followups': ('reference', 'المتابعات'), 'reviews': ('reference', 'المراجعات'),
    'app_custom_tabs': ('reference', 'تعريفات التبويبات المخصصة'),
    'ops_custom_tabs': ('reference', 'تعريفات تبويبات العمليات القديمة'),
}
for _table, (_section, _title) in EXTRA.items():
    SPEC.setdefault(_table, {'section': _section, 'title': _title, 'labels': {}})

PHOTO_COLUMNS = {'attachments', 'before_shot', 'during_shot', 'after_shot', 'quantities_shot', 'location_shot'}
# Retain raw source keys alongside these editable Odoo relations.
LINKS = {
    'ticket_id': ('tickets', 'id'), 'ticket_no': ('tickets', 'ticket_no'),
    'purchase_id': ('external_purchases', 'id'), 'custody_id': ('custody', 'id'),
    'supply_id': ('contractor_supplies', 'id'), 'new_coordination_id': ('new_coordinations', 'id'),
    'warehouse_tx_id': ('warehouse_tx', 'id'), 'return_warehouse_tx_id': ('warehouse_tx', 'id'),
    'project_code': ('projects', 'project_code'), 'contract_no': ('contracts', 'contract_no'),
    'construction_work_no': ('construction_works', 'work_no'),
}
WARNINGS = [
    'Custom editable Odoo workspace; original Flask workflows, calculations and approvals are NOT ported.',
    'Warehouse/invoice rows are historical/custom records, NOT native stock moves or posted invoices.',
    'Original user-specific permissions are NOT imported; dedicated reader/editor groups are created.',
    'Custom tab definitions are retained as data; their original Flask pages are NOT executable in Odoo.',
    'Date/time values stay source text to preserve invalid/legacy values without silent conversion.',
    'Remote/S3 attachment references are retained; only supplied local upload files are copied.',
    'Do not run concurrent importers. Existing imported rows are never overwritten or deleted.',
]


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def ident(value):
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', value):
        raise ValueError('Unsupported SQL identifier: ' + value)
    return '"' + value + '"'


def field_name(column):
    stem = re.sub('[^a-z0-9_]', '_', column.lower())
    return 'x_c_' + stem[:36] + '_' + digest(column)[:8]


def model_name(table):
    return 'x_rekaz_pm_' + table


def pack(value):
    if isinstance(value, bytes):
        return {'__base64__': base64.b64encode(value).decode('ascii')}
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Non-finite source numeric value; repair the source before import')
    return value


def kind(column):
    sql = column['type'].upper()
    if 'BLOB' in sql:
        return 'binary'
    if 'INT' in sql:
        # Source integers can exceed XML-RPC/Odoo int32. Preserve exactly as strings.
        return 'char'
    if any(x in sql for x in ('REAL', 'FLOAT', 'DOUBLE')):
        return 'float'
    return 'text' if column['name'] in {'notes', 'attachments', 'checklist_json'} else 'char'


def odoo_value(value, column):
    if value is None:
        return False
    if kind(column) == 'binary':
        return value['__base64__'] if isinstance(value, dict) else base64.b64encode(value).decode('ascii')
    if kind(column) == 'float':
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('Non-finite number')
        return number
    return str(value)


@contextlib.contextmanager
def snapshot(path):
    path = Path(path).resolve(strict=True)
    source = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    target = sqlite3.connect(':memory:')
    try:
        source.backup(target)
        target.row_factory = sqlite3.Row
        if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Source database integrity check failed')
        yield target
    finally:
        source.close()
        target.close()


def catalog(conn):
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'tickets', 'construction_works', 'warehouse_tx'}.issubset(existing):
        raise ValueError('This is not a recognized Rekaz project-management database')
    result = {}
    for table, spec in SPEC.items():
        if table not in existing:
            continue
        cols = [dict(r) for r in conn.execute('PRAGMA table_info(' + ident(table) + ')')]
        keys = [c['name'] for c in sorted(cols, key=lambda c: c['pk']) if c['pk']]
        if not keys:
            raise ValueError('No stable primary key for ' + table)
        result[table] = dict(spec, columns=cols, keys=keys)
    return result


def source_rows(conn, table, spec):
    for row in conn.execute('SELECT * FROM ' + ident(table)):
        data = {k: pack(v) for k, v in dict(row).items()}
        if table in {'followups', 'app_custom_tabs'} and data.get('section') not in GROUPS:
            continue
        if table == 'transaction_boq_lines' and data.get('module_name') not in SPEC:
            continue
        yield data


def row_key(row, spec):
    return dump([row[k] for k in spec['keys']])


def title(row, table):
    for key in ('rekaz_code', 'ticket_no', 'project_name', 'contract_name', 'name', 'title',
                'item_name', 'item_no', 'work_no', 'voucher_no', 'custody_no', 'purchase_no'):
        if row.get(key):
            return str(row[key])[:250]
    return SPEC[table]['title'] + ' #' + str(row.get('id', ''))


def media_refs(row):
    refs = set()
    for key in PHOTO_COLUMNS & row.keys():
        raw = row[key]
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            values = json.loads(raw)
        except ValueError:
            values = [raw]
        if isinstance(values, str):
            values = [values]
        if isinstance(values, list):
            refs.update(v for v in values if isinstance(v, str) and v.strip() and v not in {'نعم', 'لا'})
    return sorted(refs)


def local_media(root, ref):
    if not root:
        return None
    if ref.startswith('/media/s3/'):
        base = Path(root).resolve(strict=True)
        manifest = base / '.rekaz_media_manifest.json'
        if not manifest.is_file():
            return None
        mapped = json.loads(manifest.read_text(encoding='utf-8')).get(ref)
        if not isinstance(mapped, str) or not mapped.startswith('s3/'):
            return None
        path = (base / mapped).resolve()
        if not path.is_relative_to(base):
            raise ValueError('Attachment manifest path escapes upload directory')
        return path if path.is_file() else None
    if '://' in ref or ref.startswith(('//', '\\\\', '/media/s3/')):
        return None
    clean = unquote(ref.split('?', 1)[0]).removeprefix('/media/local/').removeprefix('local:').removeprefix('/uploads/').removeprefix('uploads/')
    base = Path(root).resolve(strict=True)
    path = (base / clean).resolve()
    if not path.is_relative_to(base):
        raise ValueError('Attachment path escapes upload directory')
    return path if path.is_file() else None


def relations(table, spec, all_specs):
    output = []
    for col in spec['columns']:
        key = col['name']
        link = LINKS.get(key)
        if key == 'file_id' and table in {'contract_boq_items', 'ticket_boq_lines', 'transaction_boq_lines'}:
            link = ('contract_boq_files', 'id')
        if key == 'item_no' and table in {'warehouse_tx', 'custody_lines', 'external_purchase_lines', 'contractor_supply_lines'}:
            link = ('warehouse_items', 'item_no')
        if link and link[0] != table and link[0] in all_specs:
            if link[1] in {c['name'] for c in all_specs[link[0]]['columns']}:
                output.append((key, link[0], link[1]))
    return output


class RPC:
    def __init__(self, url, database, login, secret, company_id, company_name):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.query or parsed.fragment or parsed.path.strip('/'):
            raise ValueError('Use the HTTPS base URL without credentials, query or path')
        self.db, self.secret = database, secret
        socket.setdefaulttimeout(90)
        common = xmlrpc.client.ServerProxy(url.rstrip('/') + '/xmlrpc/2/common', allow_none=True)
        version = common.version()
        if int(version['server_version_info'][0]) != 18:
            raise ValueError('This script targets Odoo 18 only')
        self.uid = common.authenticate(database, login, secret, {})
        if not self.uid:
            raise ValueError('Odoo authentication failed')
        self.proxy = xmlrpc.client.ServerProxy(url.rstrip('/') + '/xmlrpc/2/object', allow_none=True)
        self.context = {'lang': 'en_US', 'active_test': False}
        self.company_id = company_id
        user = self.call('res.users', 'read', [[self.uid]], fields=['company_ids'])[0]
        if company_id not in user['company_ids']:
            raise ValueError('The selected company is not assigned to this Odoo user')
        company = self.call('res.company', 'read', [[company_id]], fields=['name'])[0]
        if company['name'].strip() != company_name.strip():
            raise ValueError('Company ID/name mismatch. Select the intended Rekaz company explicitly.')
        self.context['allowed_company_ids'] = [company_id]

    def call(self, model, method, args=None, **kwargs):
        kwargs['context'] = dict(self.context, **kwargs.get('context', {}))
        # Deliberately no blind retry of writes after a network timeout.
        return self.proxy.execute_kw(self.db, self.uid, self.secret, model, method, args or [], kwargs)

    def find(self, model, domain, fields=None):
        return self.call(model, 'search_read', [domain], fields=fields or ['id'], limit=2)

    def ensure(self, model, domain, values, check=None):
        found = self.find(model, domain, ['id'] + list(check or {}))
        if len(found) > 1:
            raise ValueError('Duplicate target metadata: ' + model)
        if found:
            for field, expected in (check or {}).items():
                actual = found[0].get(field)
                if isinstance(actual, list):
                    actual = actual[0]
                if actual != expected:
                    raise ValueError('Target metadata mismatch: ' + model + '.' + field)
            return found[0]['id']
        return self.call(model, 'create', [values])


def field_element(parent, name, **attrs):
    return ET.SubElement(parent, 'field', name=name, **attrs)


def install_access(api, table, mid, group_ids):
    domain = "[('x_company_id', 'in', company_ids)]"
    rule_name = OWNER + ':' + table + ':company'
    if getattr(api, 'major_version', 18) >= 20:
        # Odoo 20 combines ACLs and record rules. No group means a restriction,
        # never a public grant; group rules grant read or create/read/update only.
        restriction = dict(name=rule_name, model_id=mid, group_id=False,
                           operation='crud', domain=domain)
        api.ensure('ir.access', [('name', '=', rule_name)], restriction,
                   {k: restriction[k] for k in ['model_id', 'group_id', 'operation', 'domain']})
        for role, gid in group_ids.items():
            access = dict(name=OWNER + ':' + table + ':' + role, model_id=mid,
                          group_id=gid, operation='cru' if role == 'editor' else 'r', domain=False)
            api.ensure('ir.access', [('name', '=', access['name'])], access,
                       {k: access[k] for k in ['model_id', 'group_id', 'operation', 'domain']})
    else:
        api.ensure('ir.rule', [('name', '=', rule_name)],
                   {'name': rule_name, 'model_id': mid, 'domain_force': domain},
                   {'domain_force': domain, 'model_id': mid})
        for role, gid in group_ids.items():
            access = dict(name=OWNER + ':' + table + ':' + role, model_id=mid, group_id=gid,
                          perm_read=True, perm_write=role == 'editor', perm_create=role == 'editor', perm_unlink=False)
            api.ensure('ir.model.access', [('name', '=', access['name'])], access,
                       {k: access[k] for k in ['model_id', 'group_id', 'perm_read', 'perm_write', 'perm_create', 'perm_unlink']})


def install(api, specs, root_title='إدارة المشاريع — ركاز', group_prefix='Rekaz PM'):
    group_ids = {}
    for role, label in [('reader', 'قراءة'), ('editor', 'إدخال وتعديل')]:
        name = group_prefix + ' / ' + label
        group_ids[role] = api.ensure('res.groups', [('name', '=', name)], {'name': name})
    # Only the operator is assigned; never assign every internal user.
    api.call('res.groups', 'write', [[group_ids['editor']],
             {getattr(api, 'group_users_field', 'users'): [[4, api.uid]]}])
    menu_groups_field = getattr(api, 'menu_groups_field', 'groups_id')
    menus = {}
    root_name = root_title
    root = api.ensure('ir.ui.menu', [('name', '=', root_name), ('parent_id', '=', False)],
                      {'name': root_name, menu_groups_field: [[6, 0, list(group_ids.values())]]})
    mids = {}
    for table, spec in specs.items():
        model = model_name(table)
        mids[table] = api.ensure('ir.model', [('model', '=', model)],
            {'name': spec['title'] + ' — ركاز', 'model': model, 'state': 'manual', 'info': OWNER},
            {'state': 'manual', 'info': OWNER})
    for table, spec in specs.items():
        model, mid = model_name(table), mids[table]
        fields = [
            ('x_company_id', 'الشركة', 'many2one', {'relation': 'res.company', 'required': True, 'on_delete': 'restrict'}),
            ('x_source_key', 'هوية السجل في ركاز', 'char', {'index': True, 'readonly': True}),
            ('x_source_hash', 'بصمة المصدر', 'char', {'readonly': True}),
            ('x_source_payload', 'نسخة بيانات المصدر JSON', 'text', {'readonly': True}),
        ]
        for c in spec['columns']:
            fields.append((field_name(c['name']), spec.get('labels', {}).get(c['name'], c['name']), kind(c), {}))
        for column, target, target_col in relations(table, spec, specs):
            fields.append(('x_rel_' + column, 'ربط: ' + spec.get('labels', {}).get(column, column),
                           'many2one', {'relation': model_name(target), 'on_delete': 'restrict'}))
        for name, label, ttype, extra in fields:
            values = dict(name=name, field_description=label, ttype=ttype, state='manual', model_id=mid, **extra)
            check = {'ttype': ttype, 'state': 'manual'}
            if 'relation' in extra:
                check['relation'] = extra['relation']
            api.ensure('ir.model.fields', [('model_id', '=', mid), ('name', '=', name)], values, check)
        install_access(api, table, mid, group_ids)
        list_arch = ET.Element('list', string=spec['title'], delete='false')
        field_element(list_arch, 'x_name')
        display = [c for c in spec['columns'] if c['name'] != 'id'][:7]
        for c in display:
            field_element(list_arch, field_name(c['name']), optional='show')
        form = ET.Element('form', string=spec['title'], delete='false')
        sheet = ET.SubElement(form, 'sheet')
        group = ET.SubElement(sheet, 'group')
        field_element(group, 'x_name')
        field_element(group, 'x_company_id')
        notebook = ET.SubElement(sheet, 'notebook')
        for offset in range(0, len(spec['columns']), 20):
            page = ET.SubElement(notebook, 'page', string='البيانات ' + str(offset // 20 + 1))
            group = ET.SubElement(page, 'group')
            for c in spec['columns'][offset:offset + 20]:
                field_element(group, field_name(c['name']))
        group = ET.SubElement(ET.SubElement(notebook, 'page', string='العلاقات والمصدر'), 'group')
        for column, _, _ in relations(table, spec, specs):
            field_element(group, 'x_rel_' + column)
        for name in ['x_source_key', 'x_source_hash', 'x_source_payload']:
            field_element(group, name, readonly='1')
        for view_type, arch in [('list', list_arch), ('form', form)]:
            name = OWNER + ':' + table + ':' + view_type
            api.ensure('ir.ui.view', [('name', '=', name), ('model', '=', model)],
                       {'name': name, 'model': model, 'type': view_type, 'arch_db': ET.tostring(arch, encoding='unicode')})
        action = api.ensure('ir.actions.act_window', [('res_model', '=', model), ('name', '=', spec['title'])],
            {'name': spec['title'], 'res_model': model, 'view_mode': 'list,form',
             'context': "{'default_x_company_id': allowed_company_ids[0]}"})
        section = spec['section']
        if section not in menus:
            menus[section] = api.ensure('ir.ui.menu', [('name', '=', GROUPS[section]), ('parent_id', '=', root)],
                {'name': GROUPS[section], 'parent_id': root, menu_groups_field: [[6, 0, list(group_ids.values())]]})
        api.ensure('ir.ui.menu', [('name', '=', spec['title']), ('parent_id', '=', menus[section])],
            {'name': spec['title'], 'parent_id': menus[section], 'action': 'ir.actions.act_window,' + str(action),
             menu_groups_field: [[6, 0, list(group_ids.values())]]})
        print('Schema ready:', table)


def transfer(api, conn, specs, args, report):
    mapping, index = {}, {}
    for table, spec in specs.items():
        model = model_name(table)
        report['tables'][table] = {'created': 0, 'existing': 0, 'source_changed': 0}
        for row in source_rows(conn, table, spec):
            key = args.source_key + ':' + table + ':' + row_key(row, spec)
            payload = dump(row)
            found = api.find(model, [('x_source_key', '=', key), ('x_company_id', '=', api.company_id)], ['id', 'x_source_hash'])
            if len(found) > 1:
                raise ValueError('Duplicate source key in target: ' + table)
            if found:
                rid = found[0]['id']
                report['tables'][table]['existing'] += 1
                if found[0]['x_source_hash'] != digest(payload):
                    report['tables'][table]['source_changed'] += 1
            else:
                values = {field_name(c['name']): odoo_value(row[c['name']], c) for c in spec['columns']}
                values.update(x_name=title(row, table), x_company_id=api.company_id, x_source_key=key,
                              x_source_hash=digest(payload), x_source_payload=payload)
                rid = api.call(model, 'create', [values])
                report['tables'][table]['created'] += 1
            mapping[(table, row_key(row, spec))] = rid
            for col, value in row.items():
                if value not in (None, '') and isinstance(value, (int, float, str)):
                    index.setdefault((table, col, str(value)), []).append(rid)
        print('Records:', table, report['tables'][table])
    for table, spec in specs.items():
        model = model_name(table)
        for row in source_rows(conn, table, spec):
            rid = mapping[(table, row_key(row, spec))]
            updates = {}
            for col, target, target_col in relations(table, spec, specs):
                if row[col] in (None, ''):
                    continue
                candidates = index.get((target, target_col, str(row[col])), [])
                if len(candidates) != 1:
                    report['unresolved_links'].append({'table': table, 'key': row_key(row, spec), 'column': col,
                                                       'reason': 'missing' if not candidates else 'ambiguous'})
                    continue
                updates['x_rel_' + col] = candidates[0]
            if updates:
                current = api.call(model, 'read', [[rid]], fields=list(updates))[0]
                # Resume incomplete links, preserving edits made in Odoo.
                empty = {k: v for k, v in updates.items() if not current.get(k)}
                if empty:
                    api.call(model, 'write', [[rid], empty])
            for ref in media_refs(row):
                path = local_media(args.uploads, ref)
                if not path:
                    report['missing_attachments'].append({'table': table, 'key': row_key(row, spec), 'ref': ref})
                    continue
                if path.stat().st_size > args.max_file_mb * 1024 * 1024:
                    raise ValueError('Attachment exceeds --max-file-mb; no silent truncation')
                content = path.read_bytes()
                marker = OWNER + ':' + digest(args.source_key + ':' + str(api.company_id) + ':' + table + ':' + row_key(row, spec) + ':' + ref)
                checksum = hashlib.sha1(content).hexdigest()
                found = api.find('ir.attachment', [('res_model', '=', model), ('res_id', '=', rid), ('description', '=', marker)], ['id', 'checksum'])
                if found:
                    if len(found) != 1 or found[0]['checksum'] != checksum:
                        report['missing_attachments'].append({'table': table, 'key': row_key(row, spec), 'ref': ref, 'reason': 'target conflict'})
                    continue
                api.call('ir.attachment', 'create', [{'name': path.name, 'type': 'binary', 'datas': base64.b64encode(content).decode(),
                    'res_model': model, 'res_id': rid, 'company_id': api.company_id, 'public': False, 'description': marker}])
                report['attachments_created'] += 1


def save_report(path, report):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        temp = f.name
    os.replace(temp, path)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''Examples (PowerShell):
  python rekaz_to_odoo18.py --sqlite "E:/Rekaz/instance/rakaz.db" --report plan.json
  $env:ODOO_URL="https://YOUR-COMPANY.odoo.com"
  $env:ODOO_DB="YOUR_DATABASE"
  $env:ODOO_LOGIN="YOUR_ADMIN_LOGIN"
  python rekaz_to_odoo18.py --sqlite "E:/Rekaz/instance/rakaz.db" --uploads "E:/Rekaz/instance/uploads" --source-key rekaz-main --company-id 2 --company-name "شركة ركاز الإنجاز للمقاولات" --apply --report result.json
API key is prompted securely, or read from ODOO_API_KEY. Do not paste it in chat.
The default run is OFFLINE and read-only. --apply creates schema and imports records.
Existing records are skipped, not overwritten. Resolve changed source rows manually.
Exit 0: complete within documented scope; 2: errors/incomplete/reconciliation needed.
No Odoo server or account is bundled. API access and admin customization rights required.
''')
    p.add_argument('--sqlite', required=True, type=Path)
    p.add_argument('--uploads', type=Path)
    p.add_argument('--source-key', default='rekaz-main')
    p.add_argument('--company-id', type=int)
    p.add_argument('--company-name')
    p.add_argument('--url', default=os.environ.get('ODOO_URL', ''))
    p.add_argument('--database', default=os.environ.get('ODOO_DB', ''))
    p.add_argument('--login', default=os.environ.get('ODOO_LOGIN', ''))
    p.add_argument('--apply', action='store_true')
    p.add_argument('--max-file-mb', type=int, default=25)
    p.add_argument('--report', type=Path, default=Path('rekaz_odoo_report.json'))
    args = p.parse_args(argv)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', args.source_key):
        p.error('--source-key must contain 1-64 ASCII letters, digits, underscores or hyphens')
    if args.max_file_mb < 1:
        p.error('--max-file-mb must be positive')
    if args.report.resolve() == args.sqlite.resolve():
        p.error('Report path must not overwrite the source database')
    if args.apply and not all([args.url, args.database, args.login, args.company_id, args.company_name]):
        p.error('--apply requires URL, database, login, company-id and exact company-name')
    report = dict(version=VERSION, generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        mode='apply' if args.apply else 'offline-plan', source_key=args.source_key, warnings=WARNINGS,
        tables={}, unresolved_links=[], missing_attachments=[], attachments_created=0, status='started')
    try:
        with snapshot(args.sqlite) as conn:
            specs = catalog(conn)
            report['schema'] = specs
            report['missing_source_tables'] = sorted(set(SPEC) - set(specs))
            report['counts'] = {table: sum(1 for _ in source_rows(conn, table, spec)) for table, spec in specs.items()}
            if args.apply:
                secret = os.environ.get('ODOO_API_KEY') or getpass.getpass('Odoo API key: ')
                api = RPC(args.url, args.database, args.login, secret, args.company_id, args.company_name)
                report['target'] = {'url': args.url, 'database': args.database, 'company_id': args.company_id, 'company_name': args.company_name}
                install(api, specs)
                transfer(api, conn, specs, args, report)
            report['status'] = 'needs_review' if (report['unresolved_links'] or report['missing_attachments'] or
                any(v.get('source_changed') for v in report['tables'].values())) else ('completed' if args.apply else 'plan_ready')
            print('Tables:', len(specs), 'Rows:', sum(report['counts'].values()))
            print('Mode:', report['mode'], 'Status:', report['status'])
    except (Exception, KeyboardInterrupt) as exc:
        report['status'] = 'failed_or_interrupted'
        # XML-RPC faults can contain personal source values. Do not log raw server tracebacks.
        report['error'] = type(exc).__name__ if isinstance(exc, xmlrpc.client.Fault) else str(exc)
        print('Stopped:', report['error'], file=sys.stderr)
    finally:
        save_report(args.report, report)
        print('Report:', args.report.resolve())
    return 0 if report['status'] in {'completed', 'plan_ready'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
