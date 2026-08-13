#!/usr/bin/env python3
"""طوارئ SSH فقط عند تعطّل SMTP.

أثناء عمل البريد: مرفوض — استخدم واجهة /admin/programmer/verify
لإرسال رمز إلى wadnooh@gmail.com و wadnooh@wadnooh.com فقط.

  cd /opt/rekaz
  sudo -u rekazapp /opt/rekaz/.venv/bin/python tools/programmer_approve.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in __import__("os").environ:
            __import__("os").environ[key] = val


def main() -> int:
    _load_env_file(Path("/etc/rekaz.env"))
    from webapp import db
    from webapp import programmer_guard as prog_guard

    db.ensure_schema()
    emails = ", ".join(prog_guard.programmer_emails())
    if prog_guard.smtp_ready():
        print("REFUSED: SMTP is working.")
        print(f"Use UI email OTP only → {emails}")
        print("SSH emergency codes are rejected while SMTP works.")
        return 2
    ok, payload, expires = prog_guard.create_ssh_emergency_code()
    if not ok:
        print(payload)
        return 2
    print("SSH emergency code (SMTP down only):")
    print(payload)
    if expires:
        print(f"Expires (UTC): {expires.strftime('%Y-%m-%d %H:%M:%S')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
