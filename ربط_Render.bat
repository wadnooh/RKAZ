@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo تم الانتقال عن Render.
echo الرابط الرسمي لركاز:
echo   https://rekaz.wadnooh.com
echo.
echo دليل النشر الحالي: نشر_على_VPS.md
echo مرجع Render المتقاعد: نشر_على_Render.md
echo.

start "" "https://rekaz.wadnooh.com/login"
start "" "نشر_على_VPS.md"

echo.
pause
