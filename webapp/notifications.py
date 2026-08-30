"""نظام الإشعارات للمستخدمين."""

from __future__ import annotations
from datetime import datetime
from flask import session, Blueprint, jsonify, request
from . import db # افتراض وجود وحدة للاتصال بقاعدة البيانات


def create_notification(user_id: int, message: str, link: str | None = None):
    """
    إنشاء إشعار جديد لمستخدم معين.
    """
    if not user_id or not message:
        return

    conn = db.connect()
    try:
        conn.execute(
            """
            INSERT INTO notifications (user_id, message, link, created_at, is_read)
            VALUES (?, ?, ?, ?, 0)
            """,
            (user_id, message, link, datetime.utcnow())
        )
        conn.commit()
    finally:
        conn.close()


def get_user_notifications(user_id: int, *, limit: int = 10) -> list[dict]:
    """
    الحصول على أحدث الإشعارات غير المقروءة للمستخدم.
    """
    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT id, message, link, created_at
            FROM notifications
            WHERE user_id = ? AND is_read = 0
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def mark_as_read(notification_id: int, user_id: int):
    """
    تحديد إشعار كمقروء.
    """
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
            (notification_id, user_id)
        )
        conn.commit()
    finally:
        conn.close()


def template_context_processor():
    """
    مُعالج سياق لإضافة الإشعارات إلى كل القوالب.
    """
    from .permissions import can # استيراد متأخر لتجنب الاعتماد الدائري
    
    user_id = session.get("user_id")
    if not user_id or not can("notifications.read"):
        return {"notifications": [], "unread_notifications_count": 0}

    notifications = get_user_notifications(user_id)
    return {
        "notifications": notifications,
        "unread_notifications_count": len(notifications),
    }


def register_module(app):
    """
    تسجيل الوحدة في تطبيق Flask.
    """
    # إضافة معالج السياق ليكون متاحاً في كل الصفحات
    app.context_processor(template_context_processor)