import contextlib
import hashlib
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import odoo_documents_copy as docs


class DocumentTests(unittest.TestCase):
    def test_resume_keeps_attachment_private_and_reuses_uploaded_binary(self):
        api = Mock(company_id=1)
        def call(model, method, *args, **kwargs):
            if method == 'fields_get':
                return {'raw': {'type': 'binary'}} if model == 'ir.attachment' else {'attachment_id': {}, 'company_id': {}}
            if method == 'search_read': return []
            if method == 'create': return 19
            if method == 'read' and model == 'documents.document': return [{'attachment_id': [8, 'file']}]
            if method == 'read' and model == 'ir.attachment':
                return [{'file_size': 7, 'checksum': hashlib.sha1(b'fixture').hexdigest(), 'company_id': [1, 'company']}]
            raise AssertionError((model, method))
        api.call.side_effect = call
        api.find.side_effect = lambda model, *args: [{'id': 8}] if model == 'ir.attachment' else []
        row = {'id': 1, 'ticket_no': 'T1', 'attachments': '/media/local/file.jpg'}
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'file.jpg'
            file.write_bytes(b'fixture')
            with patch.object(docs.inventory, 'field'), patch.object(docs.inventory, 'task_index', return_value={}), \
                 patch.object(docs.sync.m, 'snapshot', return_value=contextlib.nullcontext(None)), \
                 patch.object(docs.sync.m, 'catalog', return_value={'tickets': {}}), \
                 patch.object(docs.sync.m, 'source_rows', return_value=[row]), \
                 patch.object(docs.sync.m, 'row_key', return_value='[1]'), \
                 patch.object(docs.sync.m, 'local_media', return_value=file), patch.object(docs.sync.m, 'save_report'):
                self.assertEqual(docs.copy(api, 'fixture', temp), 2)
        creates = [c for c in api.call.call_args_list if c.args[1] == 'create']
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0].args[0], 'documents.document')
        self.assertEqual(creates[0].args[2][0]['attachment_id'], 8)

    def test_identity_distinguishes_owner_and_source_without_using_filename_only(self):
        a = docs.source_identity('photos', '[1]', '/one/file.jpg', 'project.task', 2)
        self.assertNotEqual(a, docs.source_identity('photos', '[1]', '/two/file.jpg', 'project.task', 2))
        self.assertNotEqual(a, docs.source_identity('photos', '[1]', '/one/file.jpg', 'project.task', 3))

    def test_empty_document_is_repaired_and_checksum_verified(self):
        api = Mock(company_id=1)
        data = b'%PDF-fixture'
        api.call.side_effect = [[{'attachment_id': [8, 'file']}],
                                [{'file_size': 0, 'checksum': False, 'company_id': [1, 'company']}], True,
                                [{'file_size': len(data), 'checksum': hashlib.sha1(data).hexdigest(), 'company_id': [1, 'company']}]]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'file.pdf'
            path.write_bytes(data)
            self.assertTrue(docs.verify_binary(api, 19, path, {'datas': {'readonly': False}}))
        self.assertEqual(api.call.call_args_list[2].args[:2], ('documents.document', 'write'))

    def test_nonempty_different_file_is_not_overwritten(self):
        api = Mock(company_id=1)
        api.call.side_effect = [[{'attachment_id': [8, 'file']}], [{'file_size': 2, 'checksum': 'different', 'company_id': [1, 'company']}]]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'file.pdf'
            path.write_bytes(b'original')
            with self.assertRaisesRegex(ValueError, 'not overwritten'):
                docs.verify_binary(api, 19, path, {})
        self.assertEqual(api.call.call_count, 2)

    def test_modern_raw_repairs_empty_attachment_with_exact_bytes(self):
        api = Mock(company_id=1, context={'allowed_company_ids': [1]})
        data = b'\x00\xff%PDF-original'
        api.call.side_effect = [[{'attachment_id': [8, 'file']}],
                                [{'file_size': 0, 'checksum': False, 'company_id': [1, 'company']}],
                                {'raw': {'type': 'binary', 'readonly': False}}, True,
                                [{'file_size': len(data), 'checksum': hashlib.sha1(data).hexdigest(), 'company_id': [1, 'company']}]]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'file.pdf'
            path.write_bytes(data)
            self.assertTrue(docs.verify_binary(api, 19, path, {}))
        write = api.call.call_args_list[3]
        self.assertEqual(write.args[:2], ('ir.attachment', 'write'))
        self.assertEqual(docs.base64.b64decode(write.args[2][1]['raw']), data)
        self.assertTrue(write.kwargs['context']['image_no_postprocess'])
        self.assertNotIn('datas', write.args[2][1])

    def test_unsupported_binary_schema_stops(self):
        with self.assertRaisesRegex(ValueError, 'No writable'):
            docs.binary_values(Mock(), b'file', {'raw': {'type': 'binary', 'readonly': True}})
