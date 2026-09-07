"""Fixed asset register, straight-line estimates and review history."""
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from flask import request, render_template, redirect, url_for, flash, abort
from webapp import db, permissions, helpers

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS fixed_assets (
        id INTEGER PRIMARY KEY, asset_no TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
        car_id INTEGER UNIQUE REFERENCES workshop_cars(id) ON DELETE RESTRICT,
        equipment_id INTEGER UNIQUE REFERENCES workshop_equipment(id) ON DELETE RESTRICT,
        acquired_on TEXT NOT NULL, cost TEXT NOT NULL, residual TEXT NOT NULL,
        annual_rate TEXT NOT NULL, next_review TEXT, notes TEXT,
        CHECK(car_id IS NULL OR equipment_id IS NULL))""",
    """CREATE TABLE IF NOT EXISTS fixed_asset_reviews (
        id INTEGER PRIMARY KEY, asset_id INTEGER NOT NULL REFERENCES fixed_assets(id) ON DELETE RESTRICT,
        reviewed_on TEXT NOT NULL, reviewer TEXT NOT NULL, condition TEXT NOT NULL,
        notes TEXT NOT NULL, next_review TEXT)""",
]


def ensure_schema(conn):
    for sql in SCHEMA:
        conn.execute(sql)


def depreciation(asset, as_of=None):
    as_of = as_of or date.today()
    start = date.fromisoformat(asset['acquired_on'])
    cost, residual, rate = (Decimal(str(asset[key])) for key in ('cost', 'residual', 'annual_rate'))
    base = cost - residual
    days = max(0, (as_of - start).days)
    annual = base * rate / Decimal(100)
    accumulated = min(base, annual * Decimal(days) / Decimal(365))
    money = lambda value: value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return dict(annual=money(annual), accumulated=money(accumulated), net=money(cost-accumulated))


def validate_asset(form):
    result = {key: (form.get(key) or '').strip() for key in ('asset_no','name','acquired_on','cost','residual','annual_rate','next_review','notes')}
    if not result['asset_no'] or not result['name']:
        raise ValueError('رقم الأصل واسم الأصل مطلوبان')
    date.fromisoformat(result['acquired_on'])
    if result['next_review']:
        date.fromisoformat(result['next_review'])
    try:
        cost, residual, rate = [Decimal(result[key]) for key in ('cost','residual','annual_rate')]
    except InvalidOperation:
        raise ValueError('أدخل قيماً رقمية صحيحة')
    if not all(value.is_finite() for value in (cost, residual, rate)) or not (cost >= 0 and 0 <= residual <= cost and 0 <= rate <= 100):
        raise ValueError('القيمة المتبقية بين صفر والتكلفة، ونسبة الإهلاك بين صفر و100')
    result.update(cost=str(cost), residual=str(residual), annual_rate=str(rate))
    result.update(car_id=None, equipment_id=None)
    link = form.get('link') or ''
    if link:
        kind, ident = link.split(':', 1)
        if kind not in ('car', 'equipment') or not ident.isdigit():
            raise ValueError('ربط الأصل غير صالح')
        result['car_id' if kind == 'car' else 'equipment_id'] = int(ident)
    return result


def register(app, login_required):
    def access(write=False):
        if not permissions.can('section.maintenance', 'modules.write' if write else 'modules.read'):
            abort(403)

    @app.context_processor
    def linked_asset_context():
        args = request.view_args or {}
        name = args.get('name')
        if request.endpoint != 'module_edit' or name not in ('workshop_cars','workshop_equipment') or not permissions.can('section.maintenance','modules.read'):
            return {}
        field = 'car_id' if name == 'workshop_cars' else 'equipment_id'
        conn = db.connect()
        try:
            asset = conn.execute(f'SELECT * FROM fixed_assets WHERE {field}=?',(args.get('row_id'),)).fetchone()
            asset = dict(asset) if asset else None
            if asset:
                asset.update(depreciation(asset))
            return {'linked_fixed_asset': asset}
        finally:
            conn.close()

    @app.route('/fixed-assets')
    @login_required
    def fixed_assets_home():
        access()
        conn = db.connect()
        try:
            rows = [dict(row) for row in conn.execute("""SELECT a.*, c.plate_no, e.equip_no,
                (SELECT MAX(reviewed_on) FROM fixed_asset_reviews WHERE asset_id=a.id) last_review
                FROM fixed_assets a LEFT JOIN workshop_cars c ON c.id=a.car_id
                LEFT JOIN workshop_equipment e ON e.id=a.equipment_id ORDER BY a.id DESC""")]
        finally:
            conn.close()
        today = date.today()
        for row in rows:
            row.update(depreciation(row, today))
            row['overdue'] = bool(row['next_review'] and row['next_review'] <= today.isoformat())
        totals = {key: sum((Decimal(str(row[key])) for row in rows), Decimal(0)) for key in ('cost','accumulated','net')}
        return render_template('fixed_assets.html', rows=rows, totals=totals, today=today, section='maintenance')

    @app.route('/fixed-assets/new', methods=['GET','POST'])
    @app.route('/fixed-assets/<int:asset_id>', methods=['GET','POST'])
    @login_required
    def fixed_asset_edit(asset_id=None):
        access(write=request.method == 'POST' or asset_id is None)
        conn = db.connect()
        try:
            row = dict(conn.execute('SELECT * FROM fixed_assets WHERE id=?',(asset_id,)).fetchone() or {}) if asset_id else {}
            if not asset_id and request.method == 'GET':
                row = {'link': request.args.get('link', '')}
            if asset_id and not row:
                abort(404)
            if request.method == 'POST':
                try:
                    data = validate_asset(request.form)
                    for field, table in [('car_id','workshop_cars'),('equipment_id','workshop_equipment')]:
                        if data[field] and not conn.execute(f'SELECT id FROM {table} WHERE id=?',(data[field],)).fetchone():
                            raise ValueError('السيارة أو المعدة غير موجودة')
                    if asset_id:
                        conn.execute('UPDATE fixed_assets SET '+','.join(key+'=?' for key in data)+' WHERE id=?', (*data.values(), asset_id))
                    else:
                        cursor = conn.execute('INSERT INTO fixed_assets ('+','.join(data)+') VALUES ('+','.join('?' for _ in data)+')',tuple(data.values()))
                        asset_id = cursor.lastrowid
                    conn.commit()
                    db.log_audit(helpers.current_user_name(), 'حفظ أصل ثابت', 'الأصول الثابتة', asset_id, data['asset_no'])
                    helpers.after_data_change()
                    flash('تم حفظ الأصل وربطه بنجاح', 'ok')
                    return redirect(url_for('fixed_asset_edit', asset_id=asset_id))
                except (ValueError, sqlite3.IntegrityError) as exc:
                    conn.rollback()
                    flash('تحقق من البيانات؛ يجب أن يكون رقم الأصل والربط فريدين والتواريخ والقيم صحيحة.', 'danger')
                    row.update(request.form)
            cars = [dict(r) for r in conn.execute('SELECT id,plate_no FROM workshop_cars ORDER BY plate_no')]
            equipment = [dict(r) for r in conn.execute('SELECT id,equip_no,equip_name FROM workshop_equipment ORDER BY equip_no')]
            reviews = [dict(r) for r in conn.execute('SELECT * FROM fixed_asset_reviews WHERE asset_id=? ORDER BY reviewed_on DESC,id DESC',(asset_id,))] if asset_id else []
            estimate = depreciation(row) if asset_id and request.method == 'GET' else None
            return render_template('fixed_asset_edit.html', row=row, asset_id=asset_id, cars=cars, equipment=equipment, reviews=reviews, estimate=estimate, today=date.today(), section='maintenance')
        finally:
            conn.close()

    @app.route('/fixed-assets/<int:asset_id>/review', methods=['POST'])
    @login_required
    def fixed_asset_review(asset_id):
        access(write=True)
        conn = db.connect()
        try:
            if not conn.execute('SELECT id FROM fixed_assets WHERE id=?',(asset_id,)).fetchone():
                abort(404)
            try:
                reviewed = date.fromisoformat(request.form.get('reviewed_on',''))
                next_review = request.form.get('next_review','').strip()
                if reviewed > date.today() or (next_review and date.fromisoformat(next_review) <= reviewed):
                    raise ValueError()
                condition = request.form.get('condition','')
                notes = request.form.get('notes','').strip()
                if condition not in ('جيد','يحتاج صيانة','متوقف') or not notes:
                    raise ValueError()
                conn.execute('INSERT INTO fixed_asset_reviews(asset_id,reviewed_on,reviewer,condition,notes,next_review) VALUES (?,?,?,?,?,?)',
                    (asset_id, reviewed.isoformat(), helpers.current_user_name(), condition, notes, next_review))
                conn.execute('UPDATE fixed_assets SET next_review=? WHERE id=?',(next_review,asset_id))
                conn.commit()
                db.log_audit(helpers.current_user_name(), 'مراجعة أصل ثابت', 'الأصول الثابتة', asset_id, condition)
                helpers.after_data_change()
                flash('تم تسجيل المراجعة', 'ok')
            except ValueError:
                flash('تحقق من تاريخ المراجعة وموعد المراجعة القادمة والحالة والملاحظات.', 'danger')
        finally:
            conn.close()
        return redirect(url_for('fixed_asset_edit', asset_id=asset_id))
