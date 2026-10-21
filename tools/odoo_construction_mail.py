"""Conservative construction email extraction; no mailbox access or external AI."""
import html
import re

DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')


def extract(subject, body):
    text = html.unescape(re.sub(r'<[^>]+>', ' ', body or '')).translate(DIGITS)
    subject = (subject or '').translate(DIGITS).strip()
    fields = {}
    for key, label in [('application_no', r'رقم\s*الطلب'),
                       ('work_order', r'رقم\s*(?:أمر|امر)\s*العمل'),
                       ('notification_no', r'رقم\s*(?:الإشعار|الاشعار)')]:
        candidates = set(re.findall(label + r'\s*[:：#\-]?\s*([0-9]{5,15})(?![0-9])', text))
        if len(candidates) == 1:
            fields[key] = candidates.pop()
        elif candidates:
            fields[key + '_conflict'] = True
    match = re.search(r'(?<![A-Za-z0-9])A\d{2}-\d{1,8}(?!\d)', subject, re.I)
    reference = match.group(0).upper() if match else ''
    title = reference or subject[:160] or 'معاملة إنشاءات واردة — تحتاج مراجعة'
    if fields.get('application_no'):
        title += ' — طلب ' + fields['application_no']
    if fields.get('work_order'):
        title += ' — أمر العمل ' + fields['work_order']
    fields.update(title=title, reference=reference, review_required=True)
    return fields


def action_code(project_id):
    return f'''if not env.context.get('rekaz_file_proposal_running'):
    for source in records:
        if source.project_id.id != {project_id}:
            continue
        attachments = env['ir.attachment'].search([('res_model','=','project.task'),('res_id','=',source.id),('type','=','binary')],order='id')
        targets = []
        for attachment in attachments:
            filename = attachment.name or ''
            stem = filename.rsplit('.',1)[0].upper()
            serial = stem[4:] if stem.startswith('A26-') else ''
            if not serial or not serial.isdigit():
                continue
            candidates = env['project.task'].search([('project_id','=',{project_id}),'|',('name','=ilike',filename),('x_rekaz_file_ref','=',stem)],order='id')
            target = candidates[:1]
            if not target:
                source_stem = (source.name or '').rsplit('.',1)[0].upper()
                if not targets and (not source_stem.startswith('A26-') or source_stem == stem):
                    target = source
                else:
                    target = env['project.task'].with_context(rekaz_file_proposal_running=True).create({{'name':filename,'project_id':source.project_id.id,'stage_id':source.stage_id.id}})
            if target.id not in targets:
                targets.append(target.id)
            same_name = env['ir.attachment'].search([('res_model','=','project.task'),('res_id','=',target.id),('name','=ilike',filename)])
            for existing in same_name:
                if existing.checksum != attachment.checksum:
                    raise UserError('نسختان مختلفتان للملف نفسه؛ يلزم المراجعة: ' + filename)
            if attachment.res_id != target.id:
                duplicate = env['ir.attachment'].search([('res_model','=','project.task'),('res_id','=',target.id),('checksum','=',attachment.checksum)],limit=1)
                documents = env['documents.document'].search([('attachment_id','=',attachment.id)])
                if documents:
                    documents.with_context(rekaz_file_proposal_running=True).write({{'res_model':False if duplicate else 'project.task','res_id':0 if duplicate else target.id,'x_rekaz_task':False if duplicate else target.id}})
                else:
                    attachment.with_context(rekaz_file_proposal_running=True).write({{'res_model':False if duplicate else 'project.task','res_id':0 if duplicate else target.id}})
                if attachment.res_model == 'project.task' and attachment.res_id == source.id:
                    raise UserError('تعذر فصل المستند عن المعاملة السابقة: ' + filename)
                for message in env['mail.message'].search([('model','=','project.task'),('res_id','=',source.id),('attachment_ids','in',attachment.ids)]):
                    message.with_context(rekaz_file_proposal_running=True).write({{'attachment_ids':[(3,attachment.id)]}})
            values = {{'name':filename}}
            if target.id != 199 and target.x_rekaz_mail_review != 'reviewed':
                extracted = attachment.index_content or ''
                proposal = ''
                for label in ['الأعمال المقترحة','الاعمال المقترحة','نطاق العمل','المقترح']:
                    if label in extracted:
                        proposal = extracted[extracted.find(label):][:1600]
                        break
                proposal = proposal.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;') if proposal else 'لم يتوفر نص مقترح واضح؛ يلزم قراءة الملف ومراجعته وOCR للملف المصور.'
                values['description'] = '<h3>مقترح من الملف — يحتاج مراجعة</h3><p>اسم الملف: ' + filename.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;') + '</p><pre>' + proposal + '</pre><p>دون تعبئة حقول المعاملة أو اعتماد مالي تلقائي.</p>'
            target.with_context(rekaz_file_proposal_running=True).write(values)
        if targets and source.id not in targets:
            source.with_context(rekaz_file_proposal_running=True).write({{'active':False}})
'''
