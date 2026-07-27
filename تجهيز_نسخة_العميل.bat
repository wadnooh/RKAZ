@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "OUT=نسخة_العميل_للمراجعة"
set "ZIP=نسخة_العميل_للمراجعة.zip"

echo.
echo تجهيز نسخة العميل للمراجعة والضبط...
echo.

if exist "%OUT%" rmdir /s /q "%OUT%"
if exist "%ZIP%" del /f /q "%ZIP%"

mkdir "%OUT%"
mkdir "%OUT%\webapp"
mkdir "%OUT%\للعميل"

:: ملفات التشغيل والنشر
copy /y "requirements.txt" "%OUT%\" >nul
copy /y "Procfile" "%OUT%\" >nul
copy /y "render.yaml" "%OUT%\" >nul
copy /y "wsgi.py" "%OUT%\" >nul
copy /y ".gitignore" "%OUT%\" >nul
copy /y "تشغيل البرنامج.bat" "%OUT%\" >nul
copy /y "تشغيل للشبكة.bat" "%OUT%\" >nul
copy /y "فتح دليل النشر.bat" "%OUT%\" >nul
copy /y "نشر_على_Render.md" "%OUT%\" >nul
copy /y "README.md" "%OUT%\" >nul
copy /y "للعميل\اقرأني_أولاً.md" "%OUT%\للعميل\" >nul
copy /y "للعميل\الرابط_السحابي.md" "%OUT%\للعميل\" >nul

:: كود التطبيق بدون قواعد بيانات محلية أو كاش
robocopy "webapp" "%OUT%\webapp" /E /NFL /NDL /NJH /NJS /nc /ns /np /XD __pycache__ instance .git >nul

:: قاعدة بيانات جديدة تُنشأ عند أول تشغيل — لا ننسخ rakaz.db

powershell -NoProfile -Command "Compress-Archive -Path '%OUT%\*' -DestinationPath '%ZIP%' -Force"

echo.
echo تم إنشاء:
echo   المجلد: %OUT%
echo   الملف:  %ZIP%
echo.
echo أرسل ملف ZIP للعميل مع توجيهه لفتح: للعميل\اقرأني_أولاً.md
echo.
if /i not "%~1"=="/nopause" pause
