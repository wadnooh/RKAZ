@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo Python غير مثبت أو غير موجود في PATH
  pause
  exit /b 1
)

python -c "import flask,waitress" >nul 2>&1
if errorlevel 1 (
  echo جاري تثبيت المتطلبات...
  python -m pip install -r requirements.txt
)

for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do set IP=%%a
set IP=%IP: =%

set HOST=0.0.0.0
set PORT=5070
set USE_WAITRESS=1

echo.
echo تشغيل على الشبكة المحلية (Waitress)
echo من هذا الجهاز:   http://127.0.0.1:5070
if defined IP echo من أجهزة أخرى: http://%IP%:5070
echo أغلق هذه النافذة لإيقاف التشغيل.
echo.
start "" "http://127.0.0.1:5070"
python -m webapp.app
pause
