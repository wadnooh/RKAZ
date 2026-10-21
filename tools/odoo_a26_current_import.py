if not env.user.has_group('base.group_system'):
    raise UserError('Administrator only')
body = "if not env.context.get('rekaz_file_proposal_running'):\n    for task in records:\n        if task.project_id.id != 10 or task.x_rekaz_mail_review == 'reviewed':\n            continue\n        files = env['ir.attachment'].search([('res_model','=','project.task'),('res_id','=',task.id),('type','=','binary')],order='id')\n        eligible = []\n        for attachment in files:\n            filename = attachment.name or ''\n            if filename.lower().endswith(('.pdf','.docx','.doc','.txt','.xlsx','.xls')):\n                eligible.append(attachment)\n        if not eligible:\n            continue\n        parts = ['<h3>مقترح من الملف — يحتاج مراجعة</h3>']\n        for attachment in eligible:\n            filename = (attachment.name or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')\n            parts.append('<p><strong>اسم الملف:</strong> ' + filename + '</p>')\n            extracted = attachment.index_content or ''\n            proposals = []\n            for label in ['الأعمال المقترحة','الاعمال المقترحة','نطاق العمل','المقترح']:\n                if label in extracted:\n                    excerpt = extracted[extracted.find(label):][:1600]\n                    if excerpt not in proposals:\n                        proposals.append(excerpt)\n                    break\n            if proposals:\n                proposal = proposals[0].replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')\n                parts.append('<p>نص المقترح المستخرج من الملف، دون اعتماد:</p><pre>' + proposal + '</pre>')\n            else:\n                parts.append('<p>لم يتوفر مقترح نصي واضح من الملف. يلزم قراءته ومراجعته؛ قد يحتاج الملف المصور إلى OCR. لم يتم استنتاج بيانات من نص البريد.</p>')\n        parts.append('<p>هذا الوصف مقترح للمراجعة فقط. لم يتم تعبئة أرقام المعاملة أو المبالغ أو تغيير المرحلة.</p>')\n        task.with_context(rekaz_file_proposal_running=True).write({'name':eligible[0].name[:200], 'description': ''.join(parts)})\n"
mid = env['ir.model']._get_id('project.task')
action = env['ir.actions.server'].search([('name','=','تحليل رسالة الإنشاءات الواردة'),('model_id','=',mid)],limit=1)
vals = {'name':'تحليل رسالة الإنشاءات الواردة','model_id':mid,'state':'code','code':body,'binding_model_id':mid,'binding_view_types':'form,list','group_ids':[(6,0,[env.ref('base.group_system').id])]}
if action:
    action.write(vals)
else:
    action = env['ir.actions.server'].create(vals)
auto = env['base.automation'].search([('name','=','Rekaz construction intake extraction')],limit=1)
fields = env['ir.model.fields'].search([('model','=','project.task'),('name','in',['project_id'])])
values = {'name':'Rekaz construction intake extraction','model_id':mid,'trigger':'on_create_or_write','filter_domain':repr([('project_id','=',10)]),'trigger_field_ids':[(6,0,fields.ids)],'action_server_ids':[(6,0,action.ids)],'active':True}
if auto:
    auto.write(values)
else:
    env['base.automation'].create(values)
attachment_mid = env['ir.model']._get_id('ir.attachment')
attachment_body = "tasks = env['project.task'].browse([])\nfor attachment in records:\n    if attachment.res_model == 'project.task' and attachment.res_id:\n        tasks |= env['project.task'].browse(attachment.res_id).exists()\nrecords = tasks\n" + body
attachment_action = env['ir.actions.server'].search([('name','=','Rekaz construction attachment proposal'),('model_id','=',attachment_mid)],limit=1)
av = {'name':'Rekaz construction attachment proposal','model_id':attachment_mid,'state':'code','code':attachment_body}
if attachment_action:
    attachment_action.write(av)
else:
    attachment_action = env['ir.actions.server'].create(av)
attachment_auto = env['base.automation'].search([('name','=','Rekaz construction attachment proposal')],limit=1)
attachment_fields = env['ir.model.fields'].search([('model','=','ir.attachment'),('name','in',['res_id','res_model','index_content','name'])])
aav = {'name':'Rekaz construction attachment proposal','model_id':attachment_mid,'trigger':'on_create_or_write','filter_domain':repr([('res_model','=','project.task')]),'trigger_field_ids':[(6,0,attachment_fields.ids)],'action_server_ids':[(6,0,attachment_action.ids)],'active':True}
if attachment_auto:
    attachment_auto.write(aav)
else:
    env['base.automation'].create(aav)
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'تحليل رسائل الإنشاءات','message':'تم ضبط المقترح من المرفقات في الاسم والوصف فقط. لا تعبئة لحقول المعاملة. PDF المصور يحتاج OCR عند غياب النص.','sticky':True}}



# One-time, transaction-scoped import of the four explicitly requested files.
project = env['project.project'].browse(10).exists()
if not project or project.company_id != env.company:
    raise UserError('Wrong intake project/company')
tasks = env['project.task'].with_context(active_test=False).search([('project_id','=',10)],order='id')
attachments = env['ir.attachment'].search([('res_model','=','project.task'),('res_id','in',tasks.ids),('type','=','binary')],order='id')
expected = ['a26-118.pdf','a26-117.pdf','a26-002.pdf','a26-003.pdf']
files = {}
for att in attachments:
    key = (att.name or '').lower().strip()
    if key in expected:
        if key in files and files[key].checksum != att.checksum:
            raise UserError('Conflicting versions: ' + key)
        if key not in files:
            files[key] = att
if len(files) != 4:
    raise UserError('Waiting for all four files; received ' + str(len(files)))
stage = env['project.task.type'].search([('project_ids','in',[10]),('name','=','وارد جديد — مراجعة البيانات')],limit=1)
used = []
chosen = {}
for key in expected:
    ref = key[:-4]
    target = env['project.task'].browse([])
    for task in tasks:
        if task.id not in used and (ref == (task.x_rekaz_file_ref or '').lower() or ref in (task.name or '').lower()):
            target = task
            break
    if not target:
        source = env['project.task'].browse(files[key].res_id)
        if source.id not in used:
            target = source
    if not target:
        target = env['project.task'].with_context(rekaz_file_proposal_running=True).create({'name':files[key].name,'project_id':10,'stage_id':stage.id})
    used.append(target.id)
    chosen[key] = target
# Move existing binary attachments rather than reuploading or duplicating them.
for key in expected:
    target = chosen[key]
    att = files[key]
    if att.res_id != target.id and not env['ir.attachment'].search_count([('res_model','=','project.task'),('res_id','=',target.id),('checksum','=',att.checksum)]):
        att.with_context(rekaz_file_proposal_running=True).copy({'res_id':target.id,'res_model':'project.task'})
    target.with_context(rekaz_file_proposal_running=True).write({'name':att.name})
# Keep the pre-existing manually reviewed proposal on task 199.
for key in expected:
    target = chosen[key]
    if target.id != 199:
        text = files[key].index_content or ''
        excerpt = ''
        for label in ['الأعمال المقترحة','الاعمال المقترحة','نطاق العمل','المقترح']:
            if label in text:
                excerpt = text[text.find(label):][:1600]
                break
        proposal = excerpt.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;') if excerpt else 'لم يتوفر نص مقترح واضح؛ يلزم قراءة الملف ومراجعته، وOCR إذا كان مصورًا.'
        target.with_context(rekaz_file_proposal_running=True).write({'description':'<h3>مقترح من الملف — يحتاج مراجعة</h3><p>اسم الملف: ' + files[key].name + '</p><pre>' + proposal + '</pre><p>دون تعبئة حقول المعاملة أو اعتماد مالي تلقائي.</p>'})
    if not env['ir.attachment'].search_count([('res_model','=','project.task'),('res_id','=',target.id),('checksum','=',files[key].checksum)]):
        raise UserError('Attachment verification failed')
message = 'تم حفظ 4 ملفات A26 في بطاقات مستقلة دون تكرار: '
for key in expected:
    message += key + ' (ID ' + str(chosen[key].id) + ')؛ '
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'استيراد A26 مكتمل','message':message,'sticky':True}}
