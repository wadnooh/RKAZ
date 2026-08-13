"""إرسال بريد SMTP لرموز تحقق المبرمج."""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


def smtp_configured() -> bool:
    return bool((os.environ.get("SMTP_HOST") or "").strip() and (os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "").strip())


def smtp_settings() -> dict:
    host = (os.environ.get("SMTP_HOST") or "").strip()
    port_raw = (os.environ.get("SMTP_PORT") or "").strip()
    user = (os.environ.get("SMTP_USER") or "").strip()
    password = os.environ.get("SMTP_PASS") or os.environ.get("SMTP_PASSWORD") or ""
    from_addr = (os.environ.get("SMTP_FROM") or user or "").strip()
    ssl_on = (os.environ.get("SMTP_SSL") or "").strip().lower() in {"1", "true", "yes", "on"}
    tls_on = (os.environ.get("SMTP_TLS") or "").strip().lower() in {"1", "true", "yes", "on"}
    # defaults: 465=ssl, 587=starttls
    try:
        port = int(port_raw) if port_raw else (465 if ssl_on else 587)
    except ValueError:
        port = 587
    if not ssl_on and not tls_on:
        if port == 465:
            ssl_on = True
        else:
            tls_on = True
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "from_addr": from_addr,
        "ssl": ssl_on,
        "tls": tls_on,
    }


def send_email(*, to_addrs: list[str], subject: str, body: str) -> tuple[bool, str]:
    """يرسل بريداً نصياً. يعيد (ok, رسالة_خطأ)."""
    cfg = smtp_settings()
    if not cfg["host"] or not cfg["from_addr"]:
        return False, "SMTP غير مضبوط (SMTP_HOST / SMTP_FROM)"
    recipients = [a.strip() for a in to_addrs if (a or "").strip()]
    if not recipients:
        return False, "لا يوجد مستلمون"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from_addr"]
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    try:
        if cfg["ssl"]:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30, context=context) as server:
                if cfg["user"]:
                    server.login(cfg["user"], cfg["password"])
                server.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as server:
                server.ehlo()
                if cfg["tls"]:
                    context = ssl.create_default_context()
                    server.starttls(context=context)
                    server.ehlo()
                if cfg["user"]:
                    server.login(cfg["user"], cfg["password"])
                server.send_message(msg)
        return True, ""
    except Exception as exc:
        return False, str(exc)[:240]
