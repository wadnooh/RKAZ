@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ============================================
echo   رابط عام للعميل - يعمل من اي جهاز
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo Python غير مثبت
  pause
  exit /b 1
)

python -c "import flask,waitress" >nul 2>&1
if errorlevel 1 (
  echo جاري تثبيت المتطلبات...
  python -m pip install -r requirements.txt
)

if not exist "tools\cloudflared.exe" (
  echo جاري تنزيل Cloudflare Tunnel...
  mkdir tools 2>nul
  powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'tools\cloudflared.exe'"
)

if not exist "tools\cloudflared.exe" (
  echo فشل تنزيل cloudflared. تحقق من الإنترنت ثم أعد المحاولة.
  pause
  exit /b 1
)

set HOST=127.0.0.1
set PORT=5070
set USE_WAITRESS=1

echo تشغيل النظام محلياً...
start "Rekaz-App" /min cmd /c "python -m webapp.app"

echo انتظار تشغيل السيرفر...
timeout /t 4 /nobreak >nul

echo.
echo جاري إنشاء رابط عام...
echo اترك هذه النافذة مفتوحة طالما العميل يراجع.
echo الرابط سيظهر بالأسفل (https://xxxx.trycloudflare.com)
echo.
echo --------------------------------------------

tools\cloudflared.exe tunnel --url http://127.0.0.1:5070 --no-autoupdate

echo.
echo تم إيقاف النفق. أغلق نافذة التطبيق إن بقيت مفتوحة.
pause
