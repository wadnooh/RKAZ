@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo تثبيت سحب الحفظ التلقائي على الجهاز الرئيسي
echo.

set "PS1=%~dp0سحب_الحفظ_التلقائي.ps1"
set "CFG=%~dp0sync_config.json"
set "EX=%~dp0sync_config.example.json"

if not exist "%CFG%" (
  if exist "%EX%" copy /Y "%EX%" "%CFG%" >nul
  echo تم إنشاء sync_config.json — افتحه وضع رمز المزامنة من صفحة حفظ البيانات في الموقع.
  echo ثم أعد تشغيل هذا الملف.
  notepad "%CFG%"
  pause
  exit /b 2
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
if errorlevel 1 (
  echo فشل السحب التجريبي — راجع sync_config.json ورمز المزامنة.
  pause
  exit /b 1
)

schtasks /Create /TN "RekazAutoBackupPull" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"%PS1%\"" /SC MINUTE /MO 15 /F >nul
if errorlevel 1 (
  echo تعذر إنشاء مهمة المجدول — شغّل هذا الملف كمسؤول.
  pause
  exit /b 1
)

echo.
echo تم:
echo  - سحب تجريبي ناجح
echo  - مهمة Windows كل 15 دقيقة: RekazAutoBackupPull
echo  - المجلد: %~dp0حِفظات
echo.
pause
