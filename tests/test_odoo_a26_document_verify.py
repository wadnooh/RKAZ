import unittest
from unittest.mock import Mock


class A26DocumentVerifyTests(unittest.TestCase):
    def test_verify_script_logic(self):
        code = open('tools/odoo_a26_document_verify.py', encoding='utf-8').read()

        expected = {199: 'A26-118.pdf', 201: 'A26-117.pdf', 202: 'A26-002.pdf', 204: 'A26-003.pdf'}

        class MockCompany:
            id = 1

        company = MockCompany()

        class MockUser:
            def has_group(self, group):
                return group == 'base.group_system'

        class MockAttachment:
            def __init__(self, id, name, checksum, size):
                self.id = id
                self.name = name
                self.checksum = checksum
                self.file_size = size
                self.mimetype = 'application/pdf'

            def __len__(self):
                return 1

        class MockTask:
            def __init__(self, id):
                self.id = id
                self.project_id = Mock(id=10)
                self.company_id = company

            def exists(self):
                return self

        class MockDoc:
            def __init__(self, attachment, res_id):
                self.attachment_id = attachment
                self.res_id = res_id

            def write(self, vals):
                pass

        company_obj = company

        class MockDocSet:
            def __init__(self, docs_list):
                self._docs = docs_list
                if docs_list:
                    self.attachment_id = docs_list[0].attachment_id
                    self.res_id = docs_list[0].res_id

            def __len__(self):
                return len(self._docs)

            def __bool__(self):
                return bool(self._docs)

            def with_context(self, **kwargs):
                return self

            def write(self, vals):
                pass

            def create(self, vals):
                return self

        class MockEnv:
            user = MockUser()
            company = company_obj
            context = {}

            def __getitem__(self, model):
                mock_model = Mock()
                if model == 'project.task':
                    mock_model.browse.side_effect = lambda tid: MockTask(tid)
                elif model == 'ir.attachment':
                    def search(domain):
                        res_id = [v for k, op, v in domain if k == 'res_id'][0]
                        filename = expected[res_id]
                        return MockAttachment(res_id * 10, filename, f'sha1_{res_id}', 1024)
                    mock_model.search.side_effect = search
                elif model == 'documents.document':
                    def doc_search(domain):
                        att_id = [v for k, op, v in domain if k == 'attachment_id'][0]
                        res_id = att_id // 10
                        att = MockAttachment(att_id, expected[res_id], f'sha1_{res_id}', 1024)
                        return MockDocSet([MockDoc(att, res_id)])
                    mock_model.search.side_effect = doc_search
                return mock_model

        class UserError(Exception):
            pass

        globs = {'env': MockEnv(), 'UserError': UserError}
        exec(code, globs)

        self.assertIn('action', globs)
        action = globs['action']
        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(action['params']['title'], 'اكتملت ملفات A26 الأربعة')
        self.assertIn('A26-118.pdf', action['params']['message'])
        self.assertIn('A26-117.pdf', action['params']['message'])
        self.assertIn('A26-002.pdf', action['params']['message'])
        self.assertIn('A26-003.pdf', action['params']['message'])


if __name__ == '__main__':
    unittest.main()
