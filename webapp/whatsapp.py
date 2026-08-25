from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


def configured() -> bool:
    return bool(
        (os.environ.get("WHATSAPP_ACCESS_TOKEN") or "").strip()
        and (os.environ.get("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    )


def normalize_phone(value: str | None) -> str:
    digits = re.sub(r"\D+", "", value or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        digits = "966" + digits[1:]
    return digits


def send_text(*, to_phone: str, body: str) -> tuple[bool, str]:
    phone = normalize_phone(to_phone)
    token = (os.environ.get("WHATSAPP_ACCESS_TOKEN") or "").strip()
    phone_number_id = (os.environ.get("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    api_version = (os.environ.get("WHATSAPP_API_VERSION") or "v20.0").strip() or "v20.0"
    if not token or not phone_number_id:
        return False, "WhatsApp غير مضبوط (WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID)"
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
