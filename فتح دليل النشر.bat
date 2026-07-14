@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" "نشر_على_Render.md"
start "" "https://dashboard.render.com/"
start "" "https://github.com/new"
echo.
echo تم فتح:
echo  1) دليل النشر
echo  2) لوحة Render
echo  3) إنشاء مستودع GitHub
echo.
echo اتبع الخطوات في ملف نشر_على_Render.md ثم أرسل رابط الخدمة للعميل.
pause
