import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('sync', Path(__file__).resolve().parents[1] / 'tools/rekaz_odoo_sync.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


class FakeAPI:
    company_id = 7

    def __init__(self):
        self.rows = {}

    def find(self, model, domain, fields=None):
        return [dict(r) for r in self.rows.get(model, [])
                if all(r.get(k) == v for k, op, v in domain)]

    def call(self, model, method, args=None, **kwargs):
        rows = self.rows.setdefault(model, [])
        if method == 'create':
            rows.append(dict(args[0], id=len(rows) + 1))
            return rows[-1]['id']
        if method == 'write':
            for row in rows:
                if row['id'] in args[0]:
                    row.update(args[1])
            return True
        if method == 'read':
            return [dict(r) for r in rows if r['id'] in args[0]]
        raise AssertionError(method)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE tickets(id INTEGER PRIMARY KEY,ticket_no TEXT,notes TEXT);
            CREATE TABLE construction_works(id INTEGER PRIMARY KEY,work_no TEXT);
            CREATE TABLE warehouse_tx(id INTEGER PRIMARY KEY,ticket_no TEXT);
            CREATE TABLE hr_employees(id INTEGER PRIMARY KEY,full_name TEXT);
            CREATE TABLE payroll_rows(id INTEGER PRIMARY KEY,employee_id INTEGER,payload TEXT);
            CREATE TABLE users(id INTEGER PRIMARY KEY,password TEXT);
            INSERT INTO tickets VALUES(1,'T1','before');
            INSERT INTO warehouse_tx VALUES(1,'T1');
            INSERT INTO hr_employees VALUES(1,'Employee');
            INSERT INTO payroll_rows VALUES(1,1,'salary');
            INSERT INTO users VALUES(1,'never export');
        ''')
        self.api = FakeAPI()
        self.catalog = s.m.catalog(self.db)

    def tearDown(self):
        self.db.close()

    def run_sync(self):
        return s.sync(self.api, self.db, self.catalog, 'main')

    def test_transport_newline_normalization_is_not_a_manual_edit(self):
        self.db.execute("UPDATE tickets SET notes=?", ('first\r\nsecond\rthird',))
        self.run_sync()
        target = self.api.rows[s.m.model_name('tickets')][0]
        target[s.m.field_name('notes')] = 'first\nsecond\nthird'
        result = self.run_sync()
        self.assertEqual(result['conflicts'], [])
        self.assertEqual(result['tables']['tickets']['unchanged'], 1)
        target[s.m.field_name('notes')] = 'first\nchanged\nthird'
        self.assertEqual(len(self.run_sync()['conflicts']), 1)

    def test_diagnosis_separates_source_orphans_and_target_edits_without_writes(self):
        self.run_sync()
        self.api.rows[s.m.model_name('tickets')][0][s.m.field_name('notes')] = 'target edit'
        self.db.execute("UPDATE warehouse_tx SET ticket_no='missing'")
        self.api.prefetch = lambda model, keys, fields: {
            key: [dict(row) for row in self.api.rows.get(model, []) if row.get('x_source_key') == key]
            for key in keys}
        with patch.object(self.api, 'call') as writes:
            result = s.diagnose(self.api, self.db, self.catalog, 'main')
            writes.assert_not_called()
        conflict = next(c for c in result['target_conflicts'] if c['table'] == 'tickets')
        self.assertEqual(conflict['changes'][0]['actual'], 'target edit')
        self.assertEqual(result['source_link_issues'][0]['reason'], 'source_missing')

    def test_repeat_then_changed_source_updates_without_duplicates(self):
        first = self.run_sync()
        self.assertEqual(first['tables']['tickets']['created'], 1)
        self.assertEqual(self.run_sync()['tables']['tickets']['unchanged'], 1)
        self.db.execute("UPDATE tickets SET notes='after'")
        self.assertEqual(self.run_sync()['tables']['tickets']['updated'], 1)
        rows = self.api.rows[s.m.model_name('tickets')]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][s.m.field_name('notes')], 'after')

    def test_target_edit_is_preserved_and_reported(self):
        self.run_sync()
        row = self.api.rows[s.m.model_name('tickets')][0]
        row[s.m.field_name('notes')] = 'target edit'
        self.db.execute("UPDATE tickets SET notes='new source'")
        result = self.run_sync()
        self.assertEqual(result['tables']['tickets']['conflicts'], 1)
        self.assertEqual(row[s.m.field_name('notes')], 'target edit')

    def test_hr_and_payroll_link_but_no_credentials(self):
        self.run_sync()
        self.assertIn('hr_employees', self.catalog)
        self.assertNotIn('users', self.catalog)
        row = self.api.rows[s.m.model_name('payroll_rows')][0]
        self.assertEqual(row['x_rel_employee_id'], 1)
        self.assertNotIn('never export', json.dumps(self.api.rows))

    def test_duplicate_target_rejected(self):
        self.run_sync()
        rows = self.api.rows[s.m.model_name('tickets')]
        rows.append(dict(rows[0], id=2))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.run_sync()

    def test_company_isolation(self):
        self.run_sync()
        self.api.company_id = 8
        self.run_sync()
        self.assertEqual(len(self.api.rows[s.m.model_name('tickets')]), 2)

    def test_source_delete_does_not_delete_target(self):
        self.run_sync()
        self.db.execute('DELETE FROM warehouse_tx')
        self.run_sync()
        self.assertEqual(len(self.api.rows[s.m.model_name('warehouse_tx')]), 1)

    def test_corrupt_source_metadata_prevents_write(self):
        self.run_sync()
        self.api.rows[s.m.model_name('tickets')][0]['x_source_payload'] = '{}'
        self.assertEqual(self.run_sync()['tables']['tickets']['conflicts'], 1)

    def test_writer_lock_prevents_second_process(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sync.lock'
            with s.writer_lock(path):
                with self.assertRaises(FileExistsError):
                    with s.writer_lock(path):
                        pass
            self.assertFalse(path.exists())

    def test_plan_does_not_connect_to_odoo(self):
        with tempfile.TemporaryDirectory() as folder:
            dbpath = Path(folder) / 'source.db'
            target = sqlite3.connect(dbpath)
            self.db.backup(target)
            target.close()
            report = Path(folder) / 'report.json'
            with patch.object(s, 'API', side_effect=AssertionError('network forbidden')):
                self.assertEqual(s.main(['--sqlite', str(dbpath), '--report', str(report)]), 0)
            self.assertEqual(json.loads(report.read_text())['mode'], 'plan')

    def test_odoo20_setup_uses_new_access_and_membership_fields(self):
        api = FakeAPI()
        api.uid = 1
        api.major_version = 20
        api.group_users_field = 'user_ids'
        api.menu_groups_field = 'group_ids'
        api.ensure = lambda model, domain, values, check=None: api.call(model, 'create', [values])
        s.m.install(api, {'tickets': self.catalog['tickets']})
        self.assertNotIn('ir.rule', api.rows)
        self.assertNotIn('ir.model.access', api.rows)
        accesses = api.rows['ir.access']
        restriction = next(a for a in accesses if a['group_id'] is False)
        self.assertEqual(restriction['operation'], 'crud')
        self.assertIn('company_ids', restriction['domain'])
        grants = [a for a in accesses if a['group_id']]
        self.assertEqual({a['operation'] for a in grants}, {'r', 'cru'})
        self.assertTrue(all('d' not in a['operation'] for a in grants))
        self.assertEqual(api.rows['res.groups'][1]['user_ids'], [[4, 1]])
        self.assertTrue(all('group_ids' in menu and 'groups_id' not in menu
                            for menu in api.rows['ir.ui.menu']))

    def test_legacy_setup_keeps_company_rule_and_no_delete_acl(self):
        api = FakeAPI()
        api.ensure = lambda model, domain, values, check=None: api.call(model, 'create', [values])
        s.m.install_access(api, 'tickets', 12, {'reader': 3, 'editor': 4})
        self.assertNotIn('ir.access', api.rows)
        self.assertEqual(len(api.rows['ir.rule']), 1)
        self.assertTrue(all(not a['perm_unlink'] for a in api.rows['ir.model.access']))

    def test_odoo20_authentication_and_schema_preflight(self):
        common, client = Mock(), Mock()
        common.version.return_value = {'server_version_info': [20, 0, 0, 'final', 0, 'e']}
        def json2_call(model, method, args=None, **kwargs):
            self.assertTrue(args is None or isinstance(args, (list, tuple)))
            self.assertIsInstance(kwargs, dict)
            if (model, method) == ('res.users', 'search_read'):
                return [{'id': 9, 'login': 'login'}]
            if (model, method) == ('res.users', 'read'):
                return [{'company_ids': [7]}]
            if (model, method) == ('res.company', 'read'):
                return [{'name': 'TEST'}]
            return {
                'user_ids': {'type': 'one2many'},
                'group_ids': {'type': 'many2many'},
                'model_id': {'type': 'many2one'},
                'group_id': {'type': 'many2one'},
                'operation': {'type': 'selection'},
                'domain': {'type': 'char'},
            }
        client.call.side_effect = json2_call
        with patch.object(s.xmlrpc.client, 'ServerProxy', return_value=common), \
                patch.object(s, 'JSON2Client', return_value=client), \
                patch.object(s.time, 'sleep'):
            api = s.API('https://example.odoo.com', 'db', 'login', 'secret', 7, 'TEST')
        self.assertEqual(api.major_version, 20)
        self.assertEqual(api.context['allowed_company_ids'], [7])
        self.assertEqual(api.group_users_field, 'user_ids')
        common.authenticate.assert_not_called()

    def test_unknown_version_rejected_before_authentication(self):
        common = Mock()
        common.version.return_value = {'server_version_info': [21]}
        with patch.object(s.xmlrpc.client, 'ServerProxy', return_value=common):
            with self.assertRaisesRegex(ValueError, '18/19/20'):
                s.API('https://example.odoo.com', 'db', 'login', 'secret', 7, 'TEST')
        common.authenticate.assert_not_called()

    def test_rpc_failure_reports_operation_without_secret_or_payload(self):
        api = s.API.__new__(s.API)
        api.context = {}
        api.db, api.uid, api.secret = 'db', 1, 'private-key'
        api.proxy = Mock()
        api.proxy.execute_kw.side_effect = s.xmlrpc.client.Fault(
            1, "ValueError: Invalid field 'x_missing' in model. private-key salary confidential")
        with self.assertRaises(ValueError) as failure:
            api.call('x_rekaz_sync_tickets', 'search_read', [[]])
        message = str(failure.exception)
        self.assertIn('method=search_read', message)
        self.assertIn('field=x_missing', message)
        self.assertNotIn('private-key', message)
        self.assertNotIn('salary', message)

    def test_read_retries_but_write_is_not_repeated(self):
        api = s.API.__new__(s.API)
        api._call_once = Mock(side_effect=[s.ReadTransportFailure('timeout'), []])
        with patch.object(s.time, 'sleep'):
            self.assertEqual(api.call('x_test', 'search_read', [[]]), [])
        self.assertEqual(api._call_once.call_count, 2)
        api._call_once = Mock(side_effect=s.ReadTransportFailure('timeout'))
        with self.assertRaises(ValueError):
            api.call('x_test', 'create', [{'x_name': 'test'}])
        self.assertEqual(api._call_once.call_count, 1)

    def test_prefetch_uses_batches_and_preserves_duplicate_detection(self):
        api = s.API.__new__(s.API)
        api.company_id = 7
        api.call = Mock(return_value=[{'id': 1, 'x_source_key': 'key'}, {'id': 2, 'x_source_key': 'key'}])
        found = api.prefetch('x_test', ['key'] + [str(n) for n in range(100)], ['id'])
        self.assertEqual(api.call.call_count, 2)
        self.assertGreater(len(found['key']), 1)
        self.assertIs(api.call.call_args.kwargs['limit'], False)

    def test_rpc_spacing_precedes_request(self):
        api = s.API.__new__(s.API)
        api.context = {}
        api.db, api.uid, api.secret = 'db', 1, 'key'
        api.rpc_interval, api._last_rpc = 1.5, 100
        api.proxy = Mock()
        with patch.object(s.time, 'monotonic', return_value=100.5), patch.object(s.time, 'sleep') as sleep:
            api.call('x_test', 'read', [[1]])
        sleep.assert_called_once_with(1.0)

    def test_rate_limit_reads_wait_longer_without_repeating_writes(self):
        api = s.API.__new__(s.API)
        api._call_once = Mock(side_effect=[s.ReadTransportFailure('http_status=429'), []])
        with patch.object(s.time, 'sleep') as sleep:
            api.call('x_test', 'search_read', [[]])
        sleep.assert_called_once_with(30)

    def test_duplicate_audit_is_read_only(self):
        api = Mock()
        api.prefetch.return_value = {'main:tickets:[1]': [
            {'id': 1, 'x_source_hash': 'same'}, {'id': 2, 'x_source_hash': 'same'}]}
        result = s.audit_duplicates(api, self.db, {'tickets': self.catalog['tickets']}, 'main')
        self.assertEqual(result[0]['ids'], [1, 2])
        self.assertTrue(result[0]['same_source_hash'])
        api.call.assert_not_called()

    def test_duplicate_repair_retains_records_and_persists_previous_identity(self):
        self.run_sync()
        model = s.m.model_name('tickets')
        self.api.rows[model].append(dict(self.api.rows[model][0], id=2))
        self.api.prefetch = lambda model, keys, fields: {
            key: [dict(row) for row in self.api.rows.get(model, []) if row.get('x_source_key') == key]
            for key in keys}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'journal.json'
            result = s.repair_duplicates(self.api, self.db, {'tickets': self.catalog['tickets']}, 'main', path)
            self.assertEqual(result['rekeyed'], 1)
            self.assertEqual(len(self.api.rows[model]), 2)
            self.assertEqual(self.api.rows[model][0]['x_source_key'], 'main:tickets:[1]')
            self.assertEqual(self.api.rows[model][1]['x_source_key'], 'main:tickets:[1]:duplicate:2')
            self.assertEqual(json.loads(path.read_text())['changes'][0]['old_name'], 'T1')

    def test_duplicate_repair_refuses_manually_edited_copy(self):
        self.run_sync()
        model = s.m.model_name('tickets')
        copy = dict(self.api.rows[model][0], id=2)
        copy[s.m.field_name('notes')] = 'manual change'
        self.api.rows[model].append(copy)
        self.api.prefetch = lambda model, keys, fields: {'main:tickets:[1]': [dict(r) for r in self.api.rows[model]]}
        with tempfile.TemporaryDirectory() as folder:
            result = s.repair_duplicates(self.api, self.db, {'tickets': self.catalog['tickets']}, 'main', Path(folder) / 'journal.json')
        self.assertEqual(result['rekeyed'], 0)
        self.assertEqual(len(result['skipped']), 1)
        self.assertEqual(copy['x_source_key'], 'main:tickets:[1]')

    def test_duplicate_repair_refuses_renamed_copy_by_default(self):
        self.run_sync()
        model = s.m.model_name('tickets')
        copy = dict(self.api.rows[model][0], id=2, x_name='Custom Title')
        self.api.rows[model].append(copy)
        self.api.prefetch = lambda model, keys, fields: {'main:tickets:[1]': [dict(r) for r in self.api.rows[model]]}
        with tempfile.TemporaryDirectory() as folder:
            result = s.repair_duplicates(self.api, self.db, {'tickets': self.catalog['tickets']}, 'main', Path(folder) / 'journal.json')
        self.assertEqual(result['rekeyed'], 0)
        self.assertEqual(len(result['skipped']), 1)
        self.assertIn('name_differs_from_canonical', result['skipped'][0]['differences'][0]['reasons'])

    def test_duplicate_repair_allows_renamed_copy_with_flag(self):
        self.run_sync()
        model = s.m.model_name('tickets')
        copy = dict(self.api.rows[model][0], id=2, x_name='Custom Title')
        self.api.rows[model].append(copy)
        self.api.prefetch = lambda model, keys, fields: {'main:tickets:[1]': [dict(r) for r in self.api.rows[model]]}
        with tempfile.TemporaryDirectory() as folder:
            result = s.repair_duplicates(self.api, self.db, {'tickets': self.catalog['tickets']}, 'main',
                                         Path(folder) / 'journal.json', allow_renamed=True)
        self.assertEqual(result['rekeyed'], 1)
        self.assertEqual(len(result['skipped']), 0)
        self.assertEqual(self.api.rows[model][1]['x_source_key'], 'main:tickets:[1]:duplicate:2')

    def test_duplicate_repair_still_refuses_renamed_copy_with_field_edits(self):
        self.run_sync()
        model = s.m.model_name('tickets')
        copy = dict(self.api.rows[model][0], id=2, x_name='Custom Title')
        copy[s.m.field_name('notes')] = 'manual change'
        self.api.rows[model].append(copy)
        self.api.prefetch = lambda model, keys, fields: {'main:tickets:[1]': [dict(r) for r in self.api.rows[model]]}
        with tempfile.TemporaryDirectory() as folder:
            result = s.repair_duplicates(self.api, self.db, {'tickets': self.catalog['tickets']}, 'main',
                                         Path(folder) / 'journal.json', allow_renamed=True)
        self.assertEqual(result['rekeyed'], 0)
        self.assertEqual(len(result['skipped']), 1)


if __name__ == '__main__':
    unittest.main()
