@echo off
cd /d "%~dp0"
if not exist manager.json (
  echo ERROR: copia config\manager.example.json como manager.json y configura IPs y tokens.
  pause
  exit /b 1
)
python -m mt5_manager.manager --config manager.json
pause
