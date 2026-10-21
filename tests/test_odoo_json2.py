import io
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

spec = importlib.util.spec_from_file_location(
    'rekaz_odoo_sync_json2_test',
    Path(__file__).resolve().parents[1] / 'tools' / 'rekaz_odoo_sync.py',
)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class JSON2ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = sync.JSON2Client(
            'https://reportrek.odoo.com', 'reportrek', 'test-secret',
            {'lang': 'en_US'},
        )

    def test_search_read_payload_uses_named_json2_parameters(self):
        payload = self.client.payload(
            'search_read',
            [[('name', '=', 'Example')]],
            {'fields': ['name'], 'limit': False},
            {'lang': 'ar_001'},
        )
        self.assertEqual(payload, {
            'context': {'lang': 'ar_001'},
            'domain': [('name', '=', 'Example')],
            'fields': ['name'],
            'limit': False,
        })

    def test_crud_payloads_translate_xmlrpc_positional_arguments(self):
        cases = [
            ('read', [[12]], {'fields': ['name']}, {'ids': [12], 'fields': ['name']}),
            ('create', [{'name': 'Example'}], {}, {'vals_list': [{'name': 'Example'}]}),
            ('create', [[{'name': 'First'}, {'name': 'Second'}]], {},
             {'vals_list': [{'name': 'First'}, {'name': 'Second'}]}),
            ('write', [[12], {'name': 'Changed'}], {}, {'ids': [12], 'vals': {'name': 'Changed'}}),
            ('unlink', [[12]], {}, {'ids': [12]}),
            ('fields_get', [], {'attributes': ['type']}, {'allfields': None, 'attributes': ['type']}),
        ]
        for method, args, kwargs, expected in cases:
            with self.subTest(method=method):
                payload = self.client.payload(method, args, kwargs, {'lang': 'en_US'})
                self.assertEqual(payload, {'context': {'lang': 'en_US'}, **expected})

    def test_request_uses_json2_endpoint_and_secret_only_as_bearer_header(self):
        with patch.object(sync, 'urlopen', return_value=io.BytesIO(b'[{"id": 7}]')) as urlopen:
            result = self.client.call(
                'res.users', 'search_read', [[('login', '=', 'integration@example.com')]],
                fields=['id'], limit=2,
            )
        request = urlopen.call_args.args[0]
        self.assertEqual(result, [{'id': 7}])
        self.assertEqual(request.full_url, 'https://reportrek.odoo.com/json/2/res.users/search_read')
        self.assertEqual(request.get_header('Authorization'), 'bearer test-secret')
        self.assertEqual(request.get_header('X-odoo-database'), 'reportrek')
        self.assertEqual(json.loads(request.data)['domain'], [['login', '=', 'integration@example.com']])

    def test_create_normalizes_single_record_id_for_legacy_callers(self):
        with patch.object(sync, 'urlopen', return_value=io.BytesIO(b'[6288]')):
            result = self.client.call('ir.model', 'create', [{'model': 'x_example'}])
        self.assertEqual(result, 6288)

    def test_create_keeps_multiple_record_ids(self):
        with patch.object(sync, 'urlopen', return_value=io.BytesIO(b'[6288, 6289]')):
            result = self.client.call(
                'ir.model', 'create', [[{'model': 'x_first'}, {'model': 'x_second'}]],
            )
        self.assertEqual(result, [6288, 6289])

    def test_http_errors_do_not_expose_response_or_credentials(self):
        error = HTTPError(
            'https://reportrek.odoo.com/json/2/res.users/search_read',
            401,
            'Unauthorized',
            {},
            io.BytesIO(b'{"message":"sensitive response"}'),
        )
        with patch.object(sync, 'urlopen', side_effect=error):
            with self.assertRaisesRegex(
                    ValueError,
                    'authentication_failed.*verify_database_login_and_unexpired_api_key') as raised:
                self.client.call('res.users', 'search_read', [[]])
        self.assertNotIn('sensitive response', str(raised.exception))
        self.assertNotIn('test-secret', str(raised.exception))

    def test_server_errors_include_safe_exception_class_for_diagnosis(self):
        error = HTTPError(
            'https://reportrek.odoo.com/json/2/uom.uom/read',
            500,
            'Internal Server Error',
            {},
            io.BytesIO(json.dumps({
                'name': 'odoo.exceptions.AccessError',
                'message': "Invalid field 'rounding' on model 'uom.uom'; private record data",
            }).encode()),
        )
        with patch.object(sync, 'urlopen', side_effect=error):
            with self.assertRaises(sync.ReadTransportFailure) as raised:
                self.client.call('uom.uom', 'read', [[11]], fields=['name'])
        self.assertIn('reason=access_denied', str(raised.exception))
        self.assertIn('error_class=AccessError', str(raised.exception))
        self.assertIn('invalid_field=rounding', str(raised.exception))
        self.assertNotIn('private record data', str(raised.exception))
        self.assertNotIn('test-secret', str(raised.exception))

    def test_error_class_is_validated_before_logging(self):
        self.assertEqual(
            self.client._error_class(
                b'{"name":"odoo.exceptions.ValidationError;secret"}'),
            '',
        )
        self.assertEqual(
            self.client._error_class(
                b'{"name":"odoo.exceptions.ValidationError"}'),
            'ValidationError',
        )
        self.assertEqual(
            self.client._error_field(
                b"""{"message":"Invalid field 'rounding' on model 'uom.uom'"}"""),
            'rounding',
        )
        self.assertEqual(
            self.client._error_field(
                b'{"message":"untrusted; data"}'),
            '',
        )

    def test_transient_write_failure_is_not_retryable(self):
        with patch.object(sync, 'urlopen', side_effect=URLError('offline')):
            with self.assertRaisesRegex(ValueError, 'transport failed'):
                self.client.call('res.users', 'write', [[12], {'name': 'Changed'}])

    def test_odoo_20_api_uses_json2_without_xmlrpc_authentication(self):
        common = Mock()
        common.version.return_value = {'server_version_info': [20, 0, 0]}
        client = Mock()

        def json2_call(model, method, args=None, **kwargs):
            self.assertTrue(args is None or isinstance(args, (list, tuple)))
            self.assertIsInstance(kwargs, dict)
            if (model, method) == ('res.users', 'search_read'):
                return [{'id': 7, 'login': 'integration@example.com'}]
            if (model, method) == ('res.users', 'read'):
                return [{'company_ids': [1]}]
            if (model, method) == ('res.company', 'read'):
                return [{'name': 'Rekaz'}]
            if (model, method) == ('res.groups', 'fields_get'):
                return {'user_ids': {'type': 'one2many'}}
            if (model, method) == ('ir.ui.menu', 'fields_get'):
                return {'group_ids': {'type': 'many2many'}}
            if (model, method) == ('ir.access', 'fields_get'):
                return {'model_id': {'type': 'many2one'}, 'group_id': {'type': 'many2one'},
                        'operation': {'type': 'selection'}, 'domain': {'type': 'char'}}
            self.fail(f'Unexpected JSON-2 call: {model}.{method}')

        client.call.side_effect = json2_call
        with patch.object(sync.xmlrpc.client, 'ServerProxy', return_value=common), \
                patch.object(sync, 'JSON2Client', return_value=client), \
                patch.object(sync.time, 'sleep'):
            api = sync.API(
                'https://reportrek.odoo.com', 'reportrek', 'integration@example.com',
                'test-secret', 1, 'Rekaz',
            )
        self.assertEqual(api.uid, 7)
        self.assertEqual(api.major_version, 20)
        common.authenticate.assert_not_called()
        client.call.assert_any_call('ir.access', 'fields_get', None, attributes=['type'])


if __name__ == '__main__':
    unittest.main()
