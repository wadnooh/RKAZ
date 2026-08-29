from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from webapp import db


def get_whatsapp_config() -> dict[str, str]:
    """جلب إعدادات الواتساب من جدول الإعدادات أو متغيرات البيئة."""
    try:
        settings = db.get_settings()
    except Exception:
        settings = {}

    enabled = settings.get("whatsapp_enabled", "1").strip()
    provider = settings.get("whatsapp_provider", "web_gateway").strip() or "web_gateway"
    phone_number = settings.get("whatsapp_phone_number", "").strip() or os.environ.get("WHATSAPP_PHONE_NUMBER", "").strip()
    gateway_url = settings.get("whatsapp_gateway_url", "https://api.ultramsg.com").strip() or os.environ.get("WHATSAPP_GATEWAY_URL", "").strip()
    instance_id = settings.get("whatsapp_instance_id", "").strip() or os.environ.get("WHATSAPP_INSTANCE_ID", "").strip()
    api_key = settings.get("whatsapp_api_key", "").strip() or os.environ.get("WHATSAPP_API_KEY", "").strip()
    
    # Meta Cloud API
    token = (os.environ.get("WHATSAPP_ACCESS_TOKEN") or settings.get("whatsapp_access_token") or "").strip()
    phone_number_id = (os.environ.get("WHATSAPP_PHONE_NUMBER_ID") or settings.get("whatsapp_phone_number_id") or "").strip()
    api_version = (os.environ.get("WHATSAPP_API_VERSION") or settings.get("whatsapp_api_version") or "v20.0").strip() or "v20.0"
    
    admin_phone = (os.environ.get("WHATSAPP_ADMIN_PHONE") or settings.get("whatsapp_admin_phone") or "").strip()
    auto_recipients = settings.get("whatsapp_auto_recipients", "").strip()
    
    auto_ticket_create = settings.get("whatsapp_auto_ticket_create", "1").strip()
    auto_ticket_close = settings.get("whatsapp_auto_ticket_close", "1").strip()
    auto_warehouse_tx = settings.get("whatsapp_auto_warehouse_tx", "1").strip()

    return {
        "enabled": enabled,
        "provider": provider,
        "phone_number": phone_number,
        "gateway_url": gateway_url,
        "instance_id": instance_id,
        "api_key": api_key,
        "token": token,
        "phone_number_id": phone_number_id,
        "api_version": api_version,
        "admin_phone": admin_phone,
        "auto_recipients": auto_recipients,
        "auto_ticket_create": auto_ticket_create,
        "auto_ticket_close": auto_ticket_close,
        "auto_warehouse_tx": auto_warehouse_tx,
    }


def save_whatsapp_config(updates: dict[str, str]) -> None:
    """حفظ إعدادات الواتساب في جدول الإعدادات."""
    allowed_keys = {
        "whatsapp_enabled",
        "whatsapp_provider",
        "whatsapp_phone_number",
        "whatsapp_gateway_url",
        "whatsapp_instance_id",
        "whatsapp_api_key",
        "whatsapp_access_token",
        "whatsapp_phone_number_id",
        "whatsapp_api_version",
        "whatsapp_admin_phone",
        "whatsapp_auto_recipients",
        "whatsapp_auto_ticket_create",
        "whatsapp_auto_ticket_close",
        "whatsapp_auto_warehouse_tx",
    }
    to_save = {k: str(v).strip() for k, v in updates.items() if k in allowed_keys}
    if to_save:
        db.save_settings(to_save)


def configured() -> bool:
    """التحقق من إمكانية الإرسال التلقائي."""
    cfg = get_whatsapp_config()
    if cfg.get("enabled") == "0":
        return False
    provider = cfg.get("provider")
    if provider in ("web_gateway", "ultramsg"):
        return bool(cfg.get("gateway_url") and cfg.get("instance_id") and cfg.get("api_key"))
    elif provider == "cloud_api":
        return bool(cfg.get("token") and cfg.get("phone_number_id"))
    return bool(cfg.get("phone_number") or cfg.get("api_key"))


def get_connection_status() -> dict[str, Any]:
    """فحص حالة اتصال الواتساب ويب المربوط."""
    cfg = get_whatsapp_config()
    is_conf = configured()
    phone = cfg.get("phone_number") or cfg.get("admin_phone") or ""
    provider = cfg.get("provider", "web_gateway")
    
    if not is_conf:
        return {
            "status": "disconnected",
            "status_label": "غير متصل / بانتظار ربط الحساب",
            "status_color": "danger",
            "phone": phone or "—",
            "provider": provider,
            "is_connected": False,
        }
    
    return {
        "status": "connected",
        "status_label": "متصل بنجاح وجاهز للإرسال التلقائي",
        "status_color": "ok",
        "phone": phone or "حساب واتساب المربوط",
        "provider": provider,
        "is_connected": True,
    }


def normalize_phone(value: str | None) -> str:
    """توحيد صيغة رقم الهاتف الدولي."""
    digits = re.sub(r"\D+", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        digits = "966" + digits[1:]
    elif len(digits) == 9 and digits.startswith("5"):
        digits = "966" + digits
    return digits


def send_text(*, to_phone: str, body: str, source_type: str = "", source_id: str = "") -> tuple[bool, str]:
    """إرسال رسالة واتساب تلقائية عبر البوابة وتسجيلها في السجل."""
    cfg = get_whatsapp_config()
    phone = normalize_phone(to_phone)
    if not phone:
        db.log_whatsapp_message(to_phone, body, "failed", "رقم الجوال فارغ أو غير صالح", source_type, source_id)
        return False, "لا يوجد رقم جوال صالح"

    provider = cfg.get("provider", "web_gateway")
    
    # 1. UltraMsg / WhatsApp Web Gateway
    if provider in ("web_gateway", "ultramsg"):
        gateway_url = (cfg.get("gateway_url") or "https://api.ultramsg.com").rstrip("/")
        instance_id = cfg.get("instance_id") or ""
        api_key = cfg.get("api_key") or ""
        
        if not instance_id or not api_key:
            err = "إعدادات البوابة غير مكتملة (Instance ID أو API Key فارغ)"
            db.log_whatsapp_message(phone, body, "failed", err, source_type, source_id)
            return False, err

        url = f"{gateway_url}/{instance_id}/messages/chat"
        payload = urllib.parse.urlencode({
            "token": api_key,
            "to": phone,
            "body": body,
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                res_data = resp.read().decode("utf-8", "ignore")
                if 200 <= int(resp.status) < 300:
                    db.log_whatsapp_message(phone, body, "sent", "", source_type, source_id)
                    return True, "تم الإرسال بنجاح"
                err = f"Gateway HTTP {resp.status}: {res_data[:120]}"
                db.log_whatsapp_message(phone, body, "failed", err, source_type, source_id)
                return False, err
        except Exception as exc:
            err = str(exc)[:200]
            db.log_whatsapp_message(phone, body, "failed", err, source_type, source_id)
            return False, err

    # 2. Meta WhatsApp Cloud API
    elif provider == "cloud_api":
        token = cfg.get("token") or ""
        phone_number_id = cfg.get("phone_number_id") or ""
        api_version = cfg.get("api_version") or "v20.0"
        
        if not token or not phone_number_id:
            err = "Cloud API غير مضبوط (Access Token أو Phone Number ID مفقود)"
            db.log_whatsapp_message(phone, body, "failed", err, source_type, source_id)
            return False, err

        url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                if 200 <= int(resp.status) < 300:
                    db.log_whatsapp_message(phone, body, "sent", "", source_type, source_id)
                    return True, "تم الإرسال بنجاح"
                err = f"Cloud API HTTP {resp.status}"
                db.log_whatsapp_message(phone, body, "failed", err, source_type, source_id)
                return False, err
        except Exception as exc:
            err = str(exc)[:200]
            db.log_whatsapp_message(phone, body, "failed", err, source_type, source_id)
            return False, err

    # Default / Simulator
    db.log_whatsapp_message(phone, body, "sent", "تم الإرسال عبر البوابة", source_type, source_id)
    return True, "تم الإرسال"


def send_auto_notification(body: str, recipient_override: str = "", source_type: str = "", source_id: str = "") -> list[tuple[str, bool, str]]:
    """إرسال تنبيه تلقائي لجميع الأرقام المحددة في الإعدادات."""
    cfg = get_whatsapp_config()
    if cfg.get("enabled") == "0":
        return []

    targets = []
    if recipient_override:
        targets.append(recipient_override)
    else:
        raw_list = cfg.get("auto_recipients") or ""
        for p in raw_list.replace("\n", ",").replace(";", ",").split(","):
            p_clean = p.strip()
            if p_clean and p_clean not in targets:
                targets.append(p_clean)
        
        admin_p = cfg.get("admin_phone") or ""
        if admin_p and admin_p not in targets:
            targets.append(admin_p)

    results = []
    for t in targets:
        ok, res = send_text(to_phone=t, body=body, source_type=source_type, source_id=source_id)
        results.append((t, ok, res))
    return results


def notify_ticket_created(ticket: dict, base_url: str = "") -> None:
    """إرسال إشعار تلقائي عند تسجيل عطل جديد."""
    cfg = get_whatsapp_config()
    if cfg.get("auto_ticket_create") != "1":
        return
    body = "🆕 *تم تسجيل عطل جديد في النظام*\n" + ticket_whatsapp_message(ticket, base_url)
    send_auto_notification(body, source_type="ticket_new", source_id=str(ticket.get("id") or ""))


def notify_ticket_status_change(ticket: dict, new_status: str, base_url: str = "") -> None:
    """إرسال إشعار تلقائي عند تغيير حالة العطل أو إنجازه."""
    cfg = get_whatsapp_config()
    if cfg.get("auto_ticket_close") != "1":
        return
    tno = ticket.get("ticket_no") or "—"
    rekaz = ticket.get("rekaz_code") or "—"
    body = (
        f"🔔 *تحديث حالة العطل: {tno} ({rekaz})*\n"
        f"▫️ *الحالة الجديدة:* {new_status}\n"
        f"▫️ *الفرقة:* {ticket.get('team') or '—'}\n"
        f"▫️ *الحي:* {ticket.get('district') or '—'}"
    )
    if base_url and ticket.get("id"):
        body += f"\n🔗 {base_url.rstrip('/')}/tickets/{ticket['id']}"
    send_auto_notification(body, source_type="ticket_status", source_id=str(ticket.get("id") or ""))


def notify_warehouse_movement(tx: dict, base_url: str = "") -> None:
    """إرسال إشعار تلقائي بحركة مستودع جديدة."""
    cfg = get_whatsapp_config()
    if cfg.get("auto_warehouse_tx") != "1":
        return
    vno = tx.get("voucher_no") or "—"
    ttype = tx.get("tx_type") or "حركة مستودع"
    iname = tx.get("item_name") or tx.get("item_no") or "مادة"
    qty = tx.get("qty") or "0"
    unit = tx.get("unit") or ""
    recip = tx.get("recipient") or tx.get("sender") or "—"
    
    body = (
        f"📦 *حركة مستودع جديدة — {ttype}*\n"
        f"▫️ *رقم السند:* {vno}\n"
        f"▫️ *المادة:* {iname}\n"
        f"▫️ *الكمية:* {qty} {unit}\n"
        f"▫️ *المستلم/المسلم:* {recip}\n"
        f"▫️ *التاريخ:* {tx.get('tx_date') or datetime.now().strftime('%Y-%m-%d')}"
    )
    if tx.get("ticket_no"):
        body += f"\n▫️ *رقم العطل المرتبط:* {tx.get('ticket_no')}"
    send_auto_notification(body, source_type="warehouse_tx", source_id=str(tx.get("id") or ""))


def ticket_whatsapp_message(ticket: dict, base_url: str = "") -> str:
    """صياغة رسالة واتساب منسقة لمعاملة عطل."""
    base_url = (base_url or "").rstrip("/")
    tno = ticket.get("ticket_no") or "—"
    rekaz = ticket.get("rekaz_code") or "—"
    wo = ticket.get("work_order") or "—"
    district = ticket.get("district") or "—"
    fault = ticket.get("fault_type") or "—"
    team = ticket.get("team") or "—"
    agent = ticket.get("agent") or "—"
    status = ticket.get("status") or "—"
    location = ticket.get("location") or ""
    excav = "نعم" if ticket.get("has_excavation") else "لا"
    tid = ticket.get("id")

    lines = [
        "⚡ *معاملة عطل — شركة ركاز الإنجاز*",
        f"▫️ *رقم العطل (SEC):* {tno}",
        f"▫️ *كود ركاز (ER):* {rekaz}",
        f"▫️ *أمر العمل:* {wo}",
        f"▫️ *الحي:* {district}",
        f"▫️ *نوع العطل:* {fault}",
        f"▫️ *الفرقة:* {team}",
        f"▫️ *المندوب:* {agent}",
        f"▫️ *حالة التنفيذ:* {status}",
        f"▫️ *معاملة بها حفر:* {excav}",
    ]
    if location:
        lines.append(f"📍 *الموقع:* {location}")
    if tid and base_url:
        lines.append(f"🔗 *عرض المعاملة:* {base_url}/tickets/{tid}")
    return "\n".join(lines)


def ticket_whatsapp_url(ticket: dict, base_url: str = "", phone: str = "") -> str:
    """رابط فتح المحادثة على واتساب لمشاركة بيانات العطل."""
    msg = ticket_whatsapp_message(ticket, base_url)
    clean_phone = normalize_phone(phone)
    if clean_phone:
        return f"https://wa.me/{clean_phone}?text={urllib.parse.quote(msg)}"
    return f"https://wa.me/?text={urllib.parse.quote(msg)}"


def coordination_whatsapp_message(coord: dict, base_url: str = "") -> str:
    """صياغة رسالة واتساب منسقة لتنسيق أو رخصة."""
    base_url = (base_url or "").rstrip("/")
    lines = [
        "🏗️ *بيانات التنسيق / الرخصة — ركاز*",
        f"▫️ *رقم المعاملة / الرخصة:* {coord.get('license_no') or coord.get('req_no') or '—'}",
        f"▫️ *القسم المستهدف:* {coord.get('target_section') or '—'}",
        f"▫️ *الموقع:* {coord.get('site') or '—'}",
        f"▫️ *الحالة:* {coord.get('status') or '—'}",
    ]
    if coord.get("ticket_no"):
        lines.append(f"▫️ *رقم العطل المرتبط:* {coord.get('ticket_no')}")
    if base_url:
        lines.append(f"🔗 *الرابط:* {base_url}/quality")
    return "\n".join(lines)


def coordination_whatsapp_url(coord: dict, base_url: str = "", phone: str = "") -> str:
    msg = coordination_whatsapp_message(coord, base_url)
    clean_phone = normalize_phone(phone)
    if clean_phone:
        return f"https://wa.me/{clean_phone}?text={urllib.parse.quote(msg)}"
    return f"https://wa.me/?text={urllib.parse.quote(msg)}"


def reinforcement_whatsapp_message(work: dict, base_url: str = "") -> str:
    """صياغة رسالة واتساب منسقة لمعاملة تعزيز."""
    base_url = (base_url or "").rstrip("/")
    lines = [
        "⚡ *معاملة التعزيز — ركاز*",
        f"▫️ *رقم ركاز:* {work.get('rekaz_code') or work.get('work_no') or '—'}",
    ]
    if work.get("work_order"):
        lines.append(f"▫️ *رقم أمر العمل:* {work.get('work_order')}")
    if work.get("sap_reservation_no"):
        lines.append(f"▫️ *رقم حجز الساب:* {work.get('sap_reservation_no')}")
    if work.get("notification_no"):
        lines.append(f"▫️ *رقم الإشعار:* {work.get('notification_no')}")
    lines.extend([
        f"▫️ *التاريخ:* {work.get('work_date') or '—'}",
        f"▫️ *القسم:* {work.get('department') or '—'}",
        f"▫️ *نوع العمل:* {work.get('work_type') or '—'}",
        f"▫️ *الحالة:* {work.get('status') or '—'}",
        f"▫️ *المحطة:* {work.get('station_no') or '—'}",
    ])
    if work.get("ticket_no"):
        lines.append(f"▫️ *رقم العطل:* {work.get('ticket_no')}")
    if work.get("location"):
        lines.append(f"📍 *الموقع:* {work.get('location')}")
    if work.get("id") and base_url:
        lines.append(f"🔗 *رابط المعاملة:* {base_url}/reinforcement/{work.get('id')}")
    return "\n".join(lines)


def reinforcement_whatsapp_url(work: dict, base_url: str = "", phone: str = "") -> str:
    msg = reinforcement_whatsapp_message(work, base_url)
    clean_phone = normalize_phone(phone)
    if clean_phone:
        return f"https://wa.me/{clean_phone}?text={urllib.parse.quote(msg)}"
    return f"https://wa.me/?text={urllib.parse.quote(msg)}"

