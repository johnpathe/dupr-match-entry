@echo off
cd /d "%~dp0"
start "Pickle League Ledger Server" /min ".venv\Scripts\python.exe" match_app.py
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5057"
