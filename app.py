"""توافق خلفي — شغّل دائماً عبر webapp.app

استخدم:
  python -m webapp.app
أو:
  تشغيل البرنامج.bat
"""

from webapp.app import app, create_app, main

__all__ = ["app", "create_app", "main"]

if __name__ == "__main__":
    main()
