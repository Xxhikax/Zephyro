@echo off
setlocal
set SCRIPT_DIR=%~dp0
set BACKEND_DIR=%SCRIPT_DIR%backend
set HTTP_PORT=8000

echo [Zephyro] Iniciando backend en puerto %HTTP_PORT%...
cd /d %BACKEND_DIR%
set PYTHONIOENCODING=utf-8
start /B "%BACKEND_DIR%\venv\Scripts\python.exe" -X utf8 web_app.py

echo [Zephyro] Esperando que el backend responda...
:wait_loop
timeout /t 1 /nobreak >nul
curl -sf http://localhost:%HTTP_PORT%/api/health >nul 2>&1 && goto :backend_ready
goto :wait_loop

:backend_ready
echo [Zephyro] Backend listo!
cd /d %SCRIPT_DIR%
echo [Zephyro] Iniciando Tauri...
npm run dev
