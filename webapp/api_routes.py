"""واجهة برمجية (API) للتكاملات الخارجية."""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, g, jsonify, request

from webapp import db, helpers, permissions
from webapp.tickets_routes import TICKET_FIELDS, ticket_from_form

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


def require_api_key(fn):
    """Decorator لحماية مسارات API بمفتاح."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        key = request.headers.get("X-API-Key")
        if not key:
            return jsonify({"ok": False, "error": "Missing X-API-Key header"}), 401

        user = db.get_user_by_api_key(key)
        if not user:
            return jsonify({"ok": False, "error": "Invalid API key"}), 401

        if not permissions.has_perm("api.access", role=user["role"]):
            return (
                jsonify({"ok": False, "error": "API access is not enabled for this user role"}),
                403,
            )

        g.api_user = user
        return fn(*args, **kwargs)

    return wrapper


@api_bp.route("/tickets", methods=["POST"])
@require_api_key
def create_ticket():
    """
    إنشاء عطل جديد عبر API.
    يتطلب Content-Type: application/json.
    """
    if not permissions.has_perm("tickets.write", role=g.api_user["role"]):
        return jsonify({"ok": False, "error": "Permission denied: tickets.write"}), 403

    json_data = request.get_json()
    if not json_data:
        return jsonify({"ok": False, "error": "Invalid JSON body"}), 400

    # استخدم نفس منطق النموذج لتوحيد المعالجة
    # نحاكي كائن form من بيانات JSON
    class JsonForm(dict):
        def get(self, key, default=None):
            return self.get(key, default)

    # استبدل request.form ببيانات JSON
    original_form = request.form
    request.form = JsonForm(json_data)

    try:
        data = ticket_from_form()
        if not data["ticket_no"]:
            return jsonify({"ok": False, "error": "ticket_no is required"}), 400

        conn = db.connect()
        try:
            if db.resolve_ticket_ref(data["ticket_no"], conn):
                return jsonify({"ok": False, "error": f"Ticket {data['ticket_no']} already exists"}), 409

            if not (data.get("rekaz_code") or "").strip():
                data["rekaz_code"] = db.next_series_code("er", conn)

            cols = ", ".join(TICKET_FIELDS)
            placeholders = ", ".join(["?"] * len(TICKET_FIELDS))
            cur = conn.execute(
                f"INSERT INTO tickets({cols}) VALUES ({placeholders})", [data[f] for f in TICKET_FIELDS]
            )
            new_id = cur.lastrowid
            conn.commit()

            db.log_audit(
                g.api_user["full_name"], "إضافة (API)", "عطل", new_id,
                f"{data.get('ticket_no')} / {data.get('rekaz_code')}",
            )
            helpers.after_data_change()

            return jsonify({
                "ok": True,
                "message": "Ticket created successfully",
                "ticket_id": new_id,
                "ticket_no": data["ticket_no"],
                "rekaz_code": data["rekaz_code"],
            }), 201

        finally:
            conn.close()

    finally:
        # أعد request.form الأصلي
        request.form = original_form