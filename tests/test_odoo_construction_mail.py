import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from odoo_construction_mail import extract


def test_arabic_request_and_work_order_title():
    result = extract('A26-118', '<p>رقم الطلب: ٢٦١١٥٣٥١٤</p><p>رقم أمر العمل: ٤٢٢٠٠٠٠٠١</p>')
    assert result['application_no'] == '261153514'
    assert result['work_order'] == '422000001'
    assert result['title'] == 'A26-118 — طلب 261153514 — أمر العمل 422000001'
    assert result['review_required']


def test_property_and_identity_numbers_are_not_request_ids():
    result = extract('A26-118', 'رقم الهوية: 1234567890 رقم الصك: 9876543210')
    assert 'application_no' not in result
    assert result['title'] == 'A26-118'


def test_conflicting_request_numbers_require_review():
    result = extract('A26-118', 'رقم الطلب: 261153514\nرقم الطلب: 261153515')
    assert 'application_no' not in result
    assert result['application_no_conflict']


def test_attachment_proposal_only_changes_name_and_description():
    from types import SimpleNamespace
    from odoo_construction_mail import action_code
    class Task:
        id = 9
        project_id = SimpleNamespace(id=10)
        x_rekaz_mail_review = 'pending'
        description = 'رقم الطلب: 999999999 من نص البريد'
        name = 'A26-118.pdf'
        def with_context(self, **kw):
            return self
        def write(self, values):
            self.written = values
    class Attachments:
        def search(self, domain, order=None):
            return [SimpleNamespace(name='A26-118.pdf', index_content='نطاق العمل: تمديد كابل <مراجعة>',res_id=9,checksum='abc')]
    class Tasks:
        def search(self, domain, order=None):
            class One:
                def __getitem__(self, key):
                    return self
                def __getattr__(self, name):
                    return getattr(task, name)
            return One()
    class Env:
        context = {}
        def __getitem__(self, model):
            if model == 'project.task':
                return Tasks()
            assert model == 'ir.attachment'
            return Attachments()
    task = Task()
    exec(action_code(10), {'env':Env(), 'records':[task]})
    assert set(task.written) == {'name','description'}
    assert task.written['name'] == 'A26-118.pdf'
    assert 'تمديد كابل &lt;مراجعة&gt;' in task.written['description']
    assert '999999999' not in task.written['description']
