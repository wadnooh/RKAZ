@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ربط / نشر ركاز على Render من المستودع الجديد
echo المستودع: https://github.com/wadnooh/RKAZ
echo.

start "" "https://dashboard.render.com/"
timeout /t 2 >nul
start "" "https://render.com/deploy?repo=https://github.com/wadnooh/RKAZ"

echo.
echo تم فتح:
echo  1) لوحة Render
echo  2) صفحة Deploy للمستودع wadnooh/RKAZ
echo.
echo الخطوات:
echo  - سجّل دخول Render بحساب GitHub (wadnooh)
echo  - وافق على المستودع wadnooh/RKAZ إن طُلب
echo  - اضغط Apply / Deploy على الخطة Free
echo  - انتظر حتى تصبح الحالة Live
echo.
echo الرابط المتوقع بعد النشر:
echo  https://rekaz-alenjaz.onrender.com
echo.
pause
