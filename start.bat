@echo off

chcp 65001 >nul

echo ==========================================

echo   GASTROHUB - Startup

echo ==========================================



REM Set NGROK_AUTHTOKEN di .env atau environment Windows sebelum menjalankan.

REM Jangan commit authtoken ke GitHub.



cd /d "%~dp0"

.\venv\Scripts\python.exe start.py --ngrok-domain "%NGROK_STATIC_DOMAIN%" --ngrok-token "%NGROK_AUTHTOKEN%"

pause

