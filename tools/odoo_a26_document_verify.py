if not env.user.has_group('base.group_system'):
    raise UserError('Administrator only')
expected = {199:'A26-118.pdf',201:'A26-117.pdf',202:'A26-002.pdf',204:'A26-003.pdf'}
report = []
for tid in expected:
    task = env['project.task'].browse(tid).exists()
    if not task or task.project_id.id != 10 or task.company_id != env.company:
        raise UserError('Wrong task/company')
    files = env['ir.attachment'].search([('res_model','=','project.task'),('res_id','=',tid),('name','=ilike',expected[tid])])
    if len(files) != 1 or files.file_size <= 0 or not files.checksum or files.mimetype != 'application/pdf':
        raise UserError('PDF verification failed: ' + expected[tid])
    docs = env['documents.document'].search([('attachment_id','=',files.id)])
    if len(docs) > 1:
        raise UserError('Duplicate document entry')
    vals = {'name':expected[tid],'attachment_id':files.id,'company_id':env.company.id,'res_model':'project.task','res_id':tid,'x_rekaz_task':tid,'x_rekaz_project':10,'x_rekaz_phase':'الإنشاءات — ملف المعاملة'}
    if docs:
        docs.with_context(rekaz_file_proposal_running=True).write(vals)
    else:
        docs = env['documents.document'].with_context(rekaz_file_proposal_running=True).create(vals)
    if docs.attachment_id.checksum != files.checksum or docs.res_id != tid:
        raise UserError('Document linkage verification failed')
    report.append(expected[tid] + ' — PDF سليم، مرتبط بالمستندات — ' + str(files.file_size) + ' bytes')
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'اكتملت ملفات A26 الأربعة','message':'؛ '.join(report),'sticky':True}}
