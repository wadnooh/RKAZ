"""Copy materialized Rekaz files into Odoo Documents without public links."""
import base64
import hashlib
import mimetypes
from pathlib import Path
import rekaz_odoo_sync as sync
import odoo_project_inventory as inventory


PHASES = {'before_shot': 'قبل التنفيذ', 'during_shot': 'أثناء التنفيذ',
          'after_shot': 'بعد التنفيذ', 'quantities_shot': 'حصر الكميات',
          'location_shot': 'الموقع', 'attachments': 'مرفقات'}


def source_identity(table, row_key, ref, owner_model, owner_id):
    raw = '|'.join((table, row_key, ref, owner_model or '', str(owner_id or 0)))
    return 'rekaz-doc:' + hashlib.sha256(raw.encode()).hexdigest()


def binary_values(api, data, attachment_fields=None):
    fields = attachment_fields if attachment_fields is not None else api.call(
        'ir.attachment', 'fields_get', [], attributes=['type', 'readonly'])
    # Legacy datas and modern raw both accept base64 strings over RPC.
    # Never send datas to a schema without it: modern create silently drops it.
    for name in ('datas', 'raw'):
        if name in fields and fields[name].get('type') == 'binary' and not fields[name].get('readonly'):
            return {name: base64.b64encode(data).decode('ascii')}
    raise ValueError('No writable attachment binary field; no file content changed')


def verify_binary(api, document_id, path, document_fields):
    document = api.call('documents.document', 'read', [[document_id]], fields=['attachment_id'])[0]
    attachment = document.get('attachment_id')
    if not attachment:
        raise ValueError('Document has no binary attachment; manual schema review required')
    aid = attachment[0] if isinstance(attachment, list) else attachment
    data = path.read_bytes()
    expected = hashlib.sha1(data).hexdigest()
    def read():
        return api.call('ir.attachment', 'read', [[aid]], fields=['file_size', 'checksum', 'company_id'])[0]
    current = read()
    company = current.get('company_id')
    company = company[0] if isinstance(company, list) else company
    if company not in (False, None, api.company_id):
        raise ValueError('Attachment belongs to another company; unchanged')
    repaired = False
    if not current.get('file_size') and data:
        encoded = base64.b64encode(data).decode('ascii')
        if 'datas' in document_fields and not document_fields['datas'].get('readonly'):
            api.call('documents.document', 'write', [[document_id], {'datas': encoded}])
        else:
            api.call('ir.attachment', 'write', [[aid], binary_values(api, data)],
                     context={**api.context, 'image_no_postprocess': True})
        repaired = True
        current = read()
    if current.get('file_size') != len(data) or current.get('checksum') != expected:
        raise ValueError('Binary verification failed; a nonempty differing file was not overwritten. document_id=' + str(document_id))
    return repaired


def copy(api, source, uploads):
    fields = api.call('documents.document', 'fields_get', [], attributes=['type', 'required', 'readonly'])
    if 'attachment_id' not in fields or 'company_id' not in fields:
        raise ValueError('Documents attachment/company API differs; no files were copied. Schema inspection required.')
    attachment_fields = api.call('ir.attachment', 'fields_get', [], attributes=['type', 'readonly'])
    binary_values(api, b'', attachment_fields)  # Fail before any mutation if unsupported.
    inventory.field(api, 'documents.document', 'x_rekaz_origin', 'char', 'هوية المصدر في ركاز', index=True)
    inventory.field(api, 'documents.document', 'x_rekaz_task', 'many2one', 'المعاملة', relation='project.task')
    inventory.field(api, 'documents.document', 'x_rekaz_project', 'many2one', 'المشروع', relation='project.project')
    inventory.field(api, 'documents.document', 'x_rekaz_phase', 'char', 'تصنيف المستند')
    project_rows = api.call('project.project', 'search_read',
                            [[('company_id', '=', api.company_id), ('x_rekaz_origin', '=like', 'reportrek:rekaz-main:projects:%')]],
                            fields=['x_rekaz_origin'], limit=False)
    projects = {r['x_rekaz_origin']: r for r in project_rows}
    report = {'mode': 'documents_copy', 'created': 0, 'existing': 0, 'verified': 0, 'repaired': 0, 'unlinked': [], 'missing': []}
    with sync.m.snapshot(source) as conn:
        specs = sync.m.catalog(conn)
        index = inventory.task_index(conn, api)
        tasks = api.call('project.task', 'search_read',
                         [[('company_id', '=', api.company_id), ('x_rekaz_origin', '=like', 'reportrek:rekaz-main:%')]],
                         fields=['x_rekaz_origin', 'project_id'], limit=False)
        exact_tasks = {r['x_rekaz_origin']: r for r in tasks}
        for table, spec in specs.items():
            for row in sync.m.source_rows(conn, table, spec):
                key = sync.m.row_key(row, spec)
                origin = 'reportrek:rekaz-main:' + table + ':' + key
                task = exact_tasks.get(origin) or inventory.match_task(row, index)
                project = projects.get(origin)
                project_id = project['id'] if project else (task['project_id'][0] if task and task.get('project_id') else False)
                owner_model = 'project.task' if task else ('project.project' if project else '')
                owner_id = task['id'] if task else (project['id'] if project else 0)
                seen = set()
                for column, phase in PHASES.items():
                    for ref in sync.m.media_refs({column: row.get(column)}):
                        if ref in seen:
                            continue
                        seen.add(ref)
                        doc_key = source_identity(table, key, ref, owner_model, owner_id)
                        path = sync.m.local_media(uploads, ref)
                        if path is None or path.stat().st_size > 25 * 1024 * 1024 or path.stat().st_size == 0:
                            report['missing'].append({'table': table, 'key': key, 'reference': ref})
                            continue
                        found = api.find('documents.document', [('x_rekaz_origin', '=', doc_key), ('company_id', '=', api.company_id)], ['id'])
                        if len(found) > 1:
                            raise ValueError('Duplicate document identity; inspect before continuing')
                        if found:
                            report['repaired'] += int(verify_binary(api, found[0]['id'], path, fields))
                            report['verified'] += 1
                            report['existing'] += 1
                            print('Document verified: ' + table + ' key=' + key, flush=True)
                            continue
                        path = sync.m.local_media(uploads, ref)
                        if path is None or path.stat().st_size > 25 * 1024 * 1024:
                            report['missing'].append({'table': table, 'key': key, 'reference': ref})
                            continue
                        title = phase + ' — ' + sync.m.title(row, table) + ' — ' + Path(ref).name
                        marker = 'rekaz-documents:' + doc_key
                        attachment = api.find('ir.attachment', [('description', '=', marker), ('company_id', '=', api.company_id)], ['id'])
                        if len(attachment) > 1:
                            raise ValueError('Duplicate source attachment identity')
                        if attachment:
                            aid = attachment[0]['id']
                        else:
                            vals = {'name': title, 'type': 'binary', 'company_id': api.company_id,
                                    'description': marker, 'public': False,
                                    'mimetype': mimetypes.guess_type(ref)[0] or 'application/octet-stream'}
                            vals.update(binary_values(api, path.read_bytes(), attachment_fields))
                            if owner_model:
                                vals.update(res_model=owner_model, res_id=owner_id)
                            aid = api.call('ir.attachment', 'create', [vals],
                                           context={**api.context, 'image_no_postprocess': True})
                        doc_values = {'name': title, 'attachment_id': aid,
                                      'company_id': api.company_id, 'x_rekaz_origin': doc_key,
                                      'x_rekaz_task': task['id'] if task else False,
                                      'x_rekaz_project': project_id, 'x_rekaz_phase': phase}
                        # Odoo can create a Documents entry automatically when a
                        # project attachment is uploaded. Reuse that entry.
                        auto_docs = api.find('documents.document', [('attachment_id', '=', aid)],
                                             ['id', 'company_id', 'x_rekaz_origin'])
                        if len(auto_docs) > 1:
                            raise ValueError('Multiple Documents entries reference the same attachment')
                        try:
                            if auto_docs:
                                auto = auto_docs[0]
                                company = auto.get('company_id')
                                company = company[0] if isinstance(company, list) else company
                                if company not in (False, None, api.company_id) or auto.get('x_rekaz_origin') not in (False, None, '', doc_key):
                                    raise ValueError('Existing document belongs to another company or source; left unchanged')
                                api.call('documents.document', 'write', [[auto['id']], doc_values])
                                document_id = auto['id']
                            else:
                                document_id = api.call('documents.document', 'create', [doc_values])
                            report['repaired'] += int(verify_binary(api, document_id, path, fields))
                            report['verified'] += 1
                        except ValueError:
                            report['stopped_at'] = {'table': table, 'key': key, 'attachment_id': aid,
                                                    'automatic_document_found': bool(auto_docs)}
                            sync.m.save_report(Path(__file__).resolve().parents[1] / 'outputs/reportrek_documents_report.json', report)
                            print('Documents stopped: table=' + table + ' key=' + key + ' attachment_id=' + str(aid), flush=True)
                            raise
                        report['created'] += 1
                        if not owner_id:
                            report['unlinked'].append({'table': table, 'key': key, 'document_key': doc_key})
                        print('Document copied: ' + table + ' key=' + key, flush=True)
    sync.m.save_report(Path(__file__).resolve().parents[1] / 'outputs/reportrek_documents_report.json', report)
    print(sync.m.dump(report))
    return 2 if report['missing'] or report['unlinked'] else 0
