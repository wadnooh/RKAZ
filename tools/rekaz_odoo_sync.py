#!/usr/bin/env python3
"""All-section outbound replication; Rekaz remains the source of truth.

No network calls without --apply or --setup. One writer per target database.
Custom records only: no native stock/accounting posting, no credential export.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import re
from pathlib import Path
import socket
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
import xmlrpc.client

_spec = importlib.util.spec_from_file_location(
    'rekaz_sync_migration', Path(__file__).with_name('rekaz_to_odoo18.py'))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
m.OWNER = 'rekaz_sync:v1'
m.model_name = lambda table: 'x_rekaz_sync_' + table
m.GROUPS.update(hr='الموارد البشرية', workshop='الورشة', finance='المالية')
for table, section, title in [
    ('hr_employees', 'hr', 'الموظفون'), ('hr_leaves', 'hr', 'الإجازات'),
    ('workshop_cars', 'workshop', 'سيارات الورشة'),
    ('workshop_equipment', 'workshop', 'معدات الورشة'),
    ('cash_actual', 'finance', 'التدفقات النقدية'),
    ('sop', 'reference', 'إجراءات العمل'), ('lists', 'reference', 'القوائم المرجعية'),
    ('fixed_assets', 'finance', 'الأصول الثابتة'),
    ('fixed_asset_reviews', 'finance', 'مراجعات الأصول'),
    ('payroll_periods', 'hr', 'فترات الرواتب'), ('payroll_rows', 'hr', 'تفاصيل الرواتب'),
]:
    m.SPEC[table] = {'section': section, 'title': title, 'labels': {}}
m.LINKS['employee_id'] = ('hr_employees', 'id')
m.LINKS['period_id'] = ('payroll_periods', 'id')
m.LINKS['asset_id'] = ('fixed_assets', 'id')
m.LINKS['car_id'] = ('workshop_cars', 'id')
m.LINKS['equipment_id'] = ('workshop_equipment', 'id')
EXCLUDED = {'users', 'settings', 'user_permission_overrides', 'audit_log',
            'whatsapp_logs', 'programmer_devices', 'programmer_approve_codes',
            'sqlite_sequence'}


class ReadTransportFailure(ValueError):
    pass


class JSON2Client:
    READ_METHODS = {'fields_get', 'read', 'search', 'search_count', 'search_read'}

    def __init__(self, url, database, secret, context=None):
        parsed = urlparse(url)
        if (parsed.scheme != 'https' or not parsed.netloc or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path.strip('/')):
            raise ValueError('ODOO_URL must be an HTTPS base URL')
        self.url = url.rstrip('/')
        self.database = database
        self.secret = secret
        self.context = context or {'lang': 'en_US', 'active_test': False}

    @staticmethod
    def payload(method, args=None, kwargs=None, context=None):
        args = list(args or [])
        params = dict(kwargs or {})
        body = {'context': dict(context or {})}
        params.pop('context', None)

        if method in {'search', 'search_count', 'search_read'}:
            body['domain'] = args[0] if args else params.pop('domain', [])
        elif method == 'read':
            if not args:
                raise ValueError('JSON-2 read requires record ids')
            body['ids'] = args[0]
        elif method == 'create':
            if not args:
                raise ValueError('JSON-2 create requires values')
            values = args[0]
            body['vals_list'] = values if isinstance(values, list) else [values]
        elif method == 'write':
            if len(args) < 2:
                raise ValueError('JSON-2 write requires record ids and values')
            body['ids'] = args[0]
            body['vals'] = args[1]
        elif method == 'unlink':
            if not args:
                raise ValueError('JSON-2 unlink requires record ids')
            body['ids'] = args[0]
        elif method == 'fields_get':
            body['allfields'] = args[0] if args else None
        elif args:
            raise ValueError('JSON-2 adapter does not support positional arguments for ' + method)

        body.update(params)
        return body

    @staticmethod
    def _error_reason(status, raw):
        if status == 401:
            return 'authentication_failed'
        if status == 403:
            return 'access_denied'
        if status == 404:
            return 'endpoint_not_found'
        try:
            detail = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = {}
        if not isinstance(detail, dict):
            detail = {}
        message = str(detail.get('message', ''))
        error_name = str(detail.get('name', ''))
        for marker, reason in [
            ('AccessError', 'access_denied'), ('Invalid field', 'invalid_field'),
            ('ValidationError', 'validation_error'), ('UserError', 'user_error'),
            ('KeyError', 'unknown_model_or_key'), ('TypeError', 'type_error'),
            ('ValueError', 'value_error'), ('UniqueViolation', 'duplicate_constraint'),
            ('NotNullViolation', 'required_field'),
        ]:
            if marker in message or marker in error_name:
                return reason
        return 'remote_error'

    @staticmethod
    def _error_class(raw):
        try:
            detail = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ''
        if not isinstance(detail, dict):
            return ''
        name = detail.get('name')
        if not isinstance(name, str):
            return ''
        error_class = name.rsplit('.', 1)[-1]
        return error_class if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', error_class) else ''

    @staticmethod
    def _error_field(raw):
        try:
            detail = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ''
        if not isinstance(detail, dict):
            return ''
        message = detail.get('message')
        if not isinstance(message, str):
            return ''
        match = re.search(r"Invalid field [\"']?([A-Za-z_][A-Za-z0-9_.]*)", message)
        return match.group(1) if match else ''

    def call(self, model, method, args=None, context=None, **kwargs):
        merged_context = dict(self.context)
        merged_context.update(context or kwargs.pop('context', {}) or {})
        body = self.payload(method, args, kwargs, merged_context)
        url = self.url + '/json/2/' + quote(model, safe='') + '/' + quote(method, safe='')
        request = Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={
                'Authorization': 'bearer ' + self.secret,
                'Content-Type': 'application/json; charset=utf-8',
                'X-Odoo-Database': self.database,
                'User-Agent': 'Rekaz Odoo integration',
            },
            method='POST',
        )
        try:
            with urlopen(request, timeout=60) as response:
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read()
            message = 'Odoo JSON-2 failed: model=' + model + ' method=' + method + ' http_status=' + str(exc.code)
            reason = self._error_reason(exc.code, raw)
            error_class = self._error_class(raw)
            error_field = self._error_field(raw)
            diagnostic = ' reason=' + reason
            if error_class:
                diagnostic += ' error_class=' + error_class
            if error_field:
                diagnostic += ' invalid_field=' + error_field
            if exc.code == 401:
                diagnostic += (
                    ' action=verify_database_login_and_unexpired_api_key'
                    '_for_this_odoo_database')
            if exc.code in {429, 500, 502, 503, 504}:
                if method in self.READ_METHODS:
                    raise ReadTransportFailure(message + diagnostic) from None
                raise ValueError(message) from None
            raise ValueError(message + diagnostic) from None
        except (URLError, OSError, TimeoutError) as exc:
            message = ('Odoo JSON-2 transport failed: model=' + model + ' method=' + method
                       + ' error_type=' + type(exc).__name__)
            if method in self.READ_METHODS:
                raise ReadTransportFailure(message) from None
            raise ValueError(message) from None
        try:
            result = json.loads(raw.decode('utf-8')) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Odoo JSON-2 returned an invalid response: model=' + model + ' method=' + method) from None
        if method == 'create' and isinstance(result, list) and len(result) == 1:
            return result[0]
        return result


class API(m.RPC):
    def call(self, model, method, args=None, **kwargs):
        for attempt in range(3):
            try:
                return self._call_once(model, method, args, **kwargs)
            except ReadTransportFailure as exc:
                if method not in {'read', 'search_read', 'fields_get'} or attempt == 2:
                    raise ValueError(str(exc)) from None
                print('Retry read: ' + model + '.' + method, flush=True)
                time.sleep(30 if 'http_status=429' in str(exc) else 2 ** (attempt + 1))

    def prefetch(self, model, keys, fields):
        result = {}
        for offset in range(0, len(keys), 100):
            rows = self.call(model, 'search_read',
                             [[('x_source_key', 'in', keys[offset:offset + 100]),
                               ('x_company_id', '=', self.company_id)]],
                             fields=list(dict.fromkeys(fields + ['x_source_key'])), limit=False)
            for row in rows:
                result.setdefault(row['x_source_key'], []).append(row)
        return result

    def _call_once(self, model, method, args=None, **kwargs):
        interval = getattr(self, 'rpc_interval', 1.5)
        remaining = interval - (time.monotonic() - getattr(self, '_last_rpc', 0))
        if remaining > 0:
            time.sleep(remaining)
        self._last_rpc = time.monotonic()
        if getattr(self, 'major_version', 18) == 20:
            try:
                return self.json2.call(model, method, args, **kwargs)
            except ReadTransportFailure:
                raise
            except ValueError:
                raise
            except OSError as exc:
                message = ('Odoo JSON-2 transport failed: model=' + model + ' method=' + method
                           + ' error_type=' + type(exc).__name__)
                if method in JSON2Client.READ_METHODS:
                    raise ReadTransportFailure(message) from None
                raise ValueError(message) from None
        try:
            return super().call(model, method, args, **kwargs)
        except xmlrpc.client.Fault as exc:
            # Classify the remote exception without exposing traceback/payload/secrets.
            message = exc.faultString
            reason = 'remote_error'
            for marker, category in [
                ('AccessError', 'access_denied'), ('Invalid field', 'invalid_field'),
                ('ValidationError', 'validation_error'), ('UserError', 'user_error'),
                ('KeyError', 'unknown_model_or_key'), ('TypeError', 'type_error'),
                ('ValueError', 'value_error'), ('UniqueViolation', 'duplicate_constraint'),
                ('NotNullViolation', 'required_field'),
            ]:
                if marker in message:
                    reason = category
                    break
            match = re.search(r"Invalid field [\"']?([a-z_][a-z0-9_.]*)", message)
            field = ' field=' + match.group(1) if match else ''
            # Only exception class identifiers, never remote messages or payloads.
            classes = re.findall(r'(?m)^([A-Za-z_][A-Za-z0-9_.]{0,100}):', message)
            error_class = ' error_class=' + classes[-1] if classes else ''
            raise ValueError('Odoo RPC failed: model=' + model + ' method=' + method
                             + ' reason=' + reason + field + error_class) from None
        except xmlrpc.client.ProtocolError as exc:
            message = ('Odoo transport failed: model=' + model + ' method=' + method
                       + ' http_status=' + str(exc.errcode))
            if exc.errcode in {429, 500, 502, 503, 504}:
                raise ReadTransportFailure(message) from None
            raise ValueError(message) from None
        except OSError as exc:
            raise ReadTransportFailure('Odoo transport failed: model=' + model + ' method=' + method
                                       + ' error_type=' + type(exc).__name__) from None

    def __init__(self, url, database, login, secret, company_id, company_name):
        parsed = urlparse(url)
        if (parsed.scheme != 'https' or not parsed.netloc or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path.strip('/')):
            raise ValueError('ODOO_URL must be an HTTPS base URL')
        socket.setdefaulttimeout(60)
        common = xmlrpc.client.ServerProxy(url.rstrip('/') + '/xmlrpc/2/common', allow_none=True)
        major = common.version()['server_version_info'][0]
        if major not in (18, 19, 20):
            raise ValueError('Only Odoo 18/19/20 supported; verify version before applying')
        self.major_version = major
        self.rpc_interval = float(os.environ.get('ODOO_RPC_INTERVAL', '1.5'))
        if not 1.0 <= self.rpc_interval <= 60:
            raise ValueError('ODOO_RPC_INTERVAL must be between 1 and 60 seconds')
        self.db, self.secret = database, secret
        self.company_id = company_id
        self.context = {'lang': 'en_US', 'active_test': False}
        if major == 20:
            self.json2 = JSON2Client(url, database, secret, self.context)
            users = self.json2.call(
                'res.users', 'search_read', [[('login', '=', login)]],
                fields=['id', 'login'], limit=2,
            )
            if len(users) != 1 or users[0].get('login', '').strip().casefold() != login.strip().casefold():
                raise ValueError('Authentication failed: the API key does not identify the requested Odoo user')
            self.uid = users[0]['id']
            self.proxy = None
        else:
            self.uid = common.authenticate(database, login, secret, {})
            if not self.uid:
                raise ValueError('Authentication failed')
            self.proxy = xmlrpc.client.ServerProxy(url.rstrip('/') + '/xmlrpc/2/object', allow_none=True)
        user = self.call('res.users', 'read', [[self.uid]], fields=['company_ids'])[0]
        if company_id not in user['company_ids']:
            raise ValueError('Selected company is not assigned to this user')
        company = self.call('res.company', 'read', [[company_id]], fields=['name'])[0]
        if company['name'].strip() != company_name.strip():
            raise ValueError('Company ID/name mismatch')
        self.context['allowed_company_ids'] = [company_id]
        group_fields = self.call('res.groups', 'fields_get', attributes=['type'])
        menu_fields = self.call('ir.ui.menu', 'fields_get', attributes=['type'])
        self.group_users_field = 'user_ids' if 'user_ids' in group_fields else 'users'
        self.menu_groups_field = 'group_ids' if 'group_ids' in menu_fields else 'groups_id'
        if self.group_users_field not in group_fields or self.menu_groups_field not in menu_fields:
            raise ValueError('Unrecognized group/menu schema; setup stopped before writes')
        if major == 20:
            access_fields = self.call('ir.access', 'fields_get', attributes=['type'])
            if not {'model_id', 'group_id', 'operation', 'domain'}.issubset(access_fields):
                raise ValueError('Unrecognized Odoo 20 access schema; setup stopped before writes')


def audit_duplicates(api, conn, specs, source_key):
    duplicates = []
    for table, spec in specs.items():
        keys = [source_key + ':' + table + ':' + m.row_key(row, spec)
                for row in m.source_rows(conn, table, spec)]
        records = api.prefetch(m.model_name(table), keys, ['id', 'x_source_hash'])
        for key, rows in records.items():
            if len(rows) > 1:
                duplicates.append({'table': table, 'source_key': key,
                                   'ids': sorted(r['id'] for r in rows),
                                   'same_source_hash': len({r.get('x_source_hash') for r in rows}) == 1})
    return duplicates


def repair_duplicates(api, conn, specs, source_key, journal_path, allow_renamed=False):
    journal = {'mode': 'duplicate_identity_repair', 'changes': [], 'skipped': []}
    for table, spec in specs.items():
        columns = [m.field_name(c['name']) for c in spec['columns']]
        fields = ['id', 'x_name', 'x_source_payload', 'x_source_hash'] + columns
        keys = [source_key + ':' + table + ':' + m.row_key(row, spec)
                for row in m.source_rows(conn, table, spec)]
        records = api.prefetch(m.model_name(table), keys, fields)
        for key, group in records.items():
            if len(group) < 2:
                continue
            group.sort(key=lambda row: row['id'])
            canonical = group[0]
            # A matching stored hash alone does not prove the target is unedited.
            identical = True
            differences = []
            for row in group:
                try:
                    payload = json.loads(row['x_source_payload'])
                    expected = values_for(payload, spec)
                    reasons = []
                    if m.digest(m.dump(payload)) != row['x_source_hash']:
                        reasons.append('stored_payload_hash_mismatch')
                    if row['x_source_payload'] != canonical['x_source_payload']:
                        reasons.append('payload_differs_from_canonical')
                    if row['x_name'] != canonical['x_name']:
                        reasons.append('name_differs_from_canonical')
                    fields_changed = [field for field, value in expected.items()
                                      if not equivalent(row.get(field), value)]
                    if fields_changed:
                        reasons.append('managed_fields_differ_from_payload')
                    if reasons:
                        if allow_renamed and reasons == ['name_differs_from_canonical'] and not fields_changed:
                            pass
                        else:
                            identical = False
                            diff_entry = {'id': row['id'], 'reasons': reasons,
                                          'fields': fields_changed}
                            if 'name_differs_from_canonical' in reasons:
                                diff_entry['name'] = row.get('x_name')
                                diff_entry['canonical_name'] = canonical.get('x_name')
                            differences.append(diff_entry)
                except (ValueError, TypeError, KeyError):
                    identical = False
                    differences.append({'id': row['id'], 'reasons': ['invalid_stored_payload'], 'fields': []})
            if not identical:
                journal['skipped'].append({'table': table, 'source_key': key,
                                           'ids': [row['id'] for row in group],
                                           'differences': differences})
                continue
            for row in group[1:]:
                change = {'table': table, 'id': row['id'], 'canonical_id': canonical['id'],
                          'old_source_key': key, 'old_name': row['x_name'],
                          'new_source_key': key + ':duplicate:' + str(row['id']), 'status': 'pending'}
                journal['changes'].append(change)
                # Persist reversible old values BEFORE each remote mutation.
                m.save_report(journal_path, journal)
                api.call(m.model_name(table), 'write', [[row['id']],
                         {'x_source_key': change['new_source_key'],
                          'x_name': '[نسخة مكررة محفوظة #' + str(row['id']) + '] ' + row['x_name']}])
                change['status'] = 'applied'
                m.save_report(journal_path, journal)
            print('Duplicates retained: ' + table + ' canonical=' + str(canonical['id']), flush=True)
    m.save_report(journal_path, journal)
    return {'rekeyed': len(journal['changes']), 'skipped': journal['skipped'], 'journal': str(journal_path)}


def diagnose(api, conn, specs, source_key):
    """Read-only comparison distinguishing source orphans from target changes."""
    conflicts, links = [], []
    source_index = {}
    for table, spec in specs.items():
        for row in m.source_rows(conn, table, spec):
            for column, value in row.items():
                if value not in (None, '') and isinstance(value, (str, int, float)):
                    source_index.setdefault((table, column, str(value)), []).append(m.row_key(row, spec))
    for table, spec in specs.items():
        rows = list(m.source_rows(conn, table, spec))
        keys = [source_key + ':' + table + ':' + m.row_key(row, spec) for row in rows]
        fields = ['id', 'x_name', 'x_source_payload', 'x_source_hash'] + [m.field_name(c['name']) for c in spec['columns']]
        target = api.prefetch(m.model_name(table), keys, fields)
        for row, key in zip(rows, keys):
            for column, parent, parent_column in m.relations(table, spec, specs):
                raw = row[column]
                if raw in (None, ''):
                    continue
                matches = source_index.get((parent, parent_column, str(raw)), [])
                if len(matches) != 1:
                    links.append({'table': table, 'key': m.row_key(row, spec), 'column': column,
                                  'target_table': parent, 'reason': 'source_missing' if not matches else 'source_ambiguous',
                                  'candidate_count': len(matches)})
            for current in target.get(key, []):
                try:
                    old = json.loads(current['x_source_payload'])
                    expected = values_for(old, spec)
                    expected['x_name'] = m.title(old, table)
                    changes = [{'field': field, 'expected': value, 'actual': current.get(field)}
                               for field, value in expected.items() if not equivalent(current.get(field), value)]
                    metadata_valid = m.digest(m.dump(old)) == current['x_source_hash']
                    if changes or not metadata_valid:
                        conflicts.append({'table': table, 'key': m.row_key(row, spec), 'id': current['id'],
                                          'metadata_valid': metadata_valid, 'changes': changes})
                except (ValueError, TypeError, KeyError):
                    conflicts.append({'table': table, 'key': m.row_key(row, spec), 'id': current['id'],
                                      'metadata_valid': False, 'changes': []})
        print('Diagnosed: ' + table, flush=True)
    return {'target_conflicts': conflicts, 'source_link_issues': links}


def values_for(row, spec):
    return {m.field_name(c['name']): m.odoo_value(row[c['name']], c)
            for c in spec['columns']}


def equivalent(actual, expected):
    # Odoo represents unset char/text fields as False, including empty strings.
    # XML parsers normalize CRLF and CR to LF on the wire. Keep source hashes
    # exact, but do not mistake transport newline normalization for a user edit.
    if isinstance(actual, str) and isinstance(expected, str):
        return actual.replace('\r\n', '\n').replace('\r', '\n') == expected.replace('\r\n', '\n').replace('\r', '\n')
    return actual == expected or (expected in (None, '') and actual is False)


def sync(api, conn, specs, source_key):
    result = {'tables': {}, 'conflicts': [], 'unresolved_links': []}
    mapping, index, previous_rows = {}, {}, {}
    for table, spec in specs.items():
        counts = result['tables'][table] = dict(created=0, updated=0, unchanged=0, conflicts=0)
        rows = list(m.source_rows(conn, table, spec))
        fields = ['id', 'x_source_hash', 'x_source_payload', 'x_name'] + [m.field_name(c['name']) for c in spec['columns']]
        prefetched = None
        if hasattr(api, 'prefetch'):
            keys = [source_key + ':' + table + ':' + m.row_key(row, spec) for row in rows]
            prefetched = api.prefetch(m.model_name(table), keys, fields)
        print('Sync table: ' + table + ' rows=' + str(len(rows)), flush=True)
        for position, row in enumerate(rows, 1):
            key = source_key + ':' + table + ':' + m.row_key(row, spec)
            payload = m.dump(row)
            managed = values_for(row, spec)
            found = (prefetched.get(key, []) if prefetched is not None else
                     api.find(m.model_name(table),
                              [('x_source_key', '=', key), ('x_company_id', '=', api.company_id)], fields))
            if len(found) > 1:
                raise ValueError('Duplicate target source identity in ' + table)
            values = dict(managed, x_name=m.title(row, table), x_company_id=api.company_id,
                          x_source_key=key, x_source_hash=m.digest(payload), x_source_payload=payload)
            if not found:
                rid = api.call(m.model_name(table), 'create', [values])
                counts['created'] += 1
                previous_rows[(table, m.row_key(row, spec))] = None
            else:
                current = found[0]
                rid = current['id']
                try:
                    old = json.loads(current['x_source_payload'])
                    if m.digest(m.dump(old)) != current['x_source_hash']:
                        raise ValueError('Source metadata changed')
                    previous = values_for(old, spec)
                    previous['x_name'] = m.title(old, table)
                    edited = any(not equivalent(current.get(k), v) for k, v in previous.items())
                except (ValueError, TypeError, KeyError):
                    edited = True
                if edited:
                    counts['conflicts'] += 1
                    result['conflicts'].append({'table': table, 'key': m.row_key(row, spec)})
                    continue
                previous_rows[(table, m.row_key(row, spec))] = old
                if current['x_source_hash'] == values['x_source_hash']:
                    counts['unchanged'] += 1
                else:
                    api.call(m.model_name(table), 'write', [[rid], values])
                    counts['updated'] += 1
            mapping[(table, m.row_key(row, spec))] = rid
            for column, value in row.items():
                if value not in (None, '') and isinstance(value, (str, int, float)):
                    index.setdefault((table, column, str(value)), []).append(rid)
            if position % 20 == 0:
                print('Sync progress: ' + table + ' ' + str(position) + '/' + str(len(rows)), flush=True)
    for table, spec in specs.items():
        for row in m.source_rows(conn, table, spec):
            rid = mapping.get((table, m.row_key(row, spec)))
            if not rid:
                continue
            updates = {}
            for column, target, target_column in m.relations(table, spec, specs):
                raw = row[column]
                candidates = index.get((target, target_column, str(raw)), [])
                if raw in (None, ''):
                    updates['x_rel_' + column] = False
                elif len(candidates) == 1:
                    updates['x_rel_' + column] = candidates[0]
                else:
                    result['unresolved_links'].append({'table': table, 'key': m.row_key(row, spec),
                                                       'column': column})
            if updates:
                current = api.call(m.model_name(table), 'read', [[rid]], fields=list(updates))[0]
                old = previous_rows[(table, m.row_key(row, spec))]
                safe_updates = {}
                for column, target, target_column in m.relations(table, spec, specs):
                    field = 'x_rel_' + column
                    if field not in updates:
                        continue
                    actual = current.get(field)
                    actual = actual[0] if isinstance(actual, list) else actual
                    previous = index.get((target, target_column, str(old.get(column))), []) if old else []
                    expected = previous[0] if len(previous) == 1 else False
                    if actual == updates[field]:
                        continue
                    if actual == expected or not actual:
                        safe_updates[field] = updates[field]
                    else:
                        result['conflicts'].append({'table': table, 'key': m.row_key(row, spec), 'column': column})
                        result['tables'][table]['conflicts'] += 1
                if safe_updates:
                    api.call(m.model_name(table), 'write', [[rid], safe_updates])
    return result


@contextlib.contextmanager
def writer_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # An interrupted process leaves this file. Inspect the process before removing it.
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('Odoo writer lock exists. Another transfer may be running; verify its process before removing a stale lock: ' + str(path)) from None
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        path.unlink()


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite', default=str(Path(os.environ.get('RAKAZ_DATA_DIR', root / 'instance')) / 'rakaz.db'))
    parser.add_argument('--source-key', default=os.environ.get('REKAZ_SOURCE_KEY'))
    parser.add_argument('--setup', action='store_true', help='Create custom models and restricted access in Odoo')
    parser.add_argument('--apply', action='store_true', help='Replicate current source data to Odoo')
    parser.add_argument('--diagnose', action='store_true', help='Read-only source link and target field comparison')
    parser.add_argument('--audit-duplicates', action='store_true', help='Read-only target duplicate inventory')
    parser.add_argument('--repair-duplicates', action='store_true', help='Rekey identical redundant copies, retaining all records and attachments')
    parser.add_argument('--allow-renamed', action='store_true', help='Allow rekeying duplicate copies whose managed fields match even if display name differs')
    parser.add_argument('--uploads', help='Local upload root; used to copy attachments')
    parser.add_argument('--max-file-mb', type=int, default=25)
    parser.add_argument('--report', default=str(root / 'outputs' / 'odoo_sync_report.json'))
    parser.add_argument('--lock', default=str(root / 'instance' / 'odoo_sync.lock'))
    args = parser.parse_args(argv)
    if (args.audit_duplicates or args.repair_duplicates) and sum([args.audit_duplicates, args.repair_duplicates, args.apply, args.setup]) > 1:
        raise ValueError('Run each duplicate audit/repair mode separately')
    with m.snapshot(args.sqlite) as conn:
        specs = m.catalog(conn)
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        report = {'mode': 'plan', 'direction': 'rekaz_to_odoo',
                  'tables': {t: {'rows': len(list(m.source_rows(conn, t, s))), 'section': s['section']}
                             for t, s in specs.items()},
                  'excluded': sorted(existing & EXCLUDED),
                  'unmapped': sorted(existing - set(specs) - EXCLUDED)}
        if report['unmapped']:
            m.save_report(args.report, report)
            raise ValueError('Unmapped source tables: review the report before applying')
        if args.apply or args.setup or args.audit_duplicates or args.repair_duplicates or args.diagnose:
            if not args.source_key or ':' in args.source_key:
                raise ValueError('Set a stable REKAZ_SOURCE_KEY without colon characters')
            required = ['ODOO_URL', 'ODOO_DATABASE', 'ODOO_LOGIN', 'ODOO_API_KEY',
                        'ODOO_COMPANY_ID', 'ODOO_COMPANY_NAME']
            missing = [name for name in required if not os.environ.get(name)]
            if missing:
                raise ValueError('Missing environment settings: ' + ', '.join(missing))
            with writer_lock(args.lock):
                api = API(*(os.environ[k] for k in required[:4]),
                          int(os.environ['ODOO_COMPANY_ID']), os.environ['ODOO_COMPANY_NAME'])
                if args.diagnose:
                    report.update(diagnose(api, conn, specs, args.source_key))
                    report['mode'] = 'read_only_diagnosis'
                if args.audit_duplicates:
                    report['duplicates'] = audit_duplicates(api, conn, specs, args.source_key)
                    report['mode'] = 'duplicates_audit'
                if args.repair_duplicates:
                    stamp = m.datetime.datetime.now(m.datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                    path = Path(args.report).parent / ('odoo_duplicate_repair_' + stamp + '.json')
                    report['repair'] = repair_duplicates(api, conn, specs, args.source_key, path,
                                                         allow_renamed=args.allow_renamed)
                    report['mode'] = 'duplicates_repaired'
                if args.setup:
                    m.install(api, specs, root_title='مزامنة ركاز — جميع الأقسام', group_prefix='Rekaz Sync')
                if args.apply:
                    report.update(sync(api, conn, specs, args.source_key))
                    # Reuse attachment checks: only unchanged, conflict-free records are processed.
                    safe = {t: s for t, s in specs.items()
                            if not report['tables'][t]['conflicts']
                            and any(m.media_refs(row) for row in m.source_rows(conn, t, s))}
                    media_report = dict(tables={}, unresolved_links=[], missing_attachments=[], attachments_created=0)
                    m.transfer(api, conn, safe, args, media_report)
                    report['attachments'] = media_report
                    report['attachment_copy_enabled'] = bool(args.uploads)
                if not (args.audit_duplicates or args.repair_duplicates or args.diagnose):
                    report['mode'] = 'applied' if args.apply else 'schema_ready'
        m.save_report(args.report, report)
        print(m.dump(report))
        return 2 if (report.get('duplicates') or report.get('conflicts') or report.get('unresolved_links')
                     or report.get('repair', {}).get('skipped')
                     or report.get('attachments', {}).get('missing_attachments')) else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except OSError as exc:
        print('Local file/process failure: error_type=' + type(exc).__name__
              + ' errno=' + str(exc.errno) + ' winerror=' + str(getattr(exc, 'winerror', None)), file=sys.stderr)
        sys.exit(1)
    except (xmlrpc.client.Fault, xmlrpc.client.ProtocolError):
        # Remote faults may echo credentials or source payloads; do not log raw faults.
        print('Odoo transport/schema/access failure. Verify credentials, API plan, and setup.', file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
