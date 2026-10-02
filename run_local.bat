@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Muhit yaratilmoqda...
  python -m venv .venv || goto fail
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto fail
)
if not exist ".env" copy ".env.example" ".env" >nul & echo .env yaratildi: avval uni to'ldiring & notepad .env & goto end
echo Admin panel: http://127.0.0.1:8100/admin/   (BOT_MODE=polling bo'lsa bot ham ishlaydi)
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8100
goto end
:fail
echo XATO
:end
pause
