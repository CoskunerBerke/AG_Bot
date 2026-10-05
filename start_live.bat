@echo off
REM NASDAQ gün içi long/short botu - sürekli çalışır, borsa açılışını kendisi bekler.
REM SIM (sanal) mod. Alpaca paper için: python main.py live --alpaca
cd /d "%~dp0"
chcp 65001 >nul
:loop
python main.py live
echo Bot durdu, 60 sn sonra yeniden baslatiliyor... (kapatmak icin pencereyi kapatin)
timeout /t 60 >nul
goto loop
