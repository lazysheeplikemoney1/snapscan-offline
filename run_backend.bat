@echo off
REM SnapScan local backend. Prefer a project venv, then the Windows Python launcher.
cd /d "%~dp0backend"
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
) else (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
  ) else (
    python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
  )
)
pause
