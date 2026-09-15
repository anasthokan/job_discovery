@echo off
cd /d "%~dp0"
python -m uvicorn api:app --host 127.0.0.1 --port 9001
