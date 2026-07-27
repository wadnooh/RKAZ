@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo Install automatic backup pull on the main PC
echo.
set "RUN=%~dp0run_pull.bat"
set "PS1=%~dp0pull_backup.ps1"
set "CFG=%~dp0sync_config.json"
set "EX=%~dp0sync_config.example.json"
if exist "%~dp0???_?????_????????.ps1" copy /Y "%~dp0???_?????_????????.ps1" "%PS1%" >nul
if not exist "%CFG%" (
  if exist "%EX%" copy /Y "%EX%" "%CFG%" >nul
  echo Created sync_config.json - put the sync token from the backups page, then rerun.
  notepad "%CFG%"
  pause
  exit /b 2
)
call "%RUN%"
if errorlevel 1 (
  echo Pull failed - check sync_config.json token.
  pause
  exit /b 1
)
schtasks /Create /TN "RekazAutoBackupPull" /TR "\"%RUN%\"" /SC MINUTE /MO 15 /F >nul
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
