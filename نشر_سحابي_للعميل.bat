@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo فتح صفحة النشر السحابي على Render...
echo بعد اكتمال النشر انسخ الرابط وأرسله للعميل.
echo.
echo بيانات الدخول: admin / admin123
echo.

start "" "https://render.com/deploy?repo=https://github.com/wadnooh/rekaz"
start "" "للعميل\الرابط_السحابي.md"

pause
