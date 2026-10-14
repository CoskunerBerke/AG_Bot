@echo off
chcp 65001 >nul
title AG Bot Paneli
cd /d "%~dp0"
echo AG Bot Paneli aciliyor... Kapatmak icin bu pencereyi kapat.
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8765"
python main.py panel
echo.
echo Panel durdu. Hata varsa yukarida yaziyor.
pause
