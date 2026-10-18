@echo off
chcp 65001 >nul
title AG2BOT - Grafik ve Taktik Terminali (localhost:8055)
cd /d "c:\Users\berke\OneDrive\Masaüstü\ag2bot"
start "" "http://localhost:8055"
python "c:\Users\berke\OneDrive\Masaüstü\ag2bot\nasdaq_bot\dashboard_all_tactics.py"
pause
