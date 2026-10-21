if not env.user.has_group('base.group_system'):
    raise UserError('Administrator only')
body = "if not env.context.get('rekaz_file_proposal_running'):\n    for source in records:\n        if source.project_id.id != 10:\n            continue\n        attachments = env['ir.attachment'].search([('res_model','=','project.task'),('res_id','=',source.id),('type','=','binary')],order='id')\n        targets = []\n        for attachment in attachments:\n            filename = attachment.name or ''\n            stem = filename.rsplit('.',1)[0].upper()\n            serial = stem[4:] if stem.startswith('A26-') else ''\n            if not serial or not serial.isdigit():\n                continue\n            candidates = env['project.task'].search([('project_id','=',10),'|',('name','=ilike',filename),('x_rekaz_file_ref','=',stem)],order='id')\n            target = candidates[:1]\n            if not target:\n                source_stem = (source.name or '').rsplit('.',1)[0].upper()\n                if not targets and (not source_stem.startswith('A26-') or source_stem == stem):\n                    target = source\n                else:\n                    target = env['project.task'].with_context(rekaz_file_proposal_running=True).create({'name':filename,'project_id':source.project_id.id,'stage_id':source.stage_id.id})\n            if target.id not in targets:\n                targets.append(target.id)\n            same_name = env['ir.attachment'].search([('res_model','=','project.task'),('res_id','=',target.id),('name','=ilike',filename)])\n            for existing in same_name:\n                if existing.checksum != attachment.checksum:\n                    raise UserError('نسختان مختلفتان للملف نفسه؛ يلزم المراجعة: ' + filename)\n            if attachment.res_id != target.id:\n                duplicate = env['ir.attachment'].search([('res_model','=','project.task'),('res_id','=',target.id),('checksum','=',attachment.checksum)],limit=1)\n                documents = env['documents.document'].search([('attachment_id','=',attachment.id)])\n                if documents:\n                    documents.with_context(rekaz_file_proposal_running=True).write({'res_model':False if duplicate else 'project.task','res_id':0 if duplicate else target.id,'x_rekaz_task':False if duplicate else target.id})\n                else:\n                    attachment.with_context(rekaz_file_proposal_running=True).write({'res_model':False if duplicate else 'project.task','res_id':0 if duplicate else target.id})\n                if attachment.res_model == 'project.task' and attachment.res_id == source.id:\n                    raise UserError('تعذر فصل المستند عن المعاملة السابقة: ' + filename)\n                for message in env['mail.message'].search([('model','=','project.task'),('res_id','=',source.id),('attachment_ids','in',attachment.ids)]):\n                    message.with_context(rekaz_file_proposal_running=True).write({'attachment_ids':[(3,attachment.id)]})\n            values = {'name':filename}\n            if target.id != 199 and target.x_rekaz_mail_review != 'reviewed':\n                extracted = attachment.index_content or ''\n                proposal = ''\n                for label in ['الأعمال المقترحة','الاعمال المقترحة','نطاق العمل','المقترح']:\n                    if label in extracted:\n                        proposal = extracted[extracted.find(label):][:1600]\n                        break\n                proposal = proposal.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;') if proposal else 'لم يتوفر نص مقترح واضح؛ يلزم قراءة الملف ومراجعته وOCR للملف المصور.'\n                values['description'] = '<h3>مقترح من الملف — يحتاج مراجعة</h3><p>اسم الملف: ' + filename.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;') + '</p><pre>' + proposal + '</pre><p>دون تعبئة حقول المعاملة أو اعتماد مالي تلقائي.</p>'\n            target.with_context(rekaz_file_proposal_running=True).write(values)\n        if targets and source.id not in targets:\n            source.with_context(rekaz_file_proposal_running=True).write({'active':False})\n"
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
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'تحليل رسائل الإنشاءات','message':'تم تفعيل معاملة مستقلة لكل رقم A26 مع ملفه فقط، والمقترح في الوصف دون تعبئة حقول المعاملة.','sticky':True}}

