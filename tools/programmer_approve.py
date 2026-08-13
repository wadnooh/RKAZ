#!/usr/bin/env python3
"""يولّد رمز موافقة لمرة واحدة (بديل عند تعذّر البريد).

المسار المفضّل: زر «أرسل رمز التحقق» في الواجهة → بريد المبرمج المعتمد.
هذا السكربت احتياطي عبر SSH فقط:

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
    print(f"Preferred path: email OTP to {emails}")
    print("Fallback one-time approval code:")
    code, expires = prog_guard.create_approve_code_record()
    print(code)
    print(f"Expires (UTC): {expires.strftime('%Y-%m-%d %H:%M:%S')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
