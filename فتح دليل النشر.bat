@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" "للعميل\الرابط_السحابي.md"
start "" "https://render.com/deploy?repo=https://github.com/wadnooh/RKAZ"
start "" "https://rekaz-alenjaz.onrender.com/login"
echo.
echo المستودع: https://github.com/wadnooh/RKAZ
echo Deploy:   https://render.com/deploy?repo=https://github.com/wadnooh/RKAZ
echo الرابط:   https://rekaz-alenjaz.onrender.com
echo.
pause
