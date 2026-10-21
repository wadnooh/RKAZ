if not env.user.has_group('base.group_system'):
    raise UserError('لمسؤول النظام فقط.')
results = []
task = env['project.task'].search([('x_rekaz_cost_center','!=',False),('company_id','=',env.company.id)],limit=1)
env.cr.execute('SAVEPOINT rekaz_cost_verification')
try:
    test = env['x_rekaz_cost_entry'].create({'x_name':'اختبار آلي غير محفوظ','x_task':task.id,'x_company_id':env.company.id,'x_date':datetime.date.today(),'x_category':'expense','x_source':'REKAZ-ROLLBACK-VERIFY','x_quantity':3,'x_rate':2,'x_status':'draft'})
    if test.x_total != 6:
        raise UserError('فشل حساب التكلفة.')
    results.append('حساب الكمية × التكلفة صحيح')
    rejected = False
    try:
        test.write({'x_status':'approved'})
    except Exception:
        rejected = True
    if not rejected:
        raise UserError('فشل شرط مستند الاعتماد.')
    results.append('الاعتماد دون مستند مرفوض')
    env.cr.execute('SAVEPOINT rekaz_duplicate_test')
    rejected = False
    try:
        env.cr.execute('INSERT INTO x_rekaz_cost_entry (x_name,x_company_id,x_source) VALUES (%s,%s,%s)',('duplicate',env.company.id,'REKAZ-ROLLBACK-VERIFY'))
    except Exception:
        env.cr.execute('ROLLBACK TO SAVEPOINT rekaz_duplicate_test')
        rejected = True
    env.cr.execute('RELEASE SAVEPOINT rekaz_duplicate_test')
    if not rejected:
        raise UserError('فشل منع التكرار.')
    results.append('تكرار مصدر التكلفة مرفوض من قاعدة البيانات')
    env.cr.execute('UPDATE x_rekaz_cost_entry SET x_approved_at=NOW(),x_status=%s WHERE id=%s',('approved',test.id))
    env.cr.execute('SAVEPOINT rekaz_freeze_test')
    rejected = False
    try:
        env.cr.execute('UPDATE x_rekaz_cost_entry SET x_rate=999 WHERE id=%s',(test.id,))
    except Exception:
        env.cr.execute('ROLLBACK TO SAVEPOINT rekaz_freeze_test')
        rejected = True
    env.cr.execute('RELEASE SAVEPOINT rekaz_freeze_test')
    if not rejected:
        raise UserError('فشل قفل التكلفة المعتمدة.')
    results.append('تعديل التكلفة المعتمدة مرفوض من قاعدة البيانات')
except Exception as exc:
    env.cr.execute('ROLLBACK TO SAVEPOINT rekaz_cost_verification')
    env.cr.execute('RELEASE SAVEPOINT rekaz_cost_verification')
    env.invalidate_all()
    raise UserError('فشل التحقق وتم التراجع عن بيانات الاختبار: '+str(exc))
env.cr.execute('ROLLBACK TO SAVEPOINT rekaz_cost_verification')
env.cr.execute('RELEASE SAVEPOINT rekaz_cost_verification')
env.invalidate_all()
results.append('تم التراجع عن جميع بيانات الاختبار؛ لا قيود أو تكاليف محفوظة')
action = {'type':'ir.actions.client','tag':'display_notification','params':{'title':'نتيجة التحقق الفعلي لمراكز التكلفة','message':' | '.join(results),'sticky':True}}
