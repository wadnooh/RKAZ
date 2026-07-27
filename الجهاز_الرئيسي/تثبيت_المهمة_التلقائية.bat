@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo Install automatic backup pull on the main PC
echo.

set "PS1=%~dp0سحب_الحفظ_التلقائي.ps1"
set "CFG=%~dp0sync_config.json"
set "EX=%~dp0sync_config.example.json"

if not exist "%CFG%" (
  if exist "%EX%" copy /Y "%EX%" "%CFG%" >nul
  echo Created sync_config.json - put the sync token from the backups page, then rerun.
  notepad "%CFG%"
  pause
  exit /b 2
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
if errorlevel 1 (
  echo Pull failed - check sync_config.json token.
  pause
  exit /b 1
)

schtasks /Create /TN "RekazAutoBackupPull" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"%PS1%\"" /SC MINUTE /MO 15 /F >nul
if errorlevel 1 (
  echo Could not create scheduled task - run as Administrator.
  pause
  exit /b 1
)

echo.
echo Done:
echo  - Test pull OK
echo  - Windows task every 15 min: RekazAutoBackupPull
echo  - Folder: %~dp0backups_inbox
echo.
pause
