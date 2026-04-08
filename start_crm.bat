@echo off
chcp 65001 >nul
echo.
echo  ╔══════════════════════════════════════╗
echo  ║   💎 Diamond CRM - Starting...       ║
echo  ╚══════════════════════════════════════╝
echo.
pip install flask >nul 2>&1
echo  ✅ Flask ready
echo  🌐 Opening http://127.0.0.1:5050
echo  ⏹️  To stop: Ctrl+C
echo.
start http://127.0.0.1:5050
python app.py
pause
