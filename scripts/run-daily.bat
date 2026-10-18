@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  Arena Leaderboards - scheduled fetch and push
REM
REM  Called by Windows Task Scheduler. Safe to run by hand too.
REM
REM  Exits non-zero on failure so Task Scheduler records it and
REM  your notification rule can fire. Do NOT add /b or redirect
REM  away the exit code.
REM
REM  First-time setup (run once):
REM      schtasks /create /tn "Arena Leaderboard Fetch" ^
REM        /tr "D:\aabao\Documents\GitHub\arena-ai-leaderboards\scripts\run-daily.bat" ^
REM        /sc daily /st 09:00 /rl highest
REM ============================================================

set "HERE=%~dp0"
cd /d "%HERE%"

set "PY=%HERE%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY="
if not defined PY where python >nul 2>nul
if not errorlevel 1 if not defined PY set "PY=python"
if not defined PY (
    where py >nul 2>nul
    if not errorlevel 1 set "PY=py"
)
if not defined PY (
    echo [ERROR] Python not found in PATH.
    exit /b 1
)

"%PY%" run_and_push.py %*
set "RC=%errorlevel%"

if not "%RC%"=="0" (
    echo.
    echo [FAILED] exit code %RC% - see scripts\_out\run_and_push.log
)

exit /b %RC%
