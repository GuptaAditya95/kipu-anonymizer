@echo off
REM Starts the anonymization service on Windows (same as run.sh).
REM Settings come from .env in this folder (copy .env.example to .env first), or from "set" in this window.
cd /d "%~dp0"
py -m pip install -q -r requirements.txt
if "%PORT%"=="" set PORT=9000
py -m uvicorn app.main:app --host 0.0.0.0 --port %PORT%
