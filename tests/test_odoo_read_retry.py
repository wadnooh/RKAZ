import sys
import unittest
import xmlrpc.client
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import rekaz_odoo_sync as sync


class ReadRetryTests(unittest.TestCase):
    def test_http_500_read_is_retried(self):
        api = object.__new__(sync.API)
        api.rpc_interval = 0
        fault = xmlrpc.client.ProtocolError('https://example.test', 500, 'server error', {})
        with patch.object(sync.m.RPC, 'call', side_effect=[fault, [{'id': 1}]]) as call, patch.object(sync.time, 'sleep'):
            self.assertEqual(api.call('product.product', 'search_read', []), [{'id': 1}])
            self.assertEqual(call.call_count, 2)

    def test_http_500_write_is_not_retried(self):
        api = object.__new__(sync.API)
        api.rpc_interval = 0
        fault = xmlrpc.client.ProtocolError('https://example.test', 500, 'server error', {})
        with patch.object(sync.m.RPC, 'call', side_effect=fault) as call, patch.object(sync.time, 'sleep'):
            with self.assertRaises(ValueError):
                api.call('product.product', 'create', [{}])
            self.assertEqual(call.call_count, 1)
