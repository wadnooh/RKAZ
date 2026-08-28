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
    """جلب إعدادات الواتساب من متغيرات البيئة أو جدول الإعدادات."""
    try:
        settings = db.get_settings()
    except Exception:
        settings = {}
    token = (os.environ.get("WHATSAPP_ACCESS_TOKEN") or settings.get("whatsapp_access_token") or "").strip()
    phone_number_id = (os.environ.get("WHATSAPP_PHONE_NUMBER_ID") or settings.get("whatsapp_phone_number_id") or "").strip()
    api_version = (os.environ.get("WHATSAPP_API_VERSION") or settings.get("whatsapp_api_version") or "v20.0").strip() or "v20.0"
    admin_phone = (os.environ.get("WHATSAPP_ADMIN_PHONE") or settings.get("whatsapp_admin_phone") or "").strip()
    return {
        "token": token,
        "phone_number_id": phone_number_id,
        "api_version": api_version,
        "admin_phone": admin_phone,
    }


def configured() -> bool:
    cfg = get_whatsapp_config()
    return bool(cfg["token"] and cfg["phone_number_id"])


def normalize_phone(value: str | None) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        digits = "966" + digits[1:]
    elif len(digits) == 9 and digits.startswith("5"):
        digits = "966" + digits
    return digits


def send_text(*, to_phone: str, body: str) -> tuple[bool, str]:
    cfg = get_whatsapp_config()
    phone = normalize_phone(to_phone)
    token = cfg["token"]
    phone_number_id = cfg["phone_number_id"]
    api_version = cfg["api_version"]
    if not token or not phone_number_id:
        return False, "WhatsApp Cloud API غير مضبوط (WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID)"
    if not phone:
        return False, "لا يوجد رقم جوال صالح"
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
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if 200 <= int(resp.status) < 300:
                return True, ""
            return False, f"WhatsApp HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "ignore")[:240]
        except Exception:
            detail = str(exc)
        return False, detail or str(exc)
    except Exception as exc:
        return False, str(exc)[:240]


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
        f"▫️ *رقم المعاملة:* {work.get('work_no') or '—'}",
        f"▫️ *التاريخ:* {work.get('work_date') or '—'}",
        f"▫️ *القسم:* {work.get('department') or '—'}",
        f"▫️ *نوع العمل:* {work.get('work_type') or '—'}",
        f"▫️ *الحالة:* {work.get('status') or '—'}",
        f"▫️ *المحطة:* {work.get('station_no') or '—'}",
    ]
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

