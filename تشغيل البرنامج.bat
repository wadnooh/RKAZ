@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo Python غير مثبت أو غير موجود في PATH
  pause
  exit /b 1
)

python -c "import flask" >nul 2>&1
if errorlevel 1 (
  echo جاري تثبيت المتطلبات...
  python -m pip install -r requirements.txt
)

echo.
echo تشغيل نظام متابعة الأعمال العام — مكتب خدمات خريص
echo افتح المتصفح على: http://127.0.0.1:5070
echo.
start "" "http://127.0.0.1:5070"
python -m webapp.app
pause
