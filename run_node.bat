@echo off
cd /d "%~dp0"
if not exist node.json (
  echo ERROR: copia config\node.example.json como node.json y configura este usuario.
  pause
  exit /b 1
)
python -m mt5_manager.node --config node.json
pause
