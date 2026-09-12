"""Monthly salary register based on the supplied payroll workbook (not statutory rules)."""
import json
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for
from webapp import db, helpers, permissions

INPUTS = [('basic','راتب أساسي'),('housing','السكن'),('transport','النقل'),('phone','الهاتف'),('nature','طبيعة عمل'),('days','أيام العمل'),('overtime_hours','ساعات إضافية'),('bonus','مكافآت'),('deduction','خصم'),('advance','سلفة'),('insurance','تأمينات'),('other_deductions','خصومات أخرى')]
COLUMNS = [('company','الشركة'),('emp_no','رقم الموظف'),('name','اسم الموظف'),('job','الوظيفة'),('payment','نوع الصرف'),*INPUTS[:5],('fixed','إجمالي الأجر الثابت'),('days','أيام العمل'),('daily','الأجر اليومي'),('days_pay','أجر الأيام'),('overtime_hours','ساعات إضافية'),('hourly','قيمة الساعة'),('overtime','العمل الإضافي'),('bonus','مكافآت'),('gross','إجمالي الاستحقاق'),*INPUTS[8:],('deductions','إجمالي الخصومات'),('net','الصافي المستحق'),('source_net','صافي المصدر'),('difference','فرق المراجعة')]
PAYMENTS = ('غير محدد','داخل البنك','خارج البنك')


def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS payroll_periods(
        id INTEGER PRIMARY KEY, year INTEGER NOT NULL, month INTEGER NOT NULL,
        company TEXT NOT NULL, base_days TEXT NOT NULL, daily_hours TEXT NOT NULL,
        overtime_factor TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
        UNIQUE(year,month), CHECK(month BETWEEN 1 AND 12))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS payroll_rows(
        id INTEGER PRIMARY KEY, period_id INTEGER NOT NULL REFERENCES payroll_periods(id),
        employee_id INTEGER REFERENCES hr_employees(id) ON DELETE RESTRICT,
        emp_no TEXT NOT NULL, payload TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
        UNIQUE(period_id,emp_no), UNIQUE(period_id,employee_id))""")


def number(value, label, maximum=Decimal('1000000000')):
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError('قيمة غير صالحة: '+label)
    if not result.is_finite() or result < 0 or result > maximum or result.as_tuple().exponent < -6:
        raise ValueError('أدخل رقماً موجباً أو صفراً بحد أقصى 6 منازل عشرية: '+label)
    return result


def validate(form, period):
    data = {key: (form.get(key) or '').strip() for key in ('company','emp_no','name','job','payment','notes')}
    if not data['emp_no'] or not data['name'] or any(len(value)>2000 for value in data.values()):
        raise ValueError('رقم الموظف واسمه مطلوبان؛ النصوص بحد أقصى 2000 حرف')
    if data['payment'] not in PAYMENTS:
        raise ValueError('اختر نوع الصرف')
    for key,label in INPUTS:
        data[key] = str(number(form.get(key,''), label))
    if Decimal(data['days']) > Decimal(period['base_days']):
        raise ValueError('أيام العمل تتجاوز عدد أيام الشهر المحدد في المسير')
    source = (form.get('source_net') or '').strip()
    data['source_net'] = str(number(source,'صافي المصدر')) if source else None
    return data


def calculate(data, period):
    with localcontext() as ctx:
        ctx.prec = 40
        money = lambda v: v.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        v = {key: Decimal(str(data[key])) for key,_ in INPUTS}
        fixed = sum((v[key] for key in ('basic','housing','transport','phone','nature')),Decimal(0))
        daily = fixed / Decimal(period['base_days'])
        hourly = daily / Decimal(period['daily_hours'])
        days_pay = money(v['days']*daily)
        overtime = money(v['overtime_hours']*hourly*Decimal(period['overtime_factor']))
        gross = money(days_pay+overtime+v['bonus'])
        deductions = sum((v[key] for key in ('deduction','advance','insurance','other_deductions')),Decimal(0))
        net = money(gross-deductions)
        source = data.get('source_net')
        return dict(fixed=money(fixed),daily=money(daily),hourly=money(hourly),days_pay=days_pay,overtime=overtime,gross=gross,deductions=money(deductions),net=net,difference=money(net-Decimal(source)) if source is not None else None)


def register(app, login_required):
    def access(action='view'):
        if not permissions.can('section.hr','hr.payroll.view','hr.payroll.'+action): abort(403)

    def get_period(conn, ident):
        row=conn.execute('SELECT * FROM payroll_periods WHERE id=?',(ident,)).fetchone()
        if row is None: abort(404)
        return dict(row)

    def changed(action, ident, ref):
        db.log_audit(helpers.current_user_name(),action,'مسير الرواتب',ident,ref)
        helpers.after_data_change()

    @app.route('/hr/payroll',methods=['GET','POST'])
    @login_required
    def payroll_home():
        access('write' if request.method=='POST' else 'view')
        conn=db.connect()
        try:
            if request.method=='POST':
                try:
                    year=int(request.form.get('year',''));month=int(request.form.get('month',''))
                    if not 1900<=year<=2200 or not 1<=month<=12: raise ValueError('الفترة غير صالحة')
                    company=(request.form.get('company') or '').strip()
                    if not company or len(company)>2000: raise ValueError('اسم الشركة مطلوب')
                    cursor=conn.execute('INSERT INTO payroll_periods(year,month,company,base_days,daily_hours,overtime_factor) VALUES (?,?,?,?,?,?)',(year,month,company,'30','8','1.5'))
                    conn.commit();changed('إنشاء مسير',cursor.lastrowid,f'{year}-{month:02d}')
                    return redirect(url_for('payroll_detail',period_id=cursor.lastrowid))
                except (ValueError,sqlite3.IntegrityError):
                    conn.rollback();flash('تحقق من الشهر والسنة واسم الشركة. يوجد مسير واحد لكل شهر يجمع الشركات.','danger')
            periods=[dict(r) for r in conn.execute('SELECT p.*, (SELECT count(*) FROM payroll_rows r WHERE r.period_id=p.id) count FROM payroll_periods p ORDER BY year DESC,month DESC')]
            return render_template('payroll_home.html',periods=periods,today=date.today(),section='hr',active='payroll')
        finally: conn.close()

    @app.route('/hr/payroll/<int:period_id>')
    @login_required
    def payroll_detail(period_id):
        access()
        conn=db.connect()
        try:
            period=get_period(conn,period_id)
            rows=[]
            for record in conn.execute('SELECT * FROM payroll_rows WHERE period_id=? ORDER BY id',(period_id,)):
                row=json.loads(record['payload']);row.update(calculate(row,period));row.update(id=record['id'],version=record['version']);rows.append(row)
            q=(request.args.get('q') or '').strip().casefold()
            payment=request.args.get('payment','')
            rows=[r for r in rows if (not q or any(q in r[k].casefold() for k in ('emp_no','name','job','company'))) and (not payment or r['payment']==payment)]
            totals={key:sum((r[key] for r in rows),Decimal(0)) for key in ('fixed','days_pay','overtime','gross','deductions','net')}
            totals['inside']=sum((r['net'] for r in rows if r['payment']=='داخل البنك'),Decimal(0))
            totals['outside']=sum((r['net'] for r in rows if r['payment']=='خارج البنك'),Decimal(0))
            totals['unknown']=sum(1 for r in rows if r['payment']=='غير محدد')
            totals['missing_source']=sum(1 for r in rows if r['difference'] is None)
            totals['difference']=sum((r['difference'] for r in rows if r['difference'] is not None),Decimal(0))
            if request.args.get('export')=='xlsx':
                access('export')
                safe=[{k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for k,v in r.items()} for r in rows]
                return helpers.simple_xlsx_export('مسير الرواتب', [label for _,label in COLUMNS],safe,[key for key,_ in COLUMNS],f'payroll-{period["year"]}-{period["month"]:02d}.xlsx',filters=[f'{period["year"]}/{period["month"]:02d}',period['company']],summary_lines=[f'عدد الموظفين: {len(rows)}',f'إجمالي الصافي: {totals["net"]}'])
            return render_template('payroll_detail.html',period=period,rows=rows,totals=totals,columns=COLUMNS,inputs=dict(INPUTS),payments=PAYMENTS,q=request.args.get('q',''),payment=payment,section='hr',active='payroll')
        finally: conn.close()

    @app.route('/hr/payroll/<int:period_id>/settings',methods=['POST'])
    @login_required
    def payroll_settings(period_id):
        access('write');conn=db.connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            get_period(conn,period_id)
            try:
                values=[number(request.form.get(key,''),label,limit) for key,label,limit in [('base_days','أيام الشهر',Decimal(31)),('daily_hours','ساعات اليوم',Decimal(24)),('overtime_factor','معامل الإضافي',Decimal(10))]]
                if any(v<=0 for v in values): raise ValueError('الإعدادات يجب أن تكون أكبر من صفر')
                for r in conn.execute('SELECT payload FROM payroll_rows WHERE period_id=?',(period_id,)):
                    if Decimal(json.loads(r['payload'])['days'])>values[0]: raise ValueError('توجد أيام عمل تتجاوز الإعداد الجديد')
                cursor=conn.execute('UPDATE payroll_periods SET base_days=?,daily_hours=?,overtime_factor=?,version=version+1 WHERE id=? AND version=?',(*map(str,values),period_id,request.form.get('version')))
                if not cursor.rowcount: raise ValueError('تم تحديث المسير من مستخدم آخر. أعد تحميل الصفحة')
                conn.commit();changed('تعديل إعدادات مسير',period_id,'');flash('تم حفظ الإعدادات وإعادة حساب المسير','ok')
            except ValueError as exc: conn.rollback();flash(str(exc),'danger')
        finally: conn.close()
        return redirect(url_for('payroll_detail',period_id=period_id))

    @app.route('/hr/payroll/<int:period_id>/row/new',methods=['GET','POST'])
    @app.route('/hr/payroll/<int:period_id>/row/<int:row_id>',methods=['GET','POST'])
    @login_required
    def payroll_row(period_id,row_id=None):
        access('write');conn=db.connect()
        try:
            if request.method=='POST': conn.execute('BEGIN IMMEDIATE')
            period=get_period(conn,period_id)
            existing=conn.execute('SELECT * FROM payroll_rows WHERE id=? AND period_id=?',(row_id,period_id)).fetchone() if row_id else None
            if row_id and not existing: abort(404)
            data=json.loads(existing['payload']) if existing else dict(company=period['company'],payment='غير محدد',**{key:('' if key in ('basic','days') else '0') for key,_ in INPUTS})
            version=existing['version'] if existing else 0
            employee_id=existing['employee_id'] if existing else None
            if not existing and request.method=='GET' and request.args.get('employee_id'):
                employee=conn.execute('SELECT * FROM hr_employees WHERE id=?',(request.args['employee_id'],)).fetchone()
                if employee is None: abort(404)
                employee_id=employee['id'];employee=dict(employee)
                data.update(emp_no=employee.get('emp_no') or '',name=employee.get('full_name') or '',job=employee.get('job_title') or '',basic=str(employee.get('basic_salary') or 0),housing=str(employee.get('housing_allowance') or 0))
                flash('تم جلب الاسم والرقم والوظيفة والأساسي والسكن. أدخل أيام العمل وبقية البدلات وراجعها قبل الحفظ.','ok')
            if request.method=='POST':
                try:
                    parsed=validate(request.form,period)
                    if not existing:
                        employee_id=request.form.get('employee_id') or None
                        if employee_id and not conn.execute('SELECT id FROM hr_employees WHERE id=?',(employee_id,)).fetchone():raise ValueError('الموظف غير موجود')
                    if existing:
                        cursor=conn.execute('UPDATE payroll_rows SET emp_no=?,payload=?,version=version+1 WHERE id=? AND period_id=? AND version=?',(parsed['emp_no'],json.dumps(parsed,ensure_ascii=False),row_id,period_id,request.form.get('version')))
                        if not cursor.rowcount:raise ValueError('تم تعديل الصف من مستخدم آخر. أعد تحميل الصفحة')
                    else:
                        conn.execute('INSERT INTO payroll_rows(period_id,employee_id,emp_no,payload) VALUES (?,?,?,?)',(period_id,employee_id,parsed['emp_no'],json.dumps(parsed,ensure_ascii=False)))
                    if conn.execute('SELECT version FROM payroll_periods WHERE id=?',(period_id,)).fetchone()[0]!=int(request.form.get('period_version','0')): raise ValueError('تغيرت إعدادات الشهر. أعد تحميل الصفحة')
                    conn.commit();changed('حفظ راتب',period_id,parsed['emp_no']);flash('تم حفظ الراتب وحسابه','ok')
                    return redirect(url_for('payroll_detail',period_id=period_id))
                except sqlite3.IntegrityError:conn.rollback();flash('الموظف أو رقمه موجود في مسير هذا الشهر','danger');data.update(request.form)
                except ValueError as exc:conn.rollback();flash(str(exc),'danger');data.update(request.form)
            employees=[dict(r) for r in conn.execute('SELECT id,emp_no,full_name FROM hr_employees ORDER BY full_name')]
            estimate=None
            try: estimate=calculate(validate(data,period),period)
            except ValueError: pass
            return render_template('payroll_row.html',period=period,row=data,row_id=row_id,version=version,employee_id=employee_id,employees=employees,inputs=INPUTS,payments=PAYMENTS,estimate=estimate,section='hr',active='payroll')
        finally:conn.close()

    @app.route('/hr/payroll/<int:period_id>/preview',methods=['POST'])
    @login_required
    def payroll_preview(period_id):
        access('write');conn=db.connect()
        try:
            period=get_period(conn,period_id)
            try:return jsonify({k:str(v) if v is not None else None for k,v in calculate(validate(request.form,period),period).items()})
            except ValueError as exc:return jsonify(error=str(exc)),400
        finally:conn.close()
